# vcs: version control for video timelines (demo #10)

This is a TypeScript implementation of the timeline VCS described in the top-level README. It reads and writes the same content-addressed store that `generators/vcs_store.py` produces. The bytes and hashes are identical, and that is checked against all 36 projects in `test/store.test.ts`.

| piece | path | what it does |
|---|---|---|
| core | `core/` | Store (objects / versions / refs / ops.jsonl), commit, branch from any version, checkout, log, common ancestor. There is no `main`. Clip ids are stable and carry `lineage` (split → `parent`). |
| semantic diff | `core/diff.ts` | Two timelines go in. Out come structured items (`kind`, `entity`, `track_id`, `range`, `before`/`after`) plus an editor sentence for each. |
| selective pull | `core/pull.ts` | You pick ops, or a whole branch since the common ancestor. Non-overlapping ops auto-apply. If two branches edit the same clip (or its lineage) differently, or their edits overlap in time on one track, that is a conflict. Resolving it with ours/theirs creates a **new version** on the target branch, never a merge into a main. |
| renderer | `core/render.ts` | ffmpeg renders any version: V1 sequence with `xfade` transitions, V2 overlays, drawtext titles, colour grades, audio mix with volume. |
| CLI | `cli/index.ts` | `log`, `diff`, `pull`, `resolve`, `render`, `branch`, `checkout`, `reset`. |
| API + UI | `server/`, `web/` | Fastify API on :4310 serving a React/Vite UI: project picker, peer branch list and graph, version timeline strips, diff sentences, pull dialog, conflict view (base/ours/theirs, click to pick), and render & play. |

`data/` is never modified. Every write goes to a workspace copy at `vcs/.workspace/projects/<pid>`, which is created from `data/vcs/projects/<pid>` on first use or on `reset`.

## Run

```bash
cd vcs
npm i
npm test                 # vitest: store compat, 4 flagship pull expectations, conflict + resolve, diff sentences
npm run typecheck
npm run demo             # renders renders/demo/01..05 *.mp4 (base, A, B, A+B pulled, conflict resolved)
npm start                # builds web/ and serves UI + API on http://localhost:4310 (PORT=... to change)
npm run screenshots      # with the server running: headless Chrome → docs/screenshots/
```

Requirements: Node ≥ 20 and ffmpeg with `xfade` (tested on 7.1). The screenshots step uses `playwright-core` with Chrome at `/usr/bin/google-chrome`; set `CHROME=...` to override.

CLI (default project `proj_launch_week_recap`; `-p <pid>` picks another one):

```bash
npm run vcs -- branches
npm run vcs -- log ava/tighter-cut
npm run vcs -- diff rough-cut ava/tighter-cut          # editor sentences (add --json for structured items)
npm run vcs -- pull --into ava/tighter-cut --from cara/titles-broll --dry-run
npm run vcs -- pull --into ava/tighter-cut --from cara/titles-broll
npm run vcs -- pull --into ava/tighter-cut --from ben/alt-pacing    # → conflict op_0001 vs op_0006
npm run vcs -- resolve --into ava/tighter-cut --from ben/alt-pacing --pick op_0006=theirs
npm run vcs -- pull --into ava/tighter-cut --from cara/titles-broll --ops op_0003   # cherry-pick one op
npm run vcs -- render ava/tighter-cut -o /tmp/a.mp4
npm run vcs -- reset                                    # back to data/
```

## Flagship story (`proj_launch_week_recap`)

The project starts from `rough-cut` (29s). Three peers branch off it, and a fourth branch comes off one of them:

| branch | who | ops | sentences (diff vs its parent) |
|---|---|---|---|
| `ava/tighter-cut` (A) | ava | op_0001, op_0002 | Trimmed 1.2s off the end of the B-cam clip at 0:09.0 (now 3.8s, ends at 0:12.8) · Swapped the crossfade at 0:03.0 for a whip pan (0.5s → 0.3s) |
| `cara/titles-broll` (B) | cara | op_0003–5 | Moved the product B-roll clip 1.5s later (0:15.5 → 0:17.0) on V2 · Changed the title text from “Launch Week Recap” to “Launch Week: Recap” · Added a lower-third “Shipping in 3 days” at 0:15.6 for 2.5s (Inter) |
| `ben/alt-pacing` (C) | ben | op_0006, op_0007 | Trimmed 0.6s off the end of the B-cam clip at 0:09.0 (now 4.4s, ends at 0:13.4) · Lowered the volume of the upbeat music from 80% to 50% |
| `ava/alt-ending` (D, off A) | ava | op_0008+ | Split the B-cam clip (0:25.0–0:29.0) at 0:27.0 and dropped the 2.0s tail… · Added the desk B-roll on V2 at 0:22.0 (3.0s) · Added a slide transition at 0:19.0 · grade changes |

The expected pull results in `data/vcs/projects/proj_launch_week_recap/expected/` are reproduced exactly by `test/pull.test.ts`:

- B → A: clean.
- C → A: conflict between op_0001 and op_0006. Both trim clip `clp_0003`, and their V1 ranges overlap at 13.4–14.0. op_0007 auto-applies.
- B → D: clean.
- C → B: clean.

## 60-second demo script (Loom)

1. Run `npm start`, open http://localhost:4310, and click **Reset demo**. The left column lists the branches as peers: rough-cut, ava/…, cara/…, ben/…. There is no main. The graph underneath shows each branch as a lane.
2. Click **ava/tighter-cut** and set *compared with* to `rough-cut`. The diff panel shows two sentences: the trim and the crossfade → whip pan. The before/after strips highlight the changed clip and transition. (`docs/screenshots/01_diff_A_vs_base.png`)
3. Optionally click **ava/alt-ending** to show split lineage: "Split the B-cam clip … at 0:27.0". (`02_…`)
4. On ava/tighter-cut, click **Pull from another branch…** and choose `cara/titles-broll`. All three ops are marked AUTO because they touch different clips, tracks and times. Click **Pull 3 changes**. The result is a new version on ava's branch, and the graph draws a dashed edge to show the pull. (`03_…`, `04_…`)
5. Pull again from `ben/alt-pacing`. This opens the conflict view. Ava trimmed the B-cam clip to 12.8 and Ben trimmed it to 13.4, so the overlapping 13.4–14.0 range is highlighted on the base, ours and theirs strips. The volume change is applied automatically. (`05_…`)
6. Click **Take theirs**, then **Create resolved version**. The diff now reads "Extended the end of the B-cam clip … by 0.6s" and "Lowered the volume … 80% to 50%". (`06_…`)
7. Click **Render & play** to get an ffmpeg render of that exact version, including the whip-pan xfade, lower-third and ducked music. (`07_…`) Pre-rendered copies of every step are in `renders/demo/` (see `renders.json`).

## Known gaps (honest)

- Renders are always 640×360 at 30fps. The timeline's `format`/`aspect` is not applied, and text animations are ignored. `whip_pan` and `slide` map to the closest ffmpeg `xfade` transitions.
- There is no ripple, so a trim leaves a gap and the gap renders as black. This matches the op semantics in `generators/timeline_ops.py`.
- A pull commit has one parent (the target branch head) plus a `pulled_from` field. Pulled ops carry `pulled_from_op`, so re-pulling skips them. There is no merge commit.
- Taking "theirs" on a conflict re-bases that op's `before` onto the current values and then applies its `after`. There is no field-level three-way merge inside one clip.
- The workspace is a local copy. There is no auth, no multi-user server state, and no locking.
