"""平均線（単純移動平均）との乖離ごとに、その後の値動きを過去の足で集計する。

    python -m screener.deviation            日米の日足を5年ぶん取って、表を出す

乖離 = 終値 ÷ 平均線 − 1。乖離を区切り（EDGES）で分け、区切りごとに n 本後の終値までの値動きを数える。
相場全体が上がっただけなのかを分けるため、同じ日の全銘柄の値と比べる（pullback.past_results と同じ考え方）。

注意: 今も上場していて、今の時価総額が下限以上の銘柄だけを見ている。大きく下げたまま消えた・小さくなった銘柄は
入らないので、「下に離れた」側の成績は実際より良く出る。
"""
import numpy as np
import pandas as pd

# 乖離の区切り（%）。-20%以下、-20〜-15%、…、+20%以上
EDGES = [-20, -15, -10, -5, 0, 5, 10, 15, 20]
TURNOVER_DAYS = 60


def tradable(daily, min_price, min_turnover):
    """その日の時点で、株価と売買代金（直近 TURNOVER_DAYS 日の中央値）が下限以上か（行が日、列が銘柄）"""
    close = daily["close"]
    turnover = (close * daily["volume"]).rolling(TURNOVER_DAYS).median()
    return (close >= min_price) & (turnover >= min_turnover)


def labels():
    names = [f"{EDGES[0]}%以下"]
    names += [f"{a:+}〜{b:+}%" for a, b in zip(EDGES, EDGES[1:])]
    return names + [f"+{EDGES[-1]}%以上"]


def table(close, ok, window, horizons):
    """window 本の平均線との乖離の区切りごとに、horizons 本後の値動きをまとめた表。

    close: 終値（行が足、列が銘柄）、ok: その足で対象にする銘柄か（close と同じ形）
    列: 回数、上がった割合、値動きの中央値、同じ日の全銘柄の中央値、その差
    """
    deviation = (close / close.rolling(window).mean() - 1) * 100
    group = pd.DataFrame(np.digitize(deviation.to_numpy(), EDGES), index=close.index, columns=close.columns)
    group = group.where(deviation.notna() & ok)
    rows = []
    for n in horizons:
        change = (close.shift(-n) / close - 1).replace([np.inf, -np.inf], np.nan).where(ok) * 100
        base = change.median(axis=1)
        for i, name in enumerate(labels()):
            hit = (group == i) & change.notna()
            count = int(hit.sum().sum())
            if not count:
                continue
            values = change.where(hit)
            median = float(np.nanmedian(values.to_numpy()))
            base_median = float((base * hit.sum(axis=1)).sum() / count)
            rows.append({"本後": n, "乖離": name, "回数": count,
                         "上昇%": round(float((values > 0).sum().sum() / count * 100), 1),
                         "中央値%": round(median, 2), "全銘柄%": round(base_median, 2),
                         "差%": round(median - base_median, 2)})
    return pd.DataFrame(rows)
