"""Deterministic, grounded fallback composer.

Used when no LLM is configured, when the LLM times out, or when an LLM draft
fails validation twice. Every sentence here is built only from brief fields.
"""

from __future__ import annotations

from .facts import parse_dt, fmt_date, humanize, pct


def _hinglish(brief: dict) -> bool:
    style = (brief.get("customer") or {}).get("language_style") or brief["merchant"]["language_style"]
    return "Hindi" in style and "Avoid Hindi" not in style


def _yes_close(brief: dict, action: str) -> str:
    if _hinglish(brief):
        return f"{action} — bas YES reply kar dijiye."
    return f"{action} — just reply YES."


def _offer(brief: dict) -> str | None:
    offers = brief["merchant"]["active_offers"]
    return offers[0] if offers else None


def _catalog(brief: dict) -> str | None:
    """Prefer a service+price catalog item over a percentage discount."""
    cat = brief["category"]["catalog_offers_merchant_could_add"]
    priced = [c for c in cat if "@" in c]
    return (priced or cat or [None])[0]


def _peer_hook(brief: dict) -> str | None:
    perf = brief["merchant"]["performance_30d"]
    peer = brief["category"]["peer_benchmarks"]
    ctr, pctr = perf.get("ctr"), peer.get("avg_ctr")
    if isinstance(ctr, (int, float)) and isinstance(pctr, (int, float)) and abs(ctr - pctr) >= 0.003:
        if ctr < pctr:
            return (f"Your CTR is {pct(ctr)} vs a {pct(pctr)} peer average — people find you but don't tap through")
        return (f"Your CTR is {pct(ctr)} vs a {pct(pctr)} peer average, so the listing converts well — "
                "more visibility is the lever")
    views, pviews = perf.get("views"), peer.get("avg_views_30d")
    if isinstance(views, (int, float)) and isinstance(pviews, (int, float)) and pviews and views < 0.85 * pviews:
        return f"Your listing got {views} views in 30 days vs a {pviews} peer average"
    return None


def _item_line(item: dict) -> str:
    parts = [item.get("title", "").rstrip(".")]
    if item.get("source"):
        parts.append(f"({item['source']})")
    return " ".join(p for p in parts if p)


