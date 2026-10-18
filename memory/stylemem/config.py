"""All tunables in one place. Defaults = the config selected by `python -m stylemem.evaluate --grid`
using TRAIN-ONLY prequential accuracy + the training-period drift (see SELECTION_RULE)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
MEMORY_DIR = REPO / "memory"
DOCS = MEMORY_DIR / "docs"
RESULTS = DOCS / "results"

CONTEXT_KEYS = ("footage_type", "prev_edit", "format", "section")
CATEGORIES = ("transition", "font", "color_grade", "cut_length", "caption_style", "music_mood")


@dataclass(frozen=True)
class Params:
    # time decay: weights halve every `half_life_sessions` sessions of the owner
    half_life_sessions: float = 1e9       # 1e9 = slow weights do not decay (selected on train)
    # "owner_session": every session of the owner ages every slot (wall-clock-like);
    # "slot_session": a slot only ages when a new session brings evidence to it
    # (recency among that context's own observations; unused taste does not evaporate)
    decay_clock: str = "owner_session"
    # supersession (change detection on a FAST window): a challenger must lead the
    # fast-decayed weights with >= supersede_min_share and >= min_support, in N consecutive
    # sessions that brought evidence to the slot. On supersession the slot's slow weights
    # restart from the fast window, so the old style does not linger.
    fast_half_life_sessions: float = 3.0
    supersede_min_share: float = 0.6
    supersede_after_sessions: int = 2
    # evidence weights
    w_override: float = 1.0      # user replaced the Director's choice (active choice)
    w_edit: float = 1.0          # user's own edit
    w_accept: float = 0.7        # user accepted the Director's choice (passive)
    w_reject: float = 0.5        # subtracted from an action the user overrode/undid
    w_explicit: float = 3.0      # pseudo-count for an explicit chat statement
    # confidence = Wilson lower bound of the decayed share with n = decayed support
    wilson_z: float = 1.28
    abstain_below: float = 0.5
    # "specific_first": the most specific context level that has a confident, non-stale
    #   preference wins (the agreed fallback design); "max_confidence": highest confidence
    #   over all sub-contexts regardless of specificity
    #   "backoff_chain": like specific_first but along ONE chain that drops the least
    #   informative key first (mutual information of key vs action, from the graph)
    #   "max_information": among confident non-stale contexts, the one whose keys carry the
    #   most information about the action (sum of per-key mutual information)
    slot_selection: str = "backoff_chain"
    # "level_first": deepest confident level across pools (scope-specific and pooled 'any');
    # "scope_first": exhaust the scope-specific pool (e.g. ben's team-scope work) before 'any'
    pool_order: str = "level_first"
    min_support: float = 1.5     # a slot needs this much decayed weight to be used / opened
    conflict_min_conf: float = 0.3
    max_evidence_per_pref: int = 60


DEFAULT = Params()


@dataclass
class Neo4jConfig:
    uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.environ.get("NEO4J_PASSWORD", "cardboard-demo"))
    database: str = field(default_factory=lambda: os.environ.get("NEO4J_DATABASE", "neo4j"))
