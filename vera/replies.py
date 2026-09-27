"""Reply routing: classify the inbound turn with rules, then compose the next move."""

from __future__ import annotations

import json
import re

from . import llm
from .composer import _URL_RE, validate
from .store import Conversation, MerchantState

# ------------------------------------------------------------------ classifiers

_AUTO_REPLY_PATTERNS = [
    r"thank(s| you) for (contacting|reaching out|your message|messaging)",
    r"our team will (get back|respond|contact|reach)", r"we will (get back|respond|contact) (to you )?(shortly|soon)",
    r"will (respond|reply|get back) (to you )?(shortly|soon|at the earliest)",
    r"\bautomated (assistant|message|reply|response)\b", r"\bauto[- ]?reply\b", r"this is an automated",
    r"currently (unavailable|closed|away)", r"out of (the )?office", r"(our )?business hours are",
    r"we are closed", r"aapki jaankari ke liye", r"team tak pahuncha", r"main ek automated",
    r"hamari team (aapse )?(jald|sampark)", r"for (any|urgent) (queries|enquiries),? (please )?call",
]
_AUTO_RE = re.compile("|".join(_AUTO_REPLY_PATTERNS), re.I)

_OPT_OUT_RE = re.compile(
    r"\b(stop (messag|send|texting|contacting|bothering)\w*|^stop$|unsubscribe|do ?n[o']?t (message|text|contact|send)|"
    r"not interested|leave me alone|band karo|mat bhejo|message mat|nahi chahiye|no more messages|block (you|this))\b",
    re.I)
_HOSTILE_RE = re.compile(
    r"\b(useless|spam|bakwas|bakwaas|idiot|stupid|nonsense|pathetic|fraud|scam|irritat\w*|annoying|bothering|shut up|"
    r"waste of time|bloody|damn|f+u+c+k\w*|chutiya|bewakoof)\b", re.I)
_COMMIT_RE = re.compile(
    r"\b(yes|yeah|yep|yup|haan|han|haa|ha ji|ji haan|ok(ay)?|sure|go ahead|let'?s do it|lets do it|do it|proceed|confirm(ed)?|"
    r"send it|send (me )?(the|it)|please do|done|start|book (it|me)|chalega|chalo|karo|kar do|kardo|bhej do|judna hai|"
    r"join|i want to join|sign me up|agreed|perfect|great,? (go|do)|approved|publish)\b", re.I)
_NEGATION_RE = re.compile(r"\b(don'?t|do not|not|never|nahi|nahin|mat)\b", re.I)
_NEGATIVE_SOFT_RE =re.compile(r"^\s*(no|nope|nah|nahi|nahin|no thanks|not now,? thanks|no thank you)[.! ]*$", re.I)
_LATER_RE = re.compile(
    r"\b(later|busy|baad mein|baad me|abhi nahi|not now|in a meeting|call me (later|tomorrow)|tomorrow|kal|"
    r"give me (some )?time|remind me|next week|after (lunch|some time))\b", re.I)
_OFF_TOPIC_RE = re.compile(
    r"\b(gst|income tax|itr|tax filing|file my|loan|insurance|electricity bill|passport|visa|aadhaar|pan card|"
    r"stock market|share price|crypto|cricket score|politics|election|court case|legal notice|my ca\b|accountant|"
    r"bank account|credit card|recharge)\b", re.I)
_QUESTION_RE = re.compile(r"\?|\b(what|how|why|when|which|kya|kaise|kyun|kab|kitna|price|cost|charges?)\b", re.I)
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_HINGLISH_RE = re.compile(r"\b(hai|haan|nahi|kya|karo|kar|mujhe|aap|apka|aapka|hoga|chahiye|bhej|mein|kaise|theek)\b", re.I)

# Words the replay judge treats as "still qualifying" after a commitment.
QUALIFYING_PHRASES = ["would you", "do you", "can you tell", "what if", "how about"]


def normalize(msg: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (msg or "").lower())).strip()


