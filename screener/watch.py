"""持ち株と注目の銘柄（watch.json）を読む。標準ライブラリだけで動く。

どの端末で開いても同じ銘柄が出るよう、持ち株と注目はブラウザではなく watch.json に持つ。
{"hold": {"jp": [コード, ...], "us": [...]}, "fav": {"jp": [{"code", "price", "from"}, ...], "us": [...]}}
注目の price と from は、☆を付けた日の株価と日付。
"""
import json
from pathlib import Path

WATCH = Path(__file__).resolve().parent.parent / "watch.json"


def shared(market):
    """ページに持たせる、その市場の持ち株と注目 {"hold": [コード, ...], "fav": [{"code", "price", "from"}, ...]}"""
    watch = json.loads(WATCH.read_text(encoding="utf-8"))
    return {"hold": watch["hold"].get(market, []), "fav": watch["fav"].get(market, [])}
