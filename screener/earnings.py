"""株探の決算速報（https://kabutan.jp/news/）から、業績の強い日本株を拾い、チャートの形（上昇・レンジ・転換中・下降）で分ける。

決算速報の見出し（例:「タングス、今期経常を一転2.2倍増益に上方修正・5期ぶり最高益、配当も50円増額」）だけを見る。
点数は「最高益」3点、「上方修正」2点、「増配・増額」1点、「7-9月期も増益」のような直近の四半期の伸び1点。
点数が同じなら、増益率か上方修正の幅の大きい方を上にする。減益・下方修正・赤字の見出しは外す。
株探は GitHub Actions（クラウドのサーバー）からのアクセスを断る（HTTP 405）ので、読むのは別の場所（Claude のセッションや手元のパソコン）で
`python -m screener.earnings` を動かし、結果の earnings.json をリポジトリに push する。毎日の更新（gallery.py）は、その earnings.json を読むだけ。
earnings.json が無い・古いときは、業績タブを空にして、ほかは止めない。
"""
import argparse
import json
from pathlib import Path

import html
import re
import time
import urllib.request

import numpy as np
import pandas as pd

SAVED = Path(__file__).resolve().parent.parent / "earnings.json"
NEWS_URL = "https://kabutan.jp/news/?date={month}&page={page}"
# 何日前までの決算を見るか。決算シーズンをまたいでも、ひと月半ほどは残す
DAYS = 45
# 業績タブに載せる点数の下限（最高益だけ・上方修正と増配など）
MIN_SCORE = 3
# 株探を読む間隔（秒）。1日1回、数百ページを読むので間をあける
WAIT = 1.0
MAX_PAGES = 400

ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
TITLE = re.compile(r'href="/news/\?&b=(k\d+)"[^>]*>([^<]+)<')
CODE = re.compile(r'data-code="(\w{4})"')
TIME = re.compile(r'datetime="([^"]+)"')


