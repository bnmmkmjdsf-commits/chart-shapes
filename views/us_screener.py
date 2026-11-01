"""米国株スクリーナー: SEC の財務（直近12か月）と株価で絞り込む"""
import re
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from screener import shapes
from screener.config import DATASET_PATH, HISTORY_US_PATH
from views import data

# サイドバーの条件: (列, ラベル, "max" なら上限 / "min" なら下限)
FILTERS = {
    "割安度": [
        ("per", "PER 上限（倍）", "max"),
        ("pbr", "PBR 上限（倍）", "max"),
        ("psr", "PSR 上限（倍）", "max"),
        ("fcf_yield", "FCF利回り 下限（%）", "min"),
    ],
    "配当・還元": [
        ("dividend_yield", "配当利回り 下限（%）", "min"),
        ("payout_ratio", "配当性向 上限（%）", "max"),
        ("fcf_payout", "FCF配当性向 上限（%）", "max"),
        ("shareholder_yield", "総還元利回り 下限（%）", "min"),
    ],
    "収益性": [
        ("roe", "ROE 下限（%）", "min"),
        ("op_margin", "営業利益率（TTM）下限（%）", "min"),
        ("op_margin_q", "営業利益率（直近四半期）下限（%）", "min"),
        ("gross_margin", "粗利率 下限（%）", "min"),
    ],
    "成長性": [
        ("rev_growth", "売上成長率（TTM）下限（%）", "min"),
        ("rev_growth_q", "売上成長率（直近四半期）下限（%）", "min"),
        ("ni_growth", "純利益成長率（TTM）下限（%）", "min"),
    ],
    "安全性・株価": [
        ("equity_ratio", "自己資本比率 下限（%）", "min"),
        ("change_52w", "52週騰落率 下限（%）", "min"),
    ],
}
MARKET_CAP = {
    "指定なし": 0,
    "3億ドル以上": 0.3e9,
    "20億ドル以上（中型〜）": 2e9,
    "100億ドル以上（大型）": 10e9,
    "2,000億ドル以上（超大型）": 200e9,
}
LISTED_WITHIN = {"指定なし": None, "1年以内": 365, "2年以内": 730, "3年以内": 1095}


def _num(label, fmt):
    return st.column_config.NumberColumn(label, format=fmt)


COLUMNS = {
    "symbol": st.column_config.TextColumn("ティッカー", pinned=True),
    "name": st.column_config.TextColumn("銘柄名", width="medium"),
    "sector": st.column_config.TextColumn("セクター"),
    "market_cap_b": _num("時価総額($B)", "%.1f"),
    "price": _num("株価($)", "%.2f"),
    "shape": st.column_config.TextColumn("形(1ヶ月)"),
    "per": _num("PER", "%.1f"),
    "pbr": _num("PBR", "%.2f"),
    "psr": _num("PSR", "%.2f"),
    "roe": _num("ROE", "%.1f%%"),
    "gross_margin": _num("粗利率", "%.1f%%"),
    "op_margin": _num("営業利益率", "%.1f%%"),
    "op_margin_q": _num("営業利益率(四半期)", "%.1f%%"),
    "rev_growth": _num("売上成長率", "%.1f%%"),
    "rev_growth_q": _num("売上成長率(四半期)", "%.1f%%"),
    "ni_growth": _num("純利益成長率", "%.1f%%"),
    "equity_ratio": _num("自己資本比率", "%.1f%%"),
    "fcf_yield": _num("FCF利回り", "%.1f%%"),
    "dividend_yield": _num("配当利回り", "%.2f%%"),
    "payout_ratio": _num("配当性向", "%.1f%%"),
    "fcf_payout": _num("FCF配当性向", "%.1f%%"),
    "shareholder_yield": _num("総還元利回り", "%.2f%%"),
    "change_52w": _num("52週騰落率", "%.1f%%"),
    "listed": st.column_config.TextColumn("上場日"),
    "as_of": st.column_config.TextColumn("最新決算", help="財務の数字が対象とする最新の期末日"),
    "edgar": st.column_config.LinkColumn("開示", display_text="EDGAR"),
}

