"""Figure out what an Azure / Microsoft Foundry key + endpoint can reach. Never prints the key.

Put these two lines in .env (any endpoint URL form you have is fine):
    AZURE_ENDPOINT=https://<something>.azure.com/...
    AZURE_API_KEY=<key>
then run:  py tools/probe_azure.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vera import config  # noqa: E402,F401  (loads .env)

CLAUDE_GUESSES = ["claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                  "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5", "claude-opus-4-1"]


def get(url: str, key: str) -> tuple[int, dict | None]:
    req = request.Request(url, headers={"api-key": key})
    try:
        with request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def main() -> None:
    endpoint = os.getenv("AZURE_ENDPOINT") or os.getenv("ANTHROPIC_FOUNDRY_BASE_URL") or ""
    key = os.getenv("AZURE_API_KEY") or os.getenv("ANTHROPIC_FOUNDRY_API_KEY") or ""
    if not key or not (endpoint or os.getenv("ANTHROPIC_FOUNDRY_RESOURCE")):
        sys.exit("Add AZURE_ENDPOINT and AZURE_API_KEY to .env first.")
    host = urlparse(endpoint).hostname or ""
    resource = os.getenv("ANTHROPIC_FOUNDRY_RESOURCE") or host.split(".")[0]
    print(f"endpoint host: {host or '(none)'}  -> resource name: {resource}\n")

    found_claude, found_gpt = [], []

    # 1) Claude via the Foundry Anthropic endpoint
    import anthropic
    client = anthropic.AnthropicFoundry(api_key=key, resource=resource, timeout=30, max_retries=0)
    print("Checking for Claude deployments ...")
    auth_failed = False
    for name in CLAUDE_GUESSES:
        try:
            client.messages.create(model=name, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
            found_claude.append(name)
            print(f"  [YES] Claude deployment '{name}' answers")
        except anthropic.AuthenticationError:
            auth_failed = True
            break
        except (anthropic.NotFoundError, anthropic.BadRequestError):
            pass
        except anthropic.APIConnectionError:
            print("  Anthropic endpoint not reachable for this resource.")
            break
        except anthropic.APIStatusError as e:
            print(f"  '{name}': HTTP {e.status_code}")
    if auth_failed:
        print("  Key was rejected by the Claude endpoint (key may belong to a different resource/service).")
    if not found_claude:
        print("  No Claude deployment found under the standard names (it may use a custom deployment name).")

    # 2) OpenAI-style deployments on the same resource
    print("\nChecking for OpenAI / other deployments ...")
    for base in {f"https://{resource}.openai.azure.com", f"https://{resource}.cognitiveservices.azure.com",
                 f"https://{resource}.services.ai.azure.com"}:
        status, data = get(f"{base}/openai/deployments?api-version=2022-12-01", key)
        if status == 200 and data:
            for d in data.get("data", []):
                found_gpt.append((base, d.get("id"), d.get("model")))
                print(f"  [YES] {base}: deployment '{d.get('id')}' -> model '{d.get('model')}'")
    if not found_gpt:
        print("  None listed (or listing not permitted for this key).")

    # 3) What to put in .env
    print("\n=== Recommendation ===")
    if found_claude:
        best = found_claude[0]
        print("Claude is available - the bot can use it. Put these lines in .env:")
        print(f"  ANTHROPIC_FOUNDRY_API_KEY=<same key>\n  ANTHROPIC_FOUNDRY_RESOURCE={resource}\n  VERA_MODEL={best}")
    else:
        print("No Claude found with standard names. Check the Foundry portal (ai.azure.com -> your project ->")
        print("'Models + endpoints'): if a Claude model is listed, tell me its deployment name.")
    if found_gpt:
        base, dep, model = found_gpt[0]
        print("\nFor scoring with a GPT judge, also add:")
        print(f"  AZURE_OPENAI_ENDPOINT={base}\n  JUDGE_MODEL={dep}      # model: {model}")


if __name__ == "__main__":
    main()
