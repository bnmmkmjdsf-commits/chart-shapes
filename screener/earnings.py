"""株探の決算速報（https://kabutan.jp/news/）から、業績の良い日本株と、業績が悪いのに株価が上がっている日本株を拾う。

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


# 業績の悪い見出し（減益・下方修正・赤字・減額・減配）。業績が悪いのに株価が上がっている銘柄を探すのに使う
WEAK = re.compile(r"減益|下方修正|赤字|減額|減配|未定に")
# 「赤字縮小」「黒字浮上」など、良くなった決算は出尽くしから外す
NOT_WEAK = re.compile(r"縮小|黒字浮上|黒字転換|増益|上方修正|増配")
# 決算の発表からこれだけ上がった銘柄だけ、好決算・出尽くしに載せる（%）。相場も好決算と見た銘柄に絞る
MIN_MOVE = 5.0


def weak(items):
    """業績の悪い見出しを、銘柄ごとにいちばん新しいものだけ残す [{id, code, at, date, title}]。同じ銘柄に、それより新しい強い見出しがあれば外す"""
    latest, strong_at = {}, {}
    for item in items:
        main = item["title"].split("・")[0]
        if score(item["title"]) is None and WEAK.search(main) and not NOT_WEAK.search(main):
            if item["code"] not in latest or item["at"] > latest[item["code"]]["at"]:
                latest[item["code"]] = item
        else:
            result = score(item["title"])
            if result and result[0] >= MIN_SCORE:
                strong_at[item["code"]] = max(strong_at.get(item["code"], ""), item["at"])
    return [{k: v[k] for k in ("id", "code", "at", "date", "title")} for v in latest.values()
            if v["at"] > strong_at.get(v["code"], "")]


def move_since(at, close):
    """決算を出した日からの株価の動き（%）。close は日足の終値（行が日付）。

    15時より前に出た決算は、その日のうちに株価が動くので、前の営業日の終値から見る。
    15時以降に出た決算は、その日の終値から見る（動くのは次の営業日から）。
    """
    close = close.dropna()
    when = pd.Timestamp(at)
    day = pd.Timestamp(when.date())
    before = close[close.index < day] if when.hour < 15 else close[close.index <= day]
    if before.empty or close.index[-1] <= before.index[-1]:
        return None
    return round(float((close.iloc[-1] / before.iloc[-1] - 1) * 100), 1)


def build(info, frames, log=print):
    """業績タブのデータ。frames は {"d": 日足}（取引の終わった足だけの表）。発表からの株価の動きに使う。

    返すのは {"days": 見た日数, "stocks": [{code, name, sector, date, title, score, rate, move}], "weak": [...]}。
    株探が読めないときは stocks が空。
    """
    ranked = load(log)
    stocks = []
    for item in ranked:
        symbol = f"{item['code']}.T"
        if symbol not in info.index:
            continue
        row = info.loc[symbol]
        move = move_since(item["at"], frames["d"]["close"][symbol]) if "d" in frames and symbol in frames["d"]["close"].columns else None
        if move is None or move < MIN_MOVE:
            continue
        stocks.append({**item, "move": move, "name": row["name"] or item["code"],
                       "sector": "" if pd.isna(row["sector"]) else row["sector"]})
    log(f"業績タブ: {len(stocks):,} 銘柄（点数 {MIN_SCORE} 以上・直近 {DAYS} 日・発表から +{MIN_MOVE:g}% 以上）")
    # 業績が悪いのに、決算の発表から株価が上がっている銘柄（上がった順）
    weak_stocks = []
    for item in load_weak(log):
        symbol = f"{item['code']}.T"
        if symbol not in info.index or "d" not in frames or symbol not in frames["d"]["close"].columns:
            continue
        move = move_since(item["at"], frames["d"]["close"][symbol])
        if move is None or move < MIN_MOVE:
            continue
        row = info.loc[symbol]
        weak_stocks.append({**item, "move": move, "name": row["name"] or item["code"],
                            "sector": "" if pd.isna(row["sector"]) else row["sector"]})
    weak_stocks.sort(key=lambda s: s["move"], reverse=True)
    log(f"出尽くし: {len(weak_stocks):,} 銘柄（発表から +{MIN_MOVE:g}% 以上）")
    return {"days": DAYS, "stocks": stocks, "weak": weak_stocks}


# 米国株は、Yahoo Finance の決算の予定表（1株利益の予想と実績）から拾う。株探と違い GitHub Actions からも読める
# 好決算: 黒字で、1株利益が予想を US_MIN_SURPRISE % 以上上回った。出尽くし: 予想を US_MIN_SURPRISE % 以上下回った（さらに株価が上がっている）
US_MIN_SURPRISE = 10.0
# 予想がこれより小さい（0に近い）と、少しの差でも何百%にもなるので外す（ドル）
US_MIN_ESTIMATE = 0.10
US_MIN_MARKET_CAP = 3e8


# 決算の予定表が読めないとき（Yahoo の「Invalid Crumb」など）に、読み直す回数と間（秒）
US_TRIES = 4
US_RETRY_WAIT = 15


def _reset_yahoo():
    """Yahoo に入るための合言葉（cookie と crumb）を捨てて、次に読むときに取り直させる"""
    try:
        from yfinance.data import YfData

        data = YfData()
        data._cookie = None
        data._crumb = None
    except Exception:
        pass


def us_reports(log=print, days=DAYS):
    """直近 days 日に決算を出した米国株の、1株利益の予想と実績（銘柄ごとに一番新しい決算）。

    1ページ目が何度読み直しても読めないときは、例外を出す（呼ぶ側が前回のデータを使う）
    """
    import yfinance as yf

    end = pd.Timestamp.now("America/New_York").normalize() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=days + 1)
    calendar = yf.Calendars(start=f"{start:%Y-%m-%d}", end=f"{end:%Y-%m-%d}")
    parts = []
    for page in range(100):  # 1回に100社まで。時価総額の大きい順に読んでいく
        part = error = None
        for attempt in range(US_TRIES):
            try:
                part = calendar.get_earnings_calendar(market_cap=US_MIN_MARKET_CAP, filter_most_active=False,
                                                      limit=100, offset=page * 100, force=True)
                error = None
                break
            except Exception as e:
                error = e
                log(f"  米国株の決算の予定表の {page + 1} ページ目が読めません（{e}）。"
                    + ("読み直します" if attempt + 1 < US_TRIES else "あきらめます"))
                if attempt + 1 < US_TRIES:
                    _reset_yahoo()
                    time.sleep(US_RETRY_WAIT)
        if error is not None:
            if not parts:
                raise RuntimeError(f"米国株の決算の予定表が読めません（{error}）")
            break
        if part is None or part.empty:
            break
        parts.append(part.reset_index())
        if len(part) < 100:
            break
        time.sleep(0.5)
    if not parts:
        return pd.DataFrame()
    table = pd.concat(parts, ignore_index=True).dropna(subset=["Reported EPS", "EPS Estimate"])
    when = pd.to_datetime(table["Event Start Date"], utc=True).dt.tz_convert("America/New_York")
    table = table.assign(when=when).sort_values("when", ascending=False).drop_duplicates("Symbol")
    log(f"米国株の決算: {len(table):,} 社（直近 {days} 日・時価総額 {US_MIN_MARKET_CAP / 1e8:g}億ドル以上）")
    return table


def us_move_since(when, timing, close):
    """米国株の、決算を出した日からの株価の動き（%）。引け後（AMC）の決算はその日の終値から、ほかは前の営業日の終値から見る"""
    close = close.dropna()
    day = pd.Timestamp(when.date())
    before = close[close.index <= day] if timing == "AMC" else close[close.index < day]
    if before.empty or close.index[-1] <= before.index[-1]:
        return None
    return round(float((close.iloc[-1] / before.iloc[-1] - 1) * 100), 1)


def build_us(info, close, log=print):
    """米国株の業績タブのデータ。close は日足の終値の表。形は build と同じ {"days", "stocks", "weak"}"""
    stocks, weak_stocks = [], []
    for _, r in us_reports(log).iterrows():
        symbol, est, actual, surprise = r["Symbol"], r["EPS Estimate"], r["Reported EPS"], r.get("Surprise(%)")
        if symbol not in info.index or abs(est) < US_MIN_ESTIMATE:
            continue
        if pd.isna(surprise):
            surprise = (actual / est - 1) * 100 * (1 if est > 0 else -1)
        good = actual > 0 and surprise >= US_MIN_SURPRISE
        bad = surprise <= -US_MIN_SURPRISE
        if not (good or bad):
            continue
        move = us_move_since(r["when"], r.get("Timing"), close[symbol]) if symbol in close.columns else None
        if move is None or move < MIN_MOVE:
            continue
        row = info.loc[symbol]
        item = {"code": row["code"], "date": f"{r['when'].month}/{r['when'].day:02d}",
                "title": f"1株利益 {actual:.2f}ドル（予想 {est:.2f}ドルを {abs(surprise):.0f}% {'上回る' if good else '下回る'}）",
                "surprise": round(float(surprise), 1), "move": move,
                "name": row["name"] or row["code"], "sector": "" if pd.isna(row["sector"]) else row["sector"]}
        (stocks if good else weak_stocks).append(item)
    stocks.sort(key=lambda s: s["surprise"], reverse=True)
    weak_stocks.sort(key=lambda s: s["move"], reverse=True)
    log(f"米国株の業績タブ: 好決算 {len(stocks):,} 銘柄、出尽くし {len(weak_stocks):,} 銘柄")
    return {"days": DAYS, "stocks": stocks, "weak": weak_stocks}


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


def load_weak(log=print):
    """earnings.json の、直近 DAYS 日の業績の悪い銘柄。無い・読めないときは空"""
    try:
        saved = json.loads(SAVED.read_text(encoding="utf-8"))
    except Exception:
        return []
    since = pd.Timestamp.now("Asia/Tokyo") - pd.Timedelta(days=DAYS)
    return [s for s in saved.get("weak", []) if pd.Timestamp(s["at"]) >= since]


def update(log=print, full=False):
    """株探の決算速報を読み、earnings.json を新しくする。前回の続きだけ読み、直近 DAYS 日より古い見出しは捨てる"""
    try:
        saved = json.loads(SAVED.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    since = pd.Timestamp.now("Asia/Tokyo") - pd.Timedelta(days=DAYS)
    kept = [s for s in saved.get("stocks", []) if pd.Timestamp(s["at"]) >= since]
    if saved and "weak" not in saved:
        # 業績の悪い見出しを残す前の earnings.json なら、1回だけ全部読み直す
        full = True
        log("業績の悪い見出しがまだ無いので、直近の見出しを全部読み直します")
    log("株探の決算速報を読んでいます...")
    new = headlines(log, known=[] if full else saved.get("seen", []))
    if not new and not saved:
        raise SystemExit("株探の決算速報が1件も読めませんでした（earnings.json はそのまま）")
    # 新しい見出しと、前に残した強い見出しを合わせて、銘柄ごとにいちばん良いものを残す
    kept_weak = [s for s in saved.get("weak", []) if pd.Timestamp(s["at"]) >= since]
    ranked = strong(new + kept)
    weak_items = weak(new + kept_weak + kept)
    seen = [item["id"] for item in new[:50]] or saved.get("seen", [])
    out = {"updated": f"{pd.Timestamp.now('Asia/Tokyo'):%Y-%m-%d %H:%M}", "days": DAYS, "seen": seen, "stocks": ranked, "weak": weak_items}
    SAVED.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    log(f"earnings.json を書きました: 新しい見出し {len(new):,} 件、業績の強い銘柄 {len(ranked):,}、業績の悪い銘柄 {len(weak_items):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="株探の決算速報を読み、earnings.json を新しくする（GitHub Actions からは読めない）")
    parser.add_argument("--full", action="store_true", help="前回の続きではなく、直近 DAYS 日ぶんを全部読み直す")
    update(full=parser.parse_args().full)
