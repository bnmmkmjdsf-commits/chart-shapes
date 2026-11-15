"""「底値を切り上げながら上がってきたのに、前の底値を割った」銘柄を探す（崩れ）。

終値の折れ線で見る。山と谷は、終値が直前の山（谷）から swing 以上動いたところで決まる
（それより小さい上げ下げは、山・谷に数えない）。

  - 谷が RISING_LOWS 回以上続けて切り上がっている（谷が RISING_LOWS + 1 個、どれも前の谷より高い）
  - その谷がすべて、直近 window 本の中にある（チャートに形が見える、最近の上げだけにする）
  - 終値が、最後の谷を初めて下回った足

高値の切り下げ（前の山を超えられない）は見ない。早く気づけるが、そのあとまた上がる空振りが多い。
"""
import numpy as np
import pandas as pd

RISING_LOWS = 2


def _scan(close, swing, window):
    """1銘柄の終値（数値の並び）を古い方から見て、崩れた足を返す。

    返すのは {足の番号: (切り上げていた谷の [足の番号, 終値] の組, その間の一番高い終値)}
    """
    events = {}
    lows = []        # 決まった谷の (足の番号, 終値)。切り上げが途切れたら、その谷から数え直す
    up = None        # いま上げの途中か（None はまだ向きが決まっていない）
    hi = lo = None   # 向きが変わってからの一番高い・安い (足の番号, 終値)
    peak = 0.0       # lows の最初の谷からの一番高い終値
    broken = False   # 最後の谷をもう割ったか（同じ谷で2回は数えない）

    def add_low(point):
        nonlocal lows, peak, broken
        if lows and point[1] <= lows[-1][1]:
            lows, peak = [], 0.0
        lows.append(point)
        broken = False

    for i, c in enumerate(close):
        if np.isnan(c):
            continue
        point = (i, c)
        if (len(lows) > RISING_LOWS and not broken and c < lows[-1][1]
                and lows[-RISING_LOWS - 1][0] > i - window):
            events[i] = (lows[-RISING_LOWS - 1:], peak)
            broken = True
        peak = max(peak, c)
        if hi is None:
            hi = lo = point
        elif up is None:
            hi = point if c > hi[1] else hi
            lo = point if c <= lo[1] else lo  # 同じ安値が続いたら、新しい方を谷にする
            if c >= lo[1] * (1 + swing):
                add_low(lo)
                up, hi = True, point
            elif c <= hi[1] * (1 - swing):
                up, lo = False, point
        elif up:
            if c > hi[1]:
                hi = point
            elif c <= hi[1] * (1 - swing):
                up, lo = False, point
        else:
            if c <= lo[1]:
                lo = point
            elif c >= lo[1] * (1 + swing):
                add_low(lo)
                up, hi = True, point
    return events


def scan(close, swing, window):
    """全銘柄について、崩れた足（ok。行が足、列が銘柄）と、その中身（{銘柄: _scan の返り値}）を返す"""
    ok = pd.DataFrame(False, index=close.index, columns=close.columns)
    events = {}
    for column, symbol in enumerate(close.columns):
        found = _scan(close[symbol].to_numpy(dtype=float), swing, window)
        if found:
            events[symbol] = found
            ok.iloc[list(found), column] = True
    return ok, events


def find(close, events, at):
    """at 本目の足（-1 が最新、-2 がその前）で崩れた銘柄。

    返す表（銘柄が索引）: under（割った谷から何%下か）、drop（上げの間の一番高い終値から何%下か）、
    lows（切り上げていた谷の [足の番号, 終値] の組）
    """
    i = len(close) + at
    rows = {}
    for symbol, found in events.items():
        if i in found:
            lows, peak = found[i]
            c = close[symbol].iloc[i]
            rows[symbol] = {"under": c / lows[-1][1] - 1, "drop": c / peak - 1, "lows": lows}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["under", "drop", "lows"])
