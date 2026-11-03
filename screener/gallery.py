"""週足の押し目候補（26週線タッチ＋下ひげ）を、日米それぞれ週足チャートで並べた HTML ページを作る。

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


def _universe(log):
    """市場ごとに (銘柄の情報, 週足を取る銘柄) を返す。銘柄の情報は code・name・market_cap"""
    log("米国株の銘柄と時価総額を取得中...")
    us = prices.fetch_quotes(log)
    us["name"] = us["name"].fillna(us["symbol"])
    # GOOGL / GOOG のように同じ会社の銘柄が複数あるときは、売買代金が大きい方だけ残す
    us["dollar_volume"] = us["avg_volume"] * us["price"]
    us = us.sort_values("dollar_volume", ascending=False).drop_duplicates("name")
    us = us.assign(code=us["symbol"]).set_index("symbol")[["code", "name", "market_cap"]]

    log("日本株の銘柄と時価総額を取得中...")
    jp = jpx.listing().merge(prices.fetch_jp_quotes(log)[["symbol", "market_cap"]], on="symbol", how="left")
    jp = jp.set_index("symbol")[["code", "name", "market_cap"]]
    return {
        "jp": (jp, jp.index.tolist()),
        "us": (us, us.index[us["market_cap"] >= HISTORY_MIN_MARKET_CAP].tolist()),
    }


def _round(value):
    """チャート用に有効数字4桁へ丸める（ページを軽くするため）。値が無い週は None"""
    return None if pd.isna(value) else float(f"{value:.4g}")


def _market(info, bars):
    hits = pullback.find(bars).join(info, how="inner").sort_values("market_cap", ascending=False)
    ma = pullback.moving_averages(bars["close"])
    weeks = bars["close"].index[-CHART_WEEKS:]
    series = {"o": bars["open"], "h": bars["high"], "l": bars["low"], "c": bars["close"],
              "ma13": ma[13], "ma26": ma[26], "ma52": ma[52]}
    stocks = []
    for symbol, row in hits.iterrows():
        stock = {
            "code": row["code"],
            "name": row["name"] or row["code"],
            "vsMa26": round(row["vs_ma26"] * 100, 1),
            "offHigh": round(row["off_high"] * 100, 1),
            # 下ひげを付けた週が、チャートの左から何本目か
            "signal": int(weeks.get_loc(row["signal"])),
        }
        stock.update({key: [_round(v) for v in table[symbol].loc[weeks]] for key, table in series.items()})
        stocks.append(stock)
    return {"week": f"{weeks[-1]:%Y/%m/%d}", "count": len(hits), "stocks": stocks}


def main():
    parser = argparse.ArgumentParser(description="日米の週足の押し目候補ページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    out = parser.parse_args().out

    data = {}
    for market, (info, symbols) in _universe(print).items():
        print(f"{market}: 週足を取得中...")
        data[market] = _market(info, prices.fetch_weekly(symbols, print))
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        print(f"{label}（{data[market]['week']} の週）: {data[market]['count']} 銘柄")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
