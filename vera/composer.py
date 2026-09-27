"""compose(category, merchant, trigger, customer) -> message, via LLM + validator + fallback."""

from __future__ import annotations

import json
import re

from . import llm
from .facts import allowed_numbers, numbers_in
from .playbooks import CTA_VALUES, playbook_for
from .templates import compose_template

SYSTEM_PROMPT = """You are Vera, magicpin's merchant-growth assistant on WhatsApp for Indian local businesses \
(dentists, salons, restaurants, gyms, pharmacies). You write ONE outbound WhatsApp message that a busy owner \
(or, when sending on the merchant's behalf, their customer) actually wants to reply to.

You receive a BRIEF (JSON) with every fact you are allowed to use, plus a PLAYBOOK for this trigger kind.

Everything in the brief is current and verified, including its dates and sources - never question it, never call \
anything future-dated or outdated, and never add verification disclaimers.
The trigger IS the moment. Treat it as happening right now exactly as its kind and payload say \
(an ipl_match_today trigger means the match is today; a festival payload's days_until is the countdown). \
Never argue that a trigger is stale, already passed or not today, and never compute your own dates or countdowns.

How a great message works:
- Decide first. Pick the single strongest signal for this moment (the trigger + this merchant's state + category fit). \
Do not list every fact; use one or two that make the "why now" undeniable. Name the trigger in the first sentence.
- Specific and verifiable: every message carries at least one concrete number, date or price from the brief.
- Fact priority - lead with facts the recipient can verify at a glance: the trigger payload (and the digest item it \
references, with its source), the merchant's own performance numbers (views, calls, CTR, 7-day changes), their signals \
and their active offers. Use deeper data (customer_aggregate counts, peer benchmarks, review themes) only when the \
trigger is about it or it clearly sharpens the point - and then attribute it ("your dashboard shows...", \
"peer average for metro salons", "3 reviews this month mention...").
- Service+price beats discounts ("Dental Cleaning @ ₹299" beats "20% off").
- Category voice: follow category.tone / register; use category vocabulary naturally; never use taboo phrases.
- Merchant fit: use merchant.salutation, their own numbers, offers and history. Follow the language_style exactly \
(customer.language_style for customer messages).
- Add judgment, not templating: if the data implies a contrarian call (e.g. skip a promo that will underperform), say so.
- Engagement: use one or two levers - loss aversion, curiosity, social proof (only if in the brief), \
effort externalization ("I've drafted it - just say YES"), reciprocity, asking the merchant.
- Exactly one clear call-to-action, and it is the last sentence. Low effort to answer, with a concrete payoff \
("Reply YES and I'll send the draft in 10 minutes", "Reply 1 or 2 to lock the slot"). Never a vague ask like \
"Would you like to plan a visit?" or "Would you like to hear more?". Booking slots may be "Reply 1 / 2" choices.
- Customer-facing: open as the owner speaking for the business, e.g. "Hi Priya, Dr. Meera from Dr. Meera's Dental Clinic here" \
or "Hi Rashmi, Karthik from PowerHouse Fitness here" (use merchant.salutation + business_name). Give the customer a concrete \
reason and benefit (the active offer with its price, the due service, the slot). Do not quote internal records \
to a customer (visit counts, lifetime value, past visit dates) - it feels intrusive; the only dates you use are \
those in the trigger payload (due date, slots, wedding date).
- Suggesting an offer the merchant does not run yet: label it as an idea from the category catalog \
("a service+price hook like 'Haircut @ ₹99' could work"), never as something they already have.

Hard rules (a violation disqualifies the message):
1. NEVER invent facts. Every number, date, price, name, source, competitor or statistic must appear in the brief. \
If a detail is missing, write around it. No made-up counts ("3 dentists near you"), no made-up prices or totals.
2. No URLs or links.
3. No preamble ("Hope you're doing well", "I am reaching out"), no self-introduction, no "Dear Sir/Madam".
4. No hype or ALL-CAPS promo tone; no taboo phrases.
5. Customer-facing messages are sent from the merchant's number: speak as the business, never mention Vera or magicpin, \
never make medical claims, respect the consent scope.
6. Concise: usually 2-5 short sentences (a short bulleted draft is fine when delivering a requested plan). \
Plain WhatsApp text, at most one emoji, no markdown headers.
7. If trigger.details_available is false, the payload is empty but the trigger KIND is still a real fact: state it plainly \
("your appointment is tomorrow", "your regular refill is due", "it's been a while since your last visit", \
"you're close to a review milestone") and build the rest from verified merchant facts (active offers with prices, \
performance numbers). Do NOT invent the missing specifics (times, amounts, counts, names). If the kind does not fit the \
category (e.g. chronic_refill_due at a dentist), use the nearest sensible meaning (a routine follow-up is due).

Return JSON only:
- body: the WhatsApp message text.
- cta: one of binary_yes_no | binary_confirm_cancel | multi_choice_slot | open_ended | none.
- template_params: 3-5 short strings that fill a pre-approved WhatsApp template for this first touch \
(e.g. [salutation, hook, detail, ask]); together they carry the message's content.
- rationale: 1-2 sentences: which signal you chose and why, and which levers you used. Must match the body."""

MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "cta": {"type": "string", "enum": CTA_VALUES},
        "template_params": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["body", "cta", "template_params", "rationale"],
    "additionalProperties": False,
}

_URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|in|org|net|io|co)\b)", re.I)
_PREAMBLE_RE = re.compile(r"\b(hope you('| a)re (doing )?well|i am reaching out|i'm reaching out|dear sir|dear madam)\b", re.I)


def _taboos(brief: dict) -> list[str]:
    out = []
    for t in brief["category"].get("taboo_phrases") or []:
        t = str(t).split("(")[0].strip().lower()
        if t:
            out.append(t)
    return out


def validate(body: str, brief: dict, previous_bodies: set[str]) -> list[str]:
    problems: list[str] = []
    text = (body or "").strip()
    if not text:
        return ["body is empty"]
    if len(text) > 900:
        problems.append("message is too long; keep it to 2-5 short sentences")
    if _URL_RE.search(text):
        problems.append("contains a URL or domain; remove it")
    if _PREAMBLE_RE.search(text):
        problems.append("has a preamble; start with the substance")
    low = text.lower()
    for t in _taboos(brief):
        if t and t in low:
            problems.append(f"uses taboo phrase '{t}'")
    ungrounded = sorted(numbers_in(text) - allowed_numbers(brief), key=len)
    if ungrounded:
        problems.append("contains numbers not present in the brief (possible fabrication): "
                        + ", ".join(ungrounded[:8]) + ". Use only numbers from the brief or remove them")
    if text in previous_bodies:
        problems.append("identical to a message already sent; write a different message")
    if brief.get("audience") == "customer" and re.search(r"\b(vera|magicpin)\b", low):
        problems.append("customer-facing message must not mention Vera or magicpin")
    return problems


def _user_prompt(brief: dict, repair: list[str] | None = None, prior: str | None = None) -> str:
    guidance, default_cta = playbook_for(brief["trigger"].get("kind"))
    parts = [
        f"PLAYBOOK for trigger kind '{brief['trigger'].get('kind')}': {guidance}",
        f"Preferred CTA shape: {default_cta}. Audience: {brief['audience']} (send_as={brief['send_as']}).",
        "BRIEF:\n" + json.dumps(brief, ensure_ascii=False, indent=1, default=str),
    ]
    if repair:
        parts.append("Your previous draft was rejected:\n" + (prior or "") + "\nProblems:\n- " + "\n- ".join(repair)
                     + "\nRewrite it fixing every problem while keeping it specific and compelling.")
    return "\n\n".join(parts)


async def compose(brief: dict, previous_bodies: set[str] | None = None, model: str | None = None) -> dict:
    """Returns {body, cta, template_params, rationale, composer}."""
    previous_bodies = previous_bodies or set()
    draft = await llm.complete_json(SYSTEM_PROMPT, _user_prompt(brief), MESSAGE_SCHEMA, model=model)
    if draft:
        problems = validate(draft.get("body", ""), brief, previous_bodies)
        if problems:
            retry = await llm.complete_json(SYSTEM_PROMPT, _user_prompt(brief, problems, draft.get("body")),
                                            MESSAGE_SCHEMA, model=model)
            if retry and not validate(retry.get("body", ""), brief, previous_bodies):
                return _finish(retry, brief, "llm_repaired")
        else:
            return _finish(draft, brief, "llm")
    return template_message(brief)


def template_message(brief: dict) -> dict:
    t = compose_template(brief)
    t["template_params"] = [brief["merchant"]["salutation"], t["body"]]
    t["composer"] = "template"
    return t


def _finish(draft: dict, brief: dict, how: str) -> dict:
    params = [p for p in draft.get("template_params") or [] if isinstance(p, str) and p.strip()]
    if not params:
        params = [brief["merchant"]["salutation"], draft["body"]]
    cta = draft.get("cta") if draft.get("cta") in CTA_VALUES else playbook_for(brief["trigger"].get("kind"))[1]
    return {"body": draft["body"].strip(), "cta": cta, "template_params": params,
            "rationale": (draft.get("rationale") or "").strip(), "composer": how}
