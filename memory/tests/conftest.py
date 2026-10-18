import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stylemem import data  # noqa: E402
from stylemem.engine import MemoryEngine  # noqa: E402
from stylemem.extract import RuleBasedExtractor, _Labelled  # noqa: E402


@pytest.fixture(scope="session")
def train_engine():
    e = MemoryEngine(extractor=_Labelled(RuleBasedExtractor()))
    e.ingest(data.events("train"))
    return e


class Stream:
    """Tiny synthetic event builder for unit tests (not the shared world data)."""

    def __init__(self, subject="ava", scope="private", brand=None):
        self.subject, self.scope, self.brand = subject, scope, brand
        self.events, self.n, self.s = [], 0, 0

    def session(self, decisions, statement=None):
        """decisions: list of (category, context, final_action, kind) kind in override|accept|undo_only"""
        self.s += 1
        sid = f"t_{self.subject}_s{self.s:02d}"
        base = {"session_id": sid, "subject": self.subject, "actor": self.subject, "project_id": "proj_test",
                "branch": f"{self.subject}/t-{self.s}", "project_scope": self.scope, "brand_id": self.brand,
                "text": None, "version_id": None}
        if statement:
            self._ev(base, type="explicit_statement", category=None, context=None, action=None, replaced_action=None,
                     decision_id=None, op_id=None, chat_id=f"chat_t{self.s}", text=statement)
        for i, (cat, ctx, act, kind) in enumerate(decisions):
            did = f"{sid}_d{i}"
            agent = "hard_cut" if act != "hard_cut" else "slide"
            self._ev(base, type="agent_edit", category=cat, context=ctx, action=agent if kind != "accept" else act,
                     replaced_action=None, decision_id=did, op_id=f"proj_test/op_{self.n:04d}", chat_id=None)
            if kind == "override":
                self._ev(base, type="user_override", category=cat, context=ctx, action=act, replaced_action=agent,
                         decision_id=did, op_id=f"proj_test/op_{self.n:04d}", chat_id=None)
            elif kind == "accept":
                self._ev(base, type="user_accept", category=cat, context=ctx, action=act, replaced_action=None,
                         decision_id=did, op_id=f"proj_test/op_{self.n:04d}", chat_id=None)
        return sid

    def _ev(self, base, **kw):
        self.n += 1
        self.events.append({**base, **kw, "event_id": f"ev_t{self.n:05d}",
                            "ts": f"2026-06-{1 + self.s // 28:02d}T{(self.s % 24):02d}:{self.n % 60:02d}:00+05:30"})


@pytest.fixture
def stream():
    return Stream
