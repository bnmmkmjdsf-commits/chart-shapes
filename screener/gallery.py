"""日足・週足・月足で長い下ひげを付けて出来高が増えた銘柄を、日米それぞれチャートで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取り直して作る（SEC の財務は使わない）。
GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html

見た目だけ変えたときは、株価を取り直さない screener.retemplate で作り直せる。
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from . import jpx, prices, pullback
from .build import HISTORY_MIN_MARKET_CAP

# チャートに描く足の本数
CHART_BARS = 52
# 足の種類ごとの設定。ma はチャートに描く移動平均の本数（短期・中期・長期）、
# volume_window は出来高を比べる「その前の平均」の本数、period は Yahoo から取る期間（長期の移動平均を描ける長さ）
FRAMES = {
    "d": {"label": "日足", "interval": "1d", "period": "1y", "ma": (5, 25, 75), "volume_window": 25},
    "w": {"label": "週足", "interval": "1wk", "period": "2y", "ma": (13, 26, 52), "volume_window": 13},
    "m": {"label": "月足", "interval": "1mo", "period": "10y", "ma": (12, 24, 60), "volume_window": 12},
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
# 載せる銘柄の時価総額の下限（日本株は300億円、米国株は週足を取る3億ドル以上と同じ）。
# ふだん売買の少ない小型株は、出来高の倍率が簡単に大きくなるので外す
MIN_MARKET_CAP = {"jp": 3e10, "us": HISTORY_MIN_MARKET_CAP}


def _universe(log):
    """市場ごとに (銘柄の情報, 株価を取る銘柄) を返す。銘柄の情報は code・name・sector（業種）・market_cap"""
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
    # 株価は、載せる対象（時価総額の下限以上）の銘柄だけ取る。Yahoo から取る量が減って速くなる
    return {market: (info, info.index[info["market_cap"] >= MIN_MARKET_CAP[market]].tolist())
            for market, info in [("jp", jp), ("us", us)]}


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

    return {"bar": BAR_NAME[frame].format(shown[at]), "count": len(hits),
            "stocks": [stock(symbol, row) for symbol, row in hits.iterrows()],
            "top": [stock(symbol, row) for symbol, row in tops.iterrows()]}


def _prices(info, daily):
    """保留した銘柄の「保留してからの値動き」用の、全銘柄の今の株価。

    株価を取っていない銘柄（時価総額が下限を割った銘柄など）も、スクリーナーの株価で埋まる
    """
    close = daily["close"].iloc[-1].reindex(info.index).fillna(info["price"]).dropna()
    return {info.at[symbol, "code"]: round(float(value), 2) for symbol, value in close.items()}


def main():
    parser = argparse.ArgumentParser(description="日米の「下ひげ × 出来高増」ページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    out = parser.parse_args().out

    data = {}
    for market, (info, symbols) in _universe(print).items():
        frames = {}
        for frame, spec in FRAMES.items():
            print(f"{market}: {spec['label']}を取得中...")
            bars = prices.fetch_bars(symbols, spec["interval"], spec["period"], spec["label"], print)
            frames[frame] = _frame(frame, market, info, bars, MIN_MARKET_CAP[market])
            if frame == "d":
                current = _prices(info, bars)
        data[market] = {"frames": frames, "prices": current}
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        for frame, result in data[market]["frames"].items():
            print(f"{label} {FRAMES[frame]['label']}（{result['bar']}）: {result['count']} 銘柄")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
