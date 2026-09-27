"""Score all 30 canonical test pairs with magicpin's own judge (judge_simulator.LLMScorer).

The bot's composer runs in-process (Claude via Foundry / Claude API if configured, templates otherwise),
and every message is scored by the simulator's strict judge prompt using OpenAI.

    py tools/score_pairs.py                       # judge picked from what's configured in .env
    py tools/score_pairs.py --only T09,T21        # a subset

Judge options (JUDGE_PROVIDER, auto-detected if unset):
  foundry-claude  Claude on your Foundry resource (reuses ANTHROPIC_FOUNDRY_* ; JUDGE_MODEL = deployment name)
  azure-openai    An OpenAI model deployed on Foundry/Azure: AZURE_OPENAI_ENDPOINT (https://<res>.openai.azure.com),
                  JUDGE_MODEL = deployment name, key from AZURE_OPENAI_API_KEY or ANTHROPIC_FOUNDRY_API_KEY
  openai          api.openai.com with OPENAI_API_KEY (JUDGE_MODEL default gpt-4o-mini, the simulator's default)
CHALLENGE_PACK: path to the starter pack (for judge_simulator.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vera import composer, config  # noqa: E402  (importing config loads .env)
from vera.facts import build_brief  # noqa: E402

PACK = Path(os.getenv("CHALLENGE_PACK", r"C:\Users\weird\Downloads\magicpin-ai-challenge"))
sys.path.insert(0, str(PACK))
import judge_simulator as js  # noqa: E402

D = ROOT / "data" / "expanded"
OUT = ROOT / "out"
DIMS = [("specificity", "Spec"), ("category_fit", "Cat"), ("merchant_fit", "Mer"),
        ("decision_quality", "Dec"), ("engagement_compulsion", "Eng")]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


class FoundryClaudeJudge(js.LLMProvider):
    """The simulator's judge prompt, answered by Claude on Microsoft Foundry."""

    def __init__(self, model: str):
        import anthropic
        self.model = model
        self.client = anthropic.AnthropicFoundry(timeout=90, max_retries=2)

    def name(self) -> str:
        return f"Claude on Foundry ({self.model})"

    def complete(self, prompt: str, system: str = None) -> str:
        kwargs = {"system": system} if system else {}
        resp = self.client.messages.create(model=self.model, max_tokens=4000,
                                           messages=[{"role": "user", "content": prompt}], **kwargs)
        return next((b.text for b in resp.content if b.type == "text"), "")


class AzureOpenAIJudge(js.LLMProvider):
    """The simulator's judge prompt, answered by an OpenAI deployment on Azure / Foundry."""

    def __init__(self, endpoint: str, deployment: str, key: str, api_version: str):
        self.url = (f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions"
                    f"?api-version={api_version}")
        self.deployment, self.key = deployment, key

    def name(self) -> str:
        return f"Azure OpenAI ({self.deployment})"

    def complete(self, prompt: str, system: str = None) -> str:
        from urllib import request as rq
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        req = rq.Request(self.url, data=json.dumps({"messages": msgs}).encode(),
                         headers={"api-key": self.key, "Content-Type": "application/json"})
        with rq.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]


