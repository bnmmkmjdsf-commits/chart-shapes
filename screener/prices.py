"""Yahoo Finance（yfinance 経由・非公式）から、株価・時価総額と日足の履歴を取る。

スクリーナー API は1リクエストで250銘柄ずつ返るので、米国上場株（約7,000銘柄）でも30回程度で済む。
Yahoo は応答にセクターを含めないため、米国株はセクターごとに問い合わせてラベルを付ける。
"""
import logging
import time
import warnings

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery as Q

# NasdaqGS / NYSE / NasdaqGM / NasdaqCM / NYSE American
EXCHANGES = ["NMS", "NYQ", "NGM", "NCM", "ASE"]
SECTORS = [
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
]
FIELDS = {
    "symbol": "symbol",
    "longName": "name",
    "regularMarketPrice": "price",
    "marketCap": "market_cap",
    "dividendYield": "dividend_yield",
    "fiftyTwoWeekChangePercent": "change_52w",
    "averageDailyVolume3Month": "avg_volume",
    "fullExchangeName": "exchange",
    "firstTradeDateMilliseconds": "first_trade_ms",
}
PAGE_SIZE = 250

# yfinance は取れなかった銘柄ごとにエラーを出すので抑える（取れなかった銘柄は履歴が空になる）
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _screen(query, offset):
    for attempt in range(4):
        try:
            return yf.screen(query, offset=offset, size=PAGE_SIZE, sortField="intradaymarketcap", sortAsc=False)
        except Exception:
            # 非公式 API なので 429 などで弾かれることがある。待って再試行する
            if attempt == 3:
                raise
            time.sleep(5 * 2**attempt)


def _screen_all(query):
    """スクリーナーの結果を最後までめくって、銘柄ごとの行にする"""
    rows, offset = [], 0
    while True:
        res = _screen(query, offset)
        quotes = res.get("quotes", [])
        for q in quotes:
            row = {new: q.get(old) for old, new in FIELDS.items()}
            row["name"] = row["name"] or q.get("shortName")
            rows.append(row)
        offset += len(quotes)
        if not quotes or offset >= res.get("total", 0):
            return rows
        time.sleep(0.5)


def fetch_quotes(log=print):
    """米国株（NYSE・Nasdaq など）"""
    rows = []
    for sector in SECTORS:
        query = Q("and", [
            Q("eq", ["region", "us"]),
            Q("is-in", ["exchange", *EXCHANGES]),
            Q("eq", ["sector", sector]),
        ])
        found = _screen_all(query)
        rows += [{**row, "sector": sector} for row in found]
        log(f"  {sector}: {len(found)} 銘柄")
    return pd.DataFrame(rows).drop_duplicates("symbol")


def fetch_jp_quotes(log=print):
    """東証の銘柄。業種などは JPX の一覧から付ける"""
    rows = _screen_all(Q("and", [Q("eq", ["region", "jp"]), Q("is-in", ["exchange", "JPX"])]))
    log(f"  東証: {len(rows):,} 銘柄")
    return pd.DataFrame(rows).drop_duplicates("symbol")


def fetch_history(symbols, log=print, chunk=500):
    """直近1年の日足の終値（株式分割・配当で調整済み）を、行が日付・列が銘柄の表で返す"""
    closes = []
    for i in range(0, len(symbols), chunk):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # yfinance が出す pandas の非推奨警告
            data = yf.download(list(symbols[i:i + chunk]), period="1y", interval="1d",
                               auto_adjust=True, threads=True, progress=False)
        closes.append(data["Close"])
        log(f"  株価履歴: {min(i + chunk, len(symbols)):,} / {len(symbols):,} 銘柄")
        time.sleep(1)
    return pd.concat(closes, axis=1)
