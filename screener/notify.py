"""週足の抜け（13週線が26週線を上に抜けた・下に抜けた）を、週に1回 LINE に知らせる。標準ライブラリだけで動く。

作ったページ（site/index.html）に入っているデータを読むので、株価は取り直さない。
日本株と米国株の両方で同じ週の足が終わっていて、その週をまだ知らせていないときだけ送る。
知らせた週は、ページと同じ場所の notified.json に残す（gh-pages に載るので、次の更新でも分かる）。
だから更新のたびに動かしてよく、送るのは週に1回（ふつうは土曜の朝、米国株の更新のとき）になる。

    python -m screener.notify --page site/index.html             送る（LINE_TOKEN が無ければ何もしない）
    python -m screener.notify --page site/index.html --dry-run   送らずに、文面だけ出す
    python -m screener.notify --page site/index.html --force     もう知らせた週でも送る（試し用）

LINE_TOKEN は、LINE 公式アカウントのチャネルアクセストークン。友だち全員に送る（自分だけが友だちなら自分だけに届く）。
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

from . import published

LINE_URL = "https://api.line.me/v2/bot/message/broadcast"
# LINE は1通に5つまで、1つ5,000字まで。長い週は分けて送る
MAX_CHARS = 4500
MAX_PARTS = 5
MARKETS = [("jp", "日本"), ("us", "米国")]
SIDES = [("up", 1, "上抜け"), ("down", -1, "下抜け")]


def _label(market, code, name):
    """文面に出す呼び名。日本株は「コード 名前」、米国株はコードだけ（名前が長いので）"""
    return f"{code} {name}" if market == "jp" and name else code


def _own(data, site):
    """持ち株と注目のうち、直近の週足で抜けた銘柄。一覧の順位に入らない銘柄も出す。{向き: [呼び名, ...]}"""
    found = {1: [], -1: []}
    for market, _ in MARKETS:
        names = {row[1]: row[2] for row in data[market]["top"]}
        shared = data[market]["shared"]
        for code in dict.fromkeys([*shared["hold"], *(fav["code"] for fav in shared["fav"])]):
            cross = data[market]["cross"].get(code, {}).get("w")
            if not cross or cross[1] != 0:
                continue
            name = names.get(code)
            file = site / "s" / market / f"{code}.json"
            if name is None and file.exists():
                name = json.loads(file.read_text(encoding="utf-8")).get("name")
            found[cross[0]].append(_label(market, code, name))
    return found


def message(data, site):
    """知らせる文面。行ごとの並び（長いときに行の切れ目で分けられるように）"""
    week = data["jp"]["frames"]["w"]["bar"]
    lines = [f"週足の抜け（{week}）", "13週線が26週線を抜けた銘柄"]
    own = _own(data, site)
    if own[1] or own[-1]:
        lines.append("")
        lines.append("★持ち株・注目")
        for _, direction, label in SIDES:
            if own[direction]:
                lines.append(f"{label}: " + "、".join(own[direction]))
    for market, market_label in MARKETS:
        frame = data[market]["frames"]["w"]
        for side, _, label in SIDES:
            stocks = frame[side]["stocks"]
            lines.append("")
            lines.append(f"{market_label} {label} {len(stocks)}銘柄")
            labels = [_label(market, s["code"], s["name"]) for s in stocks]
            # 日本株は1行に1銘柄、米国株はコードだけなので1行に並べる
            lines.extend(labels if market == "jp" else [" ".join(labels)] if labels else [])
    lines.append("")
    lines.append(published.PAGE_URL)
    return lines


def split(lines):
    """行の切れ目で、MAX_CHARS 字までのかたまりに分ける。MAX_PARTS を超えるぶんは切り、最後にその旨を足す"""
    parts, current = [], ""
    for line in lines:
        if current and len(current) + 1 + len(line) > MAX_CHARS:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    parts.append(current)
    if len(parts) > MAX_PARTS:
        parts = parts[:MAX_PARTS]
        parts[-1] += f"\n（長いので、続きはページで）\n{published.PAGE_URL}"
    return [part.strip("\n") for part in parts]


def send(token, parts):
    body = json.dumps({"messages": [{"type": "text", "text": part} for part in parts]}).encode("utf-8")
    request = urllib.request.Request(LINE_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as res:
        res.read()


def main():
    parser = argparse.ArgumentParser(description="週足の抜けを LINE に知らせる")
    parser.add_argument("--page", type=Path, required=True, help="作ったページ（HTML）のパス")
    parser.add_argument("--dry-run", action="store_true", help="送らずに、文面だけ出す")
    parser.add_argument("--force", action="store_true", help="もう知らせた週でも、日米の週がそろっていなくても送る")
    args = parser.parse_args()
    site = args.page.parent
    marker = site / "notified.json"

    data = json.loads(published.extract(args.page.read_text(encoding="utf-8")))
    weeks = {market: data[market]["frames"]["w"]["bar"] for market, _ in MARKETS}
    done = json.loads(marker.read_text(encoding="utf-8")).get("week") if marker.exists() else None
    parts = split(message(data, site))
    if args.dry_run:
        print("\n----------\n".join(parts))
        return
    if not args.force:
        if weeks["jp"] != weeks["us"]:
            print(f"日本株（{weeks['jp']}）と米国株（{weeks['us']}）で終わった週が違うので、まだ知らせません")
            return
        if weeks["jp"] == done:
            print(f"{done} はもう知らせました")
            return
    token = os.environ.get("LINE_TOKEN")
    if not token:
        print("LINE_TOKEN が無いので、知らせません")
        return
    send(token, parts)
    marker.write_text(json.dumps({"week": weeks["jp"]}, ensure_ascii=False), encoding="utf-8")
    print(f"{weeks['jp']} を LINE に知らせました（{len(parts)} 通ぶん、{sum(map(len, parts)):,} 字）")


if __name__ == "__main__":
    main()
