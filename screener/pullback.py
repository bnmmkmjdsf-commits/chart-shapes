"""週足で「長い下ひげを付けて、出来高が増えた」銘柄を探す。右肩上がりかどうかなど、トレンドは問わない。

条件（1本の週足について）:
  - 下ひげが値幅（高値−安値）の33%以上、かつ株価の2%以上（値動きがほとんど無い週の小さなひげを除く）
  - 出来高がその前13週の平均の VOLUME_RATIO 倍以上（買いが入った印）
  - 陽線（終値が始値より上）。下ひげを付けたあと、押し返して終わった週だけにする

見るのは1本の週足だけ。週の途中の足は出来高が少なく比べられないので、呼ぶ側が取引の終わった週を指定する。

移動平均は証券会社アプリと同じ 13週（短期）・26週（中期）・52週（長期）の単純平均で、チャートに描くだけ。
"""
import numpy as np
import pandas as pd

MA_WEEKS = (13, 26, 52)
WICK_OF_RANGE = 0.33
WICK_OF_PRICE = 0.02
VOLUME_RATIO = 1.5


def moving_averages(close):
    return {n: close.rolling(n).mean() for n in MA_WEEKS}


def _signals(bars):
    """週ごと・銘柄ごとの (条件に合うか, 下ひげの長さ（株価比）, 出来高の倍率)"""
    o, h, low, c, volume = bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
    wick = np.minimum(o, c) - low
    usual = volume.shift(1).rolling(13).mean()
    volume_ratio = volume / usual.where(usual > 0)  # 前13週の出来高が0の銘柄は比べられないので外す
    ok = ((wick >= (h - low) * WICK_OF_RANGE) & (wick >= c * WICK_OF_PRICE)
          & (volume_ratio >= VOLUME_RATIO) & (c > o))
    return ok, wick / c, volume_ratio


def find(bars, at=-1):
    """at 本目の週足（-1 が最新、-2 がその前）で条件に合う銘柄。

    返す表（銘柄が索引）: wick（下ひげの長さ、株価比）、volume_ratio（出来高の倍率）、
    score（wick × volume_ratio。「今週のTOP」の並び順に使う）
    """
    ok, wick, volume_ratio = _signals(bars)
    table = pd.DataFrame({"wick": wick.iloc[at], "volume_ratio": volume_ratio.iloc[at]})[ok.iloc[at]]
    return table.assign(score=table["wick"] * table["volume_ratio"])
