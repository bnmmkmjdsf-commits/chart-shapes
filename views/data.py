"""画面で使うデータの読み込み。

ファイルの更新時刻を version としてキャッシュのキーに含め、データを作り直したら読み直す。
（st.cache_data は先頭が _ の引数をキーに使わないので、名前に _ は付けない）
"""
import pandas as pd
import streamlit as st

from screener import shapes
from screener.config import DATASET_PATH, HISTORY_JP_PATH, HISTORY_US_PATH, JP_PATH

SECTOR_JA = {
    "Basic Materials": "素材",
    "Communication Services": "通信",
    "Consumer Cyclical": "一般消費財",
    "Consumer Defensive": "生活必需品",
    "Energy": "エネルギー",
    "Financial Services": "金融",
    "Healthcare": "ヘルスケア",
    "Industrials": "資本財",
    "Real Estate": "不動産",
    "Technology": "テクノロジー",
    "Utilities": "公益",
}
# データセットでは比率で持っている列。画面では % にして見せる
PCT_COLS = [
    "roe", "gross_margin", "op_margin", "op_margin_q", "net_margin", "rev_growth", "rev_growth_q", "ni_growth",
    "equity_ratio", "fcf_yield", "dividend_yield", "payout_ratio", "fcf_payout", "shareholder_yield", "change_52w",
]
HISTORY = {"us": HISTORY_US_PATH, "jp": HISTORY_JP_PATH}


def mtime(path):
    return path.stat().st_mtime


@st.cache_data
def us(version):
    df = pd.read_parquet(DATASET_PATH)
    df[PCT_COLS] = df[PCT_COLS] * 100
    df["market_cap_b"] = df["market_cap"] / 1e9
    df["sector"] = df["sector"].map(SECTOR_JA)
    df["edgar"] = "https://www.sec.gov/edgar/browse/?CIK=" + df["cik"].astype("string")
    return df


@st.cache_data
def jp(version):
    df = pd.read_parquet(JP_PATH)
    df[["dividend_yield", "change_52w"]] = df[["dividend_yield", "change_52w"]] * 100
    df["market_cap_oku"] = (df["market_cap"] / 1e8).round()
    return df


@st.cache_data
def chart_shapes(market, period, version):
    """market（us / jp）の株価履歴から、period（1ヶ月 など）のチャートの形を銘柄ごとに判定する"""
    result = shapes.classify(pd.read_parquet(HISTORY[market]), shapes.PERIODS[period])
    result["change"] = result["change"] * 100
    return result
