"""移動平均の「抜け」を探す。短期線が中期線を上に抜けた（上抜け）か、下に抜けた（下抜け）か。

日足・週足・月足のどれにも同じ見方を使う。前の足では短期線が中期線以下で、その足で上に出たら上抜け。逆が下抜け。
"""
import numpy as np


def moving_averages(close, windows):
    """単純移動平均（windows は本数の組。短い順）"""
    return [close.rolling(n).mean() for n in windows]


def last_cross(short, mid, done):
    """銘柄ごとに、取引の終わった足（先頭から done 本）の中でいちばん新しい抜けを返す。

    short・mid は短期線・中期線の表（行が足、列が銘柄）。
    返すのは {銘柄: (向き, 何本前か)}。向きは上抜けが 1、下抜けが -1。何本前かは、直近の足なら 0。
    抜けが無い銘柄は入らない。
    """
    s, m = short.to_numpy()[:done], mid.to_numpy()[:done]
    valid = ~np.isnan(s) & ~np.isnan(m)
    above = s > m
    both = valid[1:] & valid[:-1]   # 前の足にも平均線がある足だけで見る
    signed = np.zeros(s.shape, dtype=int)
    signed[1:][both & above[1:] & ~above[:-1]] = 1
    signed[1:][both & ~above[1:] & above[:-1]] = -1
    rows = np.arange(len(signed))[:, None]
    at = np.where(signed != 0, rows, -1).max(axis=0, initial=-1)
    return {symbol: (int(signed[i, column]), int(len(signed) - 1 - i))
            for column, (symbol, i) in enumerate(zip(short.columns, at)) if i >= 0}
