"""日米の株で、過去の成績が一番良くなる組み方を自動で選んで、今のポートフォリオを出す。

材料は、ページを作るときに書き出す銘柄ごとの月足（site/s/市場/コード.json）。株価を取り直さずに動く。

やること:
1. 組み方の候補（勢いの強い銘柄・値動きの穏やかな銘柄・指数そのまま など、条件違いで数十通り）を、
   毎月入れ替えたとして過去にさかのぼって試す（売買の手数料も引く）
2. 毎月、その時点までの直近 PICK_MONTHS か月で成績（値動きの割に増えたか）が一番良かった組み方に乗り換える。
   その月に分かることだけで選ぶので、後出しにならない（これを「自動選択」と呼ぶ）
3. 今月の「自動選択」が選んでいる組み方で、今の銘柄と割合を出す。日米は GPIF に倣って半々にする

注意: 今も上場している銘柄だけで試すので、つぶれた銘柄が入らない分、過去の成績は実際より良く出る。配当は入っていない。
"""
import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "portfolio_template.html"
INDEX = {"jp": ("N225", "日経平均"), "us": ("NDX", "ナスダック100")}
COST = 0.003          # 売り買いした金額にかかる手数料・値幅のずれ（片道 0.3%）
PICK_MONTHS = 36      # 自動選択で、成績を比べる直近の月数
MIN_HISTORY = 25      # 候補に入れるのに要る月足の本数
MIN_PRICE = {"jp": 300, "us": 5}   # 低位株（値が飛びやすい）は外す
MAX_JUMP = 3.0        # 1か月で3倍・3分の1になった足がある銘柄は、データの誤り（株式分割など）かもしれないので外す
TREND_MONTHS = 10     # 指数がこの月数の平均を下回ったら、株を売って現金で待つ（「守り」付きの組み方）


def _strategies():
    """組み方の候補。kind: mom=勢い（直近の上がり方）、lowvol=値動きの穏やかさ、score=勢い÷値動き、index=指数そのまま"""
    out = [{"kind": "index", "name": "指数そのまま"}]
    for kind in ["mom", "score", "lowvol"]:
        for look in ([6, 12] if kind != "lowvol" else [12]):
            for top in [10, 20, 30]:
                for weight in ["eq", "invvol"]:
                    for guard in [False, True]:
                        out.append({"kind": kind, "look": look, "top": top, "weight": weight, "guard": guard})
    for s in out:
        if "name" not in s:
            s["name"] = _name(s)
    return out


def _name(s):
    kind = {"mom": "勢い", "score": "勢い÷値動き", "lowvol": "値動きの穏やかさ"}[s["kind"]]
    parts = [f"{kind}（{s['look']}か月）" if s["kind"] != "lowvol" else kind, f"上位{s['top']}銘柄",
             "値動きの小さい銘柄を多め" if s["weight"] == "invvol" else "同じ金額ずつ"]
    if s["guard"]:
        parts.append(f"指数が{TREND_MONTHS}か月平均を割ったら現金")
    return "・".join(parts)


def load(site, market, liquid=None):
    """銘柄ごとの月足ファイルから、月末の終値の表（行=月、列=コード）と銘柄名を作る。

    最後の行は今月（取引の途中）の値。ファイルは古い足 p と新しい足 c に分かれているのでつなぐ。
    liquid（売買代金が足りる銘柄のコードの集まり）を渡すと、それ以外の銘柄は外す（指数は残す）
    """
    folder = Path(site) / "s" / market
    series, names = {}, {}
    for path in folder.glob("*.json"):
        code = path.stem
        if code.startswith("_"):
            continue
        if liquid is not None and code not in liquid and code != INDEX[market][0]:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        m = data.get("m") or {}
        closes = [np.nan if v is None else v for v in (m.get("p") or []) + (m.get("c") or [])]
        if len(closes) < MIN_HISTORY:
            continue
        series[code] = closes
        names[code] = data.get("name") or code
    length = max(len(v) for v in series.values())
    table = np.full((length, len(series)), np.nan)
    for j, closes in enumerate(series.values()):
        table[length - len(closes):, j] = closes  # どの銘柄も今月で終わるので、後ろにそろえる
    months = pd.period_range(end=pd.Period(date.today(), "M"), periods=length, freq="M")
    return pd.DataFrame(table, index=months, columns=list(series)), names


def _liquid_codes(site, market):
    """ページを作るときに書き出した、売買代金が足りる銘柄のコード。無ければ None（全銘柄を使う）"""
    path = Path(site) / "s" / market / "_liquid.json"
    if not path.exists():
        return None
    return set(json.loads(path.read_text(encoding="utf-8")))


