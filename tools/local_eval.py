"""Local harness: exercises a running bot the way the judge does.

    py tools/local_eval.py                      # against http://localhost:8080
    py tools/local_eval.py --bot https://...    # against a deployed URL

1. Warmup: healthz, metadata, push the full expanded dataset (5 / 50 / 200), check counts.
2. Pushes all 100 triggers, then runs one tick per canonical test pair (30).
3. Replay scenarios: auto-reply hell, intent transition, hostile -> off-topic, customer slot pick.
4. Writes out/submission.jsonl and out/report.md for review.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "expanded"
OUT = ROOT / "out"


def call(bot: str, method: str, path: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(bot + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), time.time() - t0
    except error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.time() - t0


def load(sub: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / sub).glob("*.json"))]


def main() -> None:
    ap = argparse.ArgumentParser()
    # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and adds ~2s per request.
    ap.add_argument("--bot", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    bot = args.bot.rstrip("/")
    OUT.mkdir(exist_ok=True)
    report: list[str] = ["# Local eval report\n"]

    # ---------------- warmup
    s, h, _ = call(bot, "GET", "/v1/healthz")
    print("healthz", s, h)
    s, md, _ = call(bot, "GET", "/v1/metadata")
    print("metadata", s, md.get("team_name"), md.get("model"))
    now = "2026-04-26T10:30:00Z"
    for c in load("categories"):
        call(bot, "POST", "/v1/context", {"scope": "category", "context_id": c["slug"], "version": 1, "payload": c, "delivered_at": now})
    for m in load("merchants"):
        call(bot, "POST", "/v1/context", {"scope": "merchant", "context_id": m["merchant_id"], "version": 1, "payload": m, "delivered_at": now})
    for cu in load("customers"):
        call(bot, "POST", "/v1/context", {"scope": "customer", "context_id": cu["customer_id"], "version": 1, "payload": cu, "delivered_at": now})
    s, dup, _ = call(bot, "POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 1, "payload": {}, "delivered_at": now})
    print("same-version re-push ->", s, dup)
    s, h, _ = call(bot, "GET", "/v1/healthz")
    print("after warmup:", h["contexts_loaded"])
    assert h["contexts_loaded"] == {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}, "warmup counts wrong"

    triggers = {t["id"]: t for t in load("triggers")}
    for t in triggers.values():
        call(bot, "POST", "/v1/context", {"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t, "delivered_at": now})

    # ---------------- 30 canonical pairs
    pairs = json.loads((DATA / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
    lines, slow, conv_by_test = [], 0.0, {}
    report.append("## 30 canonical test pairs\n")
    for p in pairs:
        s, res, dt = call(bot, "POST", "/v1/tick", {"now": now, "available_triggers": [p["trigger_id"]]})
        slow = max(slow, dt)
        acts = res.get("actions", [])
        a = acts[0] if acts else {}
        conv_by_test[p["test_id"]] = a.get("conversation_id")
        lines.append(json.dumps({"test_id": p["test_id"], "body": a.get("body"), "cta": a.get("cta"),
                                 "send_as": a.get("send_as"), "suppression_key": a.get("suppression_key"),
                                 "rationale": a.get("rationale")}, ensure_ascii=False))
        kind = triggers[p["trigger_id"]]["kind"]
        report.append(f"### {p['test_id']} · {kind} · {p['merchant_id']}{' · ' + p['customer_id'] if p.get('customer_id') else ''} ({dt:.1f}s)\n")
        report.append((a.get("body") or "_(no action)_") + "\n")
        report.append(f"*cta={a.get('cta')} · send_as={a.get('send_as')}* — {a.get('rationale')}\n")
        print(f"{p['test_id']} {kind:26} {dt:5.1f}s  {(a.get('body') or '(none)')[:90]!r}")
    (OUT / "submission.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"slowest tick: {slow:.1f}s")

    # ---------------- replays
    report.append("## Replay scenarios\n")

    def replay(name: str, conv: str, mid: str, msgs: list[str], role: str = "merchant", cid: str | None = None):
        report.append(f"### {name}\n")
        print(f"\n--- {name}")
        for i, msg in enumerate(msgs, start=2):
            s, r, dt = call(bot, "POST", "/v1/reply", {"conversation_id": conv, "merchant_id": mid, "customer_id": cid,
                                                       "from_role": role, "message": msg,
                                                       "received_at": now, "turn_number": i})
            line = f"{role}: {msg}\n→ **{r.get('action')}** {r.get('body') or ''} {('(wait ' + str(r.get('wait_seconds')) + 's)') if r.get('wait_seconds') else ''} — _{r.get('rationale')}_\n"
            report.append(line)
            print(f"  [{r.get('action')}] {r.get('body') or r.get('rationale')}  ({dt:.1f}s)")

    # use a fresh tick so conversations carry a real brief
    call(bot, "POST", "/v1/context", {"scope": "trigger", "context_id": "trg_x_digest", "version": 1, "delivered_at": now,
                                      "payload": {**triggers["trg_001_research_digest_dentists"], "id": "trg_x_digest", "suppression_key": "x:1"}})
    s, res, _ = call(bot, "POST", "/v1/tick", {"now": now, "available_triggers": ["trg_x_digest"]})
    conv = res["actions"][0]["conversation_id"] if res.get("actions") else "conv_replay"
    replay("Engaged → commit", conv, "m_001_drmeera_dentist_delhi",
           ["Yes please send the abstract. Also draft the patient WhatsApp.", "Ok, let's do it. What's next?"])
    replay("Auto-reply hell (fresh conv ids each turn, like the simulator)", "conv_auto", "m_002_bharat_dentist_mumbai",
           ["Thank you for contacting Bharat Dental Care! Our team will respond shortly."] * 4)
    replay("Off-topic then hostile", "conv_hostile", "m_009_apollo_pharmacy_jaipur",
           ["Btw can you also help me with my GST filing this month?", "Why are you bothering me. This is useless.",
            "Stop sending these."])
    replay("Hinglish commitment", "conv_hinglish", "m_005_pizzajunction_restaurant_delhi", ["haan theek hai, kar do"])
    replay("Customer slot pick (continues T28's recall conversation)", conv_by_test.get("T28") or "conv_priya",
           "m_001_drmeera_dentist_delhi", ["2"], role="customer", cid="c_001_priya_for_m001")

    (OUT / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\nWrote {OUT / 'submission.jsonl'} and {OUT / 'report.md'}")


if __name__ == "__main__":
    main()
