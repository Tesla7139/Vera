"""In-memory versioned context store plus conversation / merchant state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

SCOPES = ("category", "merchant", "customer", "trigger")


class ContextStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in SCOPES}

    def put(self, scope: str, context_id: str, version: int, payload: dict) -> tuple[bool, int | None]:
        """Returns (accepted, current_version_if_rejected)."""
        with self._lock:
            cur = self._data[scope].get(context_id)
            if cur is not None and cur["version"] >= version:
                return False, cur["version"]
            self._data[scope][context_id] = {"version": version, "payload": payload}
            return True, None

    def get(self, scope: str, context_id: str | None) -> dict | None:
        if not context_id:
            return None
        entry = self._data[scope].get(context_id)
        if entry is not None:
            return entry["payload"]
        if scope == "merchant":
            # The judge may key a merchant by something other than payload.merchant_id.
            for e in self._data[scope].values():
                if e["payload"].get("merchant_id") == context_id:
                    return e["payload"]
        if scope == "customer":
            for e in self._data[scope].values():
                if e["payload"].get("customer_id") == context_id:
                    return e["payload"]
        return None

    def counts(self) -> dict[str, int]:
        return {s: len(v) for s, v in self._data.items()}

    def clear(self) -> None:
        with self._lock:
            for s in SCOPES:
                self._data[s].clear()


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str | None
    customer_id: str | None
    trigger_id: str | None = None
    kind: str | None = None
    send_as: str = "vera"
    brief: dict | None = None
    turns: list[dict] = field(default_factory=list)  # {"from": "bot"|"merchant"|"customer", "body": str}
    status: str = "open"  # open | ended
    ended_reason: str | None = None
    committed: bool = False

    def bot_bodies(self) -> set[str]:
        return {t["body"].strip() for t in self.turns if t["from"] == "bot" and t.get("body")}


@dataclass
class MerchantState:
    auto_reply_streak: int = 0
    seen_inbound: dict[str, int] = field(default_factory=dict)  # normalized message -> count
    opted_out: bool = False
    wait_until_ts: float = 0.0
    sent_bodies: set[str] = field(default_factory=set)


class BotState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.conversations: dict[str, Conversation] = {}
        self.merchants: dict[str, MerchantState] = {}
        self.sent_suppression_keys: set[str] = set()
        self.customer_opt_out: set[str] = set()

    def merchant(self, merchant_id: str | None) -> MerchantState:
        key = merchant_id or "_unknown"
        if key not in self.merchants:
            self.merchants[key] = MerchantState()
        return self.merchants[key]

    def clear(self) -> None:
        with self.lock:
            self.conversations.clear()
            self.merchants.clear()
            self.sent_suppression_keys.clear()
            self.customer_opt_out.clear()