def _weights(s, close, rets, t, index_col):
    """月 t の終わりに組む割合（列ごと）。勢い・値動きは月 t までの値だけで測る"""
    n = close.shape[1]
    w = np.zeros(n)
    if s["kind"] == "index":
        w[index_col] = 1.0
        return w
    if s["guard"]:
        idx = close[t - TREND_MONTHS + 1:t + 1, index_col]
        if close[t, index_col] < np.nanmean(idx):
            return w  # 現金で待つ
    look = s.get("look", 12)
    window = rets[t - 11:t + 1]                     # 値動きの大きさは直近12か月で測る
    vol = np.nanstd(window, axis=0) * np.sqrt(12)
    ok = (~np.isnan(window).any(axis=0)) & (close[t] >= s["min_price"]) & (vol > 0)
    ok &= ~(np.abs(np.log1p(window)) > np.log(MAX_JUMP)).any(axis=0)
    ok[index_col] = False
    if s["kind"] == "lowvol":
        score = -vol
    else:
        # 直近の1か月は除く（すぐ戻りやすいため）。look か月前から1か月前までの上がり方
        mom = close[t - 1] / close[t - look] - 1
        ok &= ~np.isnan(mom)
        score = mom if s["kind"] == "mom" else mom / vol
    score = np.where(ok, score, -np.inf)
    if ok.sum() < s["top"]:
        return w
    pick = np.argsort(score)[::-1][:s["top"]]
    raw = np.ones(len(pick)) if s["weight"] == "eq" else 1 / vol[pick]
    w[pick] = raw / raw.sum()
    return w


def backtest(close_df, market):
    """どの組み方も毎月入れ替えたとして、月ごとの増え方（手数料を引いた後）を出す。

    戻り値: (月ごとの増え方の表 行=月 列=組み方の番号, 組み方の一覧, 月 t の終わりに組んだ割合 {番号: 割合の列})。
    今月（途中）の足は試しには使わず、今の割合を出すのにだけ使う
    """
    close = close_df.to_numpy()
    index_col = close_df.columns.get_loc(INDEX[market][0])
    with np.errstate(invalid="ignore", divide="ignore"):
        rets = close[1:] / close[:-1] - 1
    rets = np.vstack([np.full(close.shape[1], np.nan), rets])
    strategies = _strategies()
    for s in strategies:
        s["min_price"] = MIN_PRICE[market]
    start = 13  # 12か月の勢いと値動きが測れる最初の月
    last_done = len(close) - 2  # 試しに使う最後の月（その次の月の増え方まで分かる）
    out = np.full((len(close), len(strategies)), np.nan)
    latest = {}
    for k, s in enumerate(strategies):
        held = np.zeros(close.shape[1])
        for t in range(start, len(close)):
            w = _weights(s, close, rets, t, index_col)
            if t == len(close) - 1:
                latest[k] = w
                break
            if t > last_done:
                break
            # 前の月から持っていた分は値動きで割合がずれているので、そこから組み直した分に手数料がかかる
            trade = np.abs(w - held).sum()
            nxt = np.nan_to_num(rets[t + 1])
            gross = float(w @ nxt)
            out[t + 1, k] = gross - COST * trade
            grown = w * (1 + nxt)
            held = grown / grown.sum() if grown.sum() > 0 else grown
    table = pd.DataFrame(out, index=close_df.index, columns=range(len(strategies)))
    return table.iloc[start + 1:-1], strategies, latest


def _sharpe(r):
    r = r[~np.isnan(r)]
    if len(r) < 6 or r.std() == 0:
        return -np.inf
    return r.mean() / r.std() * np.sqrt(12)


def auto_select(table):
    """毎月、直近 PICK_MONTHS か月で一番成績の良かった組み方に乗り換える。選ぶのはその月までの成績だけ"""
    values = table.to_numpy()
    picks, rets = [], []
    for t in range(PICK_MONTHS, len(values)):
        past = values[t - PICK_MONTHS:t]
        best = int(np.argmax([_sharpe(past[:, k]) for k in range(values.shape[1])]))
        picks.append(best)
        rets.append(values[t, best])
    now = int(np.argmax([_sharpe(values[-PICK_MONTHS:, k]) for k in range(values.shape[1])]))
    return pd.Series(rets, index=table.index[PICK_MONTHS:]), picks, now


