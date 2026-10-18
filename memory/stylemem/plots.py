"""PNG figures for docs/: drift timeline, graph growth, eval bars, graph snapshots (from Neo4j)."""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

from . import data  # noqa: E402
from .config import DEFAULT, DOCS, RESULTS  # noqa: E402
from .engine import MemoryEngine  # noqa: E402

SLOT_TH = ("ava", "any", "transition", "footage_type=talking_head|prev_edit=jump_cut")
SLOT_BR = ("ava", "any", "transition", "footage_type=broll")


def drift_plot():
    eng = MemoryEngine(DEFAULT)
    rows = []

    def cb(e, sid):
        if not sid.startswith("ava_"):
            return
        r = {"n": int(sid.split("_s")[1])}
        for name, k in (("th", SLOT_TH), ("br", SLOT_BR)):
            s = e.slots.get(k)
            if s is None:
                continue
            tot = sum(s.w.values()) or 1
            ftot = sum(s.f.values()) or 1
            r[name] = {a: s.w.get(a, 0) / tot for a in ("crossfade", "whip_pan", "hard_cut")}
            r[name + "_fast"] = {a: s.f.get(a, 0) / ftot for a in ("crossfade", "whip_pan")}
            r[name + "_active"] = s.active.action if s.active else None
        rows.append(r)

    eng.ingest(data.events(), after_session=cb)
    sup = [x for x in eng.supersessions if (x["owner"], x["observed_in"], x["category"], x["context_key"]) == SLOT_TH]
    trace = json.loads((RESULTS / "drift_trace.json").read_text())["with_statements"]
    xs = [r["n"] for r in rows if "th" in r]
    fig, axes = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
    ax = axes[0]
    ax.plot(xs, [r["th"]["crossfade"] for r in rows if "th" in r], "-o", ms=3, label="crossfade share (slot weights)", color="#1f77b4")
    ax.plot(xs, [r["th"]["whip_pan"] for r in rows if "th" in r], "-o", ms=3, label="whip_pan share (slot weights)", color="#d62728")
    ax.plot(xs, [r["th_fast"]["whip_pan"] for r in rows if "th" in r], "--", lw=1, label="whip_pan share, fast window (change detector)", color="#d62728", alpha=.5)
    ax.axvspan(18, 20.5, color="orange", alpha=.15, label="planted drift ramp (s18-s20, ~65% whip_pan)")
    ax.axvline(21, color="purple", ls=":", label="chat_0005: 'into whip pans after jump cuts'")
    for x in sup:
        ax.axvline(x["session_no"], color="k", ls="--", lw=1)
        ax.text(x["session_no"] + .2, .92, f"SUPERSEDES\n{x['from_action']}->{x['to_action']}\n({x['session_id']})", fontsize=8)
    ax.set_ylabel("share in ava / talking_head + jump_cut")
    ax.set_title("Drift: ava's transition after a jump cut on talking-head footage (synthetic, planted at s18)")
    ax.legend(fontsize=7, loc="center left")
    ax.set_ylim(0, 1.05)
    ax = axes[1]
    xb = [r["n"] for r in rows if "br" in r]
    ax.plot(xb, [r["br"]["crossfade"] for r in rows if "br" in r], "-o", ms=3, color="#2ca02c", label="b-roll slot: crossfade share")
    t = {x["session_no"]: x for x in trace}
    sx = sorted(t)
    ax.step(sx, [1.0 if t[n]["drift_probe"] == "whip_pan" and not t[n]["drift_abstain"] else (0.5 if t[n]["drift_abstain"] else 0.0) for n in sx],
            where="post", color="#d62728", alpha=.7, label="suggest(talking_head, jump_cut): 1=whip_pan, 0=crossfade/other, .5=abstain")
    ax.axvspan(18, 20.5, color="orange", alpha=.15)
    ax.set_xlabel("ava session number")
    ax.set_ylabel("share / answer")
    ax.legend(fontsize=7, loc="lower left")
    ax.set_ylim(-.05, 1.05)
    ax.set_title("Control: b-roll keeps crossfade (must not drift); what suggest() returned after each session")
    fig.tight_layout()
    fig.savefig(DOCS / "drift_timeline.png", dpi=130)
    plt.close(fig)


def growth_plot():
    snaps = json.loads((RESULTS / "graph_growth_all.json").read_text())
    x = list(range(1, len(snaps) + 1))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for lab, col in (("Op", "#1f77b4"), ("Preference", "#d62728"), ("Context", "#2ca02c")):
        ax.plot(x, [s["nodes"].get(lab, 0) for s in snaps], label=f"{lab} nodes", color=col)
    ax.plot(x, [s["supersessions"] for s in snaps], label="SUPERSEDES edges", color="k", ls="--")
    ax2 = ax.twinx()
    ax2.plot(x, [s["relationships"] for s in snaps], color="grey", alpha=.4, label="all relationships")
    ax2.set_ylabel("relationships")
    ax.set_xlabel("sessions replayed (time order, all 106)")
    ax.set_ylabel("nodes / edges")
    ax.set_title("Neo4j graph growing session by session (python -m stylemem.ingest --reset --delay 1)")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(DOCS / "graph_growth.png", dpi=130)
    plt.close(fig)