def compose_template(brief: dict) -> dict:
    kind = brief["trigger"].get("kind") or ""
    p = brief["trigger"].get("payload") or {}
    m = brief["merchant"]
    sal = m["salutation"]
    biz = m["business_name"]
    item = brief["trigger"].get("referenced_digest_item")
    offer = _offer(brief)
    cust = brief.get("customer")
    has_details = brief["trigger"].get("details_available", True)

    # ------------------------------------------------------------- customer-facing
    if cust:
        name = (cust.get("name") or "there").split(" (")[0]
        parent = None
        if "(parent:" in (cust.get("name") or ""):
            parent = cust["name"].split("(parent:")[1].strip(" )")
        hello = f"Hi {parent}" if parent else f"Hi {name}"
        owner = m["salutation"] if m.get("owner_first_name") else None
        prefix = f"{hello}, {owner} from {biz} here." if owner else f"{hello}, {biz} here."
        if not has_details and kind not in ("appointment_tomorrow", "customer_lapsed_soft", "customer_lapsed_hard"):
            kind = "_generic_customer"
        if kind == "recall_due":
            slots = [s.get("label") for s in p.get("available_slots") or [] if s.get("label")]
            service = humanize(p.get("service_due", "check-up")).replace("6 month", "6-month")
            body = f"{prefix} Your {service} is due"
            due = parse_dt(p.get("due_date"))
            body += f" ({fmt_date(due)})." if due else "."
            if slots:
                opts = " or ".join(f"{s}" for s in slots[:2])
                body += (f" Apke liye slots ready hain: {opts}." if _hinglish(brief) else f" We have kept these slots for you: {opts}.")
            if offer:
                body += f" {offer}."
            if len(slots) >= 2:
                body += " Reply 1 for the first slot, 2 for the second, or tell us a time that works."
            else:
                body += " Reply YES to book, or tell us a time that works."
            return _pack(body, "multi_choice_slot" if len(slots) >= 2 else "binary_yes_no",
                         "Customer recall on the merchant's behalf with real slots and the active offer.")
        if kind == "chronic_refill_due":
            meds = ", ".join(p.get("molecule_list") or []) or "regular medicines"
            runout = parse_dt(p.get("stock_runs_out_iso"))
            who = f"{name} ji" if cust.get("senior_citizen") else name
            if _hinglish(brief):
                body = f"Namaste — {biz} yahan. {who} ki medicines ({meds})"
                body += f" {fmt_date(runout)} ko khatam hongi." if runout else " refill ke liye due hain."
                body += " Same dose, same pack ready rakh sakte hain."
                if p.get("delivery_address_saved"):
                    body += " Saved address par home delivery ho jayegi."
            else:
                body = f"Namaste — {biz} here. {who}'s medicines ({meds})"
                body += f" run out on {fmt_date(runout)}." if runout else " are due for a refill."
                body += " We can keep the same dose and pack ready."
                if p.get("delivery_address_saved"):
                    body += " Home delivery to the saved address."
            extras = [o for o in m["active_offers"] if "senior" in o.lower() or "delivery" in o.lower()]
            if extras:
                body += f" ({'; '.join(extras)} applies.)"
            body += " Reply CONFIRM to dispatch, or tell us if the dosage has changed."
            return _pack(body, "binary_confirm_cancel", "Chronic refill reminder with the exact molecules and run-out date.")
        if kind == "trial_followup":
            opts = [s.get("label") for s in p.get("next_session_options") or [] if s.get("label")]
            body = f"{prefix} Thank you for coming in for the trial."
            if opts:
                body += f" The next session is {opts[0]} — shall we keep a spot for {name}? Reply YES to confirm."
            else:
                body += f" Shall we keep a spot for {name} in the next session? Reply YES and we'll share the timing."
            return _pack(body, "binary_yes_no", "Post-trial follow-up with the real next session option.")
        if kind == "wedding_package_followup":
            days = p.get("days_to_wedding")
            step = humanize(p.get("next_step_window_open", "your next bridal session"))
            body = f"{prefix}"
            if days:
                body += f" {days} days to go for your wedding —"
            body += f" this is the right window to start the {step}."
            body += " Shall we block your preferred Saturday slot next week? Reply YES."
            return _pack(body, "binary_yes_no", "Bridal follow-up keyed to the wedding countdown and next-step window.")
        if kind in ("customer_lapsed_hard", "customer_lapsed_soft"):
            focus = p.get("previous_focus") or (cust.get("preferences") or {}).get("training_focus")
            body = f"{prefix} It's been a while since your last visit — happens to all of us, no judgment."
            if focus:
                body += f" If {humanize(focus)} is still the goal, we'd love to help you restart."
            if offer:
                body += f" {offer} is on right now."
            body += " Want us to hold a slot for you this week? Reply YES — no commitment."
            return _pack(body, "binary_yes_no", "Warm, no-guilt win-back with an active offer and a single YES.")
        if kind == "appointment_tomorrow":
            body = (f"{prefix} Reminder: your appointment is tomorrow."
                    " Reply 1 to confirm or 2 if you'd like to reschedule.")
            return _pack(body, "multi_choice_slot", "Appointment reminder; no time is invented because none was provided.")
        orig_kind = brief["trigger"].get("kind") or ""
        pharmacy = brief["category"]["slug"] == "pharmacies"
        reason = {
            "chronic_refill_due": "your regular refill is due" if pharmacy else "your routine follow-up is due",
            "recall_due": "your routine check-up is due",
            "trial_followup": "thank you for trying us out — your next session is waiting",
            "wedding_package_followup": "your next bridal prep step is coming up",
        }.get(orig_kind, "it's been a little while since your last visit")
        body = f"{prefix} Quick reminder: {reason}."
        if offer:
            body += f" {offer} is on right now."
        body += " Reply YES and we'll lock a time that suits you."
        return _pack(body, "binary_yes_no",
                     "Trigger had no event details, so the customer note uses only the relationship and the merchant's active offer."
                     if not has_details else "Customer-facing note anchored on the merchant's active offer.")

    # ------------------------------------------------------------- merchant-facing
    if not has_details and kind not in ("curious_ask_due", "dormant_with_vera"):
        return _generic_merchant(brief, kind)

    # ------------------------------------------------------------- merchant-facing
    if kind == "research_digest" and item:
        body = f"{sal}, this week's digest has one item worth your time: {_item_line(item)}."
        if item.get("trial_n"):
            body += f" Trial size: {item['trial_n']:,}."
        if item.get("summary"):
            body += f" {item['summary'].split('. ')[0].rstrip('.')}."
        hr = (m["customer_aggregate"] or {}).get("high_risk_adult_count")
        if hr:
            body += f" Relevant to your {hr} high-risk adult patients."
        body += " Want me to pull the abstract and draft a patient WhatsApp you can share?"
        return _pack(body, "open_ended", "Research digest with source citation tied to the merchant's cohort.")
    if kind in ("regulation_change", "compliance") and item:
        body = f"{sal}, compliance heads-up: {_item_line(item)}."
        if item.get("summary"):
            body += f" {item['summary']}"
        if item.get("actionable"):
            body += f" Action: {item['actionable'].rstrip('.')}."
        body += " " + _yes_close(brief, "Want a 1-page checklist for your SOP file")
        return _pack(body, "binary_yes_no", "Regulation change with deadline and concrete action; checklist offered.")
    if kind == "cde_opportunity" and item:
        dt = parse_dt(item.get("date"))
        body = f"{sal}, {item.get('title', 'a CDE session')} ({item.get('source', '')})"
        if dt:
            body += f" on {fmt_date(dt)}"
        body += "."
        if item.get("credits"):
            body += f" {item['credits']} CDE credits."
        if item.get("actionable"):
            body += f" {item['actionable'].rstrip('.')}."
        body += " " + _yes_close(brief, "Shall I block your calendar")
        return _pack(body, "binary_yes_no", "CDE invite with date, credits and fee as given.")
    if kind == "supply_alert":
        batches = ", ".join(p.get("affected_batches") or [])
        body = f"{sal}, urgent: {p.get('molecule', 'medicine')} recall"
        if batches:
            body += f" — batches {batches}"
        if p.get("manufacturer"):
            body += f" by {p['manufacturer']}"
        body += "."
        if item and item.get("summary"):
            body += f" {item['summary'].split('. ')[0].rstrip('.')}."
        rx = (m["customer_aggregate"] or {}).get("chronic_rx_count")
        if rx:
            body += f" You have {rx} chronic-Rx customers; I can filter who got these batches."
        body += " " + _yes_close(brief, "Want me to draft the customer WhatsApp + replacement steps")
        return _pack(body, "binary_yes_no", "Recall alert with batch numbers; customer notice offered.")
    if kind == "ipl_match_today":
        match, venue = p.get("match", "Today's match"), p.get("venue")
        mdt = parse_dt(p.get("match_time_iso"))
        when = f"{mdt.strftime('%I:%M%p').lstrip('0').lower()}" if mdt else "tonight"
        weekend = mdt is not None and mdt.weekday() >= 5
        body = f"{sal}, {match}{' at ' + venue if venue else ''} today, {when}."
        if weekend:
            body += " Heads-up: weekend matches usually pull people to watch at home, so dine-in covers dip."
            body += f" Better play: push {offer} for delivery tonight." if offer else " Better play: a delivery-first push tonight."
        else:
            body += " Weeknight matches usually lift covers — good night for a match-night offer."
        body += " " + _yes_close(brief, "Want me to draft the banner + story")
        return _pack(body, "binary_yes_no", "IPL trigger with weekday/weekend judgment from the digest.")
    if kind in ("perf_dip", "seasonal_perf_dip"):
        metric = humanize(p.get("metric", "views"))
        delta = pct(p.get("delta_pct"), 0) if p.get("delta_pct") is not None else None
        body = f"{sal}, your {metric} are {'down ' + delta.lstrip('-') if delta else 'down'} over the last {p.get('window', '7d').replace('d', ' days')}."
        if kind == "seasonal_perf_dip" or p.get("is_expected_seasonal"):
            beat = next(iter(brief["category"]["seasonal_beats_now"]), None)
            body += " This is the normal seasonal lull" + (f" ({beat['note']})" if beat else "") + " — not a problem with your listing."
            members = (m["customer_aggregate"] or {}).get("total_active_members")
            body += f" Best use of this window: keep your {members} members engaged." if members else " Best use of this window: retention over new ads."
            body += " " + _yes_close(brief, "Want me to draft a 4-week member challenge")
        else:
            sig = m["signals"]
            if "unverified gbp" in sig:
                body += " Biggest fixable gap: your Google profile is unverified."
            elif not m["active_offers"] and _catalog(brief):
                body += f" You have no active offer right now — '{_catalog(brief)}' is the category's strongest hook."
            elif _peer_hook(brief):
                body += f" {_peer_hook(brief)}."
            body += " " + _yes_close(brief, "Want me to fix that today")
        return _pack(body, "binary_yes_no", "Performance dip with the most likely fixable cause.")
    if kind == "perf_spike":
        metric = humanize(p.get("metric", "calls"))
        delta = pct(p.get("delta_pct"), 0)
        body = f"{sal}, your {metric} are up {delta.lstrip('+') if delta else ''} this week"
        if p.get("likely_driver"):
            body += f", most likely from your {humanize(p['likely_driver'])}"
        body += ". Let's ride it while demand is high."
        body += " " + _yes_close(brief, "Want me to post a follow-up this week")
        return _pack(body, "binary_yes_no", "Spike celebrated with the number and driver; capitalise with one post.")
    if kind == "milestone_reached":
        body = f"{sal}, you're at {p.get('value_now')} {humanize(p.get('metric', 'reviews')).replace('review count', 'reviews')}"
        if p.get("milestone_value") and p.get("is_imminent"):
            body += f" — only {p['milestone_value'] - p['value_now']} away from {p['milestone_value']}"
        body += "."
        body += " " + _yes_close(brief, "I can draft a short review-request message for your recent happy customers")
        return _pack(body, "binary_yes_no", "Imminent milestone with a concrete push to cross it.")
    if kind == "review_theme_emerged":
        body = f"{sal}, {p.get('occurrences_30d', 'several')} reviews in the last 30 days mention '{humanize(p.get('theme', 'an issue'))}'"
        if p.get("common_quote"):
            body += f" — e.g. \"{p['common_quote']}\""
        body += ". Worth fixing before it drags your rating."
        body += " " + _yes_close(brief, "Want me to draft a polite public reply + a fix note for your team")
        return _pack(body, "binary_yes_no", "Emerging review theme with quote and a drafted response.")
    if kind == "renewal_due":
        body = f"{sal}, your {p.get('plan', m['subscription'].get('plan', ''))} plan renews in {p.get('days_remaining', m['subscription'].get('days_remaining'))} days"
        if p.get("renewal_amount"):
            body += f" (₹{p['renewal_amount']:,})"
        body += "."
        perf = m["performance_30d"]
        if perf.get("views"):
            body += f" In the last 30 days your listing got {perf['views']} views and {perf.get('calls', 0)} calls."
        body += " " + _yes_close(brief, "Want me to keep it running without a break")
        return _pack(body, "binary_yes_no", "Renewal with plan, days and amount, anchored on the merchant's own results.")
    if kind == "winback_eligible":
        body = f"{sal}, it's been {p.get('days_since_expiry', '')} days since your plan paused."
        if p.get("perf_dip_pct") is not None:
            body += f" Since then your calls are {pct(p['perf_dip_pct'], 0).lstrip('-')} down"
            if p.get("lapsed_customers_added_since_expiry"):
                body += f" and {p['lapsed_customers_added_since_expiry']} more customers have lapsed"
            body += "."
        body += " " + _yes_close(brief, "Want me to restart your profile upkeep this week")
        return _pack(body, "binary_yes_no", "Win-back with what changed since expiry.")
    if kind == "dormant_with_vera":
        hook = _peer_hook(brief)
        body = f"{sal}, quick one for {biz}."
        body += f" {hook}." if hook else " I spotted a couple of easy wins on your listing."
        body += " " + _yes_close(brief, "Want me to share the 2-minute fix")
        return _pack(body, "binary_yes_no", "Re-engagement with one specific, useful account fact.")
    if kind == "gbp_unverified":
        up = pct(p.get("estimated_uplift_pct"), 0)
        body = f"{sal}, your Google profile is still unverified."
        if up:
            body += f" Verified listings typically see about {up} more visibility."
        if p.get("verification_path"):
            body += f" It's a {humanize(p['verification_path']).replace(' or ', '/')} step."
        body += " " + _yes_close(brief, "Want me to walk you through it in 5 minutes")
        return _pack(body, "binary_yes_no", "Unverified GBP with the stated uplift and a guided fix.")
    if kind == "competitor_opened":
        body = f"{sal}, {p.get('competitor_name', 'a new competitor')} opened {p.get('distance_km', '')} km away"
        if p.get("their_offer"):
            body += f" with {p['their_offer']}"
        body += "."
        if offer:
            body += f" Your {offer} is still a strong hook — let's make sure it's front and centre on Google."
        body += " " + _yes_close(brief, "Want me to post it this week")
        return _pack(body, "binary_yes_no", "Competitor opening with factual comparison to the merchant's offer.")
    if kind == "festival_upcoming":
        fdt = parse_dt(p.get("date"))
        body = f"{sal}, {p.get('festival', 'the festival')} is on {fmt_date(fdt) if fdt else p.get('date', '')}"
        if p.get("days_until"):
            body += f" — {p['days_until']} days away"
        body += ". Early planning wins: lock your festive packages before the rush."
        hook = offer or _catalog(brief)
        if hook:
            body += f" I'd build it around {hook}."
        body += " " + _yes_close(brief, "Want me to draft the festive campaign")
        return _pack(body, "binary_yes_no", "Festival planning with timing judgment and a service+price anchor.")
    if kind == "curious_ask_due":
        noun = {"restaurants": "dish", "pharmacies": "product"}.get(brief["category"]["slug"], "service")
        trends = sorted((t for t in brief["category"]["trend_signals"] if isinstance(t.get("delta_yoy"), (int, float))),
                        key=lambda t: -t["delta_yoy"])
        body = f"Hi {sal}! Quick one — what's been the most asked-for {noun} at {biz} this week?"
        if trends:
            t = trends[0]
            body += f" Searches for '{t['query']}' are up {pct(t['delta_yoy'], 0)} YoY — seeing that too?"
        body += " Tell me and I'll turn it into a Google post + a ready WhatsApp reply for price questions."
        return _pack(body, "open_ended", "Asking-the-merchant lever with reciprocity.")
    if kind == "active_planning_intent":
        topic = humanize(p.get("intent_topic", "your plan"))
        body = f"{sal}, here's a starter draft for the {topic}:\n"
        base = offer or _catalog(brief)
        if base:
            body += f"- Built on your {base}\n"
        body += "- Clear timings, one price, simple booking on WhatsApp\n- Announced via a Google post + a customer WhatsApp\n"
        body += "Edit anything you like — shall I publish it?"
        return _pack(body, "binary_yes_no", "Merchant already asked; delivering a draft instead of qualifying.")
    if kind == "category_seasonal":
        trends = [humanize(t).replace(" +", " +").replace(" -", " -") for t in p.get("trends") or []]
        body = f"{sal}, seasonal shift this month: {', '.join(trends[:4])}." if trends else f"{sal}, seasonal demand is shifting this month."
        body += " Worth moving the rising items to counter visibility."
        body += " " + _yes_close(brief, "Want me to draft a customer WhatsApp about it")
        return _pack(body, "binary_yes_no", "Seasonal demand shift turned into a shelf action.")

    return _generic_merchant(brief, kind)


