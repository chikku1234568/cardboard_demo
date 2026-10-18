"""suggest(context, actor, project_scope) -> explained, scoped, confidence-scored answer.

Scoping rules (fixed, explicit):
1. Brand hard constraints always win: a blocked action is never returned (it is listed in
   `blocked[]` with the rule).
2. Private project: the actor's own memory (observed in private projects, then pooled).
   Team standards are not applied (private > team) and are only mentioned in `why`.
3. Team-owned project: the team's memory is the recommendation; if the actor's own
   preference differs, a conflict citing BOTH sides (events + chat) is returned.
4. Creator-owned project under team/brand scope: the creator's memory from team-scope
   work (then pooled); team standard and the creator's private preference are surfaced
   as conflicts when they differ.
Confidence = Wilson lower bound of the decayed share, with n = decayed support, so 3/3 is
~0.65, not 1.0. Abstain below `abstain_below`, or when none of the context values has
ever been observed for this owner and category.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import data
from .brand import Brand
from .config import DEFAULT, Params
from .context import describe, key, normalize, parse_key, subsets


def wilson_lb(p: float, n: float, z: float) -> float:
    if n <= 0:
        return 0.0
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return max(0.0, (centre - margin) / denom)


@dataclass
class Pick:
    action: str
    confidence: float
    share: float
    support: float
    view: object
    ctx: dict
    blocked: list
    note: str | None = None
    stale: list | None = None

    def side(self, label: str) -> dict:
        v = self.view
        return {"side": label, "owner": v.owner, "observed_in": v.observed_in, "action": self.action,
                "confidence": round(self.confidence, 3), "share": round(self.share, 3),
                "context_used": v.context_key, "preference_id": v.pref_id,
                "source_events": _sources(v)}


def _sources(v, k: int = 6) -> list[str]:
    refs = [r for r, _, _ in v.evidence[:k]]
    for c in v.explicit_refs:
        if c not in refs:
            refs.append(c)
    return refs


def pick(profile, owner: str, ois: list[str], category: str, context: dict, brand: Brand | None,
         p: Params = DEFAULT) -> Pick | None:
    best = None
    cands = []
    stale = []
    for oi_rank, oi in enumerate(ois):
        if p.slot_selection == "backoff_chain":
            subs = backoff_chain(profile, owner, oi, category, context)
        elif p.slot_selection == "conditional_chain":
            subs = conditional_chain(profile, owner, oi, category, context)
        else:
            subs = subsets(context)
        for sub in subs:
            v = profile.slot(owner, oi, category, key(sub))
            if v is None:
                continue
            total = v.total
            if total < p.min_support:
                continue
            why_stale = _stale(profile, v, sub)
            if why_stale:
                stale.append(why_stale)
                continue
            action, blocked, note = v.active_action, [], None
            if brand is not None:
                rule = brand.blocks(category, action)
                if rule:
                    blocked.append({"action": action, "rule": rule, "preference_id": v.pref_id,
                                    "source_events": _sources(v, 3)})
                    allowed = [(w, a) for a, w in v.dist.items() if not brand.blocks(category, a) and w > 0]
                    if not allowed:
                        continue
                    action = max(allowed)[1]
                    note = f"next allowed action in this context after the brand filter"
            share = v.dist.get(action, 0.0) / total
            conf = wilson_lb(share, total, p.wilson_z)
            cands.append(((round(conf, 9), len(sub), -oi_rank), Pick(action, conf, share, total, v, sub, blocked, note)))
    if not cands:
        return None
    if p.slot_selection == "max_information":
        mi = {}
        conf_ok = []
        for c in cands:
            v = c[1].view
            if (v.observed_in) not in mi:
                mi[v.observed_in] = key_informativeness(profile, owner, v.observed_in, category)
            info = sum(mi[v.observed_in].get(k, 0.0) for k in c[1].ctx)
            if c[1].confidence >= p.abstain_below:
                conf_ok.append(((round(info, 6), c[0][0], c[0][2]), c[1]))
        if conf_ok:
            best = max(conf_ok, key=lambda c: c[0])[1]
    elif p.slot_selection in ("specific_first", "backoff_chain", "conditional_chain"):
        pools = [[c for c in cands if c[0][2] == -r] for r in range(len(ois))] if p.pool_order == "scope_first" \
            else [cands]
        for pool in pools:
            for level in sorted({c[0][1] for c in pool}, reverse=True):
                lv = [c for c in pool if c[0][1] == level and c[1].confidence >= p.abstain_below]
                if lv:
                    best = max(lv, key=lambda c: c[0])[1]
                    break
            if best is not None:
                break
    if best is None:
        best = max(cands, key=lambda c: c[0])[1]
    if stale:
        best.stale = stale
    return best


def _entropy(dist: dict) -> float:
    tot = sum(dist.values())
    if tot <= 0:
        return 0.0
    return -sum((w / tot) * math.log2(w / tot) for w in dist.values() if w > 0)


def key_informativeness(profile, owner, oi, category) -> dict:
    """Mutual information (bits) between each context key and the action, from the owner's
    single-key slots in the graph. Used to order the backoff chain."""
    from .config import CONTEXT_KEYS
    root = profile.slot(owner, oi, category, "*")
    if root is None:
        return {k: 0.0 for k in CONTEXT_KEYS}
    vals = data.vocab()["context_values"]
    out = {}
    for k in CONTEXT_KEYS:
        parts = [profile.slot(owner, oi, category, key({k: v})) for v in vals[k]]
        parts = [x.counts for x in parts if x is not None and x.counts]
        n = sum(sum(c.values()) for c in parts)
        if n <= 0:
            out[k] = 0.0
            continue
        pooled: dict = {}
        for c in parts:
            for a, w in c.items():
                pooled[a] = pooled.get(a, 0) + w
        out[k] = round(max(0.0, _entropy(pooled) - sum(sum(c.values()) / n * _entropy(c) for c in parts)), 4)
    return out


def backoff_chain(profile, owner, oi, category, context) -> list[dict]:
    """full context -> drop the least informative key -> ... -> '*'."""
    ctx = normalize(context)
    mi = key_informativeness(profile, owner, oi, category)
    order = sorted(ctx, key=lambda k: -mi.get(k, 0.0))
    return [{k: ctx[k] for k in order[:n]} for n in range(len(order), -1, -1)]


def conditional_mi(profile, owner, oi, category, base: dict, k: str) -> float:
    """I(action; k | base) from cumulative counts of the child slots base + {k=v}."""
    parts = []
    for val in data.vocab()["context_values"][k]:
        x = profile.slot(owner, oi, category, key({**base, k: val}))
        if x is not None and x.counts:
            parts.append(x.counts)
    n = sum(sum(c.values()) for c in parts)
    if n <= 0 or len(parts) < 2:
        return 0.0
    pooled: dict = {}
    for c in parts:
        for a, w in c.items():
            pooled[a] = pooled.get(a, 0) + w
    return max(0.0, _entropy(pooled) - sum(sum(c.values()) / n * _entropy(c) for c in parts))


def conditional_chain(profile, owner, oi, category, context) -> list[dict]:
    """Greedy backoff: at each step drop the key that carries the least information about the
    action GIVEN the other remaining keys (conditional mutual information, from the graph)."""
    cur = normalize(context)
    chain = [dict(cur)]
    while cur:
        drop = min(sorted(cur), key=lambda k: conditional_mi(profile, owner, oi, category,
                                                             {kk: vv for kk, vv in cur.items() if kk != k}, k))
        cur = {kk: vv for kk, vv in cur.items() if kk != drop}
        chain.append(dict(cur))
    return chain


def _extra_keys_uninformative(profile, v, sub, broader) -> bool:
    """True when the keys the narrower context adds carry little information about the action
    given the broader context (max conditional MI < half the MI of the broader context's most
    informative key). Then a change seen in the broader context is assumed to apply here too."""
    extra = [k for k in sub if k not in broader]
    if not extra:
        return True
    mi = key_informativeness(profile, v.owner, v.observed_in, v.category)
    base_mi = max([mi.get(k, 0.0) for k in broader] or [0.0])
    cmi = max(conditional_mi(profile, v.owner, v.observed_in, v.category, dict(broader), k) for k in extra)
    return cmi < 0.5 * base_mi


def _stale(profile, v, sub) -> str | None:
    """Graph rule over GENERALIZES_TO + SUPERSEDES: if a broader context superseded this slot's
    action AFTER this slot last saw evidence for it, the narrower preference is stale, provided
    either this slot has itself seen the new action, or the keys it adds are uninformative
    given the broader context (so the broader change should transfer)."""
    last = v.evidence[0][2] if v.evidence else v.valid_from
    pools = [v.observed_in] + (["any"] if v.observed_in != "any" and data.owner_kind(v.owner) == "creator" else [])
    for pool in pools:
        for broader in subsets(sub)[(1 if pool == v.observed_in else 0):]:
            pv = profile.slot(v.owner, pool, v.category, key(broader))
            if pv is None or pv.supersedes_action != v.active_action or pv.active_action == v.active_action:
                continue
            if v.dist.get(pv.active_action, 0.0) <= 0 and not _extra_keys_uninformative(profile, v, sub, broader):
                continue      # never saw the new action AND its extra keys matter: the broader switch came from elsewhere
            if pv.valid_from > last:
                return (f"skipped [{describe(sub)}] ({v.observed_in}): its {v.active_action} was superseded by "
                        f"{pv.active_action} in [{describe(broader)}] ({pool}) at {pv.valid_from_session} and not "
                        f"re-confirmed since")
    return None


def _unseen(profile, owner_ois_pairs, category, context) -> bool:
    ctx = normalize(context)
    if not ctx:
        return False
    for owner in {o for o, _ in owner_ois_pairs}:
        obs = profile.observed(owner, category)
        if any((k, v) in obs for k, v in ctx.items()):
            return False
    return True


def _why(pk: Pick, who: str, context: dict) -> list[str]:
    v = pk.view
    out = []
    full = normalize(context)
    if pk.ctx != full:
        out.append(f"Fell back from [{describe(full)}] to the most reliable broader context [{describe(pk.ctx)}].")
    else:
        out.append(f"Exact context match [{describe(pk.ctx)}].")
    out.append(f"{who}: {pk.action} holds {pk.share:.0%} of the decayed weight here "
               f"(effective support {pk.support:.1f} after decay, {v.support_count} supporting events, "
               f"{v.rejections} rejections of it); confidence {pk.confidence:.2f} (Wilson lower bound).")
    if v.supersedes_action and v.active_action == pk.action:
        out.append(f"Active since {v.valid_from_session}: superseded {v.supersedes_action} ({v.reason}).")
    if v.explicit_refs and v.active_action == pk.action:
        texts = [data.chats().get(c, {}).get("text", c) for c in v.explicit_refs]
        out.append("Explicit statement(s): " + "; ".join(f"'{t}' ({c})" for t, c in zip(texts, v.explicit_refs)))
    if pk.note:
        out.append(pk.note)
    if pk.stale:
        out.append(f"{len(pk.stale)} narrower context(s) skipped as stale, e.g. {pk.stale[0]}.")
    return out


def suggest(profile, context: dict, actor: str, project_scope: str, category: str,
            project_owner: str | None = None, brand_id: str | None = None, p: Params = DEFAULT) -> dict:
    team = data.team_of(actor)
    owner = project_owner or (actor if project_scope == "private" else (team or actor))
    if brand_id is None and project_scope == "team":
        brand_id = data.cast()["teams"].get(team or "", {}).get("brand_id")
    brand = Brand.load(brand_id) if brand_id else None
    owner_is_team = data.owner_kind(owner) == "team"
    ctx = normalize(context)
    why, conflicts, blocked = [], [], []
    main = None
    fallback_note = None

    if owner_is_team:
        main_pairs = [(owner, "team")]
        main = pick(profile, owner, ["team"], category, ctx, brand, p)
        personal = pick(profile, actor, ["private", "any"], category, ctx, brand, p) if actor != owner else None
        if main is None and personal is not None:
            main, fallback_note = personal, f"No team memory for this context; using {actor}'s own preference."
        elif main is not None and personal is not None and personal.action != main.action \
                and personal.confidence >= p.conflict_min_conf:
            conflicts.append(_conflict(category, ctx, team_pick=main, private_pick=personal, actor=actor, team=owner,
                                       resolution="team project: team standard applies; the private preference is "
                                                  "surfaced, not applied"))
        who = f"team {owner}"
    else:
        main_pairs = [(owner, project_scope), (owner, "any")]
        ois = [project_scope, "any"]
        main = pick(profile, owner, ois, category, ctx, brand, p)
        team_pick = pick(profile, team, ["team"], category, ctx, brand, p) if team else None
        who = f"{owner}"
        if project_scope == "team":
            private = pick(profile, owner, ["private"], category, ctx, brand, p)
            if main is None and team_pick is not None:
                main, fallback_note = team_pick, f"No personal memory for this context; using team {team} standard."
            if main is not None:
                if team_pick is not None and team_pick.action != main.action and team_pick.confidence >= p.conflict_min_conf:
                    conflicts.append(_conflict(category, ctx, team_pick=team_pick, private_pick=main, actor=owner, team=team,
                                               resolution=f"{owner}'s own project under team scope: {owner}'s team-scope "
                                                          f"behaviour applies; the team standard is surfaced"))
                if private is not None and private.action != main.action and private.confidence >= p.conflict_min_conf:
                    conflicts.append(_conflict(category, ctx, team_pick=main, private_pick=private, actor=owner, team=team,
                                               resolution=f"team-scope project: {owner}'s private-project preference "
                                                          f"is surfaced, not applied", team_label="team_scope_behaviour"))
        elif team_pick is not None and main is not None and team_pick.action != main.action:
            why.append(f"Private project: {owner}'s preference applies over team standard "
                       f"{team_pick.action} (private > team).")

    if main is not None:
        blocked.extend(main.blocked)
    if brand is not None and data.owner_kind(actor) == "creator":
        raw = pick(profile, actor, ["private", "any"], category, ctx, None, p)
        if raw is not None and brand.blocks(category, raw.action) and not any(b["action"] == raw.action for b in blocked):
            blocked.append({"action": raw.action, "rule": brand.blocks(category, raw.action),
                            "preference_id": raw.view.pref_id, "source_events": _sources(raw.view, 3),
                            "note": f"{actor}'s own preference, blocked here by brand {brand.brand_id}"})
            if main is not None:
                conflicts.append({"kind": "brand_vs_private", "category": category, "context": ctx,
                                  "private": raw.side("private"),
                                  "brand": {"brand_id": brand.brand_id, "rule": brand.blocks(category, raw.action)},
                                  "resolution": "brand hard constraint wins; the private preference is never returned",
                                  "chat_citations": []})
    for c in conflicts:
        if c["kind"] == "team_vs_private":
            c["chat_citations"] = _citations(profile, c)
        else:
            c["chat_citations"] = [{"chat_id": r, "actor": data.chats()[r]["actor"], "text": data.chats()[r]["text"],
                                    "session_id": data.chats()[r]["session_id"]}
                                   for r in c["private"]["source_events"] if r.startswith("chat_")]

    if main is None:
        return _abstain(ctx, actor, owner, project_scope, category, brand_id,
                        "no reliable memory for this context", why, conflicts, blocked)
    if _unseen(profile, main_pairs + ([(actor, "any")] if owner_is_team else []), category, ctx):
        return _abstain(ctx, actor, owner, project_scope, category, brand_id,
                        f"unseen context: none of [{describe(ctx)}] was ever observed for {owner} / {category}",
                        why, conflicts, blocked, best=main)
    why = _why(main, who if not fallback_note else main.view.owner, ctx) + why
    if fallback_note:
        why.insert(0, fallback_note)
    for b in blocked:
        why.append(f"Blocked by brand: {b['action']} ({b['rule']}).")
    if conflicts:
        why.append(f"{len(conflicts)} conflict(s) surfaced; see conflicts[].")
    abstain = main.confidence < p.abstain_below
    if abstain:
        why.append(f"Abstaining: confidence {main.confidence:.2f} < threshold {p.abstain_below}.")
    return {
        "category": category, "context": ctx, "actor": actor, "project_owner": owner,
        "project_scope": project_scope, "brand_id": brand_id,
        "action": None if abstain else main.action, "best_guess": main.action,
        "confidence": round(main.confidence, 3), "abstain": abstain,
        "why": why, "source_events": _sources(main.view, 8),
        "context_used": main.view.context_key, "memory_owner": main.view.owner,
        "observed_in": main.view.observed_in, "preference_id": main.view.pref_id,
        "conflicts": conflicts, "blocked": blocked,
    }


def _conflict(category, ctx, team_pick: Pick, private_pick: Pick, actor, team, resolution, team_label="team"):
    return {"kind": "team_vs_private", "category": category, "context": ctx,
            "private": private_pick.side("private"), "team": team_pick.side(team_label),
            "resolution": resolution, "chat_citations": []}


def _citations(profile, conflict) -> list[dict]:
    """Chat statements on either side: explicit refs of both preferences + proposals."""
    ids = []
    for side in ("private", "team"):
        ids += [r for r in conflict[side]["source_events"] if r.startswith("chat_")]
    ctx = conflict["context"]
    for prop in profile.proposals():
        pk = parse_key(prop["context_key"])
        if prop["category"] == conflict["category"] and all(ctx.get(k) == v for k, v in pk.items()):
            ids.append(prop["chat_id"])
    out = []
    for cid in dict.fromkeys(ids):
        c = data.chats().get(cid, {})
        out.append({"chat_id": cid, "actor": c.get("actor"), "text": c.get("text"), "session_id": c.get("session_id")})
    return out


def _abstain(ctx, actor, owner, scope, category, brand_id, reason, why, conflicts, blocked, best: Pick | None = None):
    return {"category": category, "context": ctx, "actor": actor, "project_owner": owner, "project_scope": scope,
            "brand_id": brand_id, "action": None, "best_guess": best.action if best else None,
            "confidence": round(best.confidence, 3) if best else 0.0, "abstain": True,
            "why": why + [f"Abstaining: {reason}."], "source_events": _sources(best.view, 8) if best else [],
            "context_used": best.view.context_key if best else None, "memory_owner": best.view.owner if best else None,
            "observed_in": best.view.observed_in if best else None,
            "preference_id": best.view.pref_id if best else None, "conflicts": conflicts, "blocked": blocked}
