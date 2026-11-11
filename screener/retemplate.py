"""公開中のページのデータをそのまま使って、今のテンプレートでページを作り直す。

見た目や文言だけを変えたときに、株価を取り直さずに済ませるためのもの（数十秒で終わる）。
データの中身が変わる変更（条件や、ページに持たせる項目）には使えない。標準ライブラリだけで動く。

    python -m screener.retemplate --out site/index.html
"""
import argparse
import json
from pathlib import Path

from . import published

TEMPLATE = Path(__file__).with_name("gallery_template.html")


def main():
    parser = argparse.ArgumentParser(description="公開中のページのデータで、ページを作り直す")
    parser.add_argument("--out", type=Path, required=True, help="書き出す HTML のパス")
    out = parser.parse_args().out

    payload = published.payload()
    data = json.loads(payload)  # 壊れたデータで公開ページを上書きしないよう、読めることを確かめる
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        counts = {frame: result["count"] for frame, result in data[market]["frames"].items()}
        print(f"{label}: {counts}（前回のデータ）")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
