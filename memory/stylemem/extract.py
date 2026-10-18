"""Chat statement -> explicit preference, in a FIXED JSON schema.

This is the only place an LLM is used. Structured VCS ops never go through here.

* `LLMExtractor` calls an OpenAI-compatible chat-completions endpoint (xAI or OpenAI) or
  the Anthropic messages API, asks for JSON in `STATEMENT_SCHEMA`, and validates the
  result against the vocab. Any failure falls back to the rule-based extractor for that
  statement and is recorded in `extractor`.
* `RuleBasedExtractor` is a clearly labelled FALLBACK: a hand-written lexicon over this
  demo's vocab. It is deterministic and adequate for these 15 synthetic statements; it is
  not a general solution and would not generalise to real chat.
"""
from __future__ import annotations

import json
import os
import re
from typing import Protocol

from . import data
from .config import CONTEXT_KEYS

STATEMENT_SCHEMA = {
    "type": "object",
    "required": ["is_preference", "kind", "target", "category", "action", "polarity",
                 "context", "scope_hint", "avoid_actions", "rationale"],
    "properties": {
        "is_preference": {"type": "boolean",
                          "description": "true only for a lasting style preference, not a one-off instruction or chatter"},
        "kind": {"enum": ["preference", "standard", "proposal", "instruction", "chatter"]},
        "target": {"enum": ["self", "team"], "description": "whose style it describes"},
        "category": {"enum": [None, "transition", "font", "color_grade", "cut_length", "caption_style", "music_mood"]},
        "action": {"type": ["string", "null"], "description": "must be one of vocab.actions[category]"},
        "polarity": {"enum": ["prefer", "avoid"]},
        "context": {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in CONTEXT_KEYS}},
        "scope_hint": {"enum": ["private", "team", "any"],
                       "description": "private = only for the speaker's own projects; any = everywhere"},
        "avoid_actions": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}


class StatementExtractor(Protocol):
    name: str

    def extract(self, text: str, actor: str, is_team_lead: bool, in_team_session: bool) -> dict: ...


def _empty(kind: str, rationale: str) -> dict:
    return {"is_preference": False, "kind": kind, "target": "self", "category": None, "action": None,
            "polarity": "prefer", "context": {k: None for k in CONTEXT_KEYS}, "scope_hint": "any",
            "avoid_actions": [], "rationale": rationale}


def validate(out: dict) -> dict:
    """Coerce/validate an extraction against the fixed schema and the vocab."""
    v = data.vocab()
    for k in STATEMENT_SCHEMA["required"]:
        if k not in out:
            raise ValueError(f"missing field {k}")
    ctx = {k: (out.get("context") or {}).get(k) for k in CONTEXT_KEYS}
    for k, val in ctx.items():
        if val is not None and val not in v["context_values"][k]:
            raise ValueError(f"bad context {k}={val}")
    out["context"] = ctx
    if out["is_preference"]:
        cat, act = out["category"], out["action"]
        if cat not in v["actions"] or act not in v["actions"][cat]:
            raise ValueError(f"action {act!r} not in vocab for {cat!r}")
    out["avoid_actions"] = [a for a in out.get("avoid_actions") or []
                            if out.get("category") in v["actions"] and a in v["actions"][out["category"]]]
    if out["target"] not in ("self", "team") or out["scope_hint"] not in ("private", "team", "any"):
        raise ValueError("bad target/scope_hint")
    return out


# ----------------------------------------------------------------------------- rule-based
_ACTION_LEXICON = [
    (r"whip ?pans?", "transition", "whip_pan"),
    (r"zoom ?punch", "transition", "zoom_punch"),
    (r"hard ?cuts?", "transition", "hard_cut"),
    (r"cross ?fades?", "transition", "crossfade"),
    (r"dip to black", "transition", "dip_to_black"),
    (r"word ?pop", "caption_style", "word_pop"),
    (r"beast ?mode", "caption_style", "beast_mode"),
    (r"stacked", "caption_style", "stacked"),
    (r"minimal modern", "caption_style", "minimal_modern"),
    (r"bebas neue", "font", "Bebas Neue"),
    (r"montserrat", "font", "Montserrat"),
    (r"\binter\b", "font", "Inter"),
    (r"playfair", "font", "Playfair Display"),
    (r"warm.{0,15}film|film.{0,15}warm", "color_grade", "warm_film"),
    (r"teal.{0,3}orange", "color_grade", "teal_orange"),
    (r"lo-?fi", "music_mood", "chill_lofi"),
]
_CONTEXT_LEXICON = [
    (r"jump cuts?", "prev_edit", "jump_cut"),
    (r"vertical|shorts\b|9:16", "format", "9:16"),
    (r"face shots?|talking head", "footage_type", "talking_head"),
    (r"b-?roll", "footage_type", "broll"),
    (r"\bintro\b|\bhook\b", "section", "hook"),
    (r"\boutro\b", "section", "outro"),
]
_PREF_MARKERS = r"\balways\b|\bi like\b|\bi prefer\b|\bprefer\b|\bstandard\b|\bkeep\b|on everything|lately|sticking with|\bis \w+ for\b|would do better|for team videos"
_COLD = r"no cold grades?"