def classify(message: str, mstate: MerchantState, from_role: str) -> str:
    text = (message or "").strip()
    norm = normalize(text)
    if not norm:
        return "empty"
    repeated = mstate.seen_inbound.get(norm, 0) >= 1 and len(norm) > 25
    if _AUTO_RE.search(text) or repeated:
        return "auto_reply"
    if _OPT_OUT_RE.search(text):
        return "opt_out"
    if _HOSTILE_RE.search(text):
        return "hostile_off_topic" if _OFF_TOPIC_RE.search(text) else "hostile"
    if from_role == "customer" and re.fullmatch(r"\s*[1-9]\s*[.)]?\s*", text):
        return "slot_choice"
    if _OFF_TOPIC_RE.search(text):
        return "off_topic"
    if _NEGATIVE_SOFT_RE.match(text):
        return "decline"
    commit = bool(_COMMIT_RE.search(text)) and not _NEGATION_RE.search(text)
    if _LATER_RE.search(text) and not commit:
        return "later"
    if commit:
        return "commit"
    if _QUESTION_RE.search(text):
        return "question"
    return "other"


def language_hint(message: str) -> str:
    if _DEVANAGARI_RE.search(message or ""):
        return "The latest message is in Hindi (Devanagari): reply in simple Hindi (Devanagari) with English product terms."
    if _HINGLISH_RE.search(message or ""):
        return "The latest message is Hinglish: reply in natural Hindi-English code-mix (Roman script)."
    return "The latest message is English: reply in English (a light Hindi touch is fine only if the merchant's language_style allows)."


# ------------------------------------------------------------------ reply composer

REPLY_SYSTEM = """You are Vera, magicpin's merchant-growth assistant, continuing a live WhatsApp conversation \
(or, when send_as is merchant_on_behalf, replying as the business to its customer).

You get the BRIEF (only facts you may use), the conversation so far, the latest inbound message, and a MODE decided \
by the router. Write the next message. Everything in the brief is current and verified (dates and sources included): \
never question it, call anything future-dated, or add verification disclaimers.

MODES:
- commit: they said yes / let's do it. Switch to action immediately and deliver: you can write content right now, so put \
the finished artifact in this message (e.g. the actual patient WhatsApp text, the Google post, the offer copy, the \
checklist), built from brief facts (digest item, patient_content_library, offers). Say what happens next in one line. \
Do NOT ask any qualifying question and do not hedge. The only allowed question is a final confirm \
(e.g. "Reply CONFIRM and I'll schedule it for tomorrow 10am").
- question: answer directly using only brief facts. If the brief lacks the answer, say you'll confirm it rather than guessing. \
Then steer back to the open next step with one low-effort ask.
- off_topic: politely say it's outside what you can help with (one short clause, suggest the right person e.g. their CA), \
then bring them back to the open thread with one ask.
- hostile: one short, sincere apology, offer to stop ("reply STOP and I won't message again"), no pitch.
- other: acknowledge briefly and move the thread forward with one specific, low-effort ask.

Rules: never invent facts, numbers, prices or names not in the brief; no URLs; no self-introduction; never repeat an earlier \
message; 1-4 short sentences; one CTA at the end; mirror the merchant's language as instructed.

Return JSON: body, cta (binary_yes_no | binary_confirm_cancel | multi_choice_slot | open_ended | none), rationale."""

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "cta": {"type": "string", "enum": ["binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "open_ended", "none"]},
        "rationale": {"type": "string"},
    },
    "required": ["body", "cta", "rationale"],
    "additionalProperties": False,
}


_TOPIC_NAMES = {
    "research_digest": "research summary", "regulation_change": "compliance checklist", "cde_opportunity": "webinar registration",
    "perf_dip": "listing fix", "perf_spike": "follow-up post", "renewal_due": "renewal", "festival_upcoming": "festive campaign",
    "curious_ask_due": "Google post", "winback_eligible": "profile restart", "ipl_match_today": "match-day banner",
    "review_theme_emerged": "review reply", "milestone_reached": "review-request message", "active_planning_intent": "draft plan",
    "seasonal_perf_dip": "member retention plan", "supply_alert": "customer notice", "category_seasonal": "seasonal customer message",
    "gbp_unverified": "Google verification", "competitor_opened": "Google post", "dormant_with_vera": "quick fix",
}


def _thread_topic(conv: Conversation) -> str:
    return _TOPIC_NAMES.get(conv.kind or "", "next step")


def _commit_ok(body: str) -> bool:
    low = body.lower()
    return not any(p in low for p in QUALIFYING_PHRASES) and any(
        w in low for w in ("done", "sending", "draft", "here", "confirm", "proceed", "next", "booked", "set up", "live"))


def _slot_labels(conv: Conversation) -> list[str]:
    p = ((conv.brief or {}).get("trigger") or {}).get("payload") or {}
    slots = p.get("available_slots") or p.get("next_session_options") or []
    return [s.get("label") for s in slots if isinstance(s, dict) and s.get("label")]


