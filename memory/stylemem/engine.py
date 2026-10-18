"""Streaming style-memory engine.

Input: memory events (derived from VCS op logs + chat). Every event carries an op_id or a
chat_id, and that id is what provenance points at.

* Structured ops -> deterministic updates (no LLM): each reviewed Director decision
  (accept / override / undo) or user edit becomes evidence for the user's final action
  in every sub-context of its context (specific -> broad lattice), per owner and per
  scope the project was in ("observed_in": private / team / any).
* Explicit chat statements and brand violations are applied IMMEDIATELY (flushed to the
  graph on arrival). Implicit evidence is aggregated and applied at session end.
* Decay: weights halve every `half_life_sessions` sessions of the owner.
* Supersession: one active Preference per slot (owner, observed_in, category, context).
  When another action leads the decayed weights in N consecutive sessions that brought
  evidence to the slot, the active preference is closed (valid_to) and a new one opened
  with a SUPERSEDES edge. An explicit statement for a different action supersedes at once.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable

from . import data
from .brand import Brand
from .config import DEFAULT, Params
from .context import key as ctx_key
from .context import normalize, parents, parse_key, subsets

POSITIVE = {"user_override": "override", "user_accept": "accept", "user_edit": "edit"}


@dataclass
class Evidence:
    ref: str            # op_id or chat_id
    ref_kind: str       # "op" | "chat"
    kind: str           # override | accept | edit | explicit | reject | undo
    ts: str
    session_id: str
    weight: float


@dataclass
class Preference:
    pref_id: str
    owner: str
    owner_kind: str
    scope: str                  # private (creator-owned) | team (team-owned) | brand
    observed_in: str            # private | team | any  (project scope the evidence came from)
    category: str
    action: str
    context_key: str
    origin: str                 # implicit | explicit
    valid_from: str
    valid_from_session: str
    reason: str
    polarity: str = "prefer"
    valid_to: str | None = None
    valid_to_session: str | None = None
    supersedes: str | None = None
    supersedes_action: str | None = None
    superseded_by: str | None = None
    explicit_refs: list = field(default_factory=list)
    # rolling stats (written on update)
    decayed_weight: float = 0.0
    slot_total: float = 0.0
    share: float = 0.0
    support_count: int = 0
    rejections: int = 0
    dist: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)


class Slot:
    __slots__ = ("owner", "observed_in", "category", "ctx", "w", "f", "t", "last_sid", "evidence", "n_pos", "n_rej",
                 "active", "challenger", "challenge", "history", "touched", "new_evidence", "new_rejects")

    def __init__(self, owner, observed_in, category, ctx, max_ev):
        self.owner, self.observed_in, self.category, self.ctx = owner, observed_in, category, ctx
        self.w: dict[str, float] = defaultdict(float)   # slow weights (confidence, share)
        self.f: dict[str, float] = defaultdict(float)   # fast weights (change detection)
        self.t = 0
        self.last_sid = None
        self.evidence: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_ev))
        self.n_pos: dict[str, int] = defaultdict(int)
        self.n_rej: dict[str, int] = defaultdict(int)
        self.active: Preference | None = None
        self.challenger: str | None = None
        self.challenge = 0
        self.history: list[Preference] = []
        self.touched = False
        self.new_evidence: list[Evidence] = []
        self.new_rejects: list[Evidence] = []

    def decay_to(self, idx: int, lam: float, lam_fast: float):
        if idx > self.t:
            k = idx - self.t
            for a in self.w:
                self.w[a] *= lam ** k
            for a in self.f:
                self.f[a] *= lam_fast ** k
            self.t = idx

    def add(self, action, w):
        self.w[action] += w
        self.f[action] += w

    def sub(self, action, w):
        self.w[action] = max(0.0, self.w.get(action, 0.0) - w)
        self.f[action] = max(0.0, self.f.get(action, 0.0) - w)

    def total(self) -> float:
        return sum(self.w.values())

    def leader(self, fast: bool = False) -> str | None:
        w = self.f if fast else self.w
        if not w:
            return None
        best = max(w.values())
        if best <= 0:
            return None
        if self.active and math.isclose(w.get(self.active.action, 0.0), best):
            return self.active.action
        return max(sorted(w), key=lambda a: w[a])


class Delta:
    """Pending graph writes, flushed to sinks per session (or immediately)."""

    def __init__(self):
        self.sessions, self.ops, self.chats, self.contexts = [], [], [], {}
        self.prefs, self.evidence, self.contra, self.supersedes = {}, [], [], []
        self.violations, self.proposals = [], []

    def empty(self) -> bool:
        return not any([self.sessions, self.ops, self.chats, self.contexts, self.prefs, self.evidence,
                        self.contra, self.supersedes, self.violations, self.proposals])


class MemoryEngine:
    def __init__(self, params: Params = DEFAULT, extractor=None, sinks: Iterable = (),
                 use_statements: bool = True, decay: bool = True):
        from .extract import default_extractor
        self.p = params
        self.extractor = extractor or default_extractor()
        self.sinks = list(sinks)
        self.use_statements = use_statements
        self.lam = 0.5 ** (1.0 / params.half_life_sessions) if decay else 1.0
        self.lam_fast = 0.5 ** (1.0 / params.fast_half_life_sessions)
        self.slots: dict[tuple, Slot] = {}
        self.owner_idx: dict[str, int] = defaultdict(int)
        self.owner_last_session: dict[str, str] = {}
        self.observed: dict[tuple, set] = defaultdict(set)     # (owner, category) -> {(k, v)}
        self.statements: list[dict] = []
        self.proposals: list[dict] = []
        self.violations: list[dict] = []
        self.supersessions: list[dict] = []
        self.sessions_done: list[str] = []
        self._buffers: dict[str, list] = defaultdict(list)
        self._session_meta: dict[str, dict] = {}
        self._session_last_ts: dict[str, str] = {}
        self._contexts_seen: set = set()
        self._pref_counter: dict[str, int] = defaultdict(int)
        self.delta = Delta()
        self.on_session_end: list[Callable] = []
        self.brands: dict[str, Brand] = {}

    # ------------------------------------------------------------------ helpers
    def brand(self, brand_id):
        if not brand_id:
            return None
        if brand_id not in self.brands:
            self.brands[brand_id] = Brand.load(brand_id)
        return self.brands[brand_id]

    def _ois(self, owner: str, scope: str) -> list[str]:
        if data.owner_kind(owner) == "team":
            return ["team"]
        return [scope, "any"]

    def _slot(self, owner, oi, cat, ctx, sid=None) -> Slot:
        k = (owner, oi, cat, ctx_key(ctx))
        s = self.slots.get(k)
        if s is None:
            s = Slot(owner, oi, cat, normalize(ctx), self.p.max_evidence_per_pref)
            s.t = self.owner_idx[owner]
            s.last_sid = sid
            self.slots[k] = s
            self._ensure_context(ctx)
        elif self.p.decay_clock == "slot_session":
            if sid is not None and sid != s.last_sid:
                for a in s.w:
                    s.w[a] *= self.lam
                for a in s.f:
                    s.f[a] *= self.lam_fast
                s.last_sid = sid
        else:
            s.decay_to(self.owner_idx[owner], self.lam, self.lam_fast)
        return s

    def age_factor(self, slot: Slot) -> float:
        """Extra decay to apply at read time (owner clock only)."""
        if self.p.decay_clock == "slot_session":
            return 1.0
        return self.lam ** (self.owner_idx[slot.owner] - slot.t)

    def _ensure_context(self, ctx):
        stack = [normalize(ctx)]
        while stack:
            c = stack.pop()
            k = ctx_key(c)
            if k in self._contexts_seen:
                continue
            self._contexts_seen.add(k)
            ps = parents(c)
            self.delta.contexts[k] = {"key": k, **{kk: c.get(kk) for kk in ("footage_type", "prev_edit", "format", "section")},
                                      "specificity": len(c), "parents": [ctx_key(p) for p in ps]}
            stack.extend(ps)

    def _new_pref(self, slot: Slot, action, origin, ts, sid, reason) -> Preference:
        base = f"{slot.owner}/{slot.observed_in}/{slot.category}/{ctx_key(slot.ctx)}/{action}"
        self._pref_counter[base] += 1
        kind = data.owner_kind(slot.owner)
        return Preference(pref_id=f"{base}#{self._pref_counter[base]}", owner=slot.owner, owner_kind=kind,
                          scope="team" if kind == "team" else "private", observed_in=slot.observed_in,
                          category=slot.category, action=action, context_key=ctx_key(slot.ctx), origin=origin,
                          valid_from=ts, valid_from_session=sid, reason=reason)

    def _open(self, slot: Slot, action, origin, ts, sid, reason):
        old = slot.active
        new = self._new_pref(slot, action, origin, ts, sid, reason)
        if old is not None:
            old.valid_to, old.valid_to_session, old.superseded_by = ts, sid, new.pref_id
            new.supersedes, new.supersedes_action = old.pref_id, old.action
            self._stat(slot, old)
            self.delta.prefs[old.pref_id] = old
            rec = {"new": new.pref_id, "old": old.pref_id, "owner": slot.owner, "observed_in": slot.observed_in,
                   "category": slot.category, "context_key": ctx_key(slot.ctx), "from_action": old.action,
                   "to_action": action, "session_id": sid, "session_no": self.owner_idx[slot.owner], "ts": ts,
                   "reason": reason}
            self.supersessions.append(rec)
            self.delta.supersedes.append(rec)
        slot.active = new
        slot.history.append(new)
        slot.challenger, slot.challenge = None, 0
        for ev in slot.evidence[action]:
            self.delta.evidence.append({"pref_id": new.pref_id, **asdict(ev)})
            if ev.ref_kind == "chat":
                new.explicit_refs.append(ev.ref)
        self._stat(slot, new)
        self.delta.prefs[new.pref_id] = new
        return new

    def _stat(self, slot: Slot, pref: Preference):
        tot = slot.total()
        pref.slot_total = round(tot, 4)
        pref.decayed_weight = round(slot.w.get(pref.action, 0.0), 4)
        pref.share = round(pref.decayed_weight / tot, 4) if tot > 0 else 0.0
        pref.support_count = slot.n_pos.get(pref.action, 0)
        pref.rejections = slot.n_rej.get(pref.action, 0)
        pref.dist = {a: round(w, 4) for a, w in sorted(slot.w.items()) if w > 1e-6}
        pref.counts = dict(sorted(slot.n_pos.items()))

    # ------------------------------------------------------------------ streaming
    def ingest(self, evs: list[dict], after_session: Callable | None = None, until_session: str | None = None):
        evs = sorted(evs, key=lambda e: (e["ts"], e["event_id"]))
        for e in evs:
            self._session_last_ts[e["session_id"]] = e["ts"]
        last_idx = {}
        for i, e in enumerate(evs):
            last_idx[e["session_id"]] = i
        for i, e in enumerate(evs):
            sid = e["session_id"]
            if sid not in self._session_meta:
                self.begin_session(sid, e)
            self.handle_event(e)
            if last_idx[sid] == i:
                self.end_session(sid)
                if after_session:
                    after_session(self, sid)
                if until_session and sid == until_session:
                    return

    def begin_session(self, sid: str, first_event: dict):
        meta = dict(data.sessions().get(sid, {}))
        owner = first_event["subject"]
        meta.update({"session_id": sid, "owner": owner, "actor": first_event["actor"],
                     "project_id": first_event["project_id"], "branch": first_event["branch"],
                     "project_scope": first_event["project_scope"], "brand_id": first_event["brand_id"]})
        self.owner_idx[owner] += 1
        meta["owner_session_idx"] = self.owner_idx[owner]
        self._session_meta[sid] = meta
        self.owner_last_session[owner] = sid
        self.delta.sessions.append(meta)

    def handle_event(self, e: dict):
        meta = self._session_meta[e["session_id"]]
        if e.get("op_id"):
            op = data.ops_index().get(e["op_id"], {})
            self.delta.ops.append({
                "op_id": e["op_id"], "type": op.get("type"), "author": op.get("author"),
                "on_behalf_of": op.get("on_behalf_of"), "ts": e["ts"], "version_id": e.get("version_id"),
                "event_type": e["type"], "category": e.get("category"), "action": e.get("action"),
                "replaced_action": e.get("replaced_action"), "decision_id": e.get("decision_id"),
                "session_id": e["session_id"], "project_id": e["project_id"],
                "replaces_op": op.get("replaces_op"), "reverts_op": op.get("reverts_op"),
                "accepts_op": op.get("accepts_op")})
        if e["type"] == "explicit_statement":
            self.apply_statement(e, meta)
            self.flush()                       # explicit -> immediately visible in the graph
            return
        b = self.brand(e.get("brand_id"))
        if b and e.get("action") and e.get("category"):
            rule = b.blocks(e["category"], e["action"])
            if rule:
                v = {"op_id": e["op_id"], "brand_id": b.brand_id, "rule": rule, "event_type": e["type"],
                     "who": "agent" if e["type"] == "agent_edit" else "user", "category": e["category"],
                     "action": e["action"], "session_id": e["session_id"], "ts": e["ts"]}
                self.violations.append(v)
                self.delta.violations.append(v)
                self.flush()                   # brand events -> immediate
        if e["type"] in POSITIVE or e["type"] == "undo":
            self._buffers[e["session_id"]].append(e)

    def apply_statement(self, e: dict, meta: dict):
        chat = data.chats().get(e["chat_id"], {})
        actor = e["actor"]
        team = data.team_of(actor)
        is_lead = team is not None and data.team_lead(team) == actor
        in_team_session = data.owner_kind(e["subject"]) == "team"
        ex = self.extractor.extract(e["text"], actor, is_lead, in_team_session)
        rec = {"chat_id": e["chat_id"], "text": e["text"], "actor": actor, "ts": e["ts"],
               "session_id": e["session_id"], "project_id": e["project_id"], "extraction": ex,
               "extractor": ex.get("extractor", getattr(self.extractor, "name", "?"))}
        if not self.use_statements:
            rec["resolution"] = "statements disabled (ablation)"
        elif not ex["is_preference"]:
            rec["resolution"] = f"not a lasting preference ({ex['kind']}); stored, no weight"
        elif ex["target"] == "team" and ex["kind"] == "standard" and is_lead:
            rec["resolution"] = "team standard set by team lead"
            self._apply_explicit(team, "team", ex, e, rec)
        elif ex["target"] == "team":
            rec["resolution"] = "proposal to the team by a non-lead; cited in conflicts, no weight"
            prop = {"chat_id": e["chat_id"], "team_id": team, "actor": actor, "category": ex["category"],
                    "action": ex["action"], "context_key": ctx_key(ex["context"]), "ts": e["ts"]}
            self.proposals.append(prop)
            self.delta.proposals.append(prop)
        else:
            oi = "private" if ex["scope_hint"] == "private" else "any"
            rec["resolution"] = f"explicit preference of {actor} (observed_in={oi})"
            self._apply_explicit(actor, oi, ex, e, rec)
        self.statements.append(rec)
        self.delta.chats.append(rec)

    def _apply_explicit(self, owner, oi, ex, e, rec):
        ctx = normalize(ex["context"])
        slot = self._slot(owner, oi, ex["category"], ctx, e["session_id"])
        act = ex["action"]
        ev = Evidence(e["chat_id"], "chat", "explicit", e["ts"], e["session_id"], self.p.w_explicit)
        slot.add(act, self.p.w_explicit)
        slot.evidence[act].append(ev)
        slot.n_pos[act] += 1
        for k, v in ctx.items():
            self.observed[(owner, ex["category"])].add((k, v))
        for bad in ex.get("avoid_actions") or []:
            slot.sub(bad, self.p.w_reject)
            slot.n_rej[bad] += 1
        if slot.active is None or slot.active.action != act:
            reason = f"explicit statement {e['chat_id']}"
            self._open(slot, act, "explicit", e["ts"], e["session_id"], reason)
        else:
            slot.active.origin = "explicit" if slot.active.origin == "explicit" else "implicit+explicit"
            slot.active.explicit_refs.append(e["chat_id"])
            self.delta.evidence.append({"pref_id": slot.active.pref_id, **asdict(ev)})
            self._stat(slot, slot.active)
            self.delta.prefs[slot.active.pref_id] = slot.active
        rec["slot"] = {"owner": owner, "observed_in": oi, "category": ex["category"], "context_key": ctx_key(ctx)}

    def end_session(self, sid: str):
        meta = self._session_meta[sid]
        owner, scope = meta["owner"], meta["project_scope"]
        ts_end = self._session_last_ts.get(sid, meta.get("started_at"))
        groups: dict[str, list] = defaultdict(list)
        for e in self._buffers.pop(sid, []):
            groups[e.get("decision_id") or e["event_id"]].append(e)
        touched: list[Slot] = []
        for dec_id, evs in groups.items():
            evs.sort(key=lambda x: x["ts"])
            cat = evs[0]["category"]
            if not cat:
                continue
            ctx = normalize(evs[-1]["context"] or evs[0]["context"])
            final = None
            rejects = []
            for x in evs:
                if x["type"] in POSITIVE and x.get("action"):
                    final = x
                elif x["type"] == "undo" and final is not None and x.get("replaced_action") == final["action"]:
                    final = None      # accepted, then undone later: no positive evidence
                if x["type"] in ("user_override", "undo") and x.get("replaced_action"):
                    rejects.append(x)
            for k, v in ctx.items():
                self.observed[(owner, cat)].add((k, v))
            for oi in self._ois(owner, scope):
                for sub in subsets(ctx):
                    slot = self._slot(owner, oi, cat, sub, sid)
                    if not slot.touched:
                        slot.touched = True
                        touched.append(slot)
                    if final is not None:
                        kind = POSITIVE[final["type"]]
                        w = {"override": self.p.w_override, "accept": self.p.w_accept, "edit": self.p.w_edit}[kind]
                        ev = Evidence(final["op_id"], "op", kind, final["ts"], sid, w)
                        slot.add(final["action"], w)
                        slot.n_pos[final["action"]] += 1
                        slot.evidence[final["action"]].append(ev)
                        slot.new_evidence.append((final["action"], ev))
                    for r in rejects:
                        ra = r["replaced_action"]
                        if final is not None and ra == final["action"]:
                            continue
                        slot.sub(ra, self.p.w_reject)
                        slot.n_rej[ra] += 1
                        slot.new_rejects.append((ra, Evidence(r["op_id"], "op", "reject" if r["type"] == "user_override" else "undo",
                                                              r["ts"], sid, self.p.w_reject)))
        for slot in touched:
            self._settle(slot, ts_end, sid)
        self.sessions_done.append(sid)
        self.flush()

    def _settle(self, slot: Slot, ts: str, sid: str):
        slot.touched = False
        act = slot.active
        for a, ev in slot.new_evidence:
            if act is not None and a == act.action:
                self.delta.evidence.append({"pref_id": act.pref_id, **asdict(ev)})
        for a, ev in slot.new_rejects:
            if act is not None and a == act.action:
                self.delta.contra.append({"pref_id": act.pref_id, **asdict(ev)})
        slot.new_evidence, slot.new_rejects = [], []
        lead = slot.leader()
        if lead is None:
            if act is not None:
                self._stat(slot, act)
                self.delta.prefs[act.pref_id] = act
            return
        if act is None:
            if slot.w[lead] >= self.p.min_support:
                self._open(slot, lead, "implicit", ts, sid, "first reliable evidence in this context")
            return
        fl = slot.leader(fast=True)
        ftot = sum(slot.f.values())
        if fl is not None and fl != act.action and slot.f[fl] >= self.p.min_support \
                and slot.f[fl] / ftot >= self.p.supersede_min_share:
            if slot.challenger == fl:
                slot.challenge += 1
            else:
                slot.challenger, slot.challenge = fl, 1
            if slot.challenge >= self.p.supersede_after_sessions:
                share = slot.f[fl] / ftot
                n = slot.challenge
                # restart the slow weights from the recent window: the old style must not linger
                slot.w = defaultdict(float, {a: v for a, v in slot.f.items()})
                self._open(slot, fl, "implicit", ts, sid,
                           f"{fl} led the recent window ({share:.0%} of fast-decayed weight) in {n} consecutive "
                           f"sessions with new evidence")
                return
        else:
            slot.challenger, slot.challenge = None, 0
        self._stat(slot, act)
        self.delta.prefs[act.pref_id] = act

    def flush(self):
        if self.delta.empty():
            return
        d, self.delta = self.delta, Delta()
        for s in self.sinks:
            s.write(d)

    def finalize(self):
        """Decay every slot to its owner's latest session and write final stats."""
        for slot in self.slots.values():
            if self.p.decay_clock != "slot_session":
                slot.decay_to(self.owner_idx[slot.owner], self.lam, self.lam_fast)
            for pref in slot.history:
                if pref is slot.active:
                    self._stat(slot, pref)
                    self.delta.prefs[pref.pref_id] = pref
        self.flush()
        for s in self.sinks:
            if hasattr(s, "finalize"):
                s.finalize(self)

    # ------------------------------------------------------------------ read side
    def profile(self):
        from .profile import EngineProfile
        return EngineProfile(self)
