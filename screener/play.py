"""遊べるページ: チャート当てクイズ（quiz.html）と、次のモデルナ探し（shape.html）。

材料は、ページを作るときに書き出す銘柄ごとの月足（site/s/市場/コード.json）と、売買代金が足りる銘柄の一覧（_liquid.json）。
株価は取り直さない。

- クイズ: ページが銘柄ごとのファイルを読み、銘柄と時期をでたらめに選んで出す。ここではテンプレートを置くだけ
- 次のモデルナ探し: 過去に1年で SURGE 倍以上になった銘柄の「上がる前の SHAPE か月の形」を手本にし、
  今の形がどれかの手本にそっくりな銘柄を並べる。形は株価の対数を平均0・ばらつき1にそろえて比べる（値段の高さや振れ幅は見ない）。
  当たりやすさも、過去の各月で「その月までに分かった手本」だけを使って選んだとして試し、ページに出す（後出しにならない）
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from screener import portfolio

HERE = Path(__file__).resolve().parent
SHAPE = 12      # 形を比べる月数
AHEAD = 12      # 急騰を測る月数（手本は、この月数で SURGE 倍以上になった銘柄）
SURGE = 3.0
TOP = 20        # 並べる銘柄数
MAX_JUMP = 3.0  # 1か月で3倍・3分の1の足がある形は、データの誤り（株式分割など）かもしれないので使わない


def _norm(x):
    """株価の並びを、対数にして平均0・ばらつき1にそろえる。値動きが無ければ None"""
    x = np.log(x)
    x = x - x.mean()
    s = x.std()
    return x / s if s > 0 else None


def _windows(c, jump, t):
    """月 t までの SHAPE+1 本の形（列ごと）と、形が使えるか"""
    cur = c[t - SHAPE:t + 1]
    ok = ~np.isnan(cur).any(axis=0) & ~jump[t - SHAPE:t].any(axis=0)
    shapes = np.zeros((cur.shape[1], SHAPE + 1))
    for j in np.nonzero(ok)[0]:
        n = _norm(cur[:, j])
        if n is None:
            ok[j] = False
        else:
            shapes[j] = n
    return shapes, ok


def _winners(c, jump):
    """過去の急騰（AHEAD か月で SURGE 倍以上）。銘柄ごとに一番大きかった1回だけ。(列, 倍率, 形の終わりの月, 急騰の終わりの月, 形)"""
    out = []
    for j in range(c.shape[1]):
        best = None
        for end in range(SHAPE + AHEAD, c.shape[0]):
            start = end - AHEAD
            seg = c[start - SHAPE:start + 1, j]
            if np.isnan(seg).any() or np.isnan(c[end, j]) or jump[start - SHAPE:start, j].any():
                continue
            ratio = c[end, j] / c[start, j]
            if ratio >= SURGE and (best is None or ratio > best[0]):
                best = (ratio, start, end)
        if best:
            shape = _norm(c[best[1] - SHAPE:best[1] + 1, j])
            if shape is not None:
                out.append((j, *best, shape))
    return out


def _pick(shapes, ok, templates):
    """どの手本に一番似ているか（相関）。(似ている度, 手本の番号)"""
    sim = shapes @ np.array([w[4] for w in templates]).T / (SHAPE + 1)
    best = sim.argmax(axis=1)
    score = np.where(ok, sim[np.arange(len(best)), best], -np.inf)
    return score, best


def _check(c, jump, winners):
    """過去の各月で、その月までに急騰し終えた手本だけを使って TOP 銘柄を選んだとして、その後 AHEAD か月を全体と比べる"""
    rows = []
    for t in range(SHAPE + AHEAD + 12, c.shape[0] - AHEAD):
        known = [w for w in winners if w[3] <= t]
        if len(known) < 5:
            continue
        shapes, ok = _windows(c, jump, t)
        ok &= ~np.isnan(c[t + AHEAD])
        score, _ = _pick(shapes, ok, known)
        top = np.argsort(score)[::-1][:TOP]
        fwd = c[t + AHEAD] / c[t] - 1
        rows.append(((fwd[top] >= 1).mean(), (fwd[ok] >= 1).mean(),
                     (fwd[top] <= -0.5).mean(), (fwd[ok] <= -0.5).mean(),
                     np.median(fwd[top]), np.median(fwd[ok])))
    r = np.array(rows)
    keys = ["double", "double_all", "half", "half_all", "median", "median_all"]
    return {"months": len(r), **{k: round(float(v), 4) for k, v in zip(keys, r.mean(axis=0))}}


def shape_market(site, market, log=print):
    liquid = portfolio._liquid_codes(site, market)
    close, names = portfolio.load(site, market, liquid)
    close = close.drop(columns=[portfolio.INDEX[market][0]]).iloc[:-1]  # 指数と、取引の途中の今月は使わない
    c = close.to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        jump = np.abs(np.log(c[1:] / c[:-1])) > np.log(MAX_JUMP)
    winners = _winners(c, jump)
    check = _check(c, jump, winners)
    t = c.shape[0] - 1
    shapes, ok = _windows(c, jump, t)
    score, best = _pick(shapes, ok, winners)
    months = [str(p) for p in close.index]
    picks = []
    for j in np.argsort(score)[::-1][:TOP]:
        w = winners[best[j]]
        code = close.columns[j]
        picks.append({
            "code": code, "name": names[code], "sim": round(float(score[j]), 3),
            "price": float(c[t, j]), "rise": round(float(c[t, j] / c[t - AHEAD, j] - 1), 4),
            "shape": [round(float(v), 3) for v in shapes[j]],
            "from": months[t - SHAPE], "to": months[t],
            "model": {"code": close.columns[w[0]], "name": names[close.columns[w[0]]], "times": round(float(w[1]), 2),
                      "from": months[w[2] - SHAPE], "start": months[w[2]], "end": months[w[3]],
                      "shape": [round(float(v), 3) for v in w[4]]},
        })
    log(f"{market}: 手本 {len(winners)} 個・{close.shape[1]:,} 銘柄から {len(picks)} 銘柄。"
        f"過去{check['months']}か月で試すと、1年で2倍になった割合 {check['double']:.1%}（全体 {check['double_all']:.1%}）・"
        f"半分以下 {check['half']:.1%}（全体 {check['half_all']:.1%}）")
    return {"picks": picks, "check": check, "models": len(winners), "universe": int(close.shape[1])}


def main():
    parser = argparse.ArgumentParser(description="チャート当てクイズと次のモデルナ探しのページを作る")
    parser.add_argument("--site", type=Path, default=portfolio.ROOT / "site", help="銘柄ごとの月足ファイル（s/）があるフォルダ")
    args = parser.parse_args()
    site = args.site
    data = {"built": pd.Timestamp.today().strftime("%Y-%m-%d"), "shape": SHAPE, "ahead": AHEAD, "surge": SURGE,
            "markets": {m: shape_market(site, m) for m in ["jp", "us"]}}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (site / "shape.html").write_text((HERE / "shape_template.html").read_text(encoding="utf-8").replace("__DATA__", payload),
                                     encoding="utf-8")
    (site / "quiz.html").write_text((HERE / "quiz_template.html").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{site / 'shape.html'} と {site / 'quiz.html'} を作りました")


if __name__ == "__main__":
    main()
