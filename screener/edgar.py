"""SEC EDGAR（米国証券取引委員会の開示システム）から財務データを取る。

会社ごとの companyfacts API で年次（10-K）と四半期（10-Q）の数字を取り、
直近12か月（TTM）の値を計算する。

初回は全社分を取る（約5,900社・10分前後）。2回目以降は EDGAR の日次インデックスを見て、
前回取得した後に決算書類を出した会社だけを取り直す。companyfacts API に提出済みの決算が
まだ載っていない会社は、提出書類の XBRL を直接読んで補う。
"""
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from .config import CACHE_DIR, SEC_USER_AGENT

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{q}/master.{d:%Y%m%d}.idx"
ARCHIVES_URL = "https://www.sec.gov/Archives/"
XBRLI = "{http://www.xbrl.org/2003/instance}"
FACTS_DIR = CACHE_DIR / "facts"
STATE_PATH = FACTS_DIR / "_state.json"

# 期間の値（損益・キャッシュフロー）。同じ意味でも会社によってタグが違うので、優先順に探す
DURATION_TAGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireOilAndGasPropertyAndEquipment",
        "PaymentsToAcquireOilAndGasProperty",
        "PaymentsForConstructionInProcess",
    ],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
}
# 時点の値（貸借対照表）
INSTANT_TAGS = {
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
}
ALL_TAGS = [tag for tags in [*DURATION_TAGS.values(), *INSTANT_TAGS.values()] for tag in tags]
# 財務諸表が載る書類。年間値は年次報告書からだけ取る（10-Q に載る「直近12か月」の値を除くため）
REPORT_FORMS = {"10-K", "10-KT", "10-Q", "10-QT", "20-F", "40-F"}
ANNUAL_FORMS = {"10-K", "10-KT", "20-F", "40-F"}
# 保存する期間。TTM とその前年比の計算には3年分あれば足りる
HISTORY_DAYS = 365 * 4
# 初回に遡って読む日次インデックスの日数。API の反映漏れを最初から見つけるため
LOOKBACK_DAYS = 120

# 持株会社化などで CIK が変わった会社。新しい CIK には過去の決算が無いので、旧会社の数字と合わせて使う
PREDECESSORS = {
    2115436: [34088],  # XOM: 2026年7月に Exxon Mobil Corp から ExxonMobil Holdings Corp へ移行
}

_local = threading.local()
_lock = threading.Lock()
_next_slot = 0.0


def _get(url, missing=(404,)):
    """SEC の上限（10 req/s）を超えないよう間隔を空けて GET する。missing のステータスなら None"""
    global _next_slot
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["User-Agent"] = SEC_USER_AGENT
    for attempt in range(4):
        with _lock:
            now = time.monotonic()
            slot = max(_next_slot, now)
            _next_slot = slot + 0.11
        time.sleep(slot - now)
        res = _local.session.get(url, timeout=60)
        if res.status_code in missing:
            return None
        if res.status_code != 429 and res.status_code < 500:
            break
        time.sleep(2**attempt)
    res.raise_for_status()
    return res


def tickers():
    """SEC 登録企業の CIK（会社ID）とティッカーの対応表。1週間キャッシュする"""
    path = CACHE_DIR / "company_tickers.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_get(TICKERS_URL).text, encoding="utf-8")
    data = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(data["data"], columns=data["fields"])


# --- 取得と差分更新 ---


def _fetch_facts(cik, since):
    """1社分の companyfacts から、必要なタグ・期間だけ抜き出して保存する"""
    res = _get(FACTS_URL.format(cik=cik))
    gaap = res.json().get("facts", {}).get("us-gaap", {}) if res else {}
    out = {}
    for tag in ALL_TAGS:
        rows = [
            [f.get("start"), f["end"], f["val"], f["form"].removesuffix("/A"), f["filed"]]
            for f in gaap.get(tag, {}).get("units", {}).get("USD", [])
            if f["form"].removesuffix("/A") in REPORT_FORMS and f["end"] >= since
        ]
        if rows:
            out[tag] = rows
    (FACTS_DIR / f"{cik}.json").write_text(json.dumps(out), encoding="utf-8")


def _try_fetch(cik, since):
    """失敗したら古いファイルも消しておき、次回の更新で「未取得」として取り直させる"""
    try:
        _fetch_facts(cik, since)
        return True
    except (requests.RequestException, ValueError):
        (FACTS_DIR / f"{cik}.json").unlink(missing_ok=True)
        return False


