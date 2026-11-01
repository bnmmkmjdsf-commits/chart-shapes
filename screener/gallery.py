"""日米の株を、直近1ヶ月のチャートの形（4つ）ごとにカードで並べた HTML ページを作る。

Yahoo Finance と JPX からデータを取り直して作る（SEC の財務は使わない）。
GitHub Actions で平日に動かし、GitHub Pages で公開している。

    python -m screener.gallery                  → site/index.html
    python -m screener.gallery --out page.html
"""
import argparse
import json
from pathlib import Path

from . import jpx, prices, shapes
from .build import HISTORY_MIN_MARKET_CAP

SHAPES = ["もみ合い?", "リバウンド?", "下落?", "下げとまった?"]
# 1つの形につき載せる銘柄数（時価総額の大きい順）
PER_SHAPE = 60
TEMPLATE = Path(__file__).with_name("gallery_template.html")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "site" / "index.html"


def _fetch(log):
    """市場ごとに (終値の表, 銘柄の情報) を取る。銘柄の情報は code・name・market_cap"""
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

    log("株価履歴を取得中...")
    us_close = prices.fetch_history(us.index[us["market_cap"] >= HISTORY_MIN_MARKET_CAP].tolist(), log)
    jp_close = prices.fetch_history(jp.index.tolist(), log)
    return {"jp": (jp_close, jp), "us": (us_close, us)}


def _market(close, info):
    result = shapes.classify(close, shapes.PERIODS["1ヶ月"]).join(info, how="inner")
    groups = {}
    for name in SHAPES:
        hits = result[result["shape"] == name].sort_values("market_cap", ascending=False)
        top = hits.head(PER_SHAPE)
        groups[name] = {
            "count": len(hits),
            "stocks": [
                {"code": code, "name": label or code, "change": round(change * 100, 1), "spark": spark}
                for code, label, change, spark in zip(top["code"], top["name"], top["change"], top["spark"])
            ],
        }
    return {"asOf": f"{close.index.max():%Y/%m/%d}", "shapes": groups}


def main():
    parser = argparse.ArgumentParser(description="日米のチャート形状の一覧ページを作る")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="書き出す HTML のパス")
    out = parser.parse_args().out

    data = {market: _market(close, info) for market, (close, info) in _fetch(print).items()}
    # JSON を <script> に埋め込むので、"</" で script が閉じないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        counts = "  ".join(f"{name} {g['count']}" for name, g in data[market]["shapes"].items())
        print(f"{label}（{data[market]['asOf']}）: {counts}")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
