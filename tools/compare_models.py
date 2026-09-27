"""Pick the best writer model: every candidate composes all 30 test pairs through the real
pipeline (prompt -> validator -> repair -> template fallback), and two different judges score
every message with magicpin's judge prompt (judge_simulator.LLMScorer).

    py tools/compare_models.py
    py tools/compare_models.py --writers gpt-6-astra,gpt-5.5-prod --judges DeepSeek-V4-Pro
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from vera import composer, config  # noqa: E402
from vera.facts import build_brief  # noqa: E402
import score_pairs as sp  # noqa: E402  (also imports judge_simulator)

D = ROOT / "data" / "expanded"
OUT = ROOT / "out"
DIMS = ["specificity", "category_fit", "merchant_fit", "decision_quality", "engagement_compulsion"]


def cases():
    cats = {p.stem: sp.load(p) for p in (D / "categories").glob("*.json")}
    out = []
    for p in sp.load(D / "test_pairs.json")["pairs"]:
        trg = sp.load(D / "triggers" / f"{p['trigger_id']}.json")
        mer = sp.load(D / "merchants" / f"{p['merchant_id']}.json")
        cust = sp.load(D / "customers" / f"{p['customer_id']}.json") if p.get("customer_id") else None
        out.append((p["test_id"], trg, mer, cats.get(mer["category_slug"], {}), cust))
    return out


async def write_all(model: str | None, items):
    async def one(tid, trg, mer, cat, cust):
        brief = build_brief(cat, mer, trg, cust, None)
        t0 = time.time()
        if model is None:
            msg = composer.template_message(brief)
        else:
            msg = await composer.compose(brief, set(), model=model)
        return tid, brief["send_as"], msg, time.time() - t0
    return await asyncio.gather(*[one(*it) for it in items])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writers", default="gpt-6-astra,gpt-5.6-sol-prod,gpt-5.6-luna-prod,gpt-5.5-prod,DeepSeek-V4-Pro")
    ap.add_argument("--judges", default="DeepSeek-V4-Pro,gpt-5.5-prod")
    args = ap.parse_args()
    if config.PROVIDER != "azure_openai":
        sys.exit("compare_models.py compares Azure deployments; set AZURE_ENDPOINT + AZURE_API_KEY in .env.")
    writers = [w.strip() for w in args.writers.split(",") if w.strip()] + ["(templates)"]
    from urllib.parse import urlparse
    u = urlparse(config.AZURE_ENDPOINT)
    base = f"{u.scheme}://{u.hostname}"
    judges = {j: sp.js.LLMScorer(sp.AzureOpenAIJudge(base, j, config.AZURE_API_KEY, config.AZURE_API_VERSION), None)
              for j in [j.strip() for j in args.judges.split(",") if j.strip()]}
    sp.js.print_llm = lambda *_: None
    sp.js.print_warn = lambda *_: None
    items = cases()
    by_id = {it[0]: it for it in items}
    results = {}

    for w in writers:
        t0 = time.time()
        # Each asyncio.run() gets a fresh loop: drop loop-bound clients/semaphore from the previous one.
        from vera import llm
        llm._clients.clear()
        llm._sem = asyncio.Semaphore(config.LLM_CONCURRENCY)
        written = asyncio.run(write_all(None if w == "(templates)" else w, items))
        paths = [m["composer"] for _, _, m, _ in written]
        lat = [dt for *_, dt in written]
        print(f"\n== {w}: wrote 30 in {time.time() - t0:.0f}s | llm={paths.count('llm')} repaired={paths.count('llm_repaired')} "
              f"template_fallback={paths.count('template')} | max latency {max(lat):.1f}s")

        def judge_one(args_):
            (tid, send_as, msg, _), jname = args_
            _, trg, mer, cat, cust = by_id[tid]
            s = judges[jname].score({"body": msg["body"], "cta": msg["cta"], "send_as": send_as}, cat, mer, trg, cust)
            return tid, jname, s

        jobs = [(x, j) for x in written for j in judges]
        with ThreadPoolExecutor(max_workers=12) as ex:
            scored = list(ex.map(judge_one, jobs))
        per_judge = {j: [s.total for tid, jj, s in scored if jj == j] for j in judges}
        dims = {d: sum(getattr(s, d) for _, _, s in scored) / len(scored) for d in DIMS}
        overall = sum(s.total for _, _, s in scored) / len(scored)
        results[w] = {"overall": overall, "per_judge": {j: sum(v) / len(v) for j, v in per_judge.items()},
                      "dims": dims, "paths": {p: paths.count(p) for p in set(paths)}, "max_latency": max(lat),
                      "messages": {tid: msg["body"] for tid, _, msg, _ in written},
                      "per_test": {tid: sum(s.total for t2, _, s in scored if t2 == tid) / len(judges) for tid, *_ in written},
                      "reasons": {f"{tid}|{jj}": {"total": s.total, **{d: getattr(s, d) for d in DIMS},
                                                  "spec": s.specificity_reason, "cat": s.category_fit_reason,
                                                  "mer": s.merchant_fit_reason, "dec": s.decision_quality_reason,
                                                  "eng": s.engagement_reason, "hint": s.hint}
                                  for tid, jj, s in scored}}
        print(f"   overall {overall:.1f}/50  " + "  ".join(f"{j}: {v:.1f}" for j, v in results[w]["per_judge"].items())
              + "  | " + " ".join(f"{d[:4]}={v:.1f}" for d, v in dims.items()))

    ranking = sorted(results.items(), key=lambda kv: -kv[1]["overall"])
    print("\n=== Ranking (avg over both judges, /50) ===")
    for w, r in ranking:
        flag = " (judges itself: read the other judge's column)" if w in judges else ""
        print(f"  {r['overall']:5.1f}  {w:22} fallbacks={r['paths'].get('template', 0) if w != '(templates)' else '-'}"
              f"  max_latency={r['max_latency']:.1f}s{flag}")
    OUT.mkdir(exist_ok=True)
    (OUT / "model_comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT / 'model_comparison.json'}")


if __name__ == "__main__":
    main()
