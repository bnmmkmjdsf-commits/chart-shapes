"""週足で「右肩上がりの銘柄が下げてきて26週線にタッチし、下ひげを付けた」形（押し目）を探す。

移動平均は証券会社アプリと同じ 13週（短期）・26週（中期）・52週（長期）の単純平均。
次の条件をすべて満たす銘柄を拾う。

  1. 右肩上がり: 52週線が13週前より5%以上高く、26週線が52週線より上
  2. 下げてきた: 直近3週の安値が、直近13週の高値から8%以上下
  3. 26週線にタッチして下ひげ: 直近3週のどこかに、安値が26週線の+2%以内まで来て（割り込みも含む）、
     下ひげが値幅の35%以上かつ株価の2%以上あり、出来高がその前13週の平均の VOLUME_RATIO 倍以上の週がある
  4. 崩れていない: 直近の終値が52週線より上
"""
import numpy as np
import pandas as pd

MA_WEEKS = (13, 26, 52)
RECENT_WEEKS = 3
TREND_RISE = 0.05
PULLBACK = 0.08
TOUCH = 0.02
WICK_OF_RANGE = 0.35
WICK_OF_PRICE = 0.02
# 下ひげの週の出来高が、その前13週の平均の何倍以上か（買いが入った印）
VOLUME_RATIO = 1.2


def moving_averages(close):
    return {n: close.rolling(n).mean() for n in MA_WEEKS}


def top(bars, n=5, at=-1):
    """「今週のTOP」: 1本の週足（at 本目。-1 が最新、-2 がその前）だけを見て、下ひげが長く出来高が増えた順に n 銘柄。

    右肩上がりかどうかは問わない。下ひげと出来高の基準は押し目候補と同じ（値幅の35%以上・株価の2%以上、
    出来高が前13週平均の1.2倍以上）で、それを満たした中から「下ひげの長さ（株価比）× 出来高の倍率」の大きい順。
    返す表（銘柄が索引）: wick（下ひげの長さ、株価比）、volume_ratio（出来高の倍率）
    """
    o, h, low, c, volume = (bars[k].iloc[at] for k in ["open", "high", "low", "close", "volume"])
    wick = np.minimum(o, c) - low
    usual = bars["volume"].iloc[at - 13:at].mean()
    volume_ratio = volume / usual.where(usual > 0)  # 前13週の出来高が0の銘柄は比べられないので外す
    ok = (wick >= (h - low) * WICK_OF_RANGE) & (wick >= c * WICK_OF_PRICE) & (volume_ratio >= VOLUME_RATIO)
    table = pd.DataFrame({"wick": wick / c, "volume_ratio": volume_ratio})[ok]
    return table.assign(score=table["wick"] * table["volume_ratio"]).nlargest(n, "score")


def find(bars):
    """bars: open / high / low / close / volume の表（行が週、列が銘柄）。条件に合う銘柄の表を返す

    返す表（銘柄が索引）: signal（下ひげを付けた週のうち最新の週）、close、vs_ma26（26週線との差）、
    off_high（直近13週の高値からの下落率）
    """
    o, h, low, c, volume = bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
    ma = moving_averages(c)
    ma26, ma52 = ma[26], ma[52]

    uptrend = (ma52.iloc[-1] >= ma52.iloc[-14] * (1 + TREND_RISE)) & (ma26.iloc[-1] > ma52.iloc[-1])
    recent_low = low.iloc[-RECENT_WEEKS:].min()
    pulled_back = recent_low <= h.iloc[-13:].max() * (1 - PULLBACK)
    wick = np.minimum(o, c) - low
    signal_weeks = (
        (low <= ma26 * (1 + TOUCH))
        & (wick >= (h - low) * WICK_OF_RANGE)
        & (wick >= c * WICK_OF_PRICE)
        & (volume >= volume.shift(1).rolling(13).mean() * VOLUME_RATIO)
    ).iloc[-RECENT_WEEKS:]
    holding = c.iloc[-1] > ma52.iloc[-1]

    hit = uptrend & pulled_back & signal_weeks.any() & holding
    symbols = hit[hit].index
    if symbols.empty:
        return pd.DataFrame(columns=["signal", "close", "vs_ma26", "off_high"])
    # 下ひげの週が複数あれば最新の週を印にする（下から見て最初に当てはまる週）
    signal = signal_weeks[symbols].iloc[::-1].idxmax()
    return pd.DataFrame({
        "signal": signal,
        "close": c.iloc[-1][symbols],
        "vs_ma26": c.iloc[-1][symbols] / ma26.iloc[-1][symbols] - 1,
        "off_high": recent_low[symbols] / h.iloc[-13:].max()[symbols] - 1,
    })
