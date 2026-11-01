"""株価と財務を突き合わせて、スクリーニングとチャート形状検索用のデータを作る。

    python -m screener.build
"""
import time

import pandas as pd

from . import edgar, jpx, prices
from .config import DATASET_PATH, HISTORY_JP_PATH, HISTORY_US_PATH, JP_PATH

# チャート形状検索の対象にする米国株の時価総額の下限（小さすぎる銘柄は除く）
HISTORY_MIN_MARKET_CAP = 3e8


def _ratio(num, den):
    """分母が正のときだけ割る（赤字や債務超過の会社で PER などが負になるのを避ける）"""
    return (num / den).where(den > 0)


def add_metrics(df):
    df["per"] = _ratio(df["market_cap"], df["net_income"])
    df["pbr"] = _ratio(df["market_cap"], df["equity"])
    df["psr"] = _ratio(df["market_cap"], df["revenue"])
    df["roe"] = _ratio(df["net_income"], df["equity"])
    df["gross_margin"] = _ratio(df["gross_profit"], df["revenue"])
    df["op_margin"] = _ratio(df["operating_income"], df["revenue"])
    df["net_margin"] = _ratio(df["net_income"], df["revenue"])
    df["rev_growth"] = _ratio(df["revenue"], df["revenue_prev"]) - 1
    df["rev_growth_q"] = _ratio(df["revenue_q"], df["revenue_q_prev"]) - 1
    # 純利益の伸びは前年・今年とも黒字のときだけ意味がある
    df["ni_growth"] = (_ratio(df["net_income"], df["net_income_prev"]) - 1).where(df["net_income"] > 0)
    df["op_margin_q"] = _ratio(df["operating_income_q"], df["revenue_q"])
    df["equity_ratio"] = _ratio(df["equity"], df["assets"])
    df["fcf"] = df["operating_cf"] - df["capex"]
    df["fcf_yield"] = _ratio(df["fcf"], df["market_cap"])
    df["payout_ratio"] = _ratio(df["dividends"], df["net_income"])
    df["fcf_payout"] = _ratio(df["dividends"], df["fcf"])
    # 配当か自社株買いのどちらかが分かれば、無い方は0として足す
    returned = df[["dividends", "buybacks"]].sum(axis=1, min_count=1)
    df["shareholder_yield"] = _ratio(returned, df["market_cap"])
    return df


def _normalize_quotes(quotes):
    # Yahoo はパーセント表記なので、財務指標とそろえて比率にする
    quotes["dividend_yield"] = quotes["dividend_yield"] / 100
    quotes["change_52w"] = quotes["change_52w"] / 100
    quotes["listed"] = pd.to_datetime(quotes.pop("first_trade_ms"), unit="ms").dt.strftime("%Y-%m-%d")
    return quotes


def build_us(log=print):
    log("米国株の株価を取得中（Yahoo Finance）...")
    quotes = _normalize_quotes(prices.fetch_quotes(log))

    log("米国株の財務データを取得中（SEC EDGAR）...")
    ciks = edgar.tickers()[["ticker", "cik"]].drop_duplicates("ticker")
    df = quotes.merge(ciks, left_on="symbol", right_on="ticker", how="left").drop(columns="ticker")
    df["cik"] = df["cik"].astype("Int64")
    universe = df["cik"].dropna().unique().tolist()
    edgar.update_facts(universe, log)
    df = df.merge(edgar.fundamentals(universe), left_on="cik", right_index=True, how="left")
    df = add_metrics(df)

    # GOOGL / GOOG のように1社で複数銘柄ある場合は、売買代金が大きい方だけ残す
    df["dollar_volume"] = df["avg_volume"] * df["price"]
    df = df.sort_values("dollar_volume", ascending=False)
    df = pd.concat([df[df["cik"].notna()].drop_duplicates("cik"), df[df["cik"].isna()]])
    df = df.sort_values("market_cap", ascending=False).reset_index(drop=True)

    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(DATASET_PATH)
    log(f"  米国株: {len(df):,} 銘柄（うち SEC の財務データあり {df['as_of'].notna().sum():,}）")
    return df


def build_jp(log=print):
    log("日本株の銘柄一覧と株価を取得中（JPX・Yahoo Finance）...")
    # 銘柄名は JPX の日本語名を使う
    quotes = _normalize_quotes(prices.fetch_jp_quotes(log)).drop(columns=["name", "exchange"])
    df = jpx.listing().merge(quotes, on="symbol", how="left")
    df = df.sort_values("market_cap", ascending=False).reset_index(drop=True)
    df.to_parquet(JP_PATH)
    log(f"  日本株: {len(df):,} 銘柄")
    return df


def build_history(us, jp, log=print):
    log("株価履歴（直近1年の日足）を取得中...")
    us_symbols = us.loc[us["market_cap"] >= HISTORY_MIN_MARKET_CAP, "symbol"].tolist()
    prices.fetch_history(us_symbols, log).to_parquet(HISTORY_US_PATH)
    prices.fetch_history(jp["symbol"].tolist(), log).to_parquet(HISTORY_JP_PATH)


def build(log=print):
    started = time.time()
    us = build_us(log)
    jp = build_jp(log)
    build_history(us, jp, log)
    log(f"完了: {time.time() - started:.0f} 秒")


if __name__ == "__main__":
    build()
