"""チャート形状検索: 直近の値動きを9つの形に分けて、形ごとに銘柄を探す（米国株・日本株）"""
import streamlit as st

from screener import shapes
from screener.config import DATASET_PATH, HISTORY_JP_PATH, HISTORY_US_PATH, JP_PATH
from views import data

US_CAP = {"指定なし": 0, "20億ドル以上": 2e9, "100億ドル以上": 1e10, "2,000億ドル以上": 2e11}
JP_CAP = {"指定なし": 0, "100億円以上": 1e10, "1,000億円以上": 1e11, "1兆円以上": 1e12}


def _num(label, fmt):
    return st.column_config.NumberColumn(label, format=fmt)


CHART = st.column_config.LineChartColumn("チャート", width="medium", help="期間の初日を100とした終値の推移")
US_COLUMNS = {
    "symbol": st.column_config.TextColumn("ティッカー", pinned=True),
    "name": st.column_config.TextColumn("銘柄名", width="medium"),
    "sector": st.column_config.TextColumn("セクター"),
    "market_cap_b": _num("時価総額($B)", "%.1f"),
    "price": _num("株価($)", "%.2f"),
    "change": _num("期間騰落率", "%.1f%%"),
    "spark": CHART,
    "per": _num("PER", "%.1f"),
    "rev_growth_q": _num("売上成長率(四半期)", "%.1f%%"),
    "dividend_yield": _num("配当利回り", "%.2f%%"),
    "as_of": st.column_config.TextColumn("最新決算"),
}
JP_COLUMNS = {
    "code": st.column_config.TextColumn("コード", pinned=True),
    "name": st.column_config.TextColumn("銘柄名", width="medium"),
    "market": st.column_config.TextColumn("市場"),
    "sector": st.column_config.TextColumn("業種"),
    "market_cap_oku": _num("時価総額(億円)", "localized"),
    "price": _num("株価(円)", "localized"),
    "change": _num("期間騰落率", "%.1f%%"),
    "spark": CHART,
    "dividend_yield": _num("配当利回り", "%.2f%%"),
    "listed": st.column_config.TextColumn("上場日"),
}
NOTES = """
- 期間内の株価を「途中で傾きが変わる2本の直線」で近似し（折れ点は期間の1/3〜2/3で最もよく合う位置）、
  前の線と後の線がそれぞれ上げ・横ばい・下げのどれかで形を決める
  （例: 前で大きく下げて後がほぼ横ばい → 下げとまった?）
- 動いた幅は、その銘柄のふだんの値動き（過去1年の日次変動率）を物差しにして測る。
  値動きの荒い銘柄と穏やかな銘柄を同じ基準で比べられる
- 前後どちらもほとんど動いていなければ もみ合い?。急上昇?・急落? は後の線で大きく動いたものだけ
- 株価は Yahoo Finance の日足（株式分割・配当で調整済み）。米国株は時価総額3億ドル以上の銘柄が対象
"""

st.title("チャート形状検索")
if not all(path.exists() for path in [DATASET_PATH, JP_PATH, HISTORY_US_PATH, HISTORY_JP_PATH]):
    st.info("まだデータがありません。左の「データを更新」を押してください。")
    st.stop()

left, right = st.columns(2)
market = left.radio("市場", ["米国株", "日本株"], horizontal=True)
period = right.radio("チャート期間", list(shapes.PERIODS), horizontal=True)
us = market == "米国株"

if us:
    info = data.us(data.mtime(DATASET_PATH))
    result = data.chart_shapes("us", period, data.mtime(HISTORY_US_PATH))
else:
    info = data.jp(data.mtime(JP_PATH))
    result = data.chart_shapes("jp", period, data.mtime(HISTORY_JP_PATH))
rows = result.join(info.set_index("symbol"), how="inner").rename_axis("symbol").reset_index()

with st.sidebar:
    st.header("絞り込み")
    caps = US_CAP if us else JP_CAP
    cap = st.selectbox("時価総額", list(caps))
    rows = rows[rows["market_cap"] >= caps[cap]] if caps[cap] else rows
    if not us:
        segments = st.multiselect("市場区分", ["プライム", "スタンダード", "グロース"], placeholder="すべて")
        rows = rows[rows["market"].isin(segments)] if segments else rows
    sectors = st.multiselect("セクター" if us else "業種", sorted(rows["sector"].dropna().unique()),
                             placeholder="すべて")
    rows = rows[rows["sector"].isin(sectors)] if sectors else rows

# 9つの形のタイル（証券会社アプリと同じ3×3の並び）。押した形の銘柄を下に出す
counts = rows["shape"].value_counts()
st.session_state.setdefault("shape", "下げとまった?")
grid = st.columns(3)
for i, (name, arrow) in enumerate(shapes.SHAPES):
    grid[i % 3].button(
        f"{arrow} **{name}** {counts.get(name, 0):,}",
        key=f"shape-{name}",
        type="primary" if name == st.session_state["shape"] else "secondary",
        width="stretch",
        on_click=st.session_state.__setitem__,
        args=("shape", name),
    )

selected = st.session_state["shape"]
hits = rows[rows["shape"] == selected].sort_values("market_cap", ascending=False)
st.markdown(f"**{selected}**　{len(hits):,} 銘柄（{market}・{period}）")
columns = US_COLUMNS if us else JP_COLUMNS
st.dataframe(hits[list(columns)], column_config=columns, hide_index=True, width="stretch", height=600)

with st.expander("形の決め方"):
    st.markdown(NOTES)