_GENERIC_OPENERS = {
    "perf_dip": "your listing numbers have slipped this week",
    "perf_spike": "your listing is getting extra attention this week — good moment to capitalise",
    "milestone_reached": "you're close to a listing milestone",
    "festival_upcoming": "festive season is the next big demand window",
    "competitor_opened": "a new competitor has opened near you",
    "review_theme_emerged": "a pattern is showing up in your recent reviews",
    "research_digest": "this week's category digest is out",
    "renewal_due": "your plan renewal is coming up",
}


def _generic_merchant(brief: dict, kind: str) -> dict:
    """Grounded nudge for triggers without event details: frame by kind, anchor on merchant facts only."""
    m = brief["merchant"]
    sal, biz = m["salutation"], m["business_name"]
    opener = _GENERIC_OPENERS.get(kind, f"quick update on {biz}")
    body = f"{sal}, {opener}."
    hook = _peer_hook(brief)
    if hook:
        body += f" {hook}."
    elif m["signals"]:
        body += f" Flag on your account: {m['signals'][0]}."
    base = _offer(brief)
    if base:
        body += f" Your {base} is the strongest hook to lead with."
    elif _catalog(brief):
        body += f" You have no live offer — a service+price hook like '{_catalog(brief)}' is the fastest fix."
    body += " " + _yes_close(brief, "Want me to set it up")
    has_details = brief["trigger"].get("details_available", True)
    return _pack(body, "binary_yes_no",
                 "Trigger had no event details, so the message anchors only on verified merchant facts." if not has_details
                 else "Grounded nudge built from the merchant's own numbers.")


def _pack(body: str, cta: str, rationale: str) -> dict:
    return {"body": " ".join(body.split(" ")).strip(), "cta": cta, "rationale": rationale}