def _us_today():
    # EDGAR の日付は米国東部時間。tzdata を入れずに済むよう UTC-5 で近似する
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def _scan_indices(checked_through):
    """checked_through の翌日以降の日次インデックスから、決算書類の提出を拾う。

    filed: 会社ごとの最新の提出日（訂正版を含む）。数字を取り直すかの判定に使う
    latest: 会社ごとの最新の書類（訂正版を除く）の [提出日, パス, 種類]。API の反映漏れの判定に使う
    """
    filed, latest = {}, {}
    day = checked_through + timedelta(days=1)
    while day <= _us_today():
        res = _get(INDEX_URL.format(d=day, q=(day.month - 1) // 3 + 1), missing=(403, 404))
        # 土日・祝日はファイルが無い。直近の平日は未公開なだけなので、そこで止めて次回に回す
        if res is None and day.weekday() < 5 and day > _us_today() - timedelta(days=4):
            break
        for line in res.text.splitlines() if res else []:
            parts = line.split("|")
            if len(parts) != 5 or not parts[0].isdigit() or parts[2].removesuffix("/A") not in REPORT_FORMS:
                continue
            cik, form, path = int(parts[0]), parts[2], parts[4]
            filed[cik] = f"{parts[3][:4]}-{parts[3][4:6]}-{parts[3][6:]}"
            if form in REPORT_FORMS:
                latest[cik] = [filed[cik], path, form]
        checked_through = day
        day += timedelta(days=1)
    return filed, latest, checked_through


def _load_state():
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = {"checked_through": (_us_today() - timedelta(days=LOOKBACK_DAYS)).isoformat()}
    state.setdefault("latest", {})
    state.setdefault("patched", {})
    return state


def _save_state(state):
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _stale(cik, filed_on, refetch_before):
    """未取得、refetch_before より前に取得、または前回取得した日以降に決算書類が出ていれば True"""
    path = FACTS_DIR / f"{cik}.json"
    if not path.exists() or path.stat().st_mtime < refetch_before:
        return True
    fetched_on = (datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) - timedelta(hours=5)).date()
    return filed_on is not None and filed_on >= fetched_on.isoformat()


def _missing(cik, filed_on):
    """filed_on に出た決算の数字が保存データに無ければ True（数字が1つも無い IFRS の会社などは対象外）"""
    path = FACTS_DIR / f"{cik}.json"
    if not path.exists():
        return False
    dates = [row[4] for rows in json.loads(path.read_text(encoding="utf-8")).values() for row in rows]
    return bool(dates) and max(dates) < filed_on


def _parse_instance(xml, form, filed, since):
    """XBRL インスタンスから、必要なタグの USD の値を companyfacts と同じ形で取り出す"""
    root = ET.fromstring(xml)
    periods = {}
    for ctx in root.iter(XBRLI + "context"):
        # 事業セグメント別などの内訳（segment / scenario 付き）は使わない
        if ctx.find(f"{XBRLI}entity/{XBRLI}segment") is not None or ctx.find(XBRLI + "scenario") is not None:
            continue
        period = ctx.find(XBRLI + "period")
        if (instant := period.find(XBRLI + "instant")) is not None:
            periods[ctx.get("id")] = (None, instant.text.strip())
        else:
            start, end = period.find(XBRLI + "startDate"), period.find(XBRLI + "endDate")
            periods[ctx.get("id")] = (start.text.strip(), end.text.strip())
    usd = {
        unit.get("id") for unit in root.iter(XBRLI + "unit")
        if (measure := unit.find(XBRLI + "measure")) is not None and measure.text.strip().endswith(":USD")
    }

    out = {}
    for el in root:
        ns, _, tag = el.tag.partition("}")
        if not ns.startswith("{http://fasb.org/us-gaap/") or tag not in ALL_TAGS:
            continue
        period = periods.get(el.get("contextRef"))
        if period is None or el.get("unitRef") not in usd or not (el.text or "").strip() or period[1] < since:
            continue
        row = [period[0], period[1], float(el.text), form, filed]
        if row not in out.setdefault(tag, []):
            out[tag].append(row)
    return out


def _patch_from_filing(cik, filed, path, form, since):
    """companyfacts API に載っていない決算を、提出書類の XBRL インスタンスから直接読んで足す。

    数字を足せたら True、書類に該当する数字が無ければ False、通信などの失敗は None（次回やり直す）
    """
    folder = ARCHIVES_URL + path.removesuffix(".txt").replace("-", "") + "/"
    try:
        res = _get(folder + "index.json")
        names = [item["name"] for item in res.json()["directory"]["item"]] if res else []
        # インライン XBRL から抽出されたインスタンス（*_htm.xml）を優先し、無ければ旧形式の *.xml
        instance = next((n for n in names if n.endswith("_htm.xml")), None) or next(
            (n for n in names if n.endswith(".xml") and not re.search(r"_(cal|def|lab|pre)\.xml$|FilingSummary", n)),
            None,
        )
        res = _get(folder + instance) if instance else None
        rows = _parse_instance(res.content, form, filed, since) if res else {}
    except (requests.RequestException, ValueError, KeyError, ET.ParseError):
        return None
    if rows:
        facts_path = FACTS_DIR / f"{cik}.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        for tag, new in rows.items():
            facts.setdefault(tag, []).extend(new)
        facts_path.write_text(json.dumps(facts), encoding="utf-8")
    return bool(rows)


def update_facts(ciks, log=print):
    """未取得の会社と、前回取得した後に決算書類を出した会社の数字を取り直す"""
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    filed, latest, checked = _scan_indices(date.fromisoformat(state["checked_through"]))
    state["latest"].update({str(c): v for c, v in latest.items()})
    wanted = set(ciks) | {p for c in ciks for p in PREDECESSORS.get(c, [])}
    since = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()

    # 取得する科目を変えたら、その時刻より前に取った保存データは新しい科目が無いので取り直す。
    # 時刻で判定するので、途中で止まっても次回は続きから再開できる
    if state.get("tags") != ALL_TAGS:
        state["tags"], state["refetch_before"] = ALL_TAGS, time.time()
        _save_state(state)
    todo = sorted(c for c in wanted if _stale(c, filed.get(c), state.get("refetch_before", 0)))
    if todo:
        note = "（全社分なので10分前後かかります）" if len(todo) > 1000 else ""
        log(f"  {len(todo):,} 社の財務を取得します{note}")
        failed = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i, ok in enumerate(pool.map(lambda c: _try_fetch(c, since), todo), 1):
                failed += not ok
                if i % 500 == 0:
                    log(f"  {i:,} / {len(todo):,} 社")
        if failed:
            log(f"  {failed:,} 社は取得に失敗しました（次回の更新で取り直します）")
        # 取り直すと、提出書類から補った数字も消えるので補完をやり直す
        for c in todo:
            state["patched"].pop(str(c), None)

    # API にまだ載っていない決算は、提出書類から直接読んで補う（1つの決算につき1回だけ試す）
    gaps = [
        (c, state["latest"][str(c)]) for c in sorted(wanted)
        if str(c) in state["latest"]
        and state["patched"].get(str(c)) != state["latest"][str(c)][0]
        and _missing(c, state["latest"][str(c)][0])
    ]
    if gaps:
        log(f"  SEC の API に未反映の決算が {len(gaps):,} 社分あるので、提出書類から直接読みます")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda g: _patch_from_filing(g[0], *g[1], since), gaps))
        for (cik, (filed_on, *_)), result in zip(gaps, results):
            if result is not None:
                state["patched"][str(cik)] = filed_on
        log(f"  補完できた会社: {sum(r is True for r in results):,} 社")

    state["checked_through"] = checked.isoformat()
    _save_state(state)


