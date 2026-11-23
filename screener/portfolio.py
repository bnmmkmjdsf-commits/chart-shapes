"""日米の株で、指数を土台に勢いの強い銘柄を足したポートフォリオを出し、過去の成績を試す。

材料は、ページを作るときに書き出す銘柄ごとの月足（site/s/市場/コード.json）。株価は取り直さない。

組み方（市場ごと。毎月の終わりに組み直す）:
- 半分は指数（日経平均・ナスダック100）。土台にして値下がりを和らげる
- 半分は上昇率TOP20（「勢い」の上位 TOP 銘柄）に同じ金額ずつ。勢い = 12か月前から1か月前までの上がり方（直近1か月は戻りやすいので除く）。
  上がっている株はしばらく上がり続けやすい、という昔から知られた傾向を使う
- 日米は GPIF の国内株・外国株と同じく半々
- ほかに、指数を入れない「上昇率TOP20だけ」を、日米半々と日本25%・米国75%（MIXES）でも出す

この形は、数十通りの組み方（勢いの測り方・銘柄数・値動きの小さい銘柄を多めにする・指数が下向きなら現金で待つ・
毎月その時点の一番に乗り換える など）を過去で試して比べて決めた。前半・後半のどちらの期間でも指数に勝ち、
下げも指数と同じくらいに収まったのがこの形だった（ほかは、片方の期間だけ良い・下げが大きい・乗り換えで負ける）。

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
TOP = 20              # 勢いの上位から組み入れる銘柄数
CORE = 0.5            # 指数に置く割合
# 日米を合わせた組み方。jp は日本株に置く割合、core は各市場で指数に置く割合。
# 日本25%・米国75% は、上昇率TOP20だけで日本の割合を0〜100%に変えて試し、増え方÷値動きが一番良かったあたり
# 先頭の組み方を、ページを開いたときに出す
MIXES = [
    {"key": "top_us", "label": f"TOP{TOP} 米国多め", "jp": 0.25, "core": 0.0, "name": f"上昇率TOP{TOP}だけ（日本25%・米国75%）"},
    {"key": "top", "label": f"TOP{TOP} 半々", "jp": 0.5, "core": 0.0, "name": f"上昇率TOP{TOP}だけ（日米半々）"},
    {"key": "both", "label": "指数＋TOP20", "jp": 0.5, "core": CORE, "name": "指数半分＋上昇率TOP20半分（日米半々）"},
]
MIN_HISTORY = 25      # 候補に入れるのに要る月足の本数
MIN_PRICE = {"jp": 300, "us": 5}   # 低位株（値が飛びやすい）は外す
MAX_JUMP = 3.0        # 1か月で3倍・3分の1になった足がある銘柄は、データの誤り（株式分割など）かもしれないので外す


STRATEGIES = [
    {"kind": "index", "name": "指数そのまま"},
    {"kind": "mom", "look": 12, "top": TOP, "name": f"上昇率TOP{TOP}だけ"},
]


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


def _weights(s, close, rets, t, index_col, min_price):
    """月 t の終わりに組む割合（列ごと）。勢いは月 t までの値だけで測る"""
    w = np.zeros(close.shape[1])
    if s["kind"] == "index":
        w[index_col] = 1.0
        return w
    window = rets[t - 11:t + 1]
    ok = (~np.isnan(window).any(axis=0)) & (close[t] >= min_price)
    ok &= ~(np.abs(np.log1p(window)) > np.log(MAX_JUMP)).any(axis=0)
    ok[index_col] = False
    mom = close[t - 1] / close[t - s["look"]] - 1
    ok &= ~np.isnan(mom)
    if ok.sum() < s["top"]:
        return w
    pick = np.argsort(np.where(ok, mom, -np.inf))[::-1][:s["top"]]
    w[pick] = 1 / len(pick)
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
    strategies = STRATEGIES
    start = 13  # 12か月の勢いと値動きが測れる最初の月
    last_done = len(close) - 2  # 試しに使う最後の月（その次の月の増え方まで分かる）
    out = np.full((len(close), len(strategies)), np.nan)
    latest = {}
    for k, s in enumerate(strategies):
        held = np.zeros(close.shape[1])
        for t in range(start, len(close)):
            w = _weights(s, close, rets, t, index_col, MIN_PRICE[market])
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


def _row(name, r):
    r = r.dropna()
    return {"name": name, **stats(r), "curve": [round(float(v), 4) for v in (1 + r).cumprod()]}


def _halves(r, index_r):
    """前半・後半それぞれで、指数より増えたか（たまたま一時期だけ良かったのではないかを見る）"""
    half = len(r) // 2
    out = []
    for label, part in [("前半", slice(None, half)), ("後半", slice(half, None))]:
        a, b = stats(r.iloc[part]), stats(index_r.iloc[part])
        out.append({"label": label, "from": str(r.index[part][0]), "to": str(r.index[part][-1]),
                    "cagr": a["cagr"], "index_cagr": b["cagr"], "maxdd": a["maxdd"], "index_maxdd": b["maxdd"]})
    return out


def build(site, log=print):
    """日米それぞれ試して、成績と今のポートフォリオをまとめる"""
    result = {"markets": {}, "built": date.today().isoformat(), "top": TOP, "core": CORE}
    mix = {}
    for market in ["jp", "us"]:
        liquid = _liquid_codes(site, market)
        close, names = load(site, market, liquid)
        log(f"{market}: {close.shape[1]:,} 銘柄・{close.shape[0]} か月"
            f"（{'売買代金で絞った' if liquid is not None else '売買代金の一覧が無いので全'}銘柄）")
        table, strategies, latest = backtest(close, market)
        index_r, mom_r = table[0], table[1]
        ours = index_r * CORE + mom_r * (1 - CORE)
        mix[market] = (index_r, mom_r)
        last = close.iloc[-1]
        index_code = INDEX[market][0]
        top = [{"code": close.columns[j], "name": names[close.columns[j]], "price": float(last.iloc[j]),
                "rise": round(float(close.iloc[-2, j] / close.iloc[-13, j] - 1), 4)}
               for j in np.nonzero(latest[1])[0]]
        top.sort(key=lambda x: x["rise"], reverse=True)
        name = INDEX[market][1]
        result["markets"][market] = {
            "universe": int(close.shape[1]),
            "liquid": liquid is not None,
            "months": [str(p) for p in ours.index],
            "rows": [_row(f"このポートフォリオ（{name}半分＋上昇率TOP{TOP}半分）", ours),
                     _row(name, index_r), _row(f"参考: 上昇率TOP{TOP}だけ", mom_r)],
            "halves": _halves(ours, index_r),
            "index": {"code": index_code, "name": names[index_code], "price": float(last[index_code])},
            "top": top,
        }
        a, b = stats(ours), stats(index_r)
        log(f"  このポートフォリオ: 年率 {a['cagr']:+.1%}・最大の下げ {a['maxdd']:.1%}・増え方÷値動き {a['sharpe']:.2f}"
            f" / {name}: 年率 {b['cagr']:+.1%}・最大の下げ {b['maxdd']:.1%}・{b['sharpe']:.2f}")
        for h in result["markets"][market]["halves"]:
            log(f"  {h['label']}（{h['from']}〜{h['to']}）: 年率 {h['cagr']:+.1%}（{name} {h['index_cagr']:+.1%}）")
        log(f"  今の上昇率TOP{TOP}: " + "、".join(f"{x['code']} {x['name']}" for x in top[:6]) + " ...")
    # 日米を合わせる（GPIF の国内株・外国株を半々にするのと同じ考え方）。為替は入れていない
    gpif = (mix["jp"][0] * 0.5 + mix["us"][0] * 0.5).dropna()
    result["mixes"] = []
    for m in MIXES:
        part = {market: index_r * m["core"] + mom_r * (1 - m["core"]) for market, (index_r, mom_r) in mix.items()}
        r = (part["jp"] * m["jp"] + part["us"] * (1 - m["jp"])).loc[gpif.index]
        result["mixes"].append({
            "key": m["key"], "label": m["label"], "jp": m["jp"], "core": m["core"],
            "months": [str(p) for p in r.index],
            "rows": [_row(m["name"], r), _row("GPIF風（日経平均とナスダック100を半々）", gpif)],
            "halves": _halves(r, gpif),
        })
        a = stats(r)
        log(f"{m['name']}: 年率 {a['cagr']:+.1%}（下げ {a['maxdd']:.1%}）")
    b = stats(gpif)
    log(f"GPIF風: 年率 {b['cagr']:+.1%}（下げ {b['maxdd']:.1%}）")
    return result


def main():
    parser = argparse.ArgumentParser(description="日米の株で、指数と勢いの強い銘柄を半々にしたポートフォリオを出す")
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
