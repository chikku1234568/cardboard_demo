"""Replay the shared op-log-derived event stream into Neo4j.

  python -m stylemem.ingest --reset                    # full replay (all splits), as fast as possible
  python -m stylemem.ingest --reset --split train      # what the evaluation memory sees
  python -m stylemem.ingest --reset --step             # session by session, press Enter to advance
  python -m stylemem.ingest --reset --delay 1.5        # live: one session every 1.5 s (watch Neo4j Browser)
  python -m stylemem.ingest --reset --until ava_s20    # stop after a session

Explicit chat statements and brand violations are flushed to Neo4j the moment they arrive;
implicit evidence is aggregated and written at each session end.
"""
from __future__ import annotations

import argparse
import json
import time

from . import data
from .config import DEFAULT, RESULTS
from .engine import MemoryEngine
from .graph import Neo4jGraph


def build(split: str | None = None, reset: bool = True, step: bool = False, delay: float = 0.0,
          until: str | None = None, snapshots: bool = True, verbose: bool = True):
    g = Neo4jGraph()
    if reset:
        g.reset()
    g.setup()
    eng = MemoryEngine(DEFAULT, sinks=[g])
    t0 = time.time()

    def after(e, sid):
        if snapshots:
            snap = g.snapshot(sid)
        if verbose:
            m = e._session_meta[sid]
            new_sup = [s for s in e.supersessions if s["session_id"] == sid]
            print(f"[{len(e.sessions_done):3d}] {sid:24s} {m['project_id']:26s} {m['project_scope']:7s} "
                  f"prefs={snap['active_preferences'] if snapshots else '-':>5} sup+={len(new_sup)}")
        if step:
            input("  Enter for next session...")
        elif delay:
            time.sleep(delay)

    eng.ingest(data.events(split), after_session=after, until_session=until)
    eng.finalize()
    stats = g.stats()
    if snapshots:
        RESULTS.mkdir(parents=True, exist_ok=True)
        name = f"graph_growth_{split or 'all'}.json"
        (RESULTS / name).write_text(json.dumps(g.snapshots, indent=1))
    if verbose:
        print(json.dumps(stats, indent=1))
        print(f"extractor: {eng.extractor.name}; {len(eng.statements)} chat statements; "
              f"{len(eng.violations)} brand-violation ops; {len(eng.supersessions)} supersessions; {time.time() - t0:.1f}s")
    g.close()
    return eng, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["all", "train", "heldout", "flagship"], default="all")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--until")
    ap.add_argument("--no-snapshots", action="store_true")
    a = ap.parse_args()
    build(None if a.split == "all" else a.split, a.reset, a.step, a.delay, a.until, not a.no_snapshots)


if __name__ == "__main__":
    main()
