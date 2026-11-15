"""1時間足・日足・週足・月足で短期の移動平均線が中期線を上に抜けた・下に抜けた銘柄を、日米それぞれチャートで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取って作る。GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html
    python -m screener.gallery --markets jp     日本株だけ取り直す（米国株は公開中のページのデータを使う）
    python -m screener.gallery --markets jp --hourly   取引時間中の更新。日本株の1時間足だけを、一覧の銘柄と watch.json の銘柄について取り直す

見た目だけ変えたときは、株価を取り直さない screener.retemplate で作り直せる。
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import cross, jpx, prices, published, watch

# チャートに描く足の本数
CHART_BARS = 52
# 足の種類ごとの、チャートに描く移動平均の本数（短期・中期・長期）。抜けは、短期線と中期線で見る
FRAMES = {
    "h": {"label": "1時間足", "ma": (25, 75, 200)},
    "d": {"label": "日足", "ma": (5, 25, 75)},
    "w": {"label": "週足", "ma": (13, 26, 52)},
    "m": {"label": "月足", "ma": (12, 24, 60)},
}
TEMPLATE = Path(__file__).with_name("gallery_template.html")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "site" / "index.html"
# 米国株の業種は Yahoo のセクター（11分類）を日本語にして出す。日本株は JPX の33業種をそのまま使う
US_SECTORS = {
    "Basic Materials": "素材",
    "Communication Services": "通信サービス",
    "Consumer Cyclical": "一般消費財",
    "Consumer Defensive": "生活必需品",
    "Energy": "エネルギー",
    "Financial Services": "金融",
    "Healthcare": "ヘルスケア",
    "Industrials": "資本財",
    "Real Estate": "不動産",
    "Technology": "テクノロジー",
    "Utilities": "公益事業",
}
INFO_COLUMNS = ["code", "name", "sector", "market_cap", "price"]
# 一覧に載せるのは、売買代金（終値 × 出来高）の大きい順に TOP_COUNT 銘柄（日米それぞれ）。全銘柄を見るのをやめ、
# いつでも売り買いできる、資金の集まっている銘柄だけにする。売買代金は直近 TURNOVER_DAYS 日の中央値で見る。
# 平均でなく中央値にするのは、決算などで出来高が跳ねた数日だけで入らないようにするため
TOP_COUNT = 200
TURNOVER_DAYS = 20
# 一覧に載せる銘柄の株価の下限（日本株は100円、米国株は5ドル）。株価が低いと1円の動きが大きな%になる
MIN_PRICE = {"jp": 100, "us": 5}
# 株価を取る銘柄の時価総額の下限（日本株は全銘柄、米国株は3億ドル以上）。
# 持ち株や注目の銘柄が一覧の下限より小さくてもチャートと抜けの札を出せるよう、一覧より広く取る。米国株は数が多いので下限を置く
HOLD_MIN_MARKET_CAP = {"jp": 0, "us": 3e8}
# 銘柄ごとのチャートのファイルを置くフォルダ（ページと同じ場所の下。その下に市場ごとのフォルダ）
STOCK_DIR = "s"


def _us_info(log):
    """米国株の銘柄の情報（code・name・sector（業種）・market_cap・price）"""
    log("米国株の銘柄と時価総額を取得中...")
    us = prices.fetch_quotes(log)
    us["name"] = us["name"].fillna(us["symbol"])
    # GOOGL / GOOG のように同じ会社の銘柄が複数あるときは、売買代金が大きい方だけ残す
    us["dollar_volume"] = us["avg_volume"] * us["price"]
    us = us.sort_values("dollar_volume", ascending=False).drop_duplicates("name")
    return us.assign(code=us["symbol"], sector=us["sector"].map(US_SECTORS)).set_index("symbol")[INFO_COLUMNS]


def _jp_info(log):
    """日本株の銘柄の情報（code・name・sector（業種）・market_cap・price）"""
    log("日本株の銘柄と時価総額を取得中...")
    jp = jpx.listing().merge(prices.fetch_jp_quotes(log)[["symbol", "market_cap", "price"]], on="symbol", how="left")
    return jp.set_index("symbol")[INFO_COLUMNS]


INFO = {"jp": _jp_info, "us": _us_info}


def _round(value):
    """チャート用に有効数字4桁へ丸める（ページを軽くするため）。値が無い足は None"""
    return None if pd.isna(value) else float(f"{value:.4g}")


def _permille(ratio):
    """比（0.012 なら 12）を千分率の整数にする。値が無い足は None"""
    return [None if not np.isfinite(v) else int(round(v * 1000)) for v in ratio]


def _fill_live_close(bars, price):
    """途中の足（今日・今週・今月）の終値が空なら、今の株価で埋める。

    週の初めなど、Yahoo（yfinance）が途中の足を始値・高値・安値・出来高だけ入れて終値を空で返すことがある。
    そのままだと全銘柄の直近の終値と移動平均が空になり、候補が0件になる。
    """
    close = bars["close"]
    last = close.index[-1]
    missing = close.loc[last].isna() & bars["open"].loc[last].notna()
    fill = price.reindex(close.columns)[missing].dropna()
    close.loc[last, fill.index] = fill.to_numpy()


# 市場ごとの時間帯と大引けの時刻
MARKET_CLOSE = {"jp": ("Asia/Tokyo", 15, 30), "us": ("America/New_York", 16, 0)}


def _bar_done(start, frame, market):
    """その足（start は足の始まりの日付）の取引が終わったか。祝日は見ない。

    1時間足は始まりから1時間たったら、日足はその日の大引け、週足は金曜の大引け、月足は翌月に入ったら、終わったとみなす。
    """
    tz, hour, minute = MARKET_CLOSE[market]
    if frame == "h":
        return pd.Timestamp.now(tz) >= start.tz_localize(tz) + pd.Timedelta(hours=1)
    day = pd.Timestamp(start.date()).tz_localize(tz)
    close_time = pd.Timedelta(hours=hour, minutes=minute)
    end = {"d": day + close_time,
           "w": day + pd.Timedelta(days=4) + close_time,
           "m": day + pd.DateOffset(months=1)}[frame]
    return pd.Timestamp.now(tz) >= end


BAR_NAME = {"h": "{:%m/%d %H:%M}", "d": "{:%Y/%m/%d}", "w": "{:%Y/%m/%d} の週", "m": "{:%Y/%m}"}


def _frame(frame, market, info, bars, listed):
    """1つの足の種類（日足・週足・月足）について、抜けた銘柄の一覧と、全銘柄のいちばん新しい抜けを作る。

    bars は株価を取った全銘柄。一覧には、その中の listed（売買代金の大きい順に並んだ銘柄）だけ載せる。
    返すのは（ページに載せる一覧, {銘柄: (向き, 何本前か)}）。
    """
    _fill_live_close(bars, info["price"])
    # 途中の足は終値がまだ動くので、取引が終わった直近の足で見る
    shown = bars["close"].index[-CHART_BARS:]
    at = -1 if _bar_done(shown[-1], frame, market) else -2
    short, mid, long = cross.moving_averages(bars["close"], FRAMES[frame]["ma"])
    found = cross.last_cross(short, mid, len(bars["close"]) + at + 1)
    series = {"o": bars["open"], "h": bars["high"], "l": bars["low"], "c": bars["close"],
              "maS": short, "maM": mid, "maL": long}
    signal = len(shown) + at  # 抜けた足が、チャートの左から何本目か
    rows = info.loc[[symbol for symbol in listed if symbol in info.index]]

    def stock(symbol, row):
        s = {
            "code": row["code"],
            "name": row["name"] or row["code"],
            "sector": "" if pd.isna(row["sector"]) else row["sector"],
            "signal": signal,
        }
        s.update({key: [_round(v) for v in table[symbol].loc[shown]] for key, table in series.items()})
        return s

    def side(direction):
        """直近の足で上抜け（1）・下抜け（-1）した銘柄を、売買代金の大きい順に"""
        stocks = [stock(symbol, row) for symbol, row in rows.iterrows() if found.get(symbol) == (direction, 0)]
        return {"count": len(stocks), "stocks": stocks}

    return {"bar": BAR_NAME[frame].format(shown[at]), "signal": signal, "up": side(1), "down": side(-1)}, found


def _charts(frame, bars, symbols):
    """銘柄ごとのファイルに入れる、1つの足の種類のチャート。{銘柄: {c, p, o, h, l}}。

    c はチャートに描く終値、p はそれより前の終値（移動平均を描くのに使う）。
    o・h・l は終値からのずれを千分率で持つ（ファイルを軽くするため）:
    始値は終値から、高値は始値と終値の高い方から上へ、安値は低い方から下へ。
    """
    close = bars["close"]
    first = max(0, len(close) - CHART_BARS)
    before = max(0, first - max(FRAMES[frame]["ma"]))
    tables = {key: bars[key].to_numpy(dtype=float) for key in ["open", "high", "low", "close"]}
    charts = {}
    for column, symbol in enumerate(close.columns):
        if symbol not in symbols:
            continue
        o, h, low, c = (tables[key][first:, column] for key in ["open", "high", "low", "close"])
        if np.isnan(c).all():
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            charts[symbol] = {
                "c": [_round(v) for v in c],
                "p": [_round(v) for v in tables["close"][before:first, column]],
                "o": _permille(o / c - 1),
                "h": _permille(h / np.maximum(o, c) - 1),
                "l": _permille(1 - low / np.minimum(o, c)),
            }
    return charts


def _write_stock(folder, code, data):
    (folder / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _stock_files(info, frames, folder):
    """持ち株と注目のカード用に、株価を取った全銘柄のチャートを、銘柄ごとのファイル（コード.json）に書き出す。

    どの銘柄が登録されるかはページを作る側では分からない。全銘柄ぶんをページに埋め込むと重いので、
    ファイルを分けて、ページが要る銘柄だけ読みに行く。frames は {足の種類: ローソク足の表}。
    ファイルの中身は {name, sector, 足の種類: _charts の1銘柄ぶん}。
    """
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):  # 上場をやめた銘柄のファイルを残さない
        old.unlink()
    stocks = {}
    symbols = set(info.index)
    for frame, bars in frames.items():
        for symbol, chart in _charts(frame, bars, symbols).items():
            stocks.setdefault(symbol, {})[frame] = chart
    for symbol, charts in stocks.items():
        row = info.loc[symbol]
        _write_stock(folder, row["code"],
                     {"name": row["name"] or row["code"], "sector": "" if pd.isna(row["sector"]) else row["sector"], **charts})
    return len(stocks)


def _prices(info, daily):
    """注目の銘柄の「☆を付けてからの値動き」用の、全銘柄の今の株価。

    株価を取っていない銘柄（時価総額が下限を割った銘柄など）も、スクリーナーの株価で埋まる。
    ページは、持ち株に足すコードがあるかどうかも、この表で確かめる
    """
    close = daily["close"].iloc[-1].reindex(info.index).fillna(info["price"]).dropna()
    return {info.at[symbol, "code"]: round(float(value), 2) for symbol, value in close.items()}


def _build(market, log, site):
    """1つの市場の株価を取り直して、ページに載せるデータを作る。銘柄ごとのチャートのファイルは site の下に書き出す"""
    info = INFO[market](log)
    # 株価は、持ち株と注目のチャートと抜けの札のために、一覧より広く取る。一覧には、その中の売買代金の大きい銘柄だけ載せる
    wide = info.index[(info["market_cap"] >= HOLD_MIN_MARKET_CAP[market]) & info["price"].notna()].tolist()
    log(f"{market}: 株価を取るのは {len(wide):,} 銘柄。日足を取得中...")
    daily = prices.fetch_bars(wide, "1d", "2y", "日足", log)
    _fill_live_close(daily, info["price"])
    weekly = prices.to_weekly(daily)  # 週足は日足から作る（別に取らない）
    log(f"{market}: 月足を取得中...")
    monthly = prices.fetch_bars(wide, "1mo", "10y", "月足", log)  # 60か月線を描くので長く取る
    log(f"{market}: 1時間足を取得中...")
    hourly = prices.fetch_bars(wide, "1h", "3mo", "1時間足", log)  # 200本線を描くので長く取る（1日に7本ほど）
    priced = info.index[info["price"] >= MIN_PRICE[market]]
    turnover = (daily["close"] * daily["volume"]).iloc[-TURNOVER_DAYS:].median().reindex(priced).dropna()
    listed = turnover.nlargest(TOP_COUNT).index.tolist()
    log(f"{market}: 一覧に載せるのは、売買代金の大きい順に {len(listed):,} 銘柄"
        f"（{TOP_COUNT} 番目の売買代金は1日 {turnover.nlargest(TOP_COUNT).iloc[-1]:,.0f}）")
    frames, crosses = {}, {}
    for frame, bars in [("h", hourly), ("d", daily), ("w", weekly), ("m", monthly)]:
        frames[frame], found = _frame(frame, market, info, bars, listed)
        for symbol, last in found.items():
            if symbol in info.index:
                crosses.setdefault(info.at[symbol, "code"], {})[frame] = list(last)
    count = _stock_files(info, {"h": hourly, "d": daily, "w": weekly, "m": monthly}, site / STOCK_DIR / market)
    log(f"{market}: 銘柄ごとのチャートのファイルを {count:,} 個書き出しました")
    # cross は、全銘柄のいちばん新しい抜け {コード: {足の種類: [向き, 何本前か]}}。持ち株と注目のカードの札に使う。
    # top は、一覧に載せる銘柄の [銘柄, コード, 名前, 業種]（売買代金の大きい順）。取引時間中の更新（_hourly）が使う
    top = [[symbol, *("" if pd.isna(v) else v for v in info.loc[symbol, ["code", "name", "sector"]])]
           for symbol in listed if symbol in info.index]
    return {"frames": frames, "cross": crosses, "top": top, "prices": _prices(info, daily)}


def _hourly(market, data, log, site):
    """取引時間中の更新。公開中のページのデータ（data）のうち、1時間足だけを取り直して入れ替える。

    取り直すのは、一覧に載せる銘柄（data["top"]）と、持ち株と注目の銘柄（watch.json）だけ。数十秒で済む。
    1時間足の一覧と抜けの札、今の株価、銘柄ごとのファイルの1時間足を入れ替える。日足・週足・月足はそのまま。
    """
    folder = site / STOCK_DIR / market
    rows = {symbol: {"code": code, "name": name, "sector": sector} for symbol, code, name, sector in data["top"]}
    listed = watch.shared(market)
    for code in [*listed["hold"], *(fav["code"] for fav in listed["fav"])]:
        symbol = f"{code}.T" if market == "jp" else code
        file = folder / f"{code}.json"
        if symbol in rows or not file.exists():  # ファイルが無いのは、株価を取っていない銘柄（コードの間違いなど）
            continue
        known = json.loads(file.read_text(encoding="utf-8"))
        rows[symbol] = {"code": code, "name": known["name"], "sector": known["sector"]}
    info = pd.DataFrame.from_dict(rows, orient="index").assign(price=np.nan)
    log(f"{market}: 1時間足を取り直すのは {len(info):,} 銘柄")
    hourly = prices.fetch_bars(info.index.tolist(), "1h", "3mo", "1時間足", log)
    data["frames"]["h"], found = _frame("h", market, info, hourly, [row[0] for row in data["top"]])
    last = hourly["close"].ffill().iloc[-1]
    for symbol, chart in _charts("h", hourly, set(info.index)).items():
        code = info.at[symbol, "code"]
        if symbol in found:
            data["cross"].setdefault(code, {})["h"] = list(found[symbol])
        else:
            data["cross"].get(code, {}).pop("h", None)
        if not pd.isna(last[symbol]):
            data["prices"][code] = round(float(last[symbol]), 2)
        file = folder / f"{code}.json"
        known = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {
            "name": info.at[symbol, "name"], "sector": info.at[symbol, "sector"]}
        _write_stock(folder, code, {**known, "h": chart})
    return data


def _published(log):
    """公開中のページのデータ。取れない・形が古いときは空（その市場は取り直すことになる）"""
    try:
        data = json.loads(published.payload())
        return {market: value for market, value in data.items()
                if set(value.get("frames", {})) == set(FRAMES) and "cross" in value and "top" in value
                and all("signal" in frame for frame in value["frames"].values())}
    except Exception as error:
        log(f"公開中のページのデータは使えません（{error}）")
        return {}


def main():
    parser = argparse.ArgumentParser(description="日米の「移動平均の抜け」ページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    parser.add_argument("--markets", choices=["both", "jp", "us"], default="both",
                        help="株価を取り直す市場。片方だけのとき、もう片方は公開中のページのデータを使う")
    parser.add_argument("--hourly", action="store_true",
                        help="取引時間中の更新。--markets の市場の1時間足だけを、一覧の銘柄と watch.json の銘柄について取り直す")
    args = parser.parse_args()
    out = args.out

    # 朝は米国市場、夕方は東証が閉じた後に動かすので、動いていない方の市場は取り直さない
    keep = _published(print) if args.markets != "both" or args.hourly else {}
    if args.hourly and set(keep) != {"jp", "us"}:
        # 1時間足だけの更新は、公開中のページのデータが土台。使えないときは何も書かずにやめる（今のページを残す）
        raise SystemExit("公開中のページのデータが使えないので、1時間足だけの更新はできません")
    data = {}
    for market in ["jp", "us"]:
        if args.hourly:
            data[market] = _hourly(market, keep[market], print, out.parent) if args.markets in ("both", market) else keep[market]
        elif args.markets in ("both", market) or market not in keep:
            data[market] = _build(market, print, out.parent)
        else:
            print(f"{market}: 公開中のページのデータを使います")
            data[market] = keep[market]
    for market in data:
        data[market]["shared"] = watch.shared(market)  # 持ち株と注目は、株価を取り直さない市場のぶんも、いつも今のファイルから入れる
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        for frame, result in data[market]["frames"].items():
            print(f"{label} {FRAMES[frame]['label']}（{result['bar']}）: "
                  f"上抜け {result['up']['count']} 銘柄、下抜け {result['down']['count']} 銘柄")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
