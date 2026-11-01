"""株スクリーナー（米国株の財務スクリーナーと、米国・日本株のチャート形状検索）

    streamlit run app.py
"""
import streamlit as st

from screener.build import build

st.set_page_config(page_title="株スクリーナー", layout="wide")

page = st.navigation([
    st.Page("views/us_screener.py", title="米国株スクリーナー", icon=":material/filter_alt:", default=True),
    st.Page("views/chart_shapes.py", title="チャート形状検索", icon=":material/show_chart:"),
])

with st.sidebar:
    if st.button("データを更新", help="株価・財務・株価履歴を取り直します（5分前後）", width="stretch"):
        with st.status("データを更新中...", expanded=True) as status:
            build(log=st.write)
            status.update(label="更新しました", state="complete", expanded=False)

page.run()
