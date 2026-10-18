"""Read-side views. The suggester only sees `SlotView`s, so the same scoring code runs on
the in-process engine (tests, eval) and on preferences loaded back from Neo4j (service)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .context import parse_key


@dataclass
class SlotView:
    owner: str
    observed_in: str
    category: str
    context_key: str
    active_action: str
    pref_id: str
    origin: str
    valid_from: str
    valid_from_session: str
    reason: str
    supersedes_action: str | None
    dist: dict                      # action -> decayed weight (as of the owner's latest session)
    evidence: list = field(default_factory=list)   # [(ref, kind, ts)] for the active action, newest first
    explicit_refs: list = field(default_factory=list)
    support_count: int = 0
    rejections: int = 0
    counts: dict = field(default_factory=dict)     # cumulative (undecayed, never reset) positive counts

    @property
    def total(self) -> float:
        return sum(self.dist.values())


class EngineProfile:
    def __init__(self, engine):
        self.e = engine

    def slot(self, owner, observed_in, category, ctx_key) -> SlotView | None:
        s = self.e.slots.get((owner, observed_in, category, ctx_key))
        if s is None or s.active is None:
            return None
        f = self.e.age_factor(s)
        a = s.active
        ev = sorted(s.evidence[a.action], key=lambda x: x.ts, reverse=True)
        return SlotView(owner, observed_in, category, ctx_key, a.action, a.pref_id, a.origin, a.valid_from,
                        a.valid_from_session, a.reason, a.supersedes_action,
                        {k: v * f for k, v in s.w.items() if v > 1e-9},
                        [(x.ref, x.kind, x.ts) for x in ev], list(dict.fromkeys(a.explicit_refs)),
                        s.n_pos.get(a.action, 0), s.n_rej.get(a.action, 0), dict(s.n_pos))

    def observed(self, owner, category) -> set:
        return self.e.observed.get((owner, category), set())

    def proposals(self):
        return self.e.proposals


class StaticProfile:
    """Built from rows (e.g. loaded from Neo4j)."""

    def __init__(self, views: list[SlotView], proposals: list[dict] | None = None):
        self.views = {(v.owner, v.observed_in, v.category, v.context_key): v for v in views}
        self._obs: dict = {}
        for v in views:
            for k, val in parse_key(v.context_key).items():
                self._obs.setdefault((v.owner, v.category), set()).add((k, val))
        self._proposals = proposals or []

    def slot(self, owner, observed_in, category, ctx_key):
        return self.views.get((owner, observed_in, category, ctx_key))

    def observed(self, owner, category):
        return self._obs.get((owner, category), set())

    def proposals(self):
        return self._proposals