def _page(month, page):
    req = urllib.request.Request(NEWS_URL.format(month=month, page=page), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", "ignore")


def headlines(log=print, days=DAYS, known=()):
    """直近 days 日の決算速報の見出し [{id, code, at, date, title}]（新しい順）。

    known は前に読んだ見出しの id。そこまで来たら、それより古いページは読まない（前回の続きだけ読む）。
    """
    known = set(known)
    now = pd.Timestamp.now("Asia/Tokyo")
    since = now - pd.Timedelta(days=days)
    months = sorted({f"{d:%Y%m}00" for d in pd.date_range(since.normalize(), now.normalize(), freq="D")}, reverse=True)
    out = []
    for month in months:
        for page in range(1, MAX_PAGES + 1):
            try:
                rows = ROW.findall(_page(month, page))
            except Exception as error:
                log(f"  株探 {month[:6]} の {page} ページ目が読めません（{error}）")
                break
            found = old = 0
            for row in rows:
                title, code, at = TITLE.search(row), CODE.search(row), TIME.search(row)
                if not (title and code and at):
                    continue
                found += 1
                when = pd.Timestamp(at.group(1))
                if when < since or title.group(1) in known:
                    old += 1
                    continue
                out.append({"id": title.group(1), "code": code.group(1), "at": when.isoformat(),
                            "date": f"{when:%m/%d}", "title": html.unescape(title.group(2)).strip()})
            time.sleep(WAIT)
            if not found or old:  # 最後のページを過ぎたか、見る期間より古くなったか、前回読んだところまで来た
                break
        log(f"  株探 {month[:4]}/{month[4:6]}: ここまで {len(out):,} 件")
        if old:
            break
    return out


def _growth(text):
    """見出しの最初の文の増益率（%）。2.2倍増益なら120、黒字浮上・転換は100とみなす"""
    m = re.search(r"([\d.]+)倍増益", text)
    if m:
        return (float(m.group(1)) - 1) * 100
    m = re.search(r"([\d.]+)％増益", text)
    if m:
        return float(m.group(1))
    return 100.0 if re.search(r"黒字(浮上|転換)", text) else None


def _revision(text):
    """上方修正の幅（%）。2.2倍上方修正なら120"""
    m = re.search(r"([\d.]+)(％|倍)上方修正", text)
    if not m:
        return 0.0
    return float(m.group(1)) if m.group(2) == "％" else (float(m.group(1)) - 1) * 100


def score(title):
    """(点数, 伸び率) を返す。業績タブに載せない見出しは None"""
    main = title.split("・")[0]
    if re.search(r"減益|下方修正|赤字|減額|減配|未定に", main):
        return None
    points = 0
    points += 3 if "最高益" in title else 0
    points += 2 if "上方修正" in title else 0
    points += 1 if re.search(r"増配|増額", title) else 0
    points += 1 if re.search(r"7-9月期も[\d.]+[％倍]", title) else 0
    growth = _growth(main)
    return points, max(growth or 0.0, _revision(title))


def strong(items):
    """銘柄ごとにいちばん良い見出しを残し、強い順に並べる [{code, date, title, score, rate}]"""
    best = {}
    for item in items:
        result = score(item["title"])
        if result is None or result[0] < MIN_SCORE:
            continue
        key = result
        if item["code"] not in best or key > best[item["code"]][0]:
            best[item["code"]] = (key, item)
    ranked = sorted(best.values(), key=lambda v: v[0], reverse=True)
    return [{"id": item["id"], "code": item["code"], "at": item["at"], "date": item["date"], "title": item["title"],
             "score": key[0], "rate": round(key[1])} for key, item in ranked]


# チャートの形を見る期間。足の数を3つに区切り、区切りごとの高値・安値が切り上がっているか・下がっているかを見る
SHAPE_BARS = {"d": 60, "w": 30}
# 区切りごとの高値・安値のぶれがこの割合以内なら、レンジ
RANGE_WIDTH = {"d": 0.06, "w": 0.10}
TOLERANCE = 0.01


def shape(high, low, close, frame):
    """1銘柄の形（"up" 上昇・"range" レンジ・"turn" 転換中・"down" 下降）と、レンジを上に抜けたか。

    形は、取引の終わった直近の足を除いた期間で見る。その期間がレンジで、直近の足の終値が期間の高値を超えたら上抜け。
    データが足りなければ (None, False)。
    """
    n = SHAPE_BARS[frame]
    h, l, c = high.dropna(), low.dropna(), close.dropna()
    if len(c) < n + 1 or len(h) < n + 1 or len(l) < n + 1:
        return None, False
    h, l = h.iloc[-n - 1:-1].to_numpy(), l.iloc[-n - 1:-1].to_numpy()
    size = n // 3
    hs = [h[i * size:(i + 1) * size].max() for i in range(3)]
    ls = [l[i * size:(i + 1) * size].min() for i in range(3)]
    t = TOLERANCE
    rising = all(b > a * (1 - t) for a, b in zip(hs, hs[1:])) and all(b > a * (1 - t) for a, b in zip(ls, ls[1:]))
    falling = all(b < a * (1 + t) for a, b in zip(hs, hs[1:])) and all(b < a * (1 + t) for a, b in zip(ls, ls[1:]))
    if rising and hs[2] > hs[0] and ls[2] > ls[0]:
        kind = "up"
    elif falling and hs[2] < hs[0] and ls[2] < ls[0]:
        kind = "down"
    elif max(hs) / min(hs) - 1 < RANGE_WIDTH[frame] and max(ls) / min(ls) - 1 < RANGE_WIDTH[frame]:
        kind = "range"
    else:
        kind = "turn"
    breakout = kind == "range" and bool(c.iloc[-1] > np.max(h))
    return kind, breakout


def build(info, frames, log=print):
    """業績タブのデータ。frames は {"d": 日足, "w": 週足}（取引の終わった足だけの表）。

    返すのは {"days": 見た日数, "stocks": [{code, name, sector, date, title, score, rate, shape: {d, w}, brk: {d, w}}]}。
    株探が読めないときは stocks が空。
    """
    ranked = load(log)
    stocks = []
    for item in ranked:
        symbol = f"{item['code']}.T"
        if symbol not in info.index:
            continue
        shapes, breaks = {}, {}
        for frame, bars in frames.items():
            if symbol not in bars["close"].columns:
                continue
            kind, brk = shape(bars["high"][symbol], bars["low"][symbol], bars["close"][symbol], frame)
            if kind:
                shapes[frame], breaks[frame] = kind, brk
        row = info.loc[symbol]
        stocks.append({**item, "name": row["name"] or item["code"],
                       "sector": "" if pd.isna(row["sector"]) else row["sector"], "shape": shapes, "brk": breaks})
    log(f"業績タブ: {len(stocks):,} 銘柄（点数 {MIN_SCORE} 以上・直近 {DAYS} 日）")
    return {"days": DAYS, "stocks": stocks}


def load(log=print):
    """earnings.json の、直近 DAYS 日の業績の強い銘柄（強い順）。無い・読めないときは空"""
    try:
        saved = json.loads(SAVED.read_text(encoding="utf-8"))
    except Exception as error:
        log(f"earnings.json が読めません（{error}）。業績タブは空にします")
        return []
    since = pd.Timestamp.now("Asia/Tokyo") - pd.Timedelta(days=DAYS)
    stocks = [s for s in saved.get("stocks", []) if pd.Timestamp(s["at"]) >= since]
    log(f"earnings.json（{saved.get('updated', '?')} に株探を読んだもの）: {len(stocks):,} 銘柄")
    return stocks


def update(log=print):
    """株探の決算速報を読み、earnings.json を新しくする。前回の続きだけ読み、直近 DAYS 日より古い見出しは捨てる"""
    try:
        saved = json.loads(SAVED.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    since = pd.Timestamp.now("Asia/Tokyo") - pd.Timedelta(days=DAYS)
    kept = [s for s in saved.get("stocks", []) if pd.Timestamp(s["at"]) >= since]
    log("株探の決算速報を読んでいます...")
    new = headlines(log, known=saved.get("seen", []))
    if not new and not saved:
        raise SystemExit("株探の決算速報が1件も読めませんでした（earnings.json はそのまま）")
    # 新しい見出しと、前に残した強い見出しを合わせて、銘柄ごとにいちばん良いものを残す
    ranked = strong(new + kept)
    seen = [item["id"] for item in new[:50]] or saved.get("seen", [])
    out = {"updated": f"{pd.Timestamp.now('Asia/Tokyo'):%Y-%m-%d %H:%M}", "days": DAYS, "seen": seen, "stocks": ranked}
    SAVED.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    log(f"earnings.json を書きました: 新しい見出し {len(new):,} 件、業績の強い銘柄 {len(ranked):,}")


if __name__ == "__main__":
    argparse.ArgumentParser(description="株探の決算速報を読み、earnings.json を新しくする（GitHub Actions からは読めない）").parse_args()
    update()
