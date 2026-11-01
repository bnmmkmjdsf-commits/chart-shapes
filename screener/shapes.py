"""チャートの形を9つに分ける。

期間内の株価を「折れ点のある2本の直線」で近似し（折れ点は期間の1/3〜2/3で最もよく合う位置）、
前の線と後の線でそれぞれどれだけ動いたかを、その銘柄のふだんの値動きを物差しにして測る。
2つの動きを平面上の矢印と見て、向き（8方向）と大きさで形を決める。
例: 前の線で大きく下げ、後の線はほぼ横ばい → 下げとまった?。どちらもほとんど動いていない → もみ合い?
"""
import numpy as np
import pandas as pd

# 証券会社アプリの9分類と同じ並び（3×3）: (名前, 矢印)
SHAPES = [
    ("上昇ストップ?", "↗→"), ("上昇?", "↗↗"), ("急上昇?", "→↗"),
    ("調整?", "↗↘"), ("もみ合い?", "→→"), ("リバウンド?", "↘↗"),
    ("急落?", "→↘"), ("下落?", "↘↘"), ("下げとまった?", "↘→"),
]
# 矢印の向き（右 = 0° から反時計回りに45°ずつ）に対応する形
DIRECTIONS = ["上昇ストップ?", "上昇?", "急上昇?", "リバウンド?", "下げとまった?", "下落?", "急落?", "調整?"]
# チャート期間 → 営業日数
PERIODS = {"1ヶ月": 21, "3ヶ月": 63, "6ヶ月": 126, "1年": 245}
# 前後の動きを合わせても、ふだんの値動きの半分に満たなければ「もみ合い」
FLAT = 0.5
# 「急上昇・急落」は後の線の動きがふだんの値動きの1.2倍以上のときだけ。届かなければ「上昇・下落」
STRONG = 1.2
# 値動きがとても小さい銘柄で、わずかな動きを大きく見積もらないための日次変動率の下限
MIN_DAILY_VOL = 0.006


def _two_lines(y):
    """y（行が日付、列が銘柄の対数株価）を折れ点のある2本の直線で近似し、折れ点と2本の傾きを返す"""
    n = len(y)
    t = np.arange(n, dtype=float)
    best_sse = np.full(y.shape[1], np.inf)
    best_k = np.zeros(y.shape[1])
    best_slopes = np.zeros((2, y.shape[1]))
    for k in range(round(n / 3), round(n * 2 / 3) + 1, max(1, n // 40)):
        x = np.column_stack([np.ones(n), np.minimum(t, k), np.maximum(t - k, 0)])
        coef = np.linalg.lstsq(x, y, rcond=None)[0]
        sse = ((y - x @ coef) ** 2).sum(axis=0)
        better = sse < best_sse
        best_sse = np.where(better, sse, best_sse)
        best_k = np.where(better, k, best_k)
        best_slopes = np.where(better, coef[1:], best_slopes)
    return best_k, best_slopes


def classify(close, days):
    """終値の表（行が日付、列が銘柄）から、直近 days 営業日のチャートの形を銘柄ごとに判定する。

    返す表: shape（形の名前）、change（期間の騰落率）、spark（期間の初日を100とした推移）
    """
    window = close.iloc[-(days + 1):]
    # 期間の8割以上の日にデータがある銘柄だけを対象にし、欠けた日は前の値で埋める
    window = window.loc[:, window.notna().mean() >= 0.8].ffill().bfill()
    n = len(window)
    k, slopes = _two_lines(np.log(window).to_numpy())
    vol = np.log(close[window.columns]).diff().std().clip(lower=MIN_DAILY_VOL).to_numpy()
    # それぞれの線で動いた幅を、その日数でのふだんの値動き（日次変動率 × √日数）で割る
    z1 = slopes[0] * np.sqrt(k) / vol
    z2 = slopes[1] * np.sqrt(n - 1 - k) / vol

    angle = np.degrees(np.arctan2(z2, z1)) % 360
    shape = np.array(DIRECTIONS)[((angle + 22.5) // 45).astype(int) % 8]
    shape = np.where((shape == "急上昇?") & (z2 < STRONG), "上昇?", shape)
    shape = np.where((shape == "急落?") & (z2 > -STRONG), "下落?", shape)
    shape = np.where(np.hypot(z1, z2) < FLAT, "もみ合い?", shape)
    return pd.DataFrame({
        "shape": shape,
        "change": window.iloc[-1] / window.iloc[0] - 1,
        "spark": (window / window.iloc[0] * 100).round(1).T.values.tolist(),
    }, index=window.columns)
