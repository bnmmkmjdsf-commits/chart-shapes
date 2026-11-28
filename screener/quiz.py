"""チャート当てクイズの問題を作る（site/quiz/市場/番号.json と site/quiz/市場/index.json）。

売買代金が足りる銘柄（s/市場/_liquid.json）から COUNT 銘柄をでたらめに選び、日足を長く取って、
銘柄ごとにでたらめな日（答えの1年が終わっている日）で切る。問題には、その日までの日足・週足・月足のローソク足と出来高、
答えには、その後1年の足を入れる。答えは「切った日の終値」と「1年後の終値」のどちらが高いか。

株価を取り直すたびに、取り直した市場の問題を作り直す（毎回ちがう問題になる）。
"""
import argparse
import json
import random
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from screener import prices

ROOT = Path(__file__).resolve().parent.parent
COUNT = 120                         # 市場ごとの問題数
AHEAD_DAYS = 365                    # 答えの長さ（日）
# 足の種類ごとの、問題に出す本数（最大・最小）
FRAMES = {
    "d": {"rule": None, "show": 120, "min": 60},
    "w": {"rule": "W-MON", "show": 104, "min": 52},
    "m": {"rule": "MS", "show": 60, "min": 24},
}
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
MAX_JUMP = 3.0  # 1日で3倍・3分の1の動きがある銘柄は、データの誤り（株式分割など）かもしれないので使わない


def _round(v):
    return None if not np.isfinite(v) else float(f"{v:.4g}")


def _resample(daily, rule):
    if rule is None:
        return daily
    # 週足は月曜、月足は1日の日付にそろえる
    label = {"W-MON": dict(label="left", closed="left"), "MS": {}}[rule]
    return daily.resample(rule, **label).agg(AGG).dropna(subset=["close"])


def _frame(daily, cut, end, spec):
    """1つの足の種類の、問題（cut まで）と答え（end まで）の足。足りなければ None"""
    bars = _resample(daily, spec["rule"])
    before = bars.index[bars.index <= cut]
    if len(before) < spec["min"]:
        return None
    first = before[max(0, len(before) - spec["show"])]
    part = bars.loc[first:end]
    n_before = int((part.index <= cut).sum())
    if n_before >= len(part):
        return None
    return {
        "from": first.strftime("%Y-%m-%d"),
        "n": n_before,  # 問題に出す本数。残りが答え
        **{k: [_round(v) for v in part[k]] for k in AGG},
    }


def _question(market, code, info, daily, rng):
    daily = daily.dropna(subset=["close"])
    if len(daily) < 300:
        return None
    jumps = np.abs(np.log(daily["close"] / daily["close"].shift())) > np.log(MAX_JUMP)
    last_cut = daily.index[-1] - pd.Timedelta(days=AHEAD_DAYS)
    first_cut = daily.index[0] + pd.Timedelta(days=200)
    days = daily.index[(daily.index >= first_cut) & (daily.index <= last_cut)]
    if len(days) == 0:
        return None
    for _ in range(5):
        cut = days[rng.randrange(len(days))]
        end = daily.index[daily.index <= cut + pd.Timedelta(days=AHEAD_DAYS)][-1]
        window = jumps.loc[cut - pd.Timedelta(days=5 * 366):end]
        if window.any():
            continue
        frames = {key: _frame(daily, cut, end, spec) for key, spec in FRAMES.items()}
        if any(f is None for f in frames.values()):
            continue
        start, stop = daily.at[cut, "close"], daily.at[end, "close"]
        return {
            "market": market, "code": code, "name": info.get("name") or code, "sector": info.get("sector") or "",
            "cut": cut.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
            "change": round(float(stop / start - 1), 4), "frames": frames,
        }
    return None


def build(site, market, log=print, seed=None):
    folder = Path(site) / "s" / market
    codes = [c for c in json.loads((folder / "_liquid.json").read_text(encoding="utf-8")) if c not in ("N225", "NDX")]
    rng = random.Random(seed)
    chosen = rng.sample(codes, min(len(codes), int(COUNT * 1.3)))  # 作れない銘柄の分も多めに取る
    symbols = {f"{c}.T" if market == "jp" else c: c for c in chosen}
    log(f"{market}: クイズ用に {len(symbols)} 銘柄の日足を取得中...")
    bars = prices.fetch_bars(list(symbols), "1d", "10y", "クイズの日足", log)
    final = Path(site) / "quiz" / market
    out = final.with_name(market + "-new")  # 作り終えてから入れ替える（途中で止まっても前の問題が残る）
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    made = 0
    for symbol, code in symbols.items():
        if made >= COUNT or symbol not in bars["close"].columns:
            continue
        daily = pd.DataFrame({k: bars[k][symbol] for k in AGG})
        path = folder / f"{code}.json"
        info = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        q = _question(market, code, info, daily, rng)
        if q is None:
            continue
        (out / f"{made}.json").write_text(json.dumps(q, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        made += 1
    (out / "index.json").write_text(json.dumps({"count": made}), encoding="utf-8")
    shutil.rmtree(final, ignore_errors=True)
    out.rename(final)
    log(f"{market}: クイズの問題を {made} 問作りました")
    return made


def main():
    parser = argparse.ArgumentParser(description="チャート当てクイズの問題を作る")
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    parser.add_argument("--markets", choices=["both", "jp", "us"], default="both", help="問題を作り直す市場")
    args = parser.parse_args()
    for market in ["jp", "us"]:
        if args.markets in ("both", market):
            try:
                build(args.site, market)
            except Exception as error:  # 作れなくても、前の問題を残してほかは止めない
                print(f"{market}: クイズの問題を作れませんでした（{error}）。前の問題を残します")


if __name__ == "__main__":
    main()
