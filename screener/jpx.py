"""JPX（日本取引所グループ）の東証上場銘柄一覧から、日本語の銘柄名・市場区分・業種を取る"""
import io
import re
import unicodedata

import pandas as pd
import requests

BASE_URL = "https://www.jpx.co.jp"
PAGE_URL = BASE_URL + "/markets/statistics-equities/misc/01.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; us-screener personal-research)"}


def listing():
    """東証の内国株式（プライム・スタンダード・グロース）の一覧。

    一覧ファイルは場所や形式（.xls → .xlsx）が変わることがあるので、毎回掲載ページからリンクを探す。
    """
    page = requests.get(PAGE_URL, headers=HEADERS, timeout=60)
    page.raise_for_status()
    link = re.search(r'href="([^"]*data_j\.xlsx?)"', page.text).group(1)
    res = requests.get(BASE_URL + link, headers=HEADERS, timeout=60)
    res.raise_for_status()

    df = pd.read_excel(io.BytesIO(res.content), dtype=str)
    df["market"] = df["市場・商品区分"].str.extract(r"^(プライム|スタンダード|グロース)（内国株式）$")[0]
    df = df.dropna(subset=["market"])
    return pd.DataFrame({
        "symbol": df["コード"] + ".T",
        "code": df["コード"],
        # 全角の英数字（ＫＤＤＩ など）を半角にそろえる
        "name": df["銘柄名"].map(lambda s: unicodedata.normalize("NFKC", s)),
        "market": df["market"],
        "sector": df["33業種区分"],
    })
