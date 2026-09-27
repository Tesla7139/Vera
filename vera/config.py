"""Runtime configuration, all overridable through environment variables."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from the project's .env (real environment variables win)."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# LLM providers (first match wins unless VERA_PROVIDER is set):
#   foundry       Claude on Microsoft Foundry: ANTHROPIC_FOUNDRY_API_KEY + ANTHROPIC_FOUNDRY_RESOURCE / _BASE_URL
#   anthropic     Claude API: ANTHROPIC_API_KEY
#   azure_openai  GPT / DeepSeek deployments on Azure / Foundry: AZURE_ENDPOINT + AZURE_API_KEY
#                 (AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY also accepted)
# With none set the bot still runs, using the deterministic template composer for every message.
FOUNDRY = bool(os.getenv("ANTHROPIC_FOUNDRY_API_KEY")) and bool(
    os.getenv("ANTHROPIC_FOUNDRY_RESOURCE") or os.getenv("ANTHROPIC_FOUNDRY_BASE_URL"))
ANTHROPIC = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_ENDPOINT") or ""
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_API_KEY") or ""
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
AZURE_OPENAI = bool(AZURE_ENDPOINT and AZURE_API_KEY)

PROVIDER = os.getenv("VERA_PROVIDER") or (
    "foundry" if FOUNDRY else "anthropic" if ANTHROPIC else "azure_openai" if AZURE_OPENAI else "none")
# Model id, or the deployment name on Foundry / Azure.
MODEL = os.getenv("VERA_MODEL") or ("gpt-6-astra" if PROVIDER == "azure_openai" else "claude-opus-5")
EFFORT = os.getenv("VERA_EFFORT", "low")  # low keeps latency well inside the judge's budget
LLM_TIMEOUT_S = float(os.getenv("VERA_LLM_TIMEOUT_S", "14"))
LLM_CONCURRENCY = int(os.getenv("VERA_LLM_CONCURRENCY", "20"))  # a full 20-action tick composes in one wave
LLM_ENABLED = os.getenv("VERA_DISABLE_LLM", "") == "" and PROVIDER != "none"

# Budgets (judge timeout is 30s; the API examples budget 10s for tick/reply).
TICK_BUDGET_S = float(os.getenv("VERA_TICK_BUDGET_S", "22"))
REPLY_BUDGET_S = float(os.getenv("VERA_REPLY_BUDGET_S", "18"))
MAX_ACTIONS_PER_TICK = 20
MAX_MERCHANT_SENDS_PER_TICK = int(os.getenv("VERA_MAX_MERCHANT_SENDS_PER_TICK", "1"))

# Identity for /v1/metadata.
TEAM_NAME = os.getenv("VERA_TEAM_NAME", "Sarthak Gupta")
TEAM_MEMBERS = [m.strip() for m in os.getenv("VERA_TEAM_MEMBERS", TEAM_NAME).split(",") if m.strip()]
CONTACT_EMAIL = os.getenv("VERA_CONTACT_EMAIL", "")
VERSION = "1.0.0"
SUBMITTED_AT = os.getenv("VERA_SUBMITTED_AT", "2026-09-27T00:00:00Z")
APPROACH = (
    "Grounded fact-sheet per (category, merchant, trigger, customer) -> trigger-kind playbook -> "
    "LLM composer with JSON schema -> validator (numbers must trace to context, taboo/URL/CTA/repeat checks) "
    "with one repair pass -> deterministic template fallback. Rule-first reply router for auto-replies, "
    "opt-outs, commitments, off-topic and deferrals; per-merchant state across conversations."
)
