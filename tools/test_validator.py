"""Offline checks for the LLM path: validator catches fabrication, repair loop, fallback."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vera import composer, llm  # noqa: E402
from vera.facts import build_brief  # noqa: E402

D = ROOT / "data" / "expanded"
cat = json.loads((D / "categories" / "dentists.json").read_text(encoding="utf-8"))
mer = json.loads((D / "merchants" / "m_001_drmeera_dentist_delhi.json").read_text(encoding="utf-8"))
trg = json.loads((D / "triggers" / "trg_001_research_digest_dentists.json").read_text(encoding="utf-8"))
brief = build_brief(cat, mer, trg, None, None)

good = ("Dr. Meera, JIDA Oct 2026 p.14: a 2,100-patient trial found 3-month fluoride varnish recall cut caries "
        "recurrence 38% vs 6-month in high-risk adults. That's directly relevant to your 124 high-risk adult patients. "
        "Want me to pull the abstract and draft a patient WhatsApp?")
fabricated = "Dr. Meera, 37 dentists in Lajpat Nagar already switched to 3-month recall. Want the list?"
url = "Dr. Meera, read the JIDA study at www.jida.in — want a summary?"

assert composer.validate(good, brief, set()) == [], composer.validate(good, brief, set())
probs = composer.validate(fabricated, brief, set())
assert any("numbers not present" in p for p in probs), probs
assert any("URL" in p for p in composer.validate(url, brief, set()))
assert any("identical" in p for p in composer.validate(good, brief, {good}))
print("validator: good passes; fabrication, URL and repeat are caught")


async def run(drafts):
    calls = iter(drafts)

    async def fake(system, user, schema, max_tokens=2000, model=None):
        return next(calls, None)

    llm.complete_json = fake
    composer.llm.complete_json = fake
    return await composer.compose(brief, set())


mk = lambda b: {"body": b, "cta": "open_ended", "template_params": ["Dr. Meera", b], "rationale": "r"}
out = asyncio.run(run([mk(good)]))
assert out["composer"] == "llm" and out["body"] == good
out = asyncio.run(run([mk(fabricated), mk(good)]))
assert out["composer"] == "llm_repaired", out
out = asyncio.run(run([mk(fabricated), mk(fabricated)]))
assert out["composer"] == "template", out
print("compose: clean draft used; bad draft repaired; twice-bad draft falls back to template")
print("fallback body:", out["body"])
