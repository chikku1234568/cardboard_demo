# Timeline schema (demo model, v1.0)

The machine-readable version is [`timeline.schema.json`](timeline.schema.json) (JSON Schema 2020-12). `generators/validate.py` checks every snapshot against it, along with some invariants the schema can't express.

**The timeline JSON is the source of truth.** A rendered video is a disposable artifact that can always be rebuilt from a timeline plus the media referenced by hash.

## Top level

| field | type | notes |
|---|---|---|
| `schema_version` | `"1.0"` | |
| `project_id`, `name` | string | Versioning happens at the project level. |
| `settings` | object | `fps`, `width`, `height`, `sample_rate`, `time_unit` (always `"seconds"`), `aspect` (`16:9`, `9:16` or `1:1`). The test media stays 640x360; `aspect` is the target format. |
| `tracks[]` | Track | Order is stacking order. |
| `transitions[]` | Transition | Sorted by id. |
| `text_layers[]` | TextLayer | Sorted by id. |
| `effects[]` | Effect | Sorted by id. |

All times are **absolute timeline seconds**, rounded to milliseconds. Ripple is not implicit. An op that shortens a clip can leave a gap, and ripple is meant to be recomputed as a separate step. Generated session branches often have gaps after trims.

## Track
`id` (`V1`, `V2`, `A1`, `T1`...), `kind` (`video` | `audio` | `text`), `name`, `clips[]`. Clips on a track must not overlap.

## Clip
| field | notes |
|---|---|
| `id` | Stable id (`clp_0001`). Survives trims and moves. |
| `asset_hash` | `sha256:<hex>` of the media file. Media is never copied into versions. See `data/media/manifest.json`. |
| `asset_label` | Human-readable hint only. Not used for identity. |
| `src_in`, `src_out` | Range in source media seconds. Requires `0 <= src_in < src_out <= asset duration`. |
| `timeline_start` | Where the clip starts on the timeline. End = `timeline_start + (src_out - src_in) / speed`. |
| `speed`, `volume` | Defaults are 1.0. Volume range is 0 to 2. |
| `music_mood` | Optional, on music clips. |
| `lineage` | `{origin, parent, created_by_op}`. A split creates **two new ids**, and both have `parent` = the original clip and the same `origin`. Lets diff/blame say "split clip X" instead of "removed 1, added 2", and lets conflict checks follow a clip through splits. |

## Transition
`id`, `track_id`, `from_clip`, `to_clip` (adjacent clips on the same track), `type` (`crossfade` | `whip_pan` | `dip_to_black` | `slide` | `zoom_punch` | `hard_cut` | `glitch`; glitch is off-brand and only shows up as a Director proposal that the user then overrides), `duration` (seconds, centred on the cut). When a clip is split, transitions are rewired to the matching half.

## TextLayer
`id`, `track_id` (must be a `text` track), `role` (`title`, `lower_third` or `captions`), `text`, `start`, `duration`, `style {font, size, color (#RRGGBB), position, animation?, preset?}`. Captions keep their style preset in `style.preset`.

## Effect
`id`, `target_id` (a clip id), `type` (`color_grade` | `blur` | `vignette` | `grain` | `speed_ramp`), `params` (free-form, e.g. `{look, intensity}`). Splitting a clip duplicates its effects onto the right half with the id suffix `_r`.

## Store layout (one content-addressed, append-only store per project)

```
projects/index.json                      every project: owner, scope, brand, sessions, counts
projects/<project_id>/project.json       owner_subject, project_scope, brand_id, sections (footage + cut type per section)
projects/<project_id>/ops.jsonl          every op of that project, in order (users AND the Director)
projects/<project_id>/store/objects/<sha256>.json   timeline snapshot as canonical JSON bytes; sha256(file) == filename
projects/<project_id>/store/versions/<sha256>.json  {project_id, timeline_hash, parents[], branch, author, timestamp,
                                                     message, op_ids[], session_id}; filename = sha256(canonical meta)
projects/<project_id>/store/refs.json    branch -> {head, created_from, created_by, created_at}. There is NO main.
projects/proj_launch_week_recap/         the flagship #10 scenario, plus scenario.json, base_timeline.json, heads/, expected/
```