NOTES = """
- 損益・キャッシュフローは **直近12か月（TTM）**：前期の年間値 + 今期の累計 − 前年同期の累計（10-K と 10-Q から計算）
- 株主資本・総資産は **最新の決算期末** の値。「最新決算」列がその期末日
- **PER** = 時価総額 ÷ 純利益、**PBR** = 時価総額 ÷ 株主資本、**PSR** = 時価総額 ÷ 売上高（赤字・債務超過は空欄）
- **ROE** = 純利益 ÷ 株主資本、**FCF利回り** = (営業CF − 設備投資) ÷ 時価総額
- **売上成長率・純利益成長率** は TTM の前年比、**売上成長率(四半期)** は直近四半期の前年同期比
- **配当性向** = 配当支払額 ÷ 純利益、**FCF配当性向** = 配当支払額 ÷ FCF、**総還元利回り** = (配当 + 自社株買い) ÷ 時価総額
- 上場直後で年次決算がまだ無い会社は、TTM の指標は空欄で、四半期の指標（成長率・営業利益率）だけ出る
- **形(1ヶ月)** は直近1ヶ月のチャートの形（「チャート形状検索」と同じ判定。時価総額3億ドル以上の銘柄のみ）
- 配当利回り・52週騰落率・時価総額・上場日は Yahoo Finance の値（非公式 API・遅延あり）
- 外国企業の ADR（TSM、ASML、トヨタなど IFRS で 20-F を出す会社）は財務データなし
- 条件を空欄にした項目は絞り込まない。条件を入れた項目が空欄の銘柄は除外される
"""

if not DATASET_PATH.exists():
    st.info("まだデータがありません。左の「データを更新」を押してください。")
    st.stop()

version = data.mtime(DATASET_PATH)
df = data.us(version)
if HISTORY_US_PATH.exists():
    df = df.assign(shape=df["symbol"].map(data.chart_shapes("us", "1ヶ月", data.mtime(HISTORY_US_PATH))["shape"]))
else:
    df = df.assign(shape=None)

with st.sidebar:
    st.header("絞り込み")
    query = st.text_input("ティッカー・銘柄名", placeholder="例: NVDA, apple")
    cap = st.selectbox("時価総額", list(MARKET_CAP), index=1)
    sectors = st.multiselect("セクター", list(data.SECTOR_JA.values()), placeholder="すべて")
    listed_within = st.selectbox("上場からの期間", list(LISTED_WITHIN))
    chart_shapes = st.multiselect("チャートの形（1ヶ月）", [name for name, _ in shapes.SHAPES], placeholder="指定なし")
    fundamentals_only = st.checkbox("SEC の財務データがある銘柄のみ", value=True)
    limits = {}
    for group, items in FILTERS.items():
        with st.expander(group, expanded=True):
            for col, label, kind in items:
                limits[col] = (kind, st.number_input(label, value=None, step=1.0, placeholder="指定なし", key=col))

view = df[df["market_cap"] >= MARKET_CAP[cap]]
if terms := [t for t in re.split(r"[,\s]+", query) if t]:
    hit = pd.Series(False, index=view.index)
    for t in terms:
        hit |= view["symbol"].str.contains(t, case=False, regex=False)
        hit |= view["name"].str.contains(t, case=False, regex=False, na=False)
    view = view[hit]
if sectors:
    view = view[view["sector"].isin(sectors)]
if days := LISTED_WITHIN[listed_within]:
    view = view[view["listed"] >= f"{datetime.now() - timedelta(days=days):%Y-%m-%d}"]
if chart_shapes:
    view = view[view["shape"].isin(chart_shapes)]
if fundamentals_only:
    view = view[view["as_of"].notna()]
for col, (kind, value) in limits.items():
    if value is not None:
        view = view[view[col] <= value] if kind == "max" else view[view[col] >= value]

st.title("米国株スクリーナー")
st.caption(
    f"データ更新: {datetime.fromtimestamp(version):%Y-%m-%d %H:%M}　｜　"
    "財務: SEC EDGAR（直近12か月）　｜　株価: Yahoo Finance（遅延あり）"
)
st.markdown(f"**{len(view):,}** 銘柄が該当（全 {len(df):,} 銘柄）")

table = view[list(COLUMNS)]
st.dataframe(table, column_config=COLUMNS, hide_index=True, width="stretch", height=640)

csv = table.rename(columns={k: v["label"] for k, v in COLUMNS.items()}).to_csv(index=False)
st.download_button("CSV をダウンロード", csv.encode("utf-8-sig"), "screener.csv", "text/csv")

with st.expander("指標の定義と注意点"):
    st.markdown(NOTES)
