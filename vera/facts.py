"""Build a grounded fact sheet ("brief") from the 4 contexts.

Everything the composer is allowed to say must be traceable to this brief.
The validator later extracts every number from a composed message and
rejects the message if a number does not appear somewhere in the brief.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Keys in trigger payloads that point at a category digest item.
DIGEST_REF_KEYS = ("top_item_id", "digest_item_id", "alert_id", "item_id")


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        v = value.strip().replace("Z", "+00:00")
        if len(v) == 10:
            return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pct(x: Any, digits: int = 1) -> str | None:
    """0.021 -> '2.1%'; -0.5 -> '-50%'."""
    if not isinstance(x, (int, float)):
        return None
    v = round(x * 100, digits)
    s = f"{v:.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s}%"


def fmt_date(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()][:3]} {dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def humanize(token: str) -> str:
    return token.replace("_", " ")


def _month_in_range(month_range: str, month: int) -> bool:
    """'Nov-Feb', 'Apr-Jun', 'Jan', 'Feb 14' -> does `month` (1-12) fall inside?"""
    names = re.findall(r"[A-Z][a-z]{2}", month_range or "")
    idx = [MONTHS.index(n) + 1 for n in names if n in MONTHS]
    if not idx:
        return False
    if len(idx) == 1:
        return month == idx[0]
    start, end = idx[0], idx[-1]
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


def language_style_for_merchant(languages: list[str]) -> str:
    langs = [l.lower() for l in (languages or [])]
    if len(langs) >= 2 and langs[1] == "hi":
        return ("English-first with light, natural Hindi code-mix in Roman script (e.g. 'aapke', 'chaliye', "
                "'bas YES bol dijiye') — the way a Delhi/North-India business peer writes on WhatsApp.")
    return "Clear, simple English (the merchant's primary business language). Avoid Hindi phrases."


def language_style_for_customer(pref: str | None) -> str:
    p = (pref or "").lower()
    if p in ("hi", "hindi"):
        return "Hindi written in Roman script (Hinglish-heavy), respectful 'aap' register; keep medicine/service names in English."
    if "hi" in p and "mix" in p:
        return "Natural Hindi-English code-mix in Roman script (e.g. 'Apke liye 2 slots ready hain')."
    if any(t in p for t in ("ta-", "te-", "kn-", "mr-")):
        return "Warm, simple English. You may open with one familiar regional greeting word, nothing more."
    return "Warm, simple English."


def salutation_for(category_slug: str, owner_first_name: str | None, business_name: str) -> str:
    if not owner_first_name:
        return f"{business_name} team"
    name = owner_first_name.strip()
    if category_slug == "dentists" and not name.lower().startswith("dr"):
        return f"Dr. {name}"
    return name


def find_digest_item(category: dict, trigger_payload: dict) -> dict | None:
    digest = category.get("digest") or []
    for key in DIGEST_REF_KEYS:
        ref = trigger_payload.get(key)
        if ref:
            for item in digest:
                if item.get("id") == ref:
                    return item
    return None


def _offer_titles(offers: list[dict], status: str) -> list[str]:
    return [o.get("title") for o in offers or [] if o.get("status") == status and o.get("title")]


def build_brief(category: dict | None, merchant: dict, trigger: dict | None,
                customer: dict | None, now: datetime | None) -> dict:
    category = category or {}
    trigger = trigger or {}
    now = now or datetime.now(timezone.utc)
    ident = merchant.get("identity") or {}
    slug = merchant.get("category_slug") or category.get("slug") or "unknown"
    business = ident.get("name") or merchant.get("merchant_id") or "your business"
    owner = ident.get("owner_first_name")
    perf = merchant.get("performance") or {}
    peer = category.get("peer_stats") or {}
    voice = category.get("voice") or {}
    tpayload = trigger.get("payload") or {}
    derived: list[str] = []

    # ---- performance vs peers -------------------------------------------------
    perf_out = dict(perf)
    if isinstance(perf.get("ctr"), (int, float)):
        perf_out["ctr_pct"] = pct(perf["ctr"])
    d7 = perf.get("delta_7d") or {}
    perf_out["delta_7d_pct"] = {k: pct(v, 0) for k, v in d7.items() if isinstance(v, (int, float))}

    peer_out = dict(peer)
    if isinstance(peer.get("avg_ctr"), (int, float)):
        peer_out["avg_ctr_pct"] = pct(peer["avg_ctr"])
    if isinstance(perf.get("ctr"), (int, float)) and isinstance(peer.get("avg_ctr"), (int, float)):
        gap = round((perf["ctr"] - peer["avg_ctr"]) * 100, 1)
        side = "above" if gap > 0 else "below"
        if abs(gap) >= 0.3:
            derived.append(f"CTR {pct(perf['ctr'])} vs {pct(peer['avg_ctr'])} peer average ({abs(gap)} points {side})")
    for metric, peer_key in (("views", "avg_views_30d"), ("calls", "avg_calls_30d"), ("directions", "avg_directions_30d")):
        mv, pv = perf.get(metric), peer.get(peer_key)
        if isinstance(mv, (int, float)) and isinstance(pv, (int, float)) and pv:
            ratio = mv / pv
            if ratio >= 1.15 or ratio <= 0.85:
                derived.append(f"30-day {metric}: {mv} vs peer average {pv} "
                               f"({'above' if ratio > 1 else 'below'} peers)")
    for k, v in (perf_out.get("delta_7d_pct") or {}).items():
        derived.append(f"Last 7 days {humanize(k.replace('_pct', ''))} change: {v}")
    if isinstance(merchant.get("review_count"), int) and isinstance(peer.get("avg_review_count"), int):
        derived.append(f"Reviews {merchant['review_count']} vs peer average {peer['avg_review_count']}")

    # ---- customers --------------------------------------------------------------
    agg = merchant.get("customer_aggregate") or {}
    total = agg.get("total_unique_ytd")
    lapsed = agg.get("lapsed_180d_plus") or agg.get("lapsed_90d_plus")
    if isinstance(total, int) and isinstance(lapsed, int) and total:
        derived.append(f"{lapsed} of {total} customers this year are lapsed")

    # ---- subscription -------------------------------------------------------------
    sub = merchant.get("subscription") or {}

    # ---- trigger ------------------------------------------------------------------
    ref_item = find_digest_item(category, tpayload)
    placeholder = bool(tpayload.get("placeholder")) or (bool(trigger) and not tpayload)
    # No "days from today" maths: the judge's clock may differ from the data's timeline, and the
    # trigger itself defines the moment. Only weekday names (always consistent) are added.
    for key, val in tpayload.items():
        dt = parse_dt(val) if isinstance(val, str) and re.match(r"\d{4}-\d{2}-\d{2}", val) else None
        if dt:
            derived.append(f"{humanize(key)}: {fmt_date(dt)} ({WEEKDAYS[dt.weekday()]})")
        if isinstance(val, (int, float)) and "pct" in key and abs(val) <= 5:
            derived.append(f"{humanize(key)}: {pct(val, 0)}")
    if isinstance(tpayload.get("value_now"), (int, float)) and isinstance(tpayload.get("milestone_value"), (int, float)):
        derived.append(f"{tpayload['milestone_value'] - tpayload['value_now']} more to reach {tpayload['milestone_value']}")
    if trigger.get("kind") == "ipl_match_today" and tpayload.get("match_time_iso"):
        mdt = parse_dt(tpayload["match_time_iso"])
        if mdt:
            weekend = mdt.weekday() >= 5
            derived.append(f"Match is on a {WEEKDAYS[mdt.weekday()]} ({'weekend' if weekend else 'weeknight'})")
    if ref_item and ref_item.get("date"):
        ddt = parse_dt(ref_item["date"])
        if ddt:
            derived.append(f"Event date: {fmt_date(ddt)}")

    # ---- category context -----------------------------------------------------------
    month = now.month
    beats = [b for b in category.get("seasonal_beats") or [] if _month_in_range(b.get("month_range", ""), month)]

    brief: dict[str, Any] = {
        "audience": "customer" if trigger.get("scope") == "customer" or customer else "merchant",
        "merchant": {
            "merchant_id": merchant.get("merchant_id"),
            "business_name": business,
            "owner_first_name": owner,
            "salutation": salutation_for(slug, owner, business),
            "locality": ident.get("locality"),
            "city": ident.get("city"),
            "verified_on_google": ident.get("verified"),
            "language_style": language_style_for_merchant(ident.get("languages") or []),
            "subscription": sub,
            "performance_30d": perf_out,
            "active_offers": _offer_titles(merchant.get("offers"), "active"),
            "expired_or_paused_offers": _offer_titles(merchant.get("offers"), "expired")
            + _offer_titles(merchant.get("offers"), "paused"),
            "customer_aggregate": agg,
            "signals": [humanize(s) for s in merchant.get("signals") or []],
            "review_themes": merchant.get("review_themes") or [],
            "recent_conversation": (merchant.get("conversation_history") or [])[-4:],
        },
        "category": {
            "slug": slug,
            "tone": humanize(str(voice.get("tone", ""))),
            "register": humanize(str(voice.get("register", ""))),
            "vocab_allowed": voice.get("vocab_allowed") or [],
            "taboo_phrases": voice.get("vocab_taboo") or voice.get("taboos") or [],
            "tone_examples": voice.get("tone_examples") or [],
            "peer_benchmarks": peer_out,
            "catalog_offers_merchant_could_add": [o.get("title") for o in category.get("offer_catalog") or []],
            "seasonal_beats_now": beats,
            "trend_signals": category.get("trend_signals") or [],
            "digest_this_week": [
                {k: v for k, v in d.items() if k in ("id", "kind", "title", "source", "summary", "actionable",
                                                       "trial_n", "patient_segment", "date", "credits")}
                for d in category.get("digest") or []
            ],
        },
        "trigger": {
            "id": trigger.get("id"),
            "kind": trigger.get("kind"),
            "scope": trigger.get("scope"),
            "source": trigger.get("source"),
            "urgency": trigger.get("urgency"),
            "payload": tpayload,
            "referenced_digest_item": ref_item,
            "details_available": not placeholder,
            "expires_at": trigger.get("expires_at"),
        },
        "derived_facts": derived,
    }
    if customer:
        cid = customer.get("identity") or {}
        rel = customer.get("relationship") or {}
        brief["customer"] = {
            "customer_id": customer.get("customer_id"),
            "name": cid.get("name"),
            "age_band": cid.get("age_band"),
            "senior_citizen": cid.get("senior_citizen"),
            "language_style": language_style_for_customer(cid.get("language_pref")),
            "state": customer.get("state"),
            "relationship": rel,
            "preferences": customer.get("preferences") or {},
            "consent_scope": (customer.get("consent") or {}).get("scope") or [],
        }
        brief["audience"] = "customer"
    brief["send_as"] = "merchant_on_behalf" if brief["audience"] == "customer" else "vera"
    return brief


# ---- grounding helpers used by the validator ----------------------------------------

# Standalone numbers only: digits glued to letters/underscores (ids like "m_001", "W17", "7d", "6pm")
# are not treated as quotable quantities.
_NUM_RE = re.compile(r"(?<![A-Za-z_\d.])\d[\d,]*(?:\.\d+)?(?![A-Za-z_\d])")


def normalize_number(tok: str) -> str:
    tok = tok.replace(",", "").strip(".")
    if not tok:
        return tok
    try:
        if "." in tok:
            f = float(tok)
            return str(int(f)) if f.is_integer() else f"{f:.4f}".rstrip("0").rstrip(".")
        return str(int(tok))
    except ValueError:
        return tok


def numbers_in(text: str) -> set[str]:
    return {normalize_number(m) for m in _NUM_RE.findall(text or "")}


def allowed_numbers(brief: dict) -> set[str]:
    blob = json.dumps(brief, ensure_ascii=False, default=str)
    # Unit-suffixed values the message may legitimately spell out: "7d" -> "7 days", "22d" -> "22 days".
    blob = re.sub(r"(?<![A-Za-z_\d])(\d+)(d|h|mo|km|pm|am)\b", r"\1 \2", blob)
    nums = numbers_in(blob)
    # Decimal fractions in the data may be quoted as percentages (0.38 -> 38).
    for n in list(nums):
        try:
            f = float(n)
        except ValueError:
            continue
        if 0 < abs(f) < 1:
            nums.add(normalize_number(f"{abs(f) * 100:.4f}"))
    # Tiny list/choice numbers and common time anchors.
    nums.update({"1", "2", "24", "48"})
    return nums
