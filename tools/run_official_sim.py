"""Run magicpin's judge_simulator.py scenarios against the local bot.

The simulator refuses to start without an LLM key; its warmup and replay scenarios
(auto-reply, intent, hostile) don't use the LLM, so a stub provider is enough for them.
With a real key, run judge_simulator.py directly with TEST_SCENARIO="phase2_short".

    py tools/run_official_sim.py "C:/Users/you/Downloads/magicpin-ai-challenge"
"""

import sys
from pathlib import Path

pack = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\weird\Downloads\magicpin-ai-challenge")
sys.path.insert(0, str(pack))
import judge_simulator as js  # noqa: E402

js.BOT_URL = "http://127.0.0.1:8080"


class Stub(js.LLMProvider):
    def complete(self, prompt, system=None):
        return "{}"

    def name(self):
        return "stub (no scoring)"


ok = js.JudgeSimulator(Stub()).run("all")
sys.exit(0 if ok else 1)
