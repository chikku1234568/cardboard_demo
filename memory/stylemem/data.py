"""Read-only loaders for the shared synthetic world in data/."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .config import DATA


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@lru_cache(maxsize=None)
def cast() -> dict:
    return json.loads((DATA / "world" / "cast.json").read_text())


@lru_cache(maxsize=None)
def brand_guidelines() -> dict:
    return json.loads((DATA / "world" / "brand_guidelines.json").read_text())


@lru_cache(maxsize=None)
def vocab() -> dict:
    return json.loads((DATA / "world" / "vocab.json").read_text())


@lru_cache(maxsize=None)
def ground_truth() -> dict:
    return json.loads((DATA / "memory" / "ground_truth.json").read_text())


@lru_cache(maxsize=None)
def sessions() -> dict[str, dict]:
    return {s["session_id"]: s for s in read_jsonl(DATA / "world" / "sessions.jsonl")}


@lru_cache(maxsize=None)
def chats() -> dict[str, dict]:
    return {c["chat_id"]: c for c in read_jsonl(DATA / "world" / "chat.jsonl")}


@lru_cache(maxsize=None)
def projects() -> dict[str, dict]:
    out = {}
    for p in sorted((DATA / "vcs" / "projects").glob("*/project.json")):
        d = json.loads(p.read_text())
        out[d["project_id"]] = d
    return out


@lru_cache(maxsize=None)
def ops_index() -> dict[str, dict]:
    """op_id -> op (the VCS op log is the shared input stream)."""
    out = {}
    for f in sorted((DATA / "vcs" / "projects").glob("*/ops.jsonl")):
        for op in read_jsonl(f):
            out[op["op_id"]] = op
    return out


def events(split: str | None = None) -> list[dict]:
    """Memory events. split in {None(all), 'train', 'heldout', 'flagship'}."""
    if split is None:
        path = DATA / "memory" / "events.jsonl"
    else:
        path = DATA / "memory" / "splits" / f"events_{split}.jsonl"
    evs = read_jsonl(path)
    evs.sort(key=lambda e: (e["ts"], e["event_id"]))
    return evs


def heldout_decisions() -> list[dict]:
    return read_jsonl(DATA / "memory" / "splits" / "decisions_heldout.jsonl")


def team_of(person: str) -> str | None:
    for tid, t in cast()["teams"].items():
        if person in t["members"]:
            return tid
    return None


def team_lead(team_id: str) -> str | None:
    return cast()["teams"].get(team_id, {}).get("lead")


def owner_kind(owner: str) -> str:
    if owner in cast()["teams"]:
        return "team"
    return "creator"


def category_of_action(action: str) -> str | None:
    for cat, acts in vocab()["actions"].items():
        if action in acts:
            return cat
    return None


def sorted_sessions_for(evs: Iterable[dict]) -> list[str]:
    first = {}
    for e in evs:
        first.setdefault(e["session_id"], e["ts"])
    return sorted(first, key=lambda s: first[s])