def template_reply(mode: str, conv: Conversation, message: str) -> dict:
    customer_facing = conv.send_as == "merchant_on_behalf"
    hindi = bool(_HINGLISH_RE.search(message or "") or _DEVANAGARI_RE.search(message or ""))
    topic = _thread_topic(conv)
    if mode == "commit":
        if customer_facing:
            body = "Done — you're booked. We'll send a reminder the day before. Reply here if anything changes."
        elif conv.committed and any(t["from"] == "bot" and "CONFIRM" in t["body"] for t in conv.turns):
            body = (f"Perfect — the {topic} is being finalised now. I'll send the finished version here for a last look, "
                    "and it goes live as soon as you reply CONFIRM.")
        else:
            body = (f"Done — drafting the {topic} now. You'll get it here to review in a few minutes, "
                    "and it goes live the moment you approve. Reply CONFIRM to proceed.")
            if hindi:
                body = ("Ho gaya — main abhi draft bana rahi hoon. Aapko yahin review ke liye bhejungi, approve karte hi live."
                        " Reply CONFIRM to proceed.")
        return {"body": body, "cta": "binary_confirm_cancel", "rationale": "Explicit commitment; switched to action mode."}
    if mode == "slot_choice":
        labels = _slot_labels(conv)
        try:
            idx = int(re.sub(r"\D", "", message)) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(labels):
            body = f"Confirmed for {labels[idx]}. See you then! Reply here if you need to change it."
        else:
            body = "Thanks! Please share a day and time that works and we'll confirm it right away."
        return {"body": body, "cta": "none", "rationale": "Customer picked a slot; confirming booking."}
    if mode == "off_topic":
        body = ("That's outside what I can help with — your CA would be the right person for it. "
                f"Coming back to the {topic} — shall I go ahead with it?")
        return {"body": body, "cta": "binary_yes_no", "rationale": "Out-of-scope ask politely declined; redirected to the thread."}
    if mode == "hostile":
        body = "Sorry for the trouble — I don't want to bother you. Reply STOP and I won't message again."
        return {"body": body, "cta": "none", "rationale": "Merchant frustrated; apologised and offered an opt-out, no pitch."}
    if mode == "auto_reply_nudge":
        body = "Looks like an auto-reply 🙂 Whenever the owner sees this, just reply YES and I'll take it from there."
        return {"body": body, "cta": "binary_yes_no", "rationale": "Detected auto-reply; one prompt for the owner."}
    if mode == "question":
        body = ("Good question — let me confirm the exact details and share them here. "
                f"Meanwhile, shall I keep the {topic} ready for you?")
        return {"body": body, "cta": "binary_yes_no", "rationale": "Answered without guessing; kept the thread moving."}
    body = f"Got it. Shall I go ahead with the {topic}? Just reply YES."
    return {"body": body, "cta": "binary_yes_no", "rationale": "Acknowledged and moved to one low-effort ask."}


async def compose_reply(mode: str, conv: Conversation, message: str) -> dict:
    if mode in ("slot_choice", "auto_reply_nudge"):
        return template_reply(mode, conv, message)
    previous = conv.bot_bodies()
    if conv.brief:
        history = [{"from": t["from"], "body": t["body"]} for t in conv.turns[-8:]]
        user = "\n\n".join([
            f"MODE: {mode}",
            language_hint(message),
            f"send_as: {conv.send_as}",
            "BRIEF:\n" + json.dumps(conv.brief, ensure_ascii=False, default=str),
            "CONVERSATION SO FAR:\n" + json.dumps(history, ensure_ascii=False),
            f"LATEST INBOUND MESSAGE: {message}",
        ])
        draft = await llm.complete_json(REPLY_SYSTEM, user, REPLY_SCHEMA, max_tokens=1500)
        if draft and draft.get("body"):
            body = draft["body"].strip()
            problems = validate(body, conv.brief, previous) if conv.brief else []
            if _URL_RE.search(body):
                problems.append("url")
            if mode == "commit" and not _commit_ok(body):
                problems.append("still qualifying after commitment")
            if not problems:
                return {"body": body, "cta": draft.get("cta", "open_ended"), "rationale": draft.get("rationale", "")}
    out = template_reply(mode, conv, message)
    if out["body"] in previous:
        out["body"] = out["body"].replace("Shall I", "Want me to").replace("Done —", "On it —")
    return out
