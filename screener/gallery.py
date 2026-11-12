"""日足・週足・月足で長い下ひげを付けて出来高が増えた銘柄を、日米それぞれチャートで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取って作る。GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html
    python -m screener.gallery --markets jp     日本株だけ取り直す（米国株は公開中のページのデータを使う）

見た目だけ変えたときは、株価を取り直さない screener.retemplate で作り直せる。
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from . import jpx, prices, published, pullback

# チャートに描く足の本数
CHART_BARS = 52
# 足の種類ごとの設定。ma はチャートに描く移動平均の本数（短期・中期・長期）、
# volume_window は出来高を比べる「その前の平均」の本数、
# after は過去の成績で「何本後の終値と比べるか」、span は過去の成績に使う期間（取ってくる株価の長さ）
FRAMES = {
    "d": {"label": "日足", "ma": (5, 25, 75), "volume_window": 25, "after": (5, 20), "span": "2年"},
    "w": {"label": "週足", "ma": (13, 26, 52), "volume_window": 13, "after": (4, 13), "span": "2年"},
    "m": {"label": "月足", "ma": (12, 24, 60), "volume_window": 12, "after": (3,), "span": "10年"},
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
TOP_COUNT = 5
# 載せる銘柄の時価総額の下限（日本株は300億円、米国株は10億ドル）。
# ふだん売買の少ない小型株は、出来高の倍率が簡単に大きくなるので外す
MIN_MARKET_CAP = {"jp": 3e10, "us": 1e9}


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

    日足はその日の大引け、週足は金曜の大引け、月足は翌月に入ったら、終わったとみなす。
    """
    tz, hour, minute = MARKET_CLOSE[market]
    day = pd.Timestamp(start.date()).tz_localize(tz)
    close_time = pd.Timedelta(hours=hour, minutes=minute)
    end = {"d": day + close_time,
           "w": day + pd.Timedelta(days=4) + close_time,
           "m": day + pd.DateOffset(months=1)}[frame]
    return pd.Timestamp.now(tz) >= end


BAR_NAME = {"d": "{:%Y/%m/%d}", "w": "{:%Y/%m/%d} の週", "m": "{:%Y/%m}"}


def _frame(frame, market, info, bars, min_market_cap):
    """1つの足の種類（日足・週足・月足）について、条件に合う銘柄とチャートのデータを作る"""
    spec = FRAMES[frame]
    _fill_live_close(bars, info["price"])
    # 途中の足は出来高が少なく比べられないので、取引が終わった直近の足で選ぶ
    shown = bars["close"].index[-CHART_BARS:]
    at = -1 if _bar_done(shown[-1], frame, market) else -2
    hits = pullback.find(bars, at, spec["volume_window"]).join(info, how="inner")
    hits = hits[hits["market_cap"] >= min_market_cap].sort_values("market_cap", ascending=False)
    tops = hits.nlargest(TOP_COUNT, "score")

    short, mid, long = pullback.moving_averages(bars["close"], spec["ma"])
    series = {"o": bars["open"], "h": bars["high"], "l": bars["low"], "c": bars["close"],
              "maS": short, "maM": mid, "maL": long}
    signal = len(shown) + at  # 下ひげを付けた足が、チャートの左から何本目か

    def stock(symbol, row):
        s = {
            "code": row["code"],
            "name": row["name"] or row["code"],
            "sector": "" if pd.isna(row["sector"]) else row["sector"],
            "signal": signal,
            "wick": round(row["wick"] * 100, 1),
            "volumeRatio": round(row["volume_ratio"], 1),
        }
        s.update({key: [_round(v) for v in table[symbol].loc[shown]] for key, table in series.items()})
        return s

    def percent(value):
        return round(value * 100, 1)

    past = [{"after": r["after"], "count": r["count"], "win": percent(r["win"]), "mean": percent(r["mean"]),
             "baseWin": percent(r["base_win"]), "baseMean": percent(r["base_mean"])}
            for r in pullback.past_results(bars, at, spec["volume_window"], spec["after"])]

    return {"bar": BAR_NAME[frame].format(shown[at]), "count": len(hits), "span": spec["span"], "past": past,
            "stocks": [stock(symbol, row) for symbol, row in hits.iterrows()],
            "top": [stock(symbol, row) for symbol, row in tops.iterrows()]}


def _prices(info, daily):
    """保留した銘柄の「保留してからの値動き」用の、全銘柄の今の株価。

    株価を取っていない銘柄（時価総額が下限を割った銘柄など）も、スクリーナーの株価で埋まる
    """
    close = daily["close"].iloc[-1].reindex(info.index).fillna(info["price"]).dropna()
    return {info.at[symbol, "code"]: round(float(value), 2) for symbol, value in close.items()}


def _build(market, log):
    """1つの市場の株価を取り直して、ページに載せるデータを作る"""
    info = INFO[market](log)
    # 株価は、載せる対象（時価総額の下限以上）の銘柄だけ取る。Yahoo から取る量が減って速くなる
    symbols = info.index[info["market_cap"] >= MIN_MARKET_CAP[market]].tolist()
    log(f"{market}: 日足を取得中...")
    daily = prices.fetch_bars(symbols, "1d", "2y", "日足", log)
    frames = {"d": _frame("d", market, info, daily, MIN_MARKET_CAP[market])}
    # 週足は日足から作る（別に取らない）。日足の途中の足の終値は _frame で埋めてあるので、週足にも入る
    frames["w"] = _frame("w", market, info, prices.to_weekly(daily), MIN_MARKET_CAP[market])
    log(f"{market}: 月足を取得中...")
    monthly = prices.fetch_bars(symbols, "1mo", "10y", "月足", log)  # 60か月線を描くので長く取る
    frames["m"] = _frame("m", market, info, monthly, MIN_MARKET_CAP[market])
    return {"frames": frames, "prices": _prices(info, daily)}


def _published(log):
    """公開中のページのデータ。取れない・形が古いときは空（その市場は取り直すことになる）"""
    try:
        data = json.loads(published.payload())
        return {market: value for market, value in data.items() if set(value.get("frames", {})) == set(FRAMES)}
    except Exception as error:
        log(f"公開中のページのデータは使えません（{error}）")
        return {}


def main():
    parser = argparse.ArgumentParser(description="日米の「下ひげ × 出来高増」ページを作る")
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
            data[market] = _build(market, print)
        else:
            print(f"{market}: 公開中のページのデータを使います")
            data[market] = keep[market]
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        for frame, result in data[market]["frames"].items():
            print(f"{label} {FRAMES[frame]['label']}（{result['bar']}）: {result['count']} 銘柄")
            for r in result.get("past", []):
                print(f"    過去 {r['after']}本後: {r['count']:,}回 上昇 {r['win']}%（全銘柄 {r['baseWin']}%）"
                      f" 平均 {r['mean']:+}%（全銘柄 {r['baseMean']:+}%）")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