class RuleBasedExtractor:
    """FALLBACK extractor (no LLM). Hand-written lexicon for this demo's vocab."""

    name = "rule_based_fallback"

    def extract(self, text: str, actor: str, is_team_lead: bool, in_team_session: bool) -> dict:
        t = text.lower()
        hits = [(cat, act) for pat, cat, act in _ACTION_LEXICON if re.search(pat, t)]
        if not hits:
            if re.match(r"^(make|can we|let's|please)\b", t):
                return validate(_empty("instruction", "one-off instruction, no named style action"))
            return validate(_empty("chatter", "no style action mentioned"))
        if not re.search(_PREF_MARKERS, t):
            return validate(_empty("instruction", "mentions a style but no lasting-preference marker"))
        cat, act = hits[0]
        ctx = {k: None for k in CONTEXT_KEYS}
        for pat, k, val in _CONTEXT_LEXICON:
            if re.search(pat, t):
                ctx[k] = val
        if cat == "caption_style" and ctx["section"]:
            ctx["section"] = None
        team_words = re.search(r"\bteam\b|\bour\b|northwind|\bbrand\b", t)
        target = "team" if team_words else "self"
        if target == "team":
            kind = "standard" if is_team_lead else "proposal"
            scope = "team"
        else:
            kind = "preference"
            scope = "private" if re.search(r"my own|my channel", t) else "any"
        avoid = ["teal_orange"] if (cat == "color_grade" and re.search(_COLD, t)) else []
        return validate({
            "is_preference": True, "kind": kind, "target": target, "category": cat, "action": act,
            "polarity": "prefer", "context": ctx, "scope_hint": scope, "avoid_actions": avoid,
            "rationale": f"lexicon match '{act}' ({cat}); context {[k for k, v in ctx.items() if v]}",
        })


# ----------------------------------------------------------------------------- LLM
_SYSTEM = (
    "You extract lasting video-editing style preferences from one chat message. "
    "Reply with ONLY a JSON object matching this schema: {schema}. "
    "Allowed actions per category: {actions}. Allowed context values: {ctxvals}. "
    "Use is_preference=false (kind 'instruction' or 'chatter') for one-off requests or small talk. "
    "kind='standard' only when a team lead sets a rule for the team; a non-lead talking about the "
    "team is kind='proposal'. scope_hint='private' only if the speaker limits it to their own projects/channel."
)


class LLMExtractor:
    """LLM extractor behind the same interface. Provider picked from env keys."""

    def __init__(self, fallback: StatementExtractor | None = None):
        self.fallback = fallback or RuleBasedExtractor()
        self.provider, self.model = self._detect()
        self.name = f"llm:{self.provider}:{self.model}" if self.provider else "unavailable"

    @staticmethod
    def _detect():
        model = os.environ.get("STYLEMEM_LLM_MODEL")
        if os.environ.get("XAI_API_KEY"):
            return "xai", model or "grok-3-mini"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai", model or "gpt-4o-mini"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic", model or "claude-3-5-haiku-latest"
        return None, None

    @property
    def available(self) -> bool:
        return self.provider is not None

    def _call(self, system: str, user: str) -> str:
        import httpx
        if self.provider == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": 400, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["content"][0]["text"]
        base = "https://api.x.ai/v1" if self.provider == "xai" else "https://api.openai.com/v1"
        key = os.environ["XAI_API_KEY" if self.provider == "xai" else "OPENAI_API_KEY"]
        r = httpx.post(f"{base}/chat/completions", timeout=30, headers={"Authorization": f"Bearer {key}"},
                       json={"model": self.model, "temperature": 0,
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def extract(self, text: str, actor: str, is_team_lead: bool, in_team_session: bool) -> dict:
        if not self.available:
            out = self.fallback.extract(text, actor, is_team_lead, in_team_session)
            out["extractor"] = self.fallback.name
            return out
        v = data.vocab()
        system = _SYSTEM.format(schema=json.dumps(STATEMENT_SCHEMA), actions=json.dumps(v["actions"]),
                                ctxvals=json.dumps(v["context_values"]))
        user = json.dumps({"speaker": actor, "speaker_is_team_lead": is_team_lead,
                           "said_in_team_project": in_team_session, "message": text})
        try:
            raw = self._call(system, user)
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            out = validate(json.loads(raw))
            out["extractor"] = self.name
            return out
        except Exception as exc:  # noqa: BLE001 - any failure -> labelled fallback
            out = self.fallback.extract(text, actor, is_team_lead, in_team_session)
            out["extractor"] = f"{self.fallback.name} (llm error: {type(exc).__name__})"
            return out


def default_extractor() -> StatementExtractor:
    """LLM if a key is present in env, otherwise the labelled rule-based fallback."""
    llm = LLMExtractor()
    return llm if llm.available else _Labelled(RuleBasedExtractor())


class _Labelled:
    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name

    def extract(self, *a, **kw):
        out = self.inner.extract(*a, **kw)
        out["extractor"] = self.name
        return out
