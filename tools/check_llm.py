"""One real composition through the configured provider: checks credentials, deployment name,
structured output and latency. Never prints secrets.

    py tools/check_llm.py
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vera import config, composer  # noqa: E402
from vera.facts import build_brief  # noqa: E402

logging.basicConfig(level=logging.WARNING)
print(f"provider={config.PROVIDER} model/deployment={config.MODEL} effort={config.EFFORT} enabled={config.LLM_ENABLED}")
if not config.LLM_ENABLED:
    sys.exit("No credentials found. Fill in .env (see .env.example).")

D = ROOT / "data" / "expanded"
load = lambda p: json.loads((D / p).read_text(encoding="utf-8"))
brief = build_brief(load("categories/dentists.json"), load("merchants/m_001_drmeera_dentist_delhi.json"),
                    load("triggers/trg_001_research_digest_dentists.json"), None, None)

t0 = time.time()
out = asyncio.run(composer.compose(brief, set()))
print(f"compose via {out['composer']} in {time.time() - t0:.1f}s")
print(out["body"])
if out["composer"] == "template":
    sys.exit("LLM path did not produce a valid draft (see warnings above) - template fallback was used.")
