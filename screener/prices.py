"""Yahoo Finance（yfinance 経由・非公式）から、株価・時価総額とローソク足を取る。

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


def fetch_bars(symbols, interval, period, label, log=print, chunk=500):
    """ローソク足（interval は "1d"・"1wk"・"1mo"）を period の期間ぶん取る。
    open / high / low / close / volume の表（行が足、列が銘柄）を返す。

    株式分割は調整済み、配当は未調整（証券会社アプリのチャートと同じ値になる）。途中の足（今日・今週・今月）も含む。
    """
    parts = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for i in range(0, len(symbols), chunk):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # yfinance が出す pandas の非推奨警告
            data = yf.download(list(symbols[i:i + chunk]), period=period, interval=interval,
                               auto_adjust=False, threads=True, progress=False)
        for key in parts:
            parts[key].append(data[key.capitalize()])
        log(f"  {label}: {min(i + chunk, len(symbols)):,} / {len(symbols):,} 銘柄")
        time.sleep(1)
    return align_bars({key: pd.concat(frames, axis=1) for key, frames in parts.items()}, interval)


# 同じ足の行をまとめるときの方法（始値は最初、高値は最大、安値は最小、終値は最後の値）。
# 出来高は最大を取る。Yahoo は途中の足を同じ出来高で2行返すことがあり、合計すると2倍になってしまう
BAR_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "max"}


def align_bars(bars, interval):
    """足の行を、その足の始まりの日付（日足はその日、週足は月曜、月足は1日。時刻は0時）にそろえる。

    Yahoo はまれに一部の銘柄だけ足の途中の日付で別の行を返す。また途中の足を
    「始まりの日の0時の空の行」と「取引時刻の行」の2行で返すことがある。そのままだと空の行ができて
    移動平均が途切れるので、同じ足の行は1行にまとめる。
    """
    aligned = {}
    for key, table in bars.items():
        index = table.index
        if index.tz is not None:
            index = index.tz_localize(None)
        start = index.normalize()
        if interval == "1wk":
            start = start - pd.to_timedelta(start.weekday, unit="D")
        elif interval == "1mo":
            start = start - pd.to_timedelta(start.day - 1, unit="D")
        aligned[key] = table.groupby(start).agg(BAR_AGG[key])
    return aligned


# 日足を週足にまとめるときの方法（出来高はその週の合計）
WEEK_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def to_weekly(daily):
    """日足から週足を作る（行は週の月曜の日付）。週足を別に取らずに済ませるためのもの"""
    weekly = {}
    for key, table in daily.items():
        monday = table.index - pd.to_timedelta(table.index.weekday, unit="D")
        weekly[key] = table.groupby(monday).agg(WEEK_AGG[key])
    return weekly
