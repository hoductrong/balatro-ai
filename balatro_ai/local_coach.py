"""Loopback-only Bionic/LM Studio coach. No Codex process or cloud fallback."""

from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .coach import _MAX_RESPONSE_BYTES, _RESPONSE_SCHEMA, CoachCallFailed
from .packet import compact_packet


def local_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Model API must be an HTTP loopback URL")
    return value.rstrip("/")


def http_json(url, body=None, timeout=10):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    # Never send local game packets through an environment HTTP proxy.
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as reply:
            raw = reply.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise CoachCallFailed("Local API response too large")
        return json.loads(raw)
    except HTTPError as exc:
        detail = exc.read(2000).decode(errors="replace")
        raise CoachCallFailed(f"Local API HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, OSError, URLError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc):
            raise TimeoutError("local model call timed out") from exc
        raise CoachCallFailed(f"Local API unavailable: {exc}") from exc
    except (ValueError, UnicodeError) as exc:
        raise CoachCallFailed("Local API returned malformed JSON") from exc


def action_schema(legal_types, *, followup=False):
    """Full canonical action grammar, never a shortlist of preferred moves."""
    integer = {"type": "integer", "minimum": 0}
    indices = {"type": "array", "items": integer}
    fields = {
        "play_cards": {"cards": {**indices, "minItems": 1, "maxItems": 5}},
        "discard_cards": {"cards": {**indices, "minItems": 1, "maxItems": 5}},
        "buy_shop_card": {"card": integer, "mode": {"type": "string", "enum": ["store", "use"]}},
        "buy_voucher": {"voucher": integer},
        "buy_pack": {"pack": integer},
        "sell_joker": {"joker": integer},
        "sell_consumable": {"consumable": integer},
        "use_consumable": {"consumable": integer, "targets": indices},
        "choose_pack_card": {"card": integer, "targets": indices},
        **{
            key: {"order": indices}
            for key in ("reorder_hand", "reorder_jokers", "reorder_consumables")
        },
        **{
            key: {}
            for key in (
                "select_blind",
                "skip_blind",
                "cash_out",
                "leave_shop",
                "reroll_shop",
                "reroll_boss",
                "skip_pack",
            )
        },
    }
    if followup:
        for kind in ("play_cards", "discard_cards", "reorder_hand"):
            fields.pop(kind)
        slot = {
            "anyOf": [
                integer,
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            ]
        }
        for properties in fields.values():
            for name in ("card", "consumable", "joker", "voucher", "pack"):
                if name in properties:
                    properties[name] = slot
            if "targets" in properties:
                properties["targets"] = {**indices, "maxItems": 0}
        fields["choose_pack_card"]["card"] = slot["anyOf"][1]
    branches = []
    for kind in legal_types or fields:
        if kind not in fields:
            continue
        properties = {"type": {"type": "string", "enum": [kind]}, **fields[kind]}
        branches.append(
            {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        )
    if not branches:
        raise CoachCallFailed("Packet contains no supported action types")
    return {"anyOf": branches}


class LocalCoach:
    reasoning_effort = "none"

    def __init__(
        self,
        model="balatro-llm",
        *,
        base_url="http://127.0.0.1:1234/v1",
        template="auto",
        model_key=None,
    ):
        self.identifier = model
        self.model = model_key or model
        self.base_url = local_url(base_url)
        if template not in {"auto", "chat", "qwen"}:
            raise ValueError("unknown local template")
        self.template = template
        self.last_timings = {}
        self.last_request_bytes = 0

    def preflight(self, timeout=10):
        models = http_json(self.base_url + "/models", timeout=timeout).get("data", [])
        if not any(item.get("id") == self.identifier for item in models):
            raise RuntimeError(f"Model {self.identifier!r} is not loaded in Bionic/LM Studio")

    def choose(self, packet, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        started = time.monotonic()
        compact = compact_packet(packet)
        system = compact.pop("instructions", "")
        system += (
            "\nLocal API wire format: action_json is a JSON OBJECT, not an escaped string. "
            "Use only the canonical fields. It will be encoded for the unchanged game validator. "
            "Copy request_id exactly. Keep plan brief; do not include calculations or private reasoning in plan. "
            "The then entries also use action_json OBJECTS with canonical fields only. "
            'For example select_blind is exactly {"type":"select_blind"}; never add a blind field. '
            "Follow-up hand actions are forbidden. Follow-up targets must be empty."
            ' A chained pack pick MUST use card={"key":"item_key"}, never an index. '
            "When the next choice depends on an unopened pack, return then=[] and reobserve."
        )
        observation = packet.get("observation", {})
        if observation.get("deck") and observation.get("stake"):
            system += (
                f"\nThis run: deck={observation['deck']}, stake={observation['stake']}. "
                "The current observation is authoritative; offline examples are conditional."
            )
        # Preserve all public numerical advice and examples. Only encoding changes.
        user = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        schema = deepcopy(_RESPONSE_SCHEMA)
        schema["properties"]["request_id"]["enum"] = [packet["request_id"]]
        schema["properties"]["action_json"] = action_schema(
            packet.get("analysis", {}).get("legal_action_types", [])
        )
        schema["properties"]["plan"]["maxLength"] = 2000
        schema["properties"]["explanation"]["maxLength"] = 500
        schema["properties"]["then"]["maxItems"] = 6
        schema["properties"]["then"]["items"]["properties"]["action_json"] = action_schema(
            [], followup=True
        )
        body = {
            "model": self.identifier,
            "temperature": 0,
            "max_tokens": 768,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "balatro_action", "strict": True, "schema": schema},
            },
        }
        template = self.template
        if template == "auto":
            template = "qwen" if "qwen3.6" in self.model.lower() else "chat"
        endpoint = "/chat/completions"
        if template == "qwen":
            # This installed MLX Qwen backend ignores the chat enable_thinking flag.
            body["prompt"] = (
                "<|im_start|>system\n" + system + "<|im_end|>\n"
                "<|im_start|>user\n" + user + "<|im_end|>\n"
                "<|im_start|>assistant\n<think>\n\n</think>\n\n"
            )
            body["stop"] = ["<|im_end|>"]
            endpoint = "/completions"
        else:
            body.update(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                chat_template_kwargs={"enable_thinking": False},
                reasoning_effort="none",
            )
        self.last_request_bytes = len(json.dumps(body, ensure_ascii=False).encode())
        reply = http_json(self.base_url + endpoint, body, timeout)
        self.last_timings = {
            "model_seconds": time.monotonic() - started,
            "usage": reply.get("usage", {}),
            "backend": "local",
            "template": template,
            "hedged": False,
        }
        try:
            choice = reply["choices"][0]
            content = choice["text"] if endpoint == "/completions" else choice["message"]["content"]
            if not isinstance(content, str) or len(content.encode()) > _MAX_RESPONSE_BYTES:
                raise ValueError("invalid response text")
            content = re.sub(r"^<think>.*?</think>\s*", "", content.strip(), flags=re.S)
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("expected an object")
            if isinstance(result.get("action_json"), dict):
                result["action_json"] = json.dumps(result["action_json"], separators=(",", ":"))
            if isinstance(result.get("then"), list):
                for entry in result["then"]:
                    if isinstance(entry, dict) and isinstance(entry.get("action_json"), dict):
                        entry["action_json"] = json.dumps(
                            entry["action_json"], separators=(",", ":")
                        )
            # The upstream runner remains the authority on request IDs and legality.
            return result
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CoachCallFailed(f"Local model returned invalid structured output: {exc}") from exc

    def close(self):
        pass
