"""「底値を切り上げながら上がってきたのに、前の底値を割った」銘柄を探す（崩れ）。

終値の折れ線で見る。山と谷は、終値が直前の山（谷）から swing 以上動いたところで決まる
（それより小さい上げ下げは、山・谷に数えない）。

  - 谷が RISING_LOWS 回以上続けて切り上がっている（谷が RISING_LOWS + 1 個、どれも前の谷より高い）
  - その谷がすべて、直近 window 本の中にある（チャートに形が見える、最近の上げだけにする）
  - 終値が、最後の谷を初めて下回った足

高値の切り下げ（前の山を超えられない）は見ない。早く気づけるが、そのあとまた上がる空振りが多い。

持ち株の状態（state）は、同じ山と谷から決める。こちらは逆さの形（下げから上げへの切り替わり）と、
早めの合図（高値の切り下げ・底値の切り上げ）も見る。
"""
import numpy as np
import pandas as pd

RISING_LOWS = 2
# 持ち株の「横ばい」。直近 RANGE_BARS 本の終値の幅（高値 ÷ 安値 − 1）が RANGE_WIDTH 以下か、
# その間に向きが RANGE_FLIPS 回以上入れ替わっていて、いまの終値がその前までの幅の中にあるとき
RANGE_BARS = 26
RANGE_WIDTH = 0.15
RANGE_FLIPS = 3   # 下げて戻しただけ（2回）は横ばいに数えない


def _scan(close, swing, window):
    """1銘柄の終値（数値の並び）を古い方から見て、崩れた足を返す。

    返すのは {足の番号: (切り上げていた谷の [足の番号, 終値] の組, その間の一番高い終値)}
    """
    events = {}
    lows = []        # 決まった谷の (足の番号, 終値)。切り上げが途切れたら、その谷から数え直す
    peak = 0.0       # lows の最初の谷が決まってからの一番高い終値
    broken = False   # 最後の谷をもう割ったか（同じ谷で2回は数えない）
    valleys = [p for p in _pivots(close, swing) if not p[3]]
    k = 0
    for i, c in enumerate(close):
        if np.isnan(c):
            continue
        if (len(lows) > RISING_LOWS and not broken and c < lows[-1][1]
                and lows[-RISING_LOWS - 1][0] > i - window):
            events[i] = (lows[-RISING_LOWS - 1:], peak)
            broken = True
        peak = max(peak, c)
        while k < len(valleys) and valleys[k][0] <= i:
            point = valleys[k][1:3]
            if lows and point[1] <= lows[-1][1]:
                lows, peak = [], 0.0
            lows.append(point)
            broken = False
            k += 1
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


def _pivots(close, swing):
    """決まった山と谷を、決まった順に返す。(決まった足の番号, 山・谷の足の番号, 終値, 山なら True) の組"""
    found = []
    up = None        # いま上げの途中か（None はまだ向きが決まっていない）
    hi = lo = None   # 向きが変わってからの一番高い・安い (足の番号, 終値)
    for i, c in enumerate(close):
        if np.isnan(c):
            continue
        point = (i, c)
        if hi is None:
            hi = lo = point
            continue
        if up is not False and c > hi[1]:
            hi = point
        if up is not True and c <= lo[1]:
            lo = point
        if up is not False and c <= hi[1] * (1 - swing):
            found.append((i, *hi, True))
            up, lo = False, point
        elif up is not True and c >= lo[1] * (1 + swing):
            found.append((i, *lo, False))
            up, hi = True, point
    return found


def state(close, swing):
    """1銘柄の終値（数値の並び。最後が判定する足）から、いまの状態を返す。

    向き（trend）は、終値が直前の谷を割ったら下げ（down）、直前の山を超えたら上げ（up）に切り替わる。
    初めは、最初に決まったのが谷なら上げ、山なら下げとする。
    返すのは
      trend: "up"・"down"・None（山か谷がまだ無く、決まらない）
      since: いまの向きになった足の番号
      flipped: 逆の向きから切り替わったか（上げ → 下げ が「崩れ」、下げ → 上げ が「反転」）
      warn: 逆向きの早めの合図が出ているか。上げの途中で山が前の山より低い（高値の切り下げ）、
            下げの途中で谷が前の谷より高い（底値の切り上げ）
      level: 次に向きが切り替わる終値（上げなら直前の谷、下げなら直前の山）。無ければ None
      pivots: 山と谷の (足の番号, 終値, 山なら True)
      range: 横ばいなら、直近 RANGE_BARS 本の終値の (下限, 上限)。横ばいでなければ None。
             横ばいの間は、山と谷を行き来するたびに向きが入れ替わってしまい、向きは当てにならない
    """
    pivots = _pivots(close, swing)
    last = {True: None, False: None}   # 直前の山・谷の (足の番号, 終値)
    prev = {True: None, False: None}   # その前の山・谷
    trend, since, flipped = None, None, False
    flips = []                         # 向きが逆に入れ替わった足の番号
    k, now = 0, np.nan   # now は値のある直近の終値
    for i, c in enumerate(close):
        while k < len(pivots) and pivots[k][0] <= i:
            _, at, value, high = pivots[k]
            prev[high], last[high] = last[high], (at, value)
            if trend is None:
                trend, since = "down" if high else "up", i
            k += 1
        if np.isnan(c):
            continue
        now = c
        if trend != "down" and last[False] and c < last[False][1]:
            trend, since, flipped = "down", i, trend == "up"
        elif trend != "up" and last[True] and c > last[True][1]:
            trend, since, flipped = "up", i, trend == "down"
        else:
            continue
        if flipped:
            flips.append(i)
    # 早めの合図は、いまの向きになってからできた山（谷）だけで見る。それをもう超えた（割った）なら消える
    side = trend == "up"   # 上げなら山、下げなら谷を見る
    mark, before, c = last[side], prev[side], now
    warn = bool(trend and mark and before and mark[0] >= since
                and (mark[1] < before[1] and c <= mark[1] if side else mark[1] > before[1] and c >= mark[1]))
    level = last[not side] if trend else None
    # 横ばい。いまの終値が、その前までの幅を抜けていたら横ばいとはしない（抜けたのは動き出しの合図）
    before = close[-RANGE_BARS - 1:-1]
    before = before[~np.isnan(before)]
    span = None
    if len(before) >= RANGE_BARS // 2 and before.min() <= now <= before.max():
        narrow = before.max() / before.min() - 1 <= RANGE_WIDTH
        swapped = sum(i >= len(close) - RANGE_BARS for i in flips) >= RANGE_FLIPS
        if narrow or swapped:
            span = (before.min(), before.max())
    return {"trend": trend, "since": since, "flipped": flipped, "warn": warn, "range": span,
            "level": level[1] if level else None,
            "pivots": [(at, value, high) for _, at, value, high in pivots]}