def stats(r):
    """月ごとの増え方から、年率の増え方・値動きの大きさ・増え方÷値動き・一番大きく減った割合"""
    r = pd.Series(r).dropna()
    curve = (1 + r).cumprod()
    years = len(r) / 12
    return {
        "cagr": float(curve.iloc[-1] ** (1 / years) - 1),
        "vol": float(r.std() * np.sqrt(12)),
        "sharpe": float(_sharpe(r.to_numpy())),
        "maxdd": float((curve / curve.cummax() - 1).min()),
        "total": float(curve.iloc[-1] - 1),
    }


def build(site, log=print):
    """日米それぞれ試して、自動選択の成績と今のポートフォリオをまとめる"""
    result = {"markets": {}, "built": date.today().isoformat()}
    oos = {}
    for market in ["jp", "us"]:
        liquid = _liquid_codes(site, market)
        close, names = load(site, market, liquid)
        log(f"{market}: {close.shape[1]:,} 銘柄・{close.shape[0]} か月"
            f"（{'売買代金で絞った' if liquid is not None else '売買代金の一覧が無いので全'}銘柄）")
        table, strategies, latest = backtest(close, market)
        auto, picks, now = auto_select(table)
        index_r = table[0]
        oos[market] = {"auto": auto, "index": index_r.loc[auto.index]}
        # 比べる表: 自動選択と、指数と、全期間で一番良かった組み方（これは後出しなので参考）
        full_best = int(np.argmax([_sharpe(table[k].to_numpy()) for k in table.columns]))
        rows = [("自動選択（後出しなし）", auto), (f"{INDEX[market][1]}", index_r.loc[auto.index]),
                (f"参考: 後から見た一番（{strategies[full_best]['name']}）", table[full_best].loc[auto.index])]
        w = latest[now]
        holdings = [{"code": close.columns[j], "name": names[close.columns[j]], "weight": round(float(w[j]), 4),
                     "price": float(close.iloc[-1, j])}
                    for j in np.argsort(w)[::-1] if w[j] > 0]
        used = pd.Series(picks).value_counts()
        result["markets"][market] = {
            "universe": int(close.shape[1]),
            "liquid": liquid is not None,
            "months": [str(p) for p in auto.index],
            "rows": [{"name": name, **stats(r), "curve": [round(float(v), 4) for v in (1 + r.fillna(0)).cumprod()]}
                     for name, r in rows],
            "now": strategies[now]["name"],
            "now_sharpe": float(_sharpe(table[now].to_numpy()[-PICK_MONTHS:])),
            "holdings": holdings,
            "used": [{"name": strategies[k]["name"], "months": int(c)} for k, c in used.head(5).items()],
            "tried": len(strategies),
        }
        s = stats(auto)
        log(f"  自動選択: 年率 {s['cagr']:+.1%}・最大の下げ {s['maxdd']:.1%}・増え方÷値動き {s['sharpe']:.2f}"
            f" / {INDEX[market][1]}: 年率 {stats(oos[market]['index'])['cagr']:+.1%}")
        log(f"  今の組み方: {strategies[now]['name']}（{len(holdings)} 銘柄）")
    # 日米を半々（GPIF の国内株・外国株と同じ考え方）。為替は入れていない
    both = (oos["jp"]["auto"] * 0.5 + oos["us"]["auto"] * 0.5).dropna()
    gpif = (oos["jp"]["index"] * 0.5 + oos["us"]["index"] * 0.5).loc[both.index]
    result["both"] = {
        "months": [str(p) for p in both.index],
        "rows": [{"name": name, **stats(r), "curve": [round(float(v), 4) for v in (1 + r).cumprod()]}
                 for name, r in [("日米の自動選択を半々", both), ("GPIF風（日経平均とナスダック100を半々）", gpif)]],
    }
    s, g = stats(both), stats(gpif)
    log(f"日米半々: 自動選択 年率 {s['cagr']:+.1%}（下げ {s['maxdd']:.1%}）/ GPIF風 年率 {g['cagr']:+.1%}（下げ {g['maxdd']:.1%}）")
    return result


def main():
    parser = argparse.ArgumentParser(description="日米の株で、過去の成績が一番良い組み方を選んでポートフォリオを出す")
    parser.add_argument("--site", type=Path, default=ROOT / "site", help="銘柄ごとの月足ファイル（s/）があるフォルダ")
    parser.add_argument("--out", type=Path, default=None, help="書き出す HTML（省略時は site/portfolio.html）")
    args = parser.parse_args()
    out = args.out or args.site / "portfolio.html"
    result = build(args.site)
    payload = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    print(f"{out} を作りました（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
