"""Neo4j temporal graph: sink for engine deltas + read-back loader for the service.

Nodes : Creator, Team, Brand, Project, Session, Op, ChatStatement, Context, Preference
Edges : (Creator)-[:MEMBER_OF|LEADS]->(Team)-[:USES_BRAND]->(Brand)
        (Creator|Team|Brand)-[:HAS_PREFERENCE]->(Preference)-[:IN_CONTEXT]->(Context)
        (Context)-[:GENERALIZES_TO]->(Context)                     specific -> broader
        (Preference)-[:EVIDENCED_BY {kind, weight}]->(Op|ChatStatement)   provenance
        (Preference)-[:CONTRADICTED_BY {kind}]->(Op)                 overrides / undos of it
        (Preference)-[:SUPERSEDES {session_id, ts, reason}]->(Preference)
        (Op)-[:IN_SESSION]->(Session)-[:OF]->(Creator|Team), (Session)-[:BY]->(Creator), (Session)-[:IN_PROJECT]->(Project)
        (Op)-[:ACCEPTS|REPLACES|REVERTS]->(Op)                       review ops from the VCS log
        (Op)-[:VIOLATES {rule}]->(Brand)
        (Creator)-[:SAID]->(ChatStatement)-[:IN_SESSION]->(Session); (ChatStatement)-[:PROPOSES_TO]->(Team)
"""
from __future__ import annotations

import json
from dataclasses import asdict

from neo4j import GraphDatabase

from . import data
from .brand import Brand
from .config import Neo4jConfig
from .profile import SlotView, StaticProfile

CONSTRAINTS = [
    ("Creator", "id"), ("Team", "id"), ("Brand", "id"), ("Project", "id"), ("Session", "id"),
    ("Op", "op_id"), ("ChatStatement", "chat_id"), ("Context", "key"), ("Preference", "pref_id"),
]


def _pref_row(p) -> dict:
    d = asdict(p)
    d["dist_json"] = json.dumps(d.pop("dist"))
    d["counts_json"] = json.dumps(d.pop("counts"))
    d["explicit_refs"] = list(dict.fromkeys(d["explicit_refs"]))
    d["status"] = "active" if p.valid_to is None else "superseded"
    return d


