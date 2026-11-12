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


def _signals(bars, volume_window):
    """すべての足について、下ひげの長さ（株価比）・出来高の倍率・条件に合うかの表（行が足、列が銘柄）"""
    o, h, low, c, volume = (bars[key] for key in ["open", "high", "low", "close", "volume"])
    wick = np.minimum(o, c) - low
    usual = volume.shift(1).rolling(volume_window).mean()
    volume_ratio = volume / usual.where(usual > 0)  # 前の出来高が0の銘柄は比べられないので外す
    ok = ((wick >= (h - low) * WICK_OF_RANGE) & (wick >= c * WICK_OF_PRICE)
          & (volume_ratio >= VOLUME_RATIO) & (c > o))
    return wick / c, volume_ratio, ok


def find(bars, at, volume_window):
    """at 本目の足（-1 が最新、-2 がその前）で条件に合う銘柄。

    bars: open / high / low / close / volume の表（行が足、列が銘柄）
    返す表（銘柄が索引）: wick（下ひげの長さ、株価比）、volume_ratio（出来高の倍率）、
    score（wick × volume_ratio。TOP の並び順に使う）
    """
    wick, volume_ratio, ok = _signals(bars, volume_window)
    table = pd.DataFrame({"wick": wick.iloc[at], "volume_ratio": volume_ratio.iloc[at]})[ok.iloc[at]]
    return table.assign(score=table["wick"] * table["volume_ratio"])


def past_results(bars, at, volume_window, horizons):
    """過去の足すべてに同じ条件を当てはめて、条件に合った銘柄のその後の値動きを集計する。

    at 本目（取引の終わった直近の足）までを使う。horizons は「何本後の終値と比べるか」の組。
    それぞれについて、回数（count）、上がった割合（win）、値動きの中央値（median）と、
    同じ時期の全銘柄の値（base_win・base_median）を返す。全銘柄と比べないと、相場全体が上がっただけなのか分からない。
    平均ではなく中央値を使う。Yahoo の株価にはまれに桁違いの値が混じり、平均はそれ1つで大きくずれる。
    """
    done = {key: table.iloc[:len(table) + at + 1] for key, table in bars.items()}
    ok = _signals(done, volume_window)[2]
    close = done["close"]
    results = []
    for n in horizons:
        change = (close.shift(-n) / close - 1).replace([np.inf, -np.inf], np.nan)
        hit = ok & change.notna()
        per_bar = hit.sum(axis=1)  # その足で条件に合った銘柄の数。全銘柄の値は、この数で重みを付けて平均する
        count = int(per_bar.sum())
        if not count:
            continue
        hit_change = change.where(hit)
        results.append({
            "after": n,
            "count": count,
            "win": float((hit_change > 0).sum().sum() / count),
            "median": float(np.nanmedian(hit_change.to_numpy())),
            "base_win": float(((change > 0).sum(axis=1) / change.notna().sum(axis=1) * per_bar).sum() / count),
            "base_median": float((change.median(axis=1) * per_bar).sum() / count),
        })
    return results