def eval_plot():
    r = json.loads((RESULTS / "eval.json").read_text())
    labels = ["most frequent\noverall", "most frequent\nper subject", "memory\n(static)", "memory\n(online)"]
    st, on = r["static"]["overall"], r["online"]["overall"]
    vals = [st["baseline_overall_acc"], st["baseline_per_subject_acc"], st["memory_best_guess_acc"], on["memory_best_guess_acc"]]
    ans = [None, None, st["acc_when_answering"], on["acc_when_answering"]]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, vals, color=["#bbb", "#999", "#d62728", "#ff7f0e"])
    for b, v, a in zip(bars, vals, ans):
        ax.text(b.get_x() + b.get_width() / 2, v + .01, f"{v:.1%}" + (f"\n(answering: {a:.1%})" if a else ""), ha="center", fontsize=8)
    ax.set_ylim(0, 1)
    ax.set_ylabel(f"next-preference accuracy, held-out (n={st['n']})")
    ax.set_title("Held-out decisions (synthetic, planted rules): memory vs baselines")
    fig.tight_layout()
    fig.savefig(DOCS / "eval_accuracy.png", dpi=130)
    plt.close(fig)


def _draw(G, path, title):
    colors = {"Creator": "#1f77b4", "Team": "#9467bd", "Brand": "#8c564b", "Context": "#2ca02c",
              "Preference": "#d62728", "PreferenceOld": "#f4a6a6", "Op": "#7f7f7f", "ChatStatement": "#ff7f0e"}
    col = {"Creator": 0, "Team": 0, "Brand": 0, "Preference": 1, "PreferenceOld": 1, "Context": 2,
           "Op": 3, "ChatStatement": 3}
    groups: dict = {}
    for n, d in sorted(G.nodes(data=True), key=lambda x: (x[1]["kind"], x[0])):
        groups.setdefault(col[d["kind"]], []).append(n)
    pos = {}
    for c, ns in groups.items():
        for i, n in enumerate(ns):
            pos[n] = (c * 3.0, -(i - (len(ns) - 1) / 2) * (10.0 / max(len(ns), 1)) * (1.4 if c == 1 else 1))
    fig, ax = plt.subplots(figsize=(15, 11))
    nx.draw_networkx_edges(G, pos, ax=ax, alpha=.35, arrows=True, arrowsize=10, connectionstyle="arc3,rad=0.12")
    nx.draw_networkx_edge_labels(G, pos, edge_labels={(u, v): d["rel"] for u, v, d in G.edges(data=True)
                                                      if d["rel"] in ("SUPERSEDES", "GENERALIZES_TO", "PROPOSES_TO")},
                                 font_size=6, ax=ax, connectionstyle="arc3,rad=0.12")
    for kind, col in colors.items():
        nodes = [n for n, d in G.nodes(data=True) if d["kind"] == kind]
        nx.draw_networkx_nodes(G, pos, nodelist=nodes, node_color=col, node_size=[200 if kind == "Op" else 700 for _ in nodes], ax=ax)
    for n, d in G.nodes(data=True):
        x, y = pos[n]
        ax.text(x + 0.18, y, d["label"], fontsize=7, va="center")
    ax.set_xlim(-0.5, 11.5)
    ax.set_title(title)
    ax.axis("off")
    handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=k) for k, c in colors.items()]
    ax.legend(handles=handles, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def graph_snapshots():
    from .graph import Neo4jGraph
    g = Neo4jGraph()
    G = nx.DiGraph()

    def add_pref(p):
        old = p["status"] != "active"
        G.add_node(p["pref_id"], kind="PreferenceOld" if old else "Preference",
                   label=f"{p['action']}\n{p['observed_in']} {'[' + p['valid_from_session'] + '..' + (p.get('valid_to_session') or '') + ']'}")

    q = """MATCH (o {id:$owner})-[:HAS_PREFERENCE]->(p:Preference {category:$cat})-[:IN_CONTEXT]->(c:Context)
           WHERE p.context_key IN $keys AND p.observed_in = $oi RETURN p, c"""
    keys = ["footage_type=talking_head|prev_edit=jump_cut", "footage_type=broll", "prev_edit=jump_cut",
            "footage_type=talking_head", "*"]
    G.add_node("ava", kind="Creator", label="Creator\nava")
    for r in g.run(q, owner="ava", cat="transition", keys=keys, oi="any"):
        p, c = dict(r["p"]), dict(r["c"])
        add_pref(p)
        G.add_node(c["key"], kind="Context", label=c["key"].replace("|", "\n"))
        G.add_edge("ava", p["pref_id"], rel="HAS_PREFERENCE")
        G.add_edge(p["pref_id"], c["key"], rel="IN_CONTEXT")
    for r in g.run("""MATCH (a:Context)-[:GENERALIZES_TO]->(b:Context) WHERE a.key IN $keys AND b.key IN $keys
                      RETURN a.key AS a, b.key AS b""", keys=keys):
        G.add_edge(r["a"], r["b"], rel="GENERALIZES_TO")
    for r in g.run("""MATCH (n:Preference)-[:SUPERSEDES]->(o:Preference) WHERE n.owner='ava' AND n.category='transition'
                      AND n.observed_in='any' AND n.context_key IN $keys RETURN n.pref_id AS n, o.pref_id AS o""", keys=keys):
        if r["n"] in G and r["o"] in G:
            G.add_edge(r["n"], r["o"], rel="SUPERSEDES")
    for r in g.run("""MATCH (p:Preference {owner:'ava', category:'transition', observed_in:'any',
                      context_key:'footage_type=talking_head|prev_edit=jump_cut'})-[e:EVIDENCED_BY]->(x)
                      WITH p, x, e ORDER BY e.ts DESC WITH p, collect(x)[0..4] AS xs UNWIND xs AS x
                      RETURN p.pref_id AS p, coalesce(x.op_id, x.chat_id) AS x, labels(x)[0] AS l, x.text AS t""" ):
        G.add_node(r["x"], kind=r["l"], label=(r["x"].split("/")[-1] if r["l"] == "Op" else r["x"] + "\n" + (r["t"] or "")[:28]))
        G.add_edge(r["p"], r["x"], rel="EVIDENCED_BY")
    for r in g.run("""MATCH (p:Preference {owner:'ava', category:'transition', observed_in:'any', context_key:'prev_edit=jump_cut'})
                      -[:EVIDENCED_BY]->(c:ChatStatement) RETURN p.pref_id AS p, c.chat_id AS c, c.text AS t"""):
        G.add_node(r["c"], kind="ChatStatement", label=r["c"] + "\n" + r["t"][:30])
        G.add_edge(r["p"], r["c"], rel="EVIDENCED_BY")
    _draw(G, DOCS / "graph_ava_transition_drift.png",
          "Neo4j subgraph: ava's transition preferences (pooled 'any'), supersession + provenance (synthetic)")

    G = nx.DiGraph()
    for n, k in (("ben", "Creator"), ("cara", "Creator"), ("team_northwind", "Team"), ("northwind", "Brand")):
        G.add_node(n, kind=k, label=f"{k}\n{n}")
    G.add_edge("team_northwind", "northwind", rel="USES_BRAND")
    for r in g.run("""MATCH (o)-[:HAS_PREFERENCE]->(p:Preference)-[:IN_CONTEXT]->(c:Context)
                      WHERE o.id IN ['ben','team_northwind'] AND p.status='active' AND
                      ((p.category='caption_style' AND p.context_key='format=9:16') OR (p.category='font' AND p.context_key='*'))
                      RETURN o.id AS o, p, c.key AS c"""):
        p = dict(r["p"])
        add_pref(p)
        G.nodes[p["pref_id"]]["label"] = f"{p['category']}: {p['action']}\n{p['owner']}/{p['observed_in']} share {p.get('share', 0):.2f}"
        G.add_node(r["c"], kind="Context", label=r["c"])
        G.add_edge(r["o"], p["pref_id"], rel="HAS_PREFERENCE")
        G.add_edge(p["pref_id"], r["c"], rel="IN_CONTEXT")
    for r in g.run("""MATCH (b:Brand)-[:HAS_PREFERENCE]->(p:Preference {category:'font'}) RETURN p"""):
        p = dict(r["p"])
        G.add_node(p["pref_id"], kind="Preference", label=f"brand font {p['polarity']}: {p['action']}")
        G.add_edge("northwind", p["pref_id"], rel="HAS_PREFERENCE")
    for r in g.run("""MATCH (a:Creator)-[:SAID]->(c:ChatStatement) WHERE c.chat_id IN ['chat_0007','chat_0008','chat_0011','chat_0012','chat_0014','chat_0015']
                      OPTIONAL MATCH (c)-[x:PROPOSES_TO]->(t:Team) RETURN a.id AS a, c.chat_id AS c, c.text AS t, t.id AS team"""):
        G.add_node(r["c"], kind="ChatStatement", label=r["c"] + "\n" + r["t"][:34])
        G.add_edge(r["a"], r["c"], rel="SAID")
        if r["team"]:
            G.add_edge(r["c"], r["team"], rel="PROPOSES_TO")
    for r in g.run("""MATCH (p:Preference)-[:EVIDENCED_BY]->(c:ChatStatement) WHERE c.chat_id IN ['chat_0007','chat_0008','chat_0011','chat_0012','chat_0015']
                      RETURN p.pref_id AS p, c.chat_id AS c"""):
        if r["p"] in G:
            G.add_edge(r["p"], r["c"], rel="EVIDENCED_BY")
    _draw(G, DOCS / "graph_ben_team_brand_conflict.png",
          "Neo4j subgraph: ben (private) vs team_northwind (team) vs northwind (brand): captions 9:16 and fonts")
    g.close()


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    drift_plot()
    growth_plot()
    eval_plot()
    graph_snapshots()
    print("wrote", sorted(p.name for p in DOCS.glob("*.png")))


if __name__ == "__main__":
    main()