class Neo4jGraph:
    def __init__(self, cfg: Neo4jConfig | None = None):
        self.cfg = cfg or Neo4jConfig()
        self.driver = GraphDatabase.driver(self.cfg.uri, auth=(self.cfg.user, self.cfg.password))
        self.snapshots: list[dict] = []
        self.record_snapshots = False

    def close(self):
        self.driver.close()

    def run(self, q, **params):
        with self.driver.session(database=self.cfg.database) as s:
            return list(s.run(q, **params))

    # ------------------------------------------------------------------ setup
    def reset(self):
        self.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")

    def setup(self):
        for label, prop in CONSTRAINTS:
            self.run(f"CREATE CONSTRAINT {label.lower()}_{prop} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE")
        self.run("CREATE INDEX pref_slot IF NOT EXISTS FOR (p:Preference) ON (p.owner, p.category, p.context_key)")
        c = data.cast()
        for pid, pp in c["people"].items():
            self.run("MERGE (n:Creator {id:$id}) SET n.role=$role, n.bio=$bio", id=pid, role=pp["role"], bio=pp["bio"])
        for tid, t in c["teams"].items():
            self.run("MERGE (t:Team {id:$id}) SET t.lead=$lead", id=tid, lead=t["lead"])
            for m in t["members"]:
                self.run("MATCH (c:Creator {id:$m}),(t:Team {id:$t}) MERGE (c)-[:MEMBER_OF]->(t)", m=m, t=tid)
            self.run("MATCH (c:Creator {id:$m}),(t:Team {id:$t}) MERGE (c)-[:LEADS]->(t)", m=t["lead"], t=tid)
            b = Brand.load(t["brand_id"])
            g = data.brand_guidelines()
            self.run("MERGE (b:Brand {id:$id}) SET b.name=$name, b.version=$v, b.hard_constraints=$h, b.palette=$pal",
                     id=b.brand_id, name=g["name"], v=g["version"], h=json.dumps(b.hard), pal=json.dumps(g["palette"]))
            self.run("MATCH (t:Team {id:$t}),(b:Brand {id:$b}) MERGE (t)-[:USES_BRAND]->(b)", t=tid, b=b.brand_id)
            self.run("MERGE (c:Context {key:'*'}) SET c.specificity=0")
            self.run("""UNWIND $rows AS r
                MERGE (p:Preference {pref_id:r.pref_id})
                SET p.owner=$b, p.owner_kind='brand', p.scope='brand', p.observed_in='brand', p.category=r.category,
                    p.action=r.action, p.polarity=r.polarity, p.hard=r.hard, p.origin='explicit', p.context_key='*',
                    p.valid_from=$since, p.status='active', p.reason='brand guidelines ' + $v
                WITH p MATCH (b:Brand {id:$b}), (c:Context {key:'*'})
                MERGE (b)-[:HAS_PREFERENCE]->(p) MERGE (p)-[:IN_CONTEXT]->(c)""",
                     rows=b.constraint_rows(), b=b.brand_id, since=g["version"], v=g["version"])

    # ------------------------------------------------------------------ sink
    def write(self, d):
        if d.sessions:
            self.run("""UNWIND $rows AS r
                MERGE (pr:Project {id:r.project_id}) SET pr.scope=r.project_scope, pr.brand_id=r.brand_id
                MERGE (s:Session {id:r.session_id})
                SET s.owner=r.owner, s.actor=r.actor, s.branch=r.branch, s.project_id=r.project_id,
                    s.project_scope=r.project_scope, s.brand_id=r.brand_id, s.started_at=r.started_at,
                    s.session_no=r.session_no, s.owner_session_idx=r.owner_session_idx, s.split=r.split
                MERGE (s)-[:IN_PROJECT]->(pr)
                WITH s, r OPTIONAL MATCH (o) WHERE (o:Creator OR o:Team) AND o.id = r.owner
                FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | MERGE (s)-[:OF]->(o))
                WITH s, r OPTIONAL MATCH (a:Creator {id:r.actor})
                FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | MERGE (s)-[:BY]->(a))""",
                     rows=[{k: s.get(k) for k in ("session_id", "owner", "actor", "branch", "project_id", "project_scope",
                                                   "brand_id", "started_at", "session_no", "owner_session_idx", "split")}
                           for s in d.sessions])
        if d.ops:
            self.run("""UNWIND $rows AS r
                MERGE (o:Op {op_id:r.op_id})
                SET o += r
                WITH o, r MATCH (s:Session {id:r.session_id}) MERGE (o)-[:IN_SESSION]->(s)""", rows=d.ops)
            for rel, fld in (("ACCEPTS", "accepts_op"), ("REPLACES", "replaces_op"), ("REVERTS", "reverts_op")):
                rows = [{"a": o["op_id"], "b": o[fld]} for o in d.ops if o.get(fld)]
                if rows:
                    self.run(f"""UNWIND $rows AS r MATCH (a:Op {{op_id:r.a}}) MERGE (b:Op {{op_id:r.b}})
                                 MERGE (a)-[:{rel}]->(b)""", rows=rows)
        if d.chats:
            self.run("""UNWIND $rows AS r
                MERGE (c:ChatStatement {chat_id:r.chat_id})
                SET c.text=r.text, c.actor=r.actor, c.ts=r.ts, c.extraction=r.extraction, c.extractor=r.extractor,
                    c.resolution=r.resolution
                WITH c, r MATCH (s:Session {id:r.session_id}) MERGE (c)-[:IN_SESSION]->(s)
                WITH c, r MATCH (a:Creator {id:r.actor}) MERGE (a)-[:SAID]->(c)""",
                     rows=[{**{k: c[k] for k in ("chat_id", "text", "actor", "ts", "session_id", "extractor", "resolution")},
                            "extraction": json.dumps(c["extraction"])} for c in d.chats])
        if d.contexts:
            rows = list(d.contexts.values())
            self.run("""UNWIND $rows AS r MERGE (c:Context {key:r.key})
                SET c.footage_type=r.footage_type, c.prev_edit=r.prev_edit, c.format=r.format, c.section=r.section,
                    c.specificity=r.specificity""", rows=rows)
            self.run("""UNWIND $rows AS r UNWIND r.parents AS pk
                MATCH (c:Context {key:r.key}) MERGE (p:Context {key:pk}) MERGE (c)-[:GENERALIZES_TO]->(p)""", rows=rows)
        if d.prefs:
            rows = [_pref_row(p) for p in d.prefs.values()]
            self.run("""UNWIND $rows AS r
                MERGE (p:Preference {pref_id:r.pref_id}) SET p += r
                WITH p, r MATCH (c:Context {key:r.context_key}) MERGE (p)-[:IN_CONTEXT]->(c)
                WITH p, r MATCH (o) WHERE (o:Creator OR o:Team) AND o.id = r.owner MERGE (o)-[:HAS_PREFERENCE]->(p)""",
                     rows=rows)
        if d.supersedes:
            self.run("""UNWIND $rows AS r MATCH (n:Preference {pref_id:r.new}), (o:Preference {pref_id:r.old})
                MERGE (n)-[x:SUPERSEDES]->(o) SET x.session_id=r.session_id, x.ts=r.ts, x.reason=r.reason""",
                     rows=d.supersedes)
        for rows, rel in ((d.evidence, "EVIDENCED_BY"), (d.contra, "CONTRADICTED_BY")):
            ops = [r for r in rows if r["ref_kind"] == "op"]
            chats = [r for r in rows if r["ref_kind"] == "chat"]
            if ops:
                self.run(f"""UNWIND $rows AS r MATCH (p:Preference {{pref_id:r.pref_id}}) MERGE (o:Op {{op_id:r.ref}})
                    MERGE (p)-[x:{rel}]->(o) SET x.kind=r.kind, x.weight=r.weight, x.ts=r.ts""", rows=ops)
            if chats:
                self.run(f"""UNWIND $rows AS r MATCH (p:Preference {{pref_id:r.pref_id}}) MERGE (c:ChatStatement {{chat_id:r.ref}})
                    MERGE (p)-[x:{rel}]->(c) SET x.kind=r.kind, x.weight=r.weight, x.ts=r.ts""", rows=chats)
        if d.violations:
            self.run("""UNWIND $rows AS r MATCH (o:Op {op_id:r.op_id}), (b:Brand {id:r.brand_id})
                MERGE (o)-[x:VIOLATES]->(b) SET x.rule=r.rule, x.who=r.who""", rows=d.violations)
        if d.proposals:
            self.run("""UNWIND $rows AS r MATCH (c:ChatStatement {chat_id:r.chat_id}), (t:Team {id:r.team_id})
                MERGE (c)-[x:PROPOSES_TO]->(t) SET x.category=r.category, x.action=r.action, x.context_key=r.context_key""",
                     rows=d.proposals)

    def snapshot(self, label: str) -> dict:
        rows = self.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c")
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
        act = self.run("MATCH (p:Preference) WHERE p.status='active' AND p.owner_kind<>'brand' RETURN count(p) AS c")[0]["c"]
        sup = self.run("MATCH ()-[r:SUPERSEDES]->() RETURN count(r) AS c")[0]["c"]
        snap = {"after": label, "nodes": {r["l"]: r["c"] for r in rows}, "relationships": rels,
                "active_preferences": act, "supersessions": sup}
        self.snapshots.append(snap)
        return snap

    # ------------------------------------------------------------------ read side
    def stats(self) -> dict:
        s = self.snapshot("now")
        self.snapshots.pop()
        return s

    def load_profile(self, evidence_per_pref: int = 8) -> StaticProfile:
        rows = self.run("""
            MATCH (p:Preference) WHERE p.status='active' AND p.owner_kind <> 'brand'
            OPTIONAL MATCH (p)-[e:EVIDENCED_BY]->(x)
            WITH p, e, x ORDER BY e.ts DESC
            WITH p, collect({ref: coalesce(x.op_id, x.chat_id), kind: e.kind, ts: e.ts})[0..$k] AS ev
            RETURN p, ev""", k=evidence_per_pref)
        views = []
        for r in rows:
            p = r["p"]
            views.append(SlotView(
                owner=p["owner"], observed_in=p["observed_in"], category=p["category"], context_key=p["context_key"],
                active_action=p["action"], pref_id=p["pref_id"], origin=p["origin"], valid_from=p["valid_from"],
                valid_from_session=p["valid_from_session"], reason=p["reason"], supersedes_action=p.get("supersedes_action"),
                dist=json.loads(p["dist_json"]), evidence=[(e["ref"], e["kind"], e["ts"]) for e in r["ev"] if e["ref"]],
                explicit_refs=list(p.get("explicit_refs") or []), support_count=p.get("support_count", 0),
                rejections=p.get("rejections", 0), counts=json.loads(p.get("counts_json") or "{}")))
        props = self.run("""MATCH (c:ChatStatement)-[x:PROPOSES_TO]->(t:Team)
            RETURN c.chat_id AS chat_id, t.id AS team_id, c.actor AS actor, x.category AS category, x.action AS action,
                   x.context_key AS context_key, c.ts AS ts""")
        return StaticProfile(views, [dict(r) for r in props])

    def history(self, owner: str, category: str, context_key: str | None = None, observed_in: str | None = None):
        q = """MATCH (o)-[:HAS_PREFERENCE]->(p:Preference {category:$cat}) WHERE o.id=$owner
               AND ($ck IS NULL OR p.context_key=$ck) AND ($oi IS NULL OR p.observed_in=$oi)
               OPTIONAL MATCH (p)-[s:SUPERSEDES]->(old:Preference)
               OPTIONAL MATCH (p)-[:EVIDENCED_BY]->(c:ChatStatement)
               RETURN p.pref_id AS pref_id, p.observed_in AS observed_in, p.context_key AS context_key, p.action AS action,
                      p.status AS status, p.origin AS origin, p.valid_from AS valid_from, p.valid_from_session AS valid_from_session,
                      p.valid_to AS valid_to, p.valid_to_session AS valid_to_session, p.share AS share,
                      p.support_count AS support_count, p.reason AS reason, old.action AS supersedes_action,
                      collect(DISTINCT c.chat_id) AS chat_ids
               ORDER BY p.context_key, p.observed_in, p.valid_from"""
        return [dict(r) for r in self.run(q, owner=owner, cat=category, ck=context_key, oi=observed_in)]

    def finalize(self, engine):
        pass
