"""一時的な調査用: 日本株の週足の直近の行を、Yahoo から来たままの形と週にそろえた後の形で表示する"""
import warnings

import pandas as pd
import yfinance as yf

from screener import prices

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 20)
symbols = ["6857.T", "8306.T", "7203.T", "AAPL"]
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    raw = yf.download(symbols[:3], period="2y", interval="1wk", auto_adjust=False, threads=True, progress=False)
print("index:", raw.index.dtype, list(raw.index[-4:]))
print(raw[["Open", "Low", "Close", "Volume"]].tail(4).to_string())
bars = prices.fetch_weekly(symbols, log=print)
for key in ["low", "close", "volume"]:
    print(key)
    print(bars[key].tail(4).to_string())
