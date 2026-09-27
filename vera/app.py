"""magicpin Vera challenge bot - HTTP surface.

Run: uvicorn vera.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .composer import compose, template_message
from .facts import build_brief, parse_dt
from .replies import classify, compose_reply, normalize, template_reply
from .store import SCOPES, BotState, ContextStore, Conversation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vera")

app = FastAPI(title="Vera challenge bot", version=config.VERSION)
START = time.time()
store = ContextStore()
state = BotState()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _json(request: Request) -> dict | None:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else None
    except Exception:
        return None


# ------------------------------------------------------------------ health / metadata

# HEAD too: uptime pingers (UptimeRobot) and Render's port probe use HEAD requests.
@app.api_route("/v1/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": store.counts()}


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": config.TEAM_NAME,
        "team_members": config.TEAM_MEMBERS,
        "model": (f"{config.MODEL}{' via Microsoft Foundry' if config.PROVIDER == 'foundry' else ''}"
                  if config.LLM_ENABLED else f"{config.MODEL} (template fallback active: no API key)"),
        "approach": config.APPROACH,
        "contact_email": config.CONTACT_EMAIL,
        "version": config.VERSION,
        "submitted_at": config.SUBMITTED_AT,
    }


# ------------------------------------------------------------------ context

@app.post("/v1/context")
async def push_context(request: Request):
    body = await _json(request)
    if body is None:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_json", "details": "body must be a JSON object"})
    scope, cid, version, payload = body.get("scope"), body.get("context_id"), body.get("version"), body.get("payload")
    if scope not in SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {list(SCOPES)}"})
    if not isinstance(cid, str) or not cid:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_context_id", "details": "context_id required"})
    if isinstance(version, bool) or not isinstance(version, (int, float)):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_version", "details": "version must be an integer"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload", "details": "payload must be an object"})
    accepted, current = store.put(scope, cid, int(version), payload)
    if not accepted:
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": current})
    return {"accepted": True, "ack_id": f"ack_{cid}_v{int(version)}", "stored_at": _now_iso()}


# ------------------------------------------------------------------ tick

def _short(mid: str | None) -> str:
    return (mid or "unknown")[:28]


def _conversation_id(trigger: dict, merchant_id: str, customer_id: str | None) -> str:
    seed = f"{trigger.get('id')}|{trigger.get('suppression_key')}|{customer_id}"
    h = hashlib.sha1(seed.encode()).hexdigest()[:6]
    who = customer_id.split("_for_")[0] if customer_id else _short(merchant_id)
    base = f"conv_{who}_{trigger.get('kind', 'msg')}_{h}"
    cid, n = base, 2
    while cid in state.conversations:
        cid, n = f"{base}_{n}", n + 1
    return cid


def _consent_ok(customer: dict) -> bool:
    prefs = customer.get("preferences") or {}
    consent = customer.get("consent") or {}
    if prefs.get("reminder_opt_in") is False:
        return False
    return bool(consent.get("scope")) and bool(consent.get("opted_in_at"))


def _select(available: list[str], now: datetime) -> list[tuple[dict, dict, dict | None, dict | None]]:
    """Choose which triggers to act on this tick. Restraint: at most N merchant-facing sends per
    merchant per tick (others stay pending for later ticks), one per customer, 20 total."""
    candidates = []
    seen = set()
    for pos, tid in enumerate(available or []):
        if not isinstance(tid, str) or tid in seen:
            continue
        seen.add(tid)
        trg = store.get("trigger", tid)
        if not trg:
            continue
        mid = trg.get("merchant_id") or (trg.get("payload") or {}).get("merchant_id")
        merchant = store.get("merchant", mid)
        if not merchant:
            continue
        key = trg.get("suppression_key") or tid
        if key in state.sent_suppression_keys:
            continue
        mstate = state.merchant(mid)
        if mstate.opted_out:
            continue
        cust = None
        cid = trg.get("customer_id")
        if trg.get("scope") == "customer" or cid:
            cust = store.get("customer", cid)
            if not cust or not _consent_ok(cust) or cid in state.customer_opt_out:
                continue
        elif mstate.wait_until_ts and time.time() < mstate.wait_until_ts:
            continue  # we promised to back off this merchant
        category = store.get("category", merchant.get("category_slug") or (trg.get("payload") or {}).get("category"))
        exp = parse_dt(trg.get("expires_at"))
        expired = bool(exp and exp < now)
        candidates.append((-(trg.get("urgency") or 0), expired, pos, trg, merchant, category, cust))

    candidates.sort(key=lambda c: (c[1], c[0], c[2]))  # unexpired first, then urgency, then judge order
    chosen, per_merchant, per_customer = [], {}, set()
    for _, _, _, trg, merchant, category, cust in candidates:
        mid = merchant.get("merchant_id") or trg.get("merchant_id")
        if cust:
            if cust.get("customer_id") in per_customer:
                continue
            per_customer.add(cust.get("customer_id"))
        else:
            if per_merchant.get(mid, 0) >= config.MAX_MERCHANT_SENDS_PER_TICK:
                continue
            per_merchant[mid] = per_merchant.get(mid, 0) + 1
        chosen.append((trg, merchant, category, cust))
        if len(chosen) >= config.MAX_ACTIONS_PER_TICK:
            break
    return chosen


async def _build_action(trg: dict, merchant: dict, category: dict | None, cust: dict | None, now: datetime) -> dict:
    brief = build_brief(category, merchant, trg, cust, now)
    mid = merchant.get("merchant_id") or trg.get("merchant_id")
    previous = state.merchant(mid).sent_bodies
    try:
        msg = await asyncio.wait_for(compose(brief, previous), timeout=config.TICK_BUDGET_S)
    except Exception as e:  # timeout or anything unexpected -> deterministic fallback, never drop
        log.warning("compose failed for %s: %r; using template", trg.get("id"), e)
        msg = template_message(brief)
    customer_id = cust.get("customer_id") if cust else None
    conv_id = _conversation_id(trg, mid, customer_id)
    kind = trg.get("kind") or "update"
    action = {
        "conversation_id": conv_id,
        "merchant_id": mid,
        "customer_id": customer_id,
        "send_as": brief["send_as"],
        "trigger_id": trg.get("id"),
        "template_name": f"{'merchant' if customer_id else 'vera'}_{kind}_v1",
        "template_params": msg["template_params"],
        "body": msg["body"],
        "cta": msg["cta"],
        "suppression_key": trg.get("suppression_key") or f"{kind}:{mid}:{trg.get('id')}",
        "rationale": msg["rationale"] or f"{kind} trigger composed from category, merchant and trigger context.",
    }
    conv = Conversation(conversation_id=conv_id, merchant_id=mid, customer_id=customer_id, trigger_id=trg.get("id"),
                        kind=kind, send_as=brief["send_as"], brief=brief)
    conv.turns.append({"from": "bot", "body": msg["body"]})
    with state.lock:
        state.conversations[conv_id] = conv
        state.sent_suppression_keys.add(action["suppression_key"])
        state.merchant(mid).sent_bodies.add(msg["body"])
    log.info("tick action %s via %s", conv_id, msg.get("composer"))
    return action


@app.post("/v1/tick")
async def tick(request: Request):
    body = await _json(request) or {}
    now = parse_dt(body.get("now")) or datetime.now(timezone.utc)
    try:
        chosen = _select(body.get("available_triggers") or [], now)
        actions = await asyncio.gather(*[_build_action(t, m, c, cu, now) for t, m, c, cu in chosen],
                                       return_exceptions=True)
        return {"actions": [a for a in actions if isinstance(a, dict)]}
    except Exception as e:
        log.exception("tick failed: %r", e)
        return {"actions": []}


# ------------------------------------------------------------------ reply

def _get_conversation(body: dict) -> Conversation:
    conv_id = str(body.get("conversation_id") or f"conv_adhoc_{int(time.time())}")
    conv = state.conversations.get(conv_id)
    if conv is None:
        mid = body.get("merchant_id")
        cid = body.get("customer_id")
        merchant = store.get("merchant", mid)
        brief = None
        if merchant:
            customer = store.get("customer", cid) if cid else None
            category = store.get("category", merchant.get("category_slug"))
            brief = build_brief(category, merchant, None, customer, None)
        conv = Conversation(conversation_id=conv_id, merchant_id=mid, customer_id=cid,
                            send_as="merchant_on_behalf" if (cid or body.get("from_role") == "customer") else "vera",
                            brief=brief)
        state.conversations[conv_id] = conv
    return conv


@app.post("/v1/reply")
async def reply(request: Request):
    body = await _json(request) or {}
    message = str(body.get("message") or "")
    from_role = str(body.get("from_role") or "merchant")
    try:
        conv = _get_conversation(body)
        mid = conv.merchant_id or body.get("merchant_id")
        mstate = state.merchant(mid)
        mode = classify(message, mstate, from_role)
        conv.turns.append({"from": from_role, "body": message})
        norm = normalize(message)
        mstate.seen_inbound[norm] = mstate.seen_inbound.get(norm, 0) + 1

        # Auto-replies: one nudge for the owner, then back off, then close.
        if mode == "auto_reply":
            mstate.auto_reply_streak += 1
            if mstate.auto_reply_streak == 1:
                out = await compose_reply("auto_reply_nudge", conv, message)
                return _send(conv, out, "")
            if mstate.auto_reply_streak == 2:
                mstate.wait_until_ts = time.time() + 86400
                return {"action": "wait", "wait_seconds": 86400,
                        "rationale": "Same canned auto-reply again: owner not at the phone. Backing off 24h."}
            return _end(conv, "auto_reply_loop",
                        f"Auto-reply {mstate.auto_reply_streak}x with no human response; closing to avoid wasting turns.")
        mstate.auto_reply_streak = 0

        if mode == "empty":
            return {"action": "wait", "wait_seconds": 3600, "rationale": "Empty message; waiting for a real reply."}
        if mode == "opt_out":
            if from_role == "customer" and conv.customer_id:
                state.customer_opt_out.add(conv.customer_id)
            else:
                mstate.opted_out = True
            return _end(conv, "opt_out", "Explicit opt-out; closing and suppressing future sends to this recipient.")
        if mode == "decline":
            return _end(conv, "declined", "Polite decline; exiting gracefully without pushing.")
        if mode == "later":
            wait = 86400 if any(w in message.lower() for w in ("tomorrow", "kal", "next week")) else 3600
            mstate.wait_until_ts = time.time() + wait
            return {"action": "wait", "wait_seconds": wait,
                    "rationale": f"Merchant asked for time; backing off {wait // 3600}h before following up."}
        if mode == "hostile_off_topic":
            mode = "off_topic"
        if conv.status == "ended" and mode not in ("commit", "question", "off_topic", "slot_choice"):
            return _end(conv, conv.ended_reason or "ended", "Conversation already closed; not re-engaging.")
        if conv.status == "ended":
            conv.status = "open"  # merchant re-engaged with a real ask
        bot_turns = sum(1 for t in conv.turns if t["from"] == "bot")
        if bot_turns >= 6 and mode not in ("commit", "question", "slot_choice"):
            return _end(conv, "max_turns", "Conversation has run long without a clear next step; closing politely.")

        if mode == "commit":
            conv.committed = True
        try:
            out = await asyncio.wait_for(compose_reply(mode, conv, message), timeout=config.REPLY_BUDGET_S)
        except Exception as e:
            log.warning("reply compose failed: %r", e)
            out = template_reply(mode, conv, message)
        return _send(conv, out, "")
    except Exception as e:
        log.exception("reply failed: %r", e)
        return {"action": "send", "body": "Thanks for the reply — I'll get back to you shortly with the details.",
                "cta": "none", "rationale": "Recovered from an internal error with a safe holding reply."}


def _send(conv: Conversation, out: dict, prefix: str) -> dict:
    body = out["body"].strip()
    if body in conv.bot_bodies():
        body = body + " (Reply YES or STOP.)" if "STOP" not in body else "Noted — I'll wait for your reply."
    conv.turns.append({"from": "bot", "body": body})
    return {"action": "send", "body": body, "cta": out.get("cta", "open_ended"),
            "rationale": (prefix + (out.get("rationale") or "")).strip()}


def _end(conv: Conversation, reason: str, rationale: str) -> dict:
    conv.status, conv.ended_reason = "ended", reason
    return {"action": "end", "rationale": rationale}


# ------------------------------------------------------------------ teardown

@app.post("/v1/teardown")
async def teardown():
    store.clear()
    state.clear()
    return {"ok": True, "wiped_at": _now_iso()}


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"service": "vera-challenge-bot", "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"]}
