# Demo #8: style memory (Python + Neo4j)

This demo learns how a creator or team edits from the shared op log, and stores those preferences in a temporal Neo4j graph. When asked what an edit should be, it returns a suggestion with confidence, reasons, source ops, any conflicts, and brand blocks. It abstains when it doesn't know.

All inputs are read-only from `../data/`. Memory is derived from the same `ops.jsonl` + chat stream that `vcs/` uses.

## Design in one screen
- **Structured ops map to graph edges deterministically. There's no LLM in this step.** Director edits, user accepts, overrides and undos all become weighted evidence (override/edit 1.0, accept 0.7, reject −0.5). An accept followed by an undo counts as nothing.
- **The LLM is used only for the 15 chat statements** (`stylemem/extract.py`), which it extracts into a fixed JSON schema validated against the vocab. With no `XAI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in the env, a lexicon-based extractor runs instead, and it's labelled `rule_based_fallback` everywhere. The checked-in run used that lexicon.
- **Graph.** Node types are `Creator`, `Team`, `Brand`, `Context`, `Preference`, `Op`, `Session`, `ChatStatement` and `Project`.
  - A `Preference` has `category, action, scope, observed_in, valid_from/valid_to (+session), share, decayed_weight, support_count, status`.
  - Provenance edges run `(:Preference)-[:EVIDENCED_BY {kind,weight}]->(:Op|:ChatStatement)` and `-[:CONTRADICTED_BY]->(:Op)`.
  - Supersession is recorded as `(:Preference)-[:SUPERSEDES]->(:Preference)`.
  - The context lattice is `(:Context)-[:GENERALIZES_TO]->(:Context)`.
  - Brand rules are stored as `scope:'brand'` preferences.
- **Time and drift.** Each (owner, pool, category, context) slot keeps slow weights, which give share and confidence, and a fast window with a half-life of 3 sessions.
  - Supersession: a challenger that leads the fast window with ≥60% share in **2 consecutive sessions with new evidence** closes the old preference (`valid_to`) and opens a new one.
  - Slow weights then restart from the fast window, so the old style doesn't linger.
  - An explicit statement for a different action supersedes immediately.
- **Streaming ingest.** Ingest runs session by session. Explicit statements and brand-violating ops apply immediately. Implicit evidence is aggregated per session.
- **Context fallback.** The search goes from specific to broad. Keys are dropped least-informative first, using mutual information computed from the graph's counts.
  - A narrower slot is skipped as **stale** when a broader context superseded its action after the narrower slot's last evidence. That rule applies only when the narrower slot has also seen the new action, or when its extra keys are uninformative given the broader context.
- **Scoping.**
  - Brand hard constraints always win; they filter.
  - In private projects, private preferences beat team ones.
  - In team projects the team standard applies, and the creator's private preference is returned in `conflicts[]` with both sides' source ops plus chat citations.
  - Team standards come only from the team lead's statements and team-project behaviour. A non-lead's "the team should…" is recorded as a proposal with no weight.
- **`suggest(context, actor, project_scope)`** returns `{action, best_guess, confidence, why[], source_events[], conflicts[], blocked[], abstain}`.
  - Confidence is the Wilson lower bound on share, with n = decayed support, so 3/3 gives 0.62, not 100%.
  - It abstains when confidence is below 0.5, or when no key of the context was ever observed for that owner and category.

The knobs live in `stylemem/config.py`: backoff from the specific context, no slow decay, a fast half-life of 3 sessions, supersession after 2 sessions, `level_first` pool order. `python -m stylemem.evaluate --grid` is a local sweep over the synthetic train sessions. It is not a published score.

## Run
```bash
# one-time: Neo4j 5 (Java 17+) on bolt://localhost:7687, user neo4j / password cardboard-demo
#   (override with NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD)
cd memory
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

