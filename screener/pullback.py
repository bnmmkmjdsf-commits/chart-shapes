"""ローソク足で「長い下ひげを付けて、出来高が増えた」銘柄を探す。右肩上がりかどうかなど、トレンドは問わない。

日足・週足・月足のどれにも同じ条件を使う（1本の足について）:
  - 下ひげが値幅（高値−安値）の33%以上、かつ株価の2%以上（値動きがほとんど無い足の小さなひげを除く）
  - 出来高がその前の volume_window 本の平均の VOLUME_RATIO 倍以上（買いが入った印）
  - 陽線（終値が始値より上）。下ひげを付けたあと、押し返して終わった足だけにする

見るのは1本の足だけ。途中の足は出来高が少なく比べられないので、呼ぶ側が取引の終わった足を指定する。
"""
import numpy as np
import pandas as pd

WICK_OF_RANGE = 0.33
WICK_OF_PRICE = 0.02
VOLUME_RATIO = 1.5


def moving_averages(close, windows):
    """チャートに描く単純移動平均（windows は本数の組。短い順）"""
    return [close.rolling(n).mean() for n in windows]


def find(bars, at, volume_window):
    """at 本目の足（-1 が最新、-2 がその前）で条件に合う銘柄。

    bars: open / high / low / close / volume の表（行が足、列が銘柄）
    返す表（銘柄が索引）: wick（下ひげの長さ、株価比）、volume_ratio（出来高の倍率）、
    score（wick × volume_ratio。TOP の並び順に使う）
    """
    o, h, low, c, volume = (bars[key].iloc[at] for key in ["open", "high", "low", "close", "volume"])
    wick = np.minimum(o, c) - low
    usual = bars["volume"].shift(1).rolling(volume_window).mean().iloc[at]
    volume_ratio = volume / usual.where(usual > 0)  # 前の出来高が0の銘柄は比べられないので外す
    ok = ((wick >= (h - low) * WICK_OF_RANGE) & (wick >= c * WICK_OF_PRICE)
          & (volume_ratio >= VOLUME_RATIO) & (c > o))
    table = pd.DataFrame({"wick": wick / c, "volume_ratio": volume_ratio})[ok]
    return table.assign(score=table["wick"] * table["volume_ratio"])