Canonical JSON means `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8. Identical timelines share one object. For example, a review commit that only accepts changes produces the same snapshot as its parent, so there are fewer snapshots than versions.

In the generated world, each project starts with an import commit on `rough-cut`. **Each session is its own branch** (`<actor>/cut-NN`) taken from that import. A session branch holds an optional `set_format` commit, then pairs of commits: one where the Director proposes ops, and one where the user reviews them.

## Op log entry (`ops.jsonl`)

```json
{"op_id": "proj_ava_02/op_0004", "project_id": "proj_ava_02", "session_id": "ava_s04", "branch": "ava/cut-04",
 "parent_version": "<version id>", "result_version": "<version id>",
 "author": "director", "on_behalf_of": "ava", "timestamp": "2026-06-22T19:00:35+05:30",
 "type": "add_transition", "decision_id": "ava_s04_d04",
 "context": {"footage_type": "talking_head", "prev_edit": "scene_change", "format": "16:9", "section": "body"},
 "replaces_op": null, "reverts_op": null, "accepts_op": null,
 "payload": {"transition": {"id": "tr_02", "track_id": "V1", "from_clip": "clp_0002", "to_clip": "clp_0003",
                            "type": "crossfade", "duration": 0.4}},
 "touches": {"entities": ["transition:tr_02"], "ranges": [{"track_id": "V1", "start": 10.8, "end": 11.2}]}}
```

- `author` is a person (`ava`, `ben`, `cara`) or `director`. A Director op always has `on_behalf_of` set.
- `before` and `after` in the payload act as both a precondition and the source for diffs.
- `touches` lists the entities and the time ranges on each track that the op affects. It is computed against the timeline the op applied to, so a conflict check needs no replay. For a trim, the range covers only the stretch between the old edge and the new edge.
- `context` uses the four memory context keys. A key is `null` when it doesn't apply, for example on `set_format`.

### Review ops (how accept / override / undo live in history)
| user reaction | op(s) |
|---|---|
| accept | `accept` with `accepts_op` = the Director op. The timeline doesn't change. |
| override | A normal op that edits the same entity in place, with `replaces_op` = the Director op. For example, `change_transition`, `set_text_style`, `update_effect`, `trim_clip` or `replace_music`. |
| undo | `revert` with `reverts_op` = the Director op. `payload.inverse` is the inverse op that gets applied, e.g. `remove_transition`. |
| undo, then pick something else | `revert` followed by a re-add op with `replaces_op` set |

### Op types (`generators/timeline_ops.py`)
- **Structural:** `add_track`, `add_clip`, `delete_clip`, `trim_clip`, `move_clip`, `split_clip`, `set_volume`, `set_speed`.
- **Transitions:** `add_transition`, `change_transition`, `remove_transition`.
- **Text:** `add_text`, `edit_text`, `remove_text`, `set_text_style`.
- **Effects:** `add_effect`, `update_effect`, `remove_effect`.
- **Other:** `replace_music`, `set_format`.
- **Review:** `accept`, `revert`.

### Which ops carry a style preference (read by `derive_events.py`)
| category | ops |
|---|---|
| transition | `add_transition.type`, `change_transition.after.type` |
| font | `add_text` with role `title` or `lower_third`, `set_text_style.after.font` |
| caption_style | `add_text` with role `captions` (`style.preset`), `set_text_style.after.preset` |
| color_grade | `add_effect` of type `color_grade` (`params.look`), `update_effect.after.params.look` |
| cut_length | `trim_clip`, bucketed by the resulting clip length: short < 1.5 s ≤ medium ≤ 4 s < long |
| music_mood | `add_clip.clip.music_mood`, `replace_music.after.music_mood` |

The test media only has two music beds, so `music_mood` is a label on the clip. `upbeat` and `corporate` use `music_upbeat`; every other mood uses `music_calm`.

## Selective pull and the conflict rule
There is no main branch to merge into. Each branch is a peer and can start from any version. To bring work across, you **pull selected ops** from another branch onto yours, and the result is a new version on your branch. Two ops conflict when either of these is true:
1. they touch the same entity (a clip id or clip lineage origin, a transition, a text layer, or an effect) with different results, or
2. they touch overlapping time ranges on the same track.

`generators/vcs_store.py::ops_conflict` is the reference implementation. The flagship's expected outcomes are in `projects/proj_launch_week_recap/scenario.json`.
