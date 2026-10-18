# cardboard_demo

These are two small weekend demos for problems on [Cardboard](https://www.cardboard.ai)'s hard-problems list. Cardboard is an agentic, browser-based video editor. This repo holds the synthetic input data and generators, plus both demos: `vcs/` (#10) and `memory/` (#8).

The proposal is the method, drawn in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): version control for one timeline, a style memory derived from that history, and the loop that connects them. The synthetic world is there so the method can be traced. It is not a measured result.

Start with the architecture page. Then [data/README.md](data/README.md) for how the fake world is laid out, [vcs/README.md](vcs/README.md) to run the timeline demo, and [memory/README.md](memory/README.md) to run the memory demo. [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) is the long version.

## Demo #10: version control for video timelines
**Status: built, in [`vcs/`](vcs/README.md)** (TypeScript core + CLI + ffmpeg renderer + React UI). Run it with `cd vcs && npm i && npm test && npm run demo && npm start` and open http://localhost:4310. The demo script is in [vcs/README.md](vcs/README.md).

- **The timeline JSON is the source of truth.** A rendered mp4 is a disposable artifact, and media is referenced by sha256 rather than copied.
- **Versioning is per project, and there is no main branch.** Any collaborator can branch from any version. Branches are peers. You can go back to an older version, keep going, or start a new branch from it.
- **Storage is content-addressed and append-only.** Snapshots and version objects are named by their hash, and branch refs point at versions. This is the same design as Bit, version control for Excel.
- **Clip IDs stay stable and carry lineage.** A split gives two new clips whose `parent` is the original. That way a diff can say "split clip X", and a conflict check can follow a clip across splits.
- **Diffs are semantic.** They're built from the ops' `before`/`after` and describe what an editor did: trimmed by 1.2s, moved 1.5s later, swapped a crossfade for a whip pan.
- **Selective pull replaces merge.** You choose ops from another branch and apply them to yours. A conflict is raised when two ops touch the same clip (or lineage), or when they touch overlapping time ranges on the same track. Finished videos are never blended together.

## Demo #8: learning a creator's style over time
**Status: built, in [`memory/`](memory/README.md)** (Python + Neo4j + FastAPI + LangGraph agent). To run it, start Neo4j on bolt://localhost:7687, then `cd memory && pip install -r requirements.txt && python -m stylemem.ingest --reset && pytest -q tests && uvicorn stylemem.api:app --port 8808`. The demo script is in [memory/README.md](memory/README.md).

- **Structured signals come first.** All of them except brand rules are derived from the shared op log and chat log. They are:
  - Director edit ops.
  - User accept, override, or undo of those edits, recorded as `accept`, `replaces_op` and `reverts_op` in the VCS history. An override records the action it replaced.
  - Explicit chat statements.
  - Brand guideline JSON, applied as hard constraints.
- **Scoping.** Preferences are kept per creator and per team, and conflicts between them are surfaced rather than silently resolved.
- **Time.** Preferences carry `valid_from` and `valid_to`. When a new choice consistently takes over, the old preference is superseded, so the memory follows drift without holding on to old style.
- **Context.** A small fixed set of keys: `footage_type`, `prev_edit`, `format`, `section`.

## One world, one input stream
Both demos read the same synthetic world.

- **Cast.** Two creators (`ava`, `ben`) and a team lead (`cara`) work on one team, `team_northwind`, under the `northwind` brand. A Director agent acts on behalf of whoever is in the session.
- **Projects.** There are 36 projects. Each has its own content-addressed store, and every session is a branch.
- **The op log is the shared input.** Every edit, including the user's accept, override or undo of a Director edit, is a VCS op in a project's `ops.jsonl`.
- **Memory is derived.** The #8 memory events are *derived* from those op logs plus a chat log, by `generators/derive_events.py`. They are never generated separately.
- **The flagship #10 scenario is one of the 36 projects.** `proj_launch_week_recap` contains a base version and branches A to D, with a planned conflict. Its ops also appear as memory events.

See [`data/README.md`](data/README.md) for the diagram, a file-by-file table, the counts, and the planted rules.

## Layout
```
memory/                stylemem/ (engine, Neo4j graph, suggest, API, agent), tests/, docs/ (graph pictures, example suggestions)
vcs/                   timeline VCS demo (#10)
generators/
  world_spec.py        cast, brand, vocab, planted rules, statements, planted violations (pure data)
  gen_media.py         tiny labelled test media + manifest with sha256
  timeline_ops.py      op semantics (apply op -> new timeline), incl. accept/revert review ops
  vcs_store.py         per-project store, op construction, touches, conflict rule
  flagship.py          the scripted #10 scenario + expected selective-pull results
  gen_world.py         all projects/sessions/ops/chat + ground truth
  derive_events.py     op logs + chat -> memory events, decisions, splits
  validate.py          integrity, cross-link and replay checks, quick stats
  minischema.py        tiny JSON Schema subset validator (uses jsonschema if installed)
data/
  README.md            how the data connects (diagram + file table)  <- read this
  media/               11 assets (~1.4 MB) + manifest.json
  world/               cast, brand_guidelines, vocab, sessions, chat, planted_labels
  vcs/                 schema.md, timeline.schema.json, projects/<project_id>/{project.json, ops.jsonl, store/}
  memory/              ground_truth.json + DERIVED events.jsonl, decisions_labeled.jsonl, splits/
scripts/generate_all.sh
Makefile
```

## Regenerate
Requires Python 3.10+ and ffmpeg/ffprobe with `drawtext` (libfreetype).
```
make data            # media (if missing) -> world -> derive -> validate
make world           # regenerate projects/sessions/chat/ground truth
make derive          # re-derive memory events from the op logs + chat
make validate        # checks only
make media           # force re-encode media
```

## Honesty notes
- All data is synthetic. Preferences are planted, as documented in `data/memory/ground_truth.json`.
- This repo does not report accuracy. `memory/stylemem/evaluate.py` can replay the planted world as a local check that the engine follows those rules. That check is not a result.