# --- 直近12か月（TTM）の計算 ---


@dataclass(frozen=True, slots=True)
class Fact:
    start: date | None  # 時点の値（貸借対照表）は None
    end: date
    val: float
    form: str

    @property
    def days(self):
        return (self.end - self.start).days

    @property
    def annual(self):
        return self.form in ANNUAL_FORMS and 340 <= self.days <= 380


def _pool(facts, tags):
    """候補タグの値を1つにまとめる。同じ期間の値は、優先順が高いタグ・新しい提出のものを採用する"""
    rows = [(priority, row) for priority, tag in enumerate(tags) for row in facts.get(tag, [])]
    rows.sort(key=lambda x: x[1][4], reverse=True)
    rows.sort(key=lambda x: x[0])
    seen, pool = set(), []
    for _, (start, end, val, form, _filed) in rows:
        if (start, end) not in seen:
            seen.add((start, end))
            pool.append(Fact(start and date.fromisoformat(start), date.fromisoformat(end), val, form))
    return pool


def _near(facts, end, start=None, tol=10):
    """期末（と期首）が tol 日以内で一致する値のうち、最も近いもの。52/53週決算のずれを吸収する"""
    hits = [
        f for f in facts
        if abs((f.end - end).days) <= tol and (start is None or abs((f.start - start).days) <= tol)
    ]
    return min(hits, key=lambda f: abs((f.end - end).days) + (abs((f.start - start).days) if start else 0), default=None)


