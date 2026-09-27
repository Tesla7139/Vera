# Vera challenge bot — approach

**One idea drives the design: the bot may only say what the context proves.** Every message is built from a grounded fact sheet, written to a trigger-specific playbook, and checked before it is sent.

## Pipeline (per `/v1/tick` action)
1. **Fact sheet.** Built from category, merchant, trigger and customer. It resolves the digest item the trigger points to (`top_item_id` → JIDA p.14 and so on) and precomputes the comparisons a good message needs: CTR vs peer average, 7-day deltas as percentages, lapsed share, days to a deadline, weekday vs weekend for IPL, and reviews left to a milestone. It sets the salutation (`Dr. Meera`), picks a language style from `languages` or `language_pref` (Hinglish only where it fits), and keeps category voice, taboos, and only the seasonal beats for the current month.
2. **Playbook by trigger kind (29 kinds).** Tells the model what "good" means for each kind. `research_digest` means cite the source and tie it to the merchant's cohort. `ipl_match_today` means apply the weekend-vs-weeknight judgment from the digest. `active_planning_intent` means deliver the draft, don't ask more questions. `recall_due` means offer the real slots.
3. **LLM composer** (gpt-6-astra on Microsoft Foundry, low reasoning effort, strict JSON-schema output). Returns body, CTA, template params and rationale. I picked the model by measurement, not by name. Five deployments each wrote all 30 test pairs through the full pipeline, and two different judge models scored every message with the challenge's own judge prompt. gpt-6-astra came first (about 36/50 averaged over 3 runs, about 40/50 on triggers with real data). The code can also run Claude, via the Claude API or Foundry.
4. **Validator.** Every standalone number in the message must appear in the fact sheet, which catches fabricated stats, prices and counts. It also rejects URLs, taboo phrases, preambles, repeats of earlier messages, and any mention of Vera or magicpin in customer-facing text. A failed draft gets **one repair pass** with the list of problems; if that also fails, the bot uses a deterministic, grounded **template composer**. The same templates run everything when no API key is set, so the bot never goes silent.
5. **Holding back.** At most one merchant-facing message per merchant per tick, sorted by urgency, with the rest queued for later ticks. Suppression keys are never sent twice. Customer sends require consent. Opted-out merchants and customers, and merchants we promised to wait for, are skipped. Triggers without details (`{"placeholder": true}`) get a message built only from verified merchant facts; the event details are never invented.

## Replies (`/v1/reply`)
Rules decide first, and the LLM only writes the text:
- **Auto-reply** (canned phrasing, or the same text seen again). Detected per merchant across conversations: first a single nudge to the owner, then `wait` 24h, then `end`.
- **Opt-out** ends the conversation and suppresses future messages.
- **Hostile** gets one sincere apology and an offer to stop.
- **Off-topic** (e.g. GST) is declined politely, then the bot steers back to the open topic.
- **Commitment** ("ok let's do it", "haan kar do") switches straight to action mode. A check blocks qualifying questions here.
- **Customer slot pick** ("2") confirms the actual slot label.
- **"Later" / "busy"** returns a `wait` of the matching length.

The bot mirrors the language of each incoming message.

## Tradeoffs
- **Precision over flourish.** A true, specific line beats a vivid made-up one. The number check is strict, so a draft that invents a statistic gets repaired or replaced.
- **Deterministic by construction.** Rules, fact sheet, validator and templates are pure functions. LLM outputs are cached by input hash, so the same input returns the same message.
- **Latency.** Low effort, 14s LLM timeout, compositions run in parallel, and a template covers anything that runs out of time. Ticks and replies stay under the 30s limit.
- **State is in memory, with one worker**, as the brief allows. The host has to stay always-on.

## What would help most
Real open appointment slots per merchant, per-customer purchase history (e.g. which customers got a recalled batch), and each offer's price breakdown. Several of the strongest messages depend on facts like these, and the bot deliberately won't guess them.

## Run
```
pip install -r requirements.txt
set ANTHROPIC_API_KEY=...        # optional; without it the template composer runs
uvicorn vera.app:app --host 0.0.0.0 --port 8080
py tools/local_eval.py           # warmup + 30 test pairs + replay scenarios -> out/report.md
py tools/test_validator.py       # offline checks of the grounding validator
```
