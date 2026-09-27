"""Per-trigger-kind composition guidance.

Each entry: (guidance for the composer, default CTA shape).
Kinds not listed fall back to DEFAULT.
"""

PLAYBOOKS: dict[str, tuple[str, str]] = {
    # ---------------- merchant-facing: knowledge / external ----------------
    "research_digest": (
        "Lead with the single most relevant digest item (use trigger.referenced_digest_item if present). "
        "State the finding with its numbers (trial size, effect) and cite the source exactly as given (journal + issue/page). "
        "Tie it to THIS merchant's cohort or case-mix using customer_aggregate / signals (e.g. their high-risk count). "
        "Offer effort externalization: you will pull the abstract and draft a patient-facing WhatsApp. One question at the end.",
        "open_ended"),
    "regulation_change": (
        "Compliance alert. Name the rule change, the authority/source, the exact deadline and what concretely changes "
        "(before -> after values). Say who is affected and who is not. Offer a ready checklist/SOP note. "
        "Urgent but calm, no fear-mongering. End with one yes/no ask.",
        "binary_yes_no"),
    "cde_opportunity": (
        "Professional-development invite. Event title, speaker/organiser, date + time, credits, fee exactly as given. "
        "One line on why it is relevant to this practice. Offer to block the calendar / register. Binary ask.",
        "binary_yes_no"),
    "category_trend_movement": (
        "Share the trend with its exact delta and segment, connect it to an offer the merchant has (or a catalog offer "
        "they could add), and offer to update their Google profile / post. Binary ask.",
        "binary_yes_no"),
    "category_seasonal": (
        "Seasonal demand shift for this category. Quote the specific up/down movements from the payload/digest. "
        "Turn it into 2-3 concrete shelf/menu/service actions max, then offer to do one piece of work (post, customer message). "
        "Binary ask.",
        "binary_yes_no"),
    "festival_upcoming": (
        "Festival planning. Name the festival, date and days remaining. Show judgment about timing: if it is months away, "
        "frame it as early planning (lock packages/slots before competitors) rather than a promo today. "
        "Use a category-appropriate service+price offer from the merchant's active offers or the catalog (say 'could add' if it is not active). "
        "Offer to draft the campaign. Binary ask.",
        "binary_yes_no"),
    "competitor_opened": (
        "A competitor opened nearby. Give name, distance and their offer exactly as in the payload. Compare factually with "
        "the merchant's own active offer/strengths (ratings, review themes) - never disparage the competitor. "
        "Recommend one defensive move (e.g. highlight a strength, a matching service+price, a Google post). Binary ask.",
        "binary_yes_no"),
    "weather_heatwave": (
        "Weather event. Tie the condition to category-specific demand, recommend one timely action, offer to do it. Binary ask.",
        "binary_yes_no"),
    "local_news_event": (
        "Local event. Explain the concrete impact on this business today and one adjustment. Binary ask.",
        "binary_yes_no"),
    "ipl_match_today": (
        "IPL match today. Give teams, venue and time. APPLY JUDGMENT using the digest and derived facts: weekend matches "
        "shift people to home-watching (lower dine-in covers) while weeknight matches lift covers. If it is a weekend match, "
        "recommend a delivery/takeaway push using an existing active offer instead of a dine-in match-night promo; "
        "if weeknight, push the dine-in match-night offer. Offer to draft the banner/post. Binary ask.",
        "binary_yes_no"),
    # ---------------- merchant-facing: internal performance ----------------
    "perf_dip": (
        "Performance dip. State the metric, the drop and the window exactly. Diagnose using THIS merchant's signals "
        "(unverified profile, no active offers, stale posts, CTR vs peers) - pick the single most likely cause. "
        "Propose one fix you can execute (e.g. activate a catalog service+price offer, post, verify). Loss-aversion framing. Binary ask.",
        "binary_yes_no"),
    "perf_spike": (
        "Performance spike. Celebrate with the exact number and window, name the likely driver from the payload. "
        "Recommend how to capitalise while demand is high (one action). Binary ask.",
        "binary_yes_no"),
    "seasonal_perf_dip": (
        "Expected seasonal dip. Pre-empt anxiety: say the drop is normal for this season (use digest/seasonal beats), "
        "quote their exact drop, recommend shifting focus (e.g. retention of their existing members/customers with the count) "
        "instead of acquisition spend now. Offer one concrete retention piece. Binary ask.",
        "binary_yes_no"),
    "milestone_reached": (
        "Milestone. Give the current value and the milestone (and how many remain if imminent). "
        "Offer a concrete push to cross it (e.g. a review-request message to recent happy customers) that you will draft. Binary ask.",
        "binary_yes_no"),
    "review_theme_emerged": (
        "Review pattern. Quote the theme, count and window, and the customer quote if given. Recommend one operational fix "
        "and offer to draft a polite public response template. Binary ask.",
        "binary_yes_no"),
    "renewal_due": (
        "Subscription renewal. Days remaining, plan and renewal amount exactly as given. Connect to what they would lose "
        "using their own numbers (views/calls), but no scare tactics. Binary YES to renew.",
        "binary_yes_no"),
    "winback_eligible": (
        "Lapsed subscriber win-back. Days since expiry and what has happened since (their perf change, lapsed customers added) "
        "with exact numbers. One reason to restart now and a low-friction restart. Binary ask.",
        "binary_yes_no"),
    "dormant_with_vera": (
        "Merchant has gone quiet. Do not guilt them. Re-open with one genuinely useful, specific fact about their account "
        "(best: a peer comparison or a missed-demand signal), then a tiny, easy question or YES ask.",
        "binary_yes_no"),
    "gbp_unverified": (
        "Google profile not verified. Give the estimated uplift exactly as given and the verification path. "
        "Offer to walk them through it in a few minutes. Binary ask.",
        "binary_yes_no"),
    "curious_ask_due": (
        "Curiosity / asking-the-merchant conversation. The point is to ASK, not to tell: open with ONE specific question "
        "about what customers are asking for this week. Do not claim you know what is in demand. You may add one short, "
        "clearly-labelled guess anchored on their own active offer (e.g. 'still the Haircut @ ₹99, or something new?'). "
        "Offer reciprocity: you will turn their answer into a Google post + a ready reply template in 5 minutes. Open-ended.",
        "open_ended"),
    "active_planning_intent": (
        "The merchant ALREADY asked for this - do not qualify again. Deliver a concrete, editable first draft right now "
        "(short bulleted structure is fine), priced only from their active offers/catalog and facts in the brief; "
        "do not invent partner names, buildings or prices not in the brief. End with one approval question "
        "(e.g. publish/send it?).",
        "binary_yes_no"),
    "supply_alert": (
        "Urgent supply/recall alert. Molecule, batch numbers, manufacturer and source exactly as given; the risk level as stated "
        "in the digest (do not exaggerate). Use their chronic/repeat customer counts only as given - never invent an affected count. "
        "Offer to draft the customer notice and the replacement workflow. Binary ask.",
        "binary_yes_no"),
    # ---------------- customer-facing (sent on the merchant's behalf) ----------------
    "recall_due": (
        "Customer recall reminder on the merchant's behalf. Greet by name, name the clinic, say which service is due and "
        "when (from payload). Offer the exact available slots from the payload (labels as given) and the price from the "
        "merchant's active offer if relevant. Reply 1/2 style choice is allowed for booking, plus 'or tell us a time'. "
        "No medical claims.",
        "multi_choice_slot"),
    "appointment_tomorrow": (
        "Appointment reminder on the merchant's behalf. Greet by name, confirm it is tomorrow at the business, "
        "only mention a time if it is in the payload. Ask to reply 1 to confirm or 2 to reschedule.",
        "multi_choice_slot"),
    "customer_lapsed_soft": (
        "Gentle check-in with a customer who has not visited in a while. Warm, zero guilt, reference their past services "
        "if known, one relevant active offer, easy YES to book.",
        "binary_yes_no"),
    "customer_lapsed_hard": (
        "Win-back of a long-lapsed customer. Warm and no-judgment ('happens to everyone'), connect to their previous goal/focus "
        "from the payload, offer something low-commitment from the merchant's active offers, and remove friction "
        "('no commitment'). Single YES.",
        "binary_yes_no"),
    "trial_followup": (
        "Follow-up after a trial. Thank them, offer the next session options exactly as in the payload. If the customer is a child, "
        "address the parent. Single easy reply.",
        "multi_choice_slot"),
    "chronic_refill_due": (
        "Chronic prescription refill reminder. Respectful (use 'ji' for seniors, address the family member if the channel is via "
        "son/daughter). List the medicines and the run-out date from the payload. Mention delivery to the saved address and any "
        "applicable active offer (e.g. senior discount) - do NOT compute prices or totals that are not in the brief. "
        "Reply CONFIRM to dispatch.",
        "binary_confirm_cancel"),
    "wedding_package_followup": (
        "Bridal follow-up on the salon's behalf. Days to the wedding and the trial date from the payload; the next-step window "
        "(e.g. skin-prep program). Mention a price only if it is in the merchant's offers or catalog. Honour preferred slot. Single YES.",
        "binary_yes_no"),
}

DEFAULT = (
    "Explain in one line why you are messaging now (the trigger), anchor on one or two verifiable facts from the brief, "
    "recommend one concrete action you will take for them, and end with one low-effort question.",
    "binary_yes_no",
)

# Kinds where the message is informational enough that "open_ended" is acceptable.
CTA_VALUES = ["binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "open_ended", "none"]


def playbook_for(kind: str | None) -> tuple[str, str]:
    return PLAYBOOKS.get(kind or "", DEFAULT)
