"""Worst-case tick: 20 fresh triggers for 20 different merchants in ONE /v1/tick call.
Run against a bot that already has the dataset loaded (e.g. right after tools/local_eval.py).

    py tools/burst_tick.py [--bot http://127.0.0.1:8080]
"""

import argparse
import json
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data" / "expanded"


def post(bot, path, body, timeout=60):
    req = request.Request(bot + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


ap = argparse.ArgumentParser()
ap.add_argument("--bot", default="http://127.0.0.1:8080")
bot = ap.parse_args().bot.rstrip("/")
stamp = int(time.time())
seen, ids = set(), []
for p in sorted((D / "triggers").glob("*.json")):
    t = json.loads(p.read_text(encoding="utf-8"))
    if t["merchant_id"] in seen or t.get("customer_id"):
        continue
    seen.add(t["merchant_id"])
    t = {**t, "id": f"{t['id']}_burst{stamp}", "suppression_key": f"{t['suppression_key']}:burst{stamp}"}
    post(bot, "/v1/context", {"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t,
                              "delivered_at": "2026-04-26T12:00:00Z"})
    ids.append(t["id"])
    if len(ids) == 20:
        break
t0 = time.time()
res = post(bot, "/v1/tick", {"now": "2026-04-26T12:00:00Z", "available_triggers": ids})
dt = time.time() - t0
acts = res.get("actions", [])
print(f"{len(ids)} triggers -> {len(acts)} actions in {dt:.1f}s (judge limit 30s)")
required = ["conversation_id", "merchant_id", "send_as", "trigger_id", "template_name", "template_params",
            "body", "cta", "suppression_key", "rationale"]
bad = [a.get("trigger_id") for a in acts if any(k not in a or a[k] in (None, "") for k in required)]
print("all actions have every required field" if not bad else f"MISSING FIELDS in {bad}")
