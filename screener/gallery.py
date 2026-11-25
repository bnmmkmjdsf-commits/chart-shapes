"""決算の良かった銘柄（業績タブ）と、持ち株・注目の銘柄を、日米それぞれ週足のチャートで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取って作る。GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html
    python -m screener.gallery --markets jp     日本株だけ取り直す（米国株は公開中のページのデータを使う）

見た目だけ変えたときは、株価を取り直さない screener.retemplate で作り直せる。
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import cross, earnings, jpx, prices, published, watch

# チャートに描く足の本数
CHART_BARS = 52
# 銘柄ごとのファイルに入れる足と、描く移動平均の本数（短期・中期・長期）。
# カードのチャートと、持ち株・注目の札の「抜け」（短期線と中期線）は週足で見る。月足はポートフォリオ（screener/portfolio.py）が使う。
# 日足などほかの足は、銘柄を押した先の株探で見る。日足は、週足を作るのと、決算の発表からの値動き・今の株価・売買代金に使う
FRAMES = {"w": {"label": "週足", "ma": (13, 26, 52)}, "m": {"label": "月足", "ma": (12, 24, 60)}}
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
# 株価を取る銘柄の時価総額の下限（日本株は全銘柄、米国株は3億ドル以上）。
# 業績タブ・持ち株・注目のどの銘柄でもチャートを出せるよう広く取る。米国株は数が多いので下限を置く
HOLD_MIN_MARKET_CAP = {"jp": 0, "us": 3e8}
# 業績タブ（好決算・改善）に載せる銘柄の、1日の売買代金（終値 × 出来高）の下限（日本株は30億円、米国株は2億ドル）。
# 売りたいときにすぐ売れる銘柄だけにする。売買代金は直近 TURNOVER_DAYS 日の中央値で見る
# （平均でなく中央値にするのは、決算の発表の日などに出来高が跳ねた数日だけで入らないようにするため）
MIN_TURNOVER = {"jp": 30e8, "us": 2e8}
# ポートフォリオ（screener/portfolio.py）が使う銘柄の、1日の売買代金の下限（日本株は5億円、米国株は2000万ドル）。業績タブより広く取る
PORTFOLIO_MIN_TURNOVER = {"jp": 5e8, "us": 2e7}
TURNOVER_DAYS = 20
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

# 株価の指数。個別銘柄と同じように、持ち株と注目のカードにチャートと抜けの札を出す。
# {表に出すコード: (Yahoo の記号, 名前, 業種欄の表示)}
INDEXES = {"jp": {"N225": ("^N225", "日経平均", "指数")}, "us": {"NDX": ("^NDX", "ナスダック100", "指数")}}


def _symbol(market, code):
    """表に出すコードから、Yahoo の記号を引く（日本株は 7203 → 7203.T、指数は N225 → ^N225）"""
    if code in INDEXES[market]:
        return INDEXES[market][code][0]
    return f"{code}.T" if market == "jp" else code


def _with_indexes(market, info):
    """銘柄の情報の表に、その市場の指数を足す。株価は日足から入れるので、ここでは空"""
    rows = {symbol: {"code": code, "name": name, "sector": sector, "market_cap": np.nan, "price": np.nan}
            for code, (symbol, name, sector) in INDEXES[market].items()}
    if not rows:
        return info
    return pd.concat([info, pd.DataFrame.from_dict(rows, orient="index")[INFO_COLUMNS]])


def _round(value):
    """チャート用に有効数字4桁へ丸める（ページを軽くするため）。値が無い足は None"""
    return None if pd.isna(value) else float(f"{value:.4g}")


def _permille(ratio):
    """比（0.012 なら 12）を千分率の整数にする。値が無い足は None"""
    return [None if not np.isfinite(v) else int(round(v * 1000)) for v in ratio]


def _fill_live_close(bars, price):
    """途中の足（今日）の終値が空なら、今の株価で埋める。

    Yahoo（yfinance）が途中の足を始値・高値・安値・出来高だけ入れて終値を空で返すことがある。
    そのままだと全銘柄の直近の終値と移動平均が空になる。
    """
    close = bars["close"]
    last = close.index[-1]
    missing = close.loc[last].isna() & bars["open"].loc[last].notna()
    fill = price.reindex(close.columns)[missing].dropna()
    close.loc[last, fill.index] = fill.to_numpy()


# 市場ごとの時間帯と大引けの時刻
MARKET_CLOSE = {"jp": ("Asia/Tokyo", 15, 30), "us": ("America/New_York", 16, 0)}
# 売買代金タブに載せる銘柄の数（日米それぞれ。SBI証券のランキングと同じ50位まで）
RANK_COUNT = 50


def _day_done(day, market):
    """その日の取引が終わったか（大引けを過ぎたか）。祝日は見ない"""
    tz, hour, minute = MARKET_CLOSE[market]
    return pd.Timestamp.now(tz) >= pd.Timestamp(day.date()).tz_localize(tz) + pd.Timedelta(hours=hour, minutes=minute)


def _rank(info, daily, market):
    """売買代金タブ。取引が終わった直近の1日の売買代金（終値 × 出来高）の大きい順に RANK_COUNT 銘柄。指数は入れない。

    返すのは {"date": "2026/11/25", "stocks": [{code, name, sector, turnover（売買代金）, change（前の日からの値動き %）}]}
    """
    close, volume = daily["close"], daily["volume"]
    at = -1 if _day_done(close.index[-1], market) else -2
    turnover = (close.iloc[at] * volume.iloc[at]).reindex(info.index).dropna()
    turnover = turnover[[not symbol.startswith("^") for symbol in turnover.index]]
    stocks = []
    for symbol, value in turnover.nlargest(RANK_COUNT).items():
        row, now, before = info.loc[symbol], close.iloc[at][symbol], close.iloc[at - 1][symbol]
        stocks.append({"code": row["code"], "name": row["name"] or row["code"],
                       "sector": "" if pd.isna(row["sector"]) else row["sector"], "turnover": round(float(value)),
                       "change": round(float((now / before - 1) * 100), 1) if before and not pd.isna(before) else None})
    return {"date": f"{close.index[at]:%Y/%m/%d}", "stocks": stocks}


def _week_done(monday, market):
    """その週（monday はその週の月曜）の取引が終わったか（金曜の大引けを過ぎたか）。祝日は見ない"""
    tz, hour, minute = MARKET_CLOSE[market]
    end = pd.Timestamp(monday.date()).tz_localize(tz) + pd.Timedelta(days=4, hours=hour, minutes=minute)
    return pd.Timestamp.now(tz) >= end


def _crosses(info, weekly, market):
    """全銘柄の、週足でのいちばん新しい抜け {コード: {"w": [向き（上抜けが1）, 何本前か]}}。持ち株と注目のカードの札に使う。

    途中の週は終値がまだ動くので、取引が終わった直近の週までで見る
    """
    close = weekly["close"]
    done = len(close) if _week_done(close.index[-1], market) else len(close) - 1
    short, mid, _ = cross.moving_averages(close, FRAMES["w"]["ma"])
    return {info.at[symbol, "code"]: {"w": list(last)}
            for symbol, last in cross.last_cross(short, mid, done).items() if symbol in info.index}


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
    info = _with_indexes(market, INFO[market](log))
    wide = info.index[(info["market_cap"] >= HOLD_MIN_MARKET_CAP[market]) & info["price"].notna()].tolist()
    wide += [symbol for symbol, _, _ in INDEXES[market].values()]  # 指数は時価総額も株価も無いので、決め打ちで入れる
    log(f"{market}: 株価を取るのは {len(wide):,} 銘柄。日足を取得中...")
    daily = prices.fetch_bars(wide, "1d", "2y", "日足", log)  # 週足（52週線とチャートの52本）を作れるよう2年取る
    _fill_live_close(daily, info["price"])
    weekly = prices.to_weekly(daily)  # 週足は日足から作る（別に取らない）
    log(f"{market}: 月足を取得中...")
    monthly = prices.fetch_bars(wide, "1mo", "10y", "月足", log)  # 60か月線とチャートの52本が描けるよう長く取る
    _fill_live_close(monthly, info["price"])
    count = _stock_files(info, {"w": weekly, "m": monthly}, site / STOCK_DIR / market)
    log(f"{market}: 銘柄ごとのチャートのファイルを {count:,} 個書き出しました")
    _write_liquid(info, daily, site / STOCK_DIR / market, market, log)
    # cross は持ち株と注目のカードの札、prices は今の株価（☆を付けてからの値動きと、持ち株に足すコードの確かめに使う）
    result = {"cross": _crosses(info, weekly, market), "prices": _prices(info, daily)}
    if market == "jp":
        # 日本株の業績タブ（株探の決算速報で、好決算と、良くなった決算（改善）のあと株価が上がった銘柄）
        earn = earnings.build(info, {"d": daily}, log)
    else:
        # 米国株の業績タブ（Yahoo Finance の決算の予定表で、1株利益が予想を大きく上回った銘柄）。読めなくても止めない
        try:
            earn = earnings.build_us(info, daily["close"], log)
        except Exception as error:
            log(f"米国株の決算が読めませんでした（{error}）。業績タブの米国株は、公開中のページのものを残します")
            earn = _published_us_earn(log)
    result["earn"] = _mark_since(_liquid(earn, market, info, daily, log), market, log)
    result["rank"] = _rank(info, daily, market)
    return result


def _mark_since(earn, market, log):
    """業績タブの銘柄に、一覧に入った日（since。日付の文字列）を付ける。ページが「新」と「11/20から」を出すのに使う。

    公開中のページの同じ一覧（好決算・改善）にもう入っていた銘柄は、そのときの日付を引き継ぐ。新しく入った銘柄は今日。
    前の形のページ（since が無い）に入っていた銘柄は、いつ入ったか分からないので空にする（「新」も日付も出さない）
    """
    today = f"{pd.Timestamp.now(MARKET_CLOSE[market][0]):%Y-%m-%d}"
    try:
        before = json.loads(published.payload())[market]["earn"]
    except Exception as error:
        log(f"  公開中のページの業績タブが読めないので、入った日は今日にします（{error}）")
        before = {}
    out = dict(earn)
    for key in ("stocks", "improve"):
        known = {s["code"]: s.get("since", "") for s in before.get(key, [])}
        out[key] = [{**s, "since": known[s["code"]] if s["code"] in known else today} for s in earn.get(key, [])]
    return out


def _write_liquid(info, daily, folder, market, log):
    """ポートフォリオ（screener/portfolio.py）が使う、1日の売買代金が PORTFOLIO_MIN_TURNOVER 以上の銘柄のコード（_liquid.json）。
    売買の少ない銘柄を組み入れないため"""
    turnover = (daily["close"] * daily["volume"]).iloc[-TURNOVER_DAYS:].median()
    codes = sorted(info.at[symbol, "code"] for symbol, value in turnover.items()
                   if symbol in info.index and value >= PORTFOLIO_MIN_TURNOVER[market])
    (folder / "_liquid.json").write_text(json.dumps(codes), encoding="utf-8")
    log(f"{market}: 売買代金が足りる銘柄 {len(codes):,} 個（ポートフォリオ用）")


def _liquid(earn, market, info, daily, log):
    """業績タブの銘柄を、1日の売買代金（直近 TURNOVER_DAYS 日の中央値）が MIN_TURNOVER 以上の銘柄だけにし、
    売買代金の大きい順に並べる（大型株・よく売買される銘柄が上に来る）"""
    limit = MIN_TURNOVER[market]
    turnover = (daily["close"] * daily["volume"]).iloc[-TURNOVER_DAYS:].median().dropna()
    by_code = {info.at[symbol, "code"]: value for symbol, value in turnover.items() if symbol in info.index}
    keep = lambda items: sorted((s for s in items if by_code.get(s["code"], 0) >= limit),
                                key=lambda s: by_code[s["code"]], reverse=True)
    out = {**earn, "stocks": keep(earn.get("stocks", [])), "improve": keep(earn.get("improve", [])), "min_turnover": limit}
    log(f"{market}: 売買代金で絞った業績タブ: 好決算 {len(earn.get('stocks', []))} → {len(out['stocks'])} 銘柄、"
        f"改善 {len(earn.get('improve', []))} → {len(out['improve'])} 銘柄（1日 {limit:,.0f} 以上）")
    return out


def _published_us_earn(log):
    """米国株の決算が読めなかったときの代わり。公開中のページの業績タブ（米国株）を、今の決まりで絞り直して使う"""
    empty = {"days": earnings.DAYS, "stocks": [], "improve": []}
    try:
        earn = json.loads(published.payload())["us"]["earn"]
    except Exception as error:
        log(f"  公開中のページの米国株の業績も使えません（{error}）。空にします")
        return empty
    keep = lambda items: [s for s in items if s.get("move") is not None and s["move"] >= earnings.MIN_MOVE
                          and s.get("excess", s["move"]) >= earnings.MIN_MOVE]
    return {"days": earn.get("days", earnings.DAYS), "stocks": keep(earn.get("stocks", [])), "improve": keep(earn.get("improve", []))}


def _published(log):
    """公開中のページのデータ。取れない・形が古いときは空（その市場は取り直すことになる）。

    前の形のページにあった、抜けの一覧（frames）と一覧の銘柄（top）は、もう使わないので捨てる
    """
    try:
        data = json.loads(published.payload())
        return {market: {k: v for k, v in value.items() if k not in ("frames", "top")}
                for market, value in data.items() if "cross" in value and "prices" in value}
    except Exception as error:
        log(f"公開中のページのデータは使えません（{error}）")
        return {}


def main():
    parser = argparse.ArgumentParser(description="日米の業績と、持ち株・注目のページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    parser.add_argument("--markets", choices=["both", "jp", "us"], default="both",
                        help="株価を取り直す市場。片方だけのとき、もう片方は公開中のページのデータを使う")
    args = parser.parse_args()
    out = args.out

    # 朝は米国市場、夕方は東証が閉じた後に動かすので、動いていない方の市場は取り直さない
    keep = _published(print) if args.markets != "both" else {}
    data = {}
    for market in ["jp", "us"]:
        if args.markets in ("both", market) or market not in keep:
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
        earn = data[market].get("earn") or {}
        print(f"{label}: 好決算 {len(earn.get('stocks', []))} 銘柄、改善 {len(earn.get('improve', []))} 銘柄")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