def _span(pool, start, end):
    """start に始まり end に終わる期間の値。累計の数字が無い会社は、3か月ごとの数字をつないで求める"""
    if hit := _near(pool, end, start=start):
        return hit.val
    quarters = [f for f in pool if 80 <= f.days <= 100]
    total, cursor = 0.0, start
    while q := min((f for f in quarters if abs((f.start - cursor).days) <= 10),
                   key=lambda f: abs((f.start - cursor).days), default=None):
        if q.end > end + timedelta(days=10):
            break
        total += q.val
        if abs((q.end - end).days) <= 10:
            return total
        cursor = q.end + timedelta(days=1)
    return None


def _ttm(pool, as_of):
    """as_of に終わる直近12か月の値。

    年次決算の期末ならその年間値。四半期末なら「前期の年間値 + 今期の累計 − 前年同期の累計」。
    """
    annual = [f for f in pool if f.annual]
    if hit := _near(annual, as_of):
        return hit.val
    base = max((f for f in annual if f.end < as_of - timedelta(days=10)), key=lambda f: f.end, default=None)
    if base is None:
        return None
    ytd = _span(pool, base.end + timedelta(days=1), as_of)
    prev = _span(pool, base.start, as_of - timedelta(days=364))
    if ytd is None or prev is None:
        return None
    return base.val + ytd - prev


def _quarter(pool, as_of):
    """as_of に終わる3か月の値。第4四半期は 10-K に単独で載らないので「年間値 − 9か月累計」で求める"""
    if q := _near([f for f in pool if 80 <= f.days <= 100], as_of):
        return q.val
    if a := _near([f for f in pool if f.annual], as_of):
        nine = _near(pool, a.end - timedelta(days=91), start=a.start)
        if nine and 250 <= nine.days <= 290:
            return a.val - nine.val
    return None


def _summarize(facts):
    pools = {field: [f for f in _pool(facts, tags) if f.start] for field, tags in DURATION_TAGS.items()}
    # 純利益の TTM が計算できる最新の期末を、その会社の「最新決算」とする
    as_of = next((end for end in sorted({f.end for f in pools["net_income"]}, reverse=True)
                  if _ttm(pools["net_income"], end) is not None), None)
    if as_of is None:
        # 上場直後で年次決算がまだ無い会社は、最新の四半期末を基準にして四半期の数字だけ出す
        quarters = [f.end for f in pools["revenue"] + pools["net_income"] if 80 <= f.days <= 100]
        as_of = max(quarters, default=None)
    if as_of is None:
        return None

    year_ago = as_of - timedelta(days=364)
    row = {"as_of": as_of.isoformat()}
    row.update({field: _ttm(pool, as_of) for field, pool in pools.items()})
    row["revenue_prev"] = _ttm(pools["revenue"], year_ago)
    row["net_income_prev"] = _ttm(pools["net_income"], year_ago)
    row["revenue_q"] = _quarter(pools["revenue"], as_of)
    row["revenue_q_prev"] = _quarter(pools["revenue"], year_ago)
    row["operating_income_q"] = _quarter(pools["operating_income"], as_of)
    for field, tags in INSTANT_TAGS.items():
        pool = [f for f in _pool(facts, tags) if not f.start]
        row[field] = max(pool, key=lambda f: f.end).val if pool else None
    return row


def _load_facts(cik):
    """保存済みの数字を読む。前身の会社があれば、その数字も合わせる"""
    merged = {}
    for c in [cik, *PREDECESSORS.get(cik, [])]:
        path = FACTS_DIR / f"{c}.json"
        if path.exists():
            for tag, rows in json.loads(path.read_text(encoding="utf-8")).items():
                merged.setdefault(tag, []).extend(rows)
    return merged


def fundamentals(ciks):
    """各社の直近12か月（TTM）の損益・CF と、最新の貸借対照表を cik 単位で返す"""
    rows = {}
    for cik in ciks:
        if row := _summarize(_load_facts(cik)):
            rows[cik] = row
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "cik"
    # 値が1つも無い列が object 型になって割り算で落ちないよう、数値型にそろえる
    return df.astype({col: "float64" for col in df.columns if col != "as_of"})
