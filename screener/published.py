"""公開中のページから、埋め込まれているデータ（JSON）を取り出す。標準ライブラリだけで動く"""
import re
import urllib.request

PAGE_URL = "https://bnmmkmjdsf-commits.github.io/chart-shapes/"


def payload():
    """テンプレートの「const DATA = __DATA__;」に入っている JSON を、文字列のまま返す"""
    with urllib.request.urlopen(PAGE_URL, timeout=60) as res:
        page = res.read().decode("utf-8")
    return re.search(r"const DATA = (.*?);\nconst STEP", page, re.S).group(1)