def make_judge() -> js.LLMProvider:
    choice = os.getenv("JUDGE_PROVIDER") or (
        "openai" if os.getenv("OPENAI_API_KEY") else
        "azure-openai" if os.getenv("AZURE_OPENAI_ENDPOINT") else
        "foundry-claude" if config.FOUNDRY else "")
    if choice == "openai":
        return js.OpenAIProvider(os.environ["OPENAI_API_KEY"], os.getenv("JUDGE_MODEL", ""))
    if choice == "azure-openai":
        key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("ANTHROPIC_FOUNDRY_API_KEY")
        if not (os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("JUDGE_MODEL") and key):
            sys.exit("azure-openai judge needs AZURE_OPENAI_ENDPOINT, JUDGE_MODEL (deployment) and a key.")
        return AzureOpenAIJudge(os.environ["AZURE_OPENAI_ENDPOINT"], os.environ["JUDGE_MODEL"], key,
                                os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    if choice == "foundry-claude":
        return FoundryClaudeJudge(os.getenv("JUDGE_MODEL") or config.MODEL)
    sys.exit("No judge configured: fill ANTHROPIC_FOUNDRY_* (or AZURE_OPENAI_* / OPENAI_API_KEY) in .env.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    judge = make_judge()
    scorer = js.LLMScorer(judge, dataset=None)
    js.print_llm = lambda *_: None  # silence per-call chatter

    cats = {p.stem: load(p) for p in (D / "categories").glob("*.json")}
    pairs = load(D / "test_pairs.json")["pairs"]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    print(f"composer: {config.PROVIDER if config.LLM_ENABLED else 'templates'} ({config.MODEL}) | judge: {judge.name()}\n")

    rows, md = [], ["# Judge scores (judge_simulator.LLMScorer)\n",
                    f"Composer: {'Claude ' + config.MODEL + ' via ' + config.PROVIDER if config.LLM_ENABLED else 'templates'} · "
                    f"Judge: {judge.name()}\n",
                    "| Test | Kind | Spec | Cat | Mer | Dec | Eng | Total | via |", "|---|---|---|---|---|---|---|---|---|"]
    details = []
    for p in pairs:
        if only and p["test_id"] not in only:
            continue
        trg = load(D / "triggers" / f"{p['trigger_id']}.json")
        mer = load(D / "merchants" / f"{p['merchant_id']}.json")
        cust = load(D / "customers" / f"{p['customer_id']}.json") if p.get("customer_id") else None
        cat = cats.get(mer["category_slug"], {})
        brief = build_brief(cat, mer, trg, cust, None)
        t0 = time.time()
        msg = asyncio.run(composer.compose(brief, set()))
        dt = time.time() - t0
        action = {"body": msg["body"], "cta": msg["cta"], "send_as": brief["send_as"]}
        s = scorer.score(action, cat, mer, trg, cust)
        vals = [getattr(s, k) for k, _ in DIMS]
        rows.append((p["test_id"], trg["kind"], vals, s.total, msg["composer"]))
        print(f"{p['test_id']} {trg['kind']:26} " + " ".join(f"{v:2}" for v in vals)
              + f"  = {s.total:2}/50  [{msg['composer']}, {dt:.1f}s]")
        md.append(f"| {p['test_id']} | {trg['kind']} | " + " | ".join(str(v) for v in vals)
                  + f" | **{s.total}** | {msg['composer']} |")
        details.append(f"### {p['test_id']} · {trg['kind']} — {s.total}/50\n\n{msg['body']}\n\n"
                       + "\n".join(f"- **{label}** {getattr(s, k)}: {getattr(s, k.replace('engagement_compulsion', 'engagement') + '_reason', '')}"
                                   for k, label in DIMS)
                       + (f"\n- *Hint:* {s.hint}" if s.hint else "") + "\n")

    if rows:
        n = len(rows)
        avgs = [sum(r[2][i] for r in rows) / n for i in range(5)]
        total = sum(r[3] for r in rows) / n
        print("\navg      " + " ".join(f"{label}={a:.1f}" for (_, label), a in zip(DIMS, avgs)) + f"   TOTAL {total:.1f}/50 ({total * 2:.0f}%)")
        worst = sorted(rows, key=lambda r: r[3])[:5]
        print("lowest:  " + ", ".join(f"{r[0]} {r[1]} ({r[3]})" for r in worst))
        md.append(f"| **avg** | | {' | '.join(f'{a:.1f}' for a in avgs)} | **{total:.1f}** | |\n")
        OUT.mkdir(exist_ok=True)
        (OUT / "scores.md").write_text("\n".join(md) + "\n\n## Details\n\n" + "\n".join(details), encoding="utf-8")
        print(f"\nWrote {OUT / 'scores.md'}")


if __name__ == "__main__":
    main()
