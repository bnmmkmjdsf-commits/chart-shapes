"""公開中のページのデータをそのまま使って、今のテンプレートでページを作り直す。

見た目や文言だけを変えたときに、株価を取り直さずに済ませるためのもの（数十秒で終わる）。
データの中身が変わる変更（条件や、ページに持たせる項目）には使えない。標準ライブラリだけで動く。

    python -m screener.retemplate --out site/index.html
"""
import argparse
import json
import re
import urllib.request
from pathlib import Path

PAGE_URL = "https://bnmmkmjdsf-commits.github.io/chart-shapes/"
TEMPLATE = Path(__file__).with_name("gallery_template.html")


def main():
    parser = argparse.ArgumentParser(description="公開中のページのデータで、ページを作り直す")
    parser.add_argument("--out", type=Path, required=True, help="書き出す HTML のパス")
    out = parser.parse_args().out

    with urllib.request.urlopen(PAGE_URL, timeout=60) as res:
        page = res.read().decode("utf-8")
    # テンプレートの「const DATA = __DATA__;」に入っている JSON を取り出す
    payload = re.search(r"const DATA = (.*?);\nconst STEP", page, re.S).group(1)
    data = json.loads(payload)  # 壊れたデータで公開ページを上書きしないよう、読めることを確かめる
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    for market, label in [("jp", "日本株"), ("us", "米国株")]:
        print(f"{label}（{data[market]['week']} の週）: {data[market]['count']} 銘柄（前回のデータ）")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
