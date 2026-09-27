"""Run magicpin's judge_simulator.py scenarios against the local bot.

The simulator refuses to start without an LLM key; its warmup and replay scenarios
(auto-reply, intent, hostile) don't use the LLM, so a stub provider is enough for them.
With a real key, run judge_simulator.py directly with TEST_SCENARIO="phase2_short".

    py tools/run_official_sim.py [--bot URL] [--pack "C:/Users/you/Downloads/magicpin-ai-challenge"]
"""

import argparse
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--bot", default="http://127.0.0.1:8080")
ap.add_argument("--pack", default=r"C:\Users\weird\Downloads\magicpin-ai-challenge")
args = ap.parse_args()
sys.path.insert(0, str(Path(args.pack)))
import judge_simulator as js  # noqa: E402

js.BOT_URL = args.bot.rstrip("/")


class Stub(js.LLMProvider):
    def complete(self, prompt, system=None):
        return "{}"

    def name(self):
        return "stub (no scoring)"


ok = js.JudgeSimulator(Stub()).run("all")
sys.exit(0 if ok else 1)
