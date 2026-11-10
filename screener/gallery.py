"""週足で長い下ひげを付けて出来高が増えた銘柄を、日米それぞれ週足チャートで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取り直して作る（SEC の財務は使わない）。
GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from . import jpx, prices, pullback
from .build import HISTORY_MIN_MARKET_CAP

# チャートに描く週数
CHART_WEEKS = 52
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
# 載せる銘柄の時価総額の下限（日本株は300億円、米国株は週足を取る3億ドル以上と同じ）。
# ふだん売買の少ない小型株は、出来高の倍率が簡単に大きくなるので外す
MIN_MARKET_CAP = {"jp": 3e10, "us": HISTORY_MIN_MARKET_CAP}


def _universe(log):
    """市場ごとに (銘柄の情報, 週足を取る銘柄) を返す。銘柄の情報は code・name・sector（業種）・market_cap"""
    log("米国株の銘柄と時価総額を取得中...")
    us = prices.fetch_quotes(log)
    us["name"] = us["name"].fillna(us["symbol"])
    # GOOGL / GOOG のように同じ会社の銘柄が複数あるときは、売買代金が大きい方だけ残す
    us["dollar_volume"] = us["avg_volume"] * us["price"]
    us = us.sort_values("dollar_volume", ascending=False).drop_duplicates("name")
    us = us.assign(code=us["symbol"], sector=us["sector"].map(US_SECTORS)).set_index("symbol")[INFO_COLUMNS]

    log("日本株の銘柄と時価総額を取得中...")
    jp = jpx.listing().merge(prices.fetch_jp_quotes(log)[["symbol", "market_cap", "price"]], on="symbol", how="left")
    jp = jp.set_index("symbol")[INFO_COLUMNS]
    return {
        "jp": (jp, jp.index.tolist()),
        "us": (us, us.index[us["market_cap"] >= HISTORY_MIN_MARKET_CAP].tolist()),
    }


def _round(value):
    """チャート用に有効数字4桁へ丸める（ページを軽くするため）。値が無い週は None"""
    return None if pd.isna(value) else float(f"{value:.4g}")


def _fill_live_close(bars, price):
    """今週の足の終値が空なら、今の株価で埋める。

    週の初めなど、Yahoo（yfinance）が今週の足を始値・高値・安値・出来高だけ入れて終値を空で返すことがある。
    そのままだと全銘柄の直近の終値と移動平均が空になり、候補が0件になる。
    """
    close = bars["close"]
    last = close.index[-1]
    missing = close.loc[last].isna() & bars["open"].loc[last].notna()
    fill = price.reindex(close.columns)[missing].dropna()
    close.loc[last, fill.index] = fill.to_numpy()


# 市場ごとの時間帯と大引けの時刻
MARKET_CLOSE = {"jp": ("Asia/Tokyo", 15, 30), "us": ("America/New_York", 16, 0)}


def _week_done(monday, market):
    """その週（月曜の日付）の取引が終わったか。金曜の大引けを過ぎていれば終わったとみなす（祝日は見ない）"""
    tz, hour, minute = MARKET_CLOSE[market]
    friday_close = pd.Timestamp(monday.date()).tz_localize(tz) + pd.Timedelta(days=4, hours=hour, minutes=minute)
    return pd.Timestamp.now(tz) >= friday_close


def _market(market, info, bars, min_market_cap):
    _fill_live_close(bars, info["price"])
    # 週の途中の足は出来高が少なく比べられないので、取引が終わった直近の週の足で選ぶ
    weeks = bars["close"].index[-CHART_WEEKS:]
    at = -1 if _week_done(weeks[-1], market) else -2
    hits = pullback.find(bars, at).join(info, how="inner").sort_values("market_cap", ascending=False)
    hits = hits[hits["market_cap"] >= min_market_cap]
    tops = hits.nlargest(TOP_COUNT, "score")

    ma = pullback.moving_averages(bars["close"])
    series = {"o": bars["open"], "h": bars["high"], "l": bars["low"], "c": bars["close"],
              "ma13": ma[13], "ma26": ma[26], "ma52": ma[52]}

    def stock(symbol, row, signal_week):
        s = {
            "code": row["code"],
            "name": row["name"] or row["code"],
            "sector": "" if pd.isna(row["sector"]) else row["sector"],
            # 下ひげを付けた週が、チャートの左から何本目か
            "signal": int(weeks.get_loc(signal_week)),
        }
        s.update({key: [_round(v) for v in table[symbol].loc[weeks]] for key, table in series.items()})
        return s

    def measured(symbol, row):
        s = stock(symbol, row, weeks[at])
        s.update({"wick": round(row["wick"] * 100, 1), "volumeRatio": round(row["volume_ratio"], 1)})
        return s

    stocks = [measured(symbol, row) for symbol, row in hits.iterrows()]
    top = [measured(symbol, row) for symbol, row in tops.iterrows()]
    # 保留した銘柄の「保留してからの値動き」用に、全銘柄の直近の株価を持たせる
    close = bars["close"].iloc[-1].dropna()
    codes = info["code"].reindex(close.index).dropna()
    prices = {code: round(float(close[symbol]), 2) for symbol, code in codes.items()}
    return {"week": f"{weeks[at]:%Y/%m/%d}", "count": len(hits), "stocks": stocks, "top": top, "prices": prices}


def main():
    parser = argparse.ArgumentParser(description="日米の週足の「下ひげ × 出来高増」ページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    out = parser.parse_args().out

    data = {}
    for market, (info, symbols) in _universe(print).items():
        print(f"{market}: 週足を取得中...")
        data[market] = _market(market, info, prices.fetch_weekly(symbols, print), MIN_MARKET_CAP[market])
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        print(f"{label}（{data[market]['week']} の週）: {data[market]['count']} 銘柄")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