python -m stylemem.ingest --reset                 # full history -> Neo4j (~12 s). --split train, --step, --delay 0.5, --until ava_s20 for live replay
python -m stylemem.evaluate --grid                # local recovery check on the planted split; not a result to cite
python -m stylemem.demos                          # drift / conflict / brand / abstain JSON -> docs/results/
python -m stylemem.plots                          # PNGs -> docs/
pytest -q tests                                   # 16 tests (scoping, supersession, abstain, extraction, engine==Neo4j)
uvicorn stylemem.api:app --port 8808              # API
python -m stylemem.agent --api http://localhost:8808 --project proj_team_northwind_11 --actor ben
```

API (port 8808):
- `POST /suggest`, body `{"context":{...},"actor":"ava","project_scope":"private","project_owner":null,"brand_id":null,"category":"transition"}`. Omit `category` to get a list covering every category.
- `GET /check?category=transition&action=glitch&brand_id=northwind`
- `GET /stats`
- `GET /preferences/history?owner=ava&category=transition&context_key=footage_type=talking_head|prev_edit=jump_cut`
- `POST /reload`
- `GET /health`

The agent (`stylemem/agent.py`, LangGraph) runs `plan_slots → query_memory → brand_gate → compose_ops → (llm_note)`. It pre-fills VCS-vocabulary ops (`add_transition`, `add_effect`, `trim_clip`, `add_clip`, `add_text`). Each op carries `memory.{action,confidence,why,source_events,conflicts,blocked}`, and abstentions become `ask_user` items. It works without an LLM.

## Demo script (5 minutes)
1. **Graph growing:** `python -m stylemem.ingest --reset --step --delay 0.3`. Watch Neo4j Browser at http://localhost:7474 with `MATCH (p:Preference) RETURN count(p)`. Then see `docs/graph_growth.png`.
2. **Drift:** run `curl -s localhost:8808/preferences/history?owner=ava\&category=transition\&context_key=footage_type%3Dtalking_head%7Cprev_edit%3Djump_cut`. It shows crossfade closed at ava_s23 and whip_pan opened. B-roll stays crossfade. See `docs/drift_timeline.png` and `docs/graph_ava_transition_drift.png`.
3. **Team conflict:** suggest `caption_style` at `{talking_head, jump_cut, 9:16, hook}` for ben.
   - With `project_scope=team, project_owner=team_northwind`, it returns stacked, with beast_mode in `conflicts[]` citing chat_0014.
   - With `project_scope=private`, it returns beast_mode with no conflict.
   - See `docs/results/demo_conflict.json`.
4. **Brand:** `font` for ben with `brand_id=northwind` returns Montserrat, with Bebas Neue in `blocked[]`. Without a brand it returns Bebas Neue. `/check` blocks glitch, Comic Sans MS and dramatic_horror. See `docs/results/demo_brand.json`.
5. **Abstain:** an unseen or sparse context returns `abstain:true` with the reason. See `docs/results/demo_abstain.json`.
6. **Agent:** `python -m stylemem.agent ...` pre-fills ops with reasons and conflicts.

Neo4j Browser queries:
```cypher
MATCH (p:Preference)-[:IN_CONTEXT]->(c:Context {key:'footage_type=talking_head|prev_edit=jump_cut'})
WHERE p.pref_id STARTS WITH 'ava/any/transition/' RETURN p.action, p.valid_from_session, p.valid_to_session, p.status
MATCH g=(new:Preference)-[:SUPERSEDES]->(old:Preference) WHERE new.pref_id STARTS WITH 'ava/' AND new.category='transition' RETURN g LIMIT 25
MATCH g=(:Team)-[:HAS_PREFERENCE]->(p:Preference {status:'active', category:'caption_style'})-[:EVIDENCED_BY]->(:Op) RETURN g LIMIT 50
```

## What a run shows
The synthetic history is a scripted example of the method, not a measurement.

- Ava's talking-head jump cuts are planted to move from crossfade to whip pan. After ingest, that slot's active preference changes and the b-roll slot stays crossfade. `docs/drift_timeline.png` and `docs/graph_ava_transition_drift.png` are pictures of that.
- Ben's private captions and the team's captions are different preferences. A team project returns the team one and carries his private one as a conflict. `docs/graph_ben_team_brand_conflict.png`.
- A Northwind project filters a banned font or transition out of the suggestion.
- An unseen situation comes back as an abstention.
- `docs/graph_growth.png` is the graph filling in as sessions are ingested.

`python -m stylemem.evaluate` writes a local file under `docs/results/`. It checks whether the engine recovers the planted rules. Do not cite it as accuracy.

## Honesty notes / known gaps
- **No real LLM was used.** The statement extractor ran as `rule_based_fallback`. The LLM path (`LLMExtractor`, using xAI, OpenAI-compatible or Anthropic over httpx, with fallback on error) is untested live, because no key was available.
- **The slot-selection design was shaped on this same synthetic world.** That covered the backoff chain, the staleness rule and its informativeness gate, and accept-then-undo labelling.
- In ben's *own* project under team scope, captions come back as beast_mode (his team-scope behaviour), with the team's stacked surfaced as a conflict. `scope_first` would return the team standard there. `level_first` is what `config.py` uses.
- Chat statements repeat a signal the edits already carry, on this synthetic data.
- Data is synthetic: 3 people, 106 sessions. Context is limited to 4 keys.
