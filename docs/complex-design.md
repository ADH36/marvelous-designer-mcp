# Complex garment controls (v0.5)

Version 0.5 adds 25 tools, bringing the total to **67**. These enable more of the
construction, arrangement and inspection cycle for a garment controlled through
Codex or Hermes. They do not turn measurements into a finished, autonomously
fitted garment. New signatures come from the [official MD API list](https://developer.marvelousdesigner.com/list.html)
and [API options](https://developer.marvelousdesigner.com/optiontype.html).
The subsequent MD 2026.0.315 live session exercised all 25 new v0.5 tool names
and built two basic garments; not every parameter branch or complex workflow
was covered. The report led to [v0.6 implementation changes](live-test-improvements.md).
Those changes are **untested**, at the user's request.
Version 0.7 adds [geometric fit diagnostics, construction drafts and fuller recovery](remaining-gaps.md),
also without tests or live calls.
Check installed signatures with `md_api` and trial on a checkpointed disposable
project before using them on production garments.

## Geometry and construction

`create_curved_pattern` takes native `[x,y,type]` vertices. Types are 0=straight,
2=spline, 3=Bezier. Input validation checks unique finite vertices and the polygon
formed by their chords; it cannot prove the resulting curve is nonintersecting.
Native Bezier control handles are not exposed by this wrapper. Inspect curves in
the 2D editor and previews. Do not call a curved piece a fitted sleeve automatically.

`create_internal_shape` creates an open internal line or closed construction
shape using the same vertex format. It reports MD's returned child index.
`sew_internal_edges` accepts optional `child_a` and `child_b`: omitted child means
boundary, supplied child means internal shape. At least one must be internal.
Boundary bounds and internal lengths are checked before the documented sewing
overload. A returned child index or successful seam call still needs visual
inspection. These tools do not automatically create darts, fold angles, seam
allowances, pleat ratios, buttons or zippers.

`move_pattern_2d` changes the piece's **2D editor position**, not its position on
the avatar. `copy_pattern` makes a native duplicate with a 2D offset. Neither
performs dimensional grading. `export_pattern_json` exports actual MD-native
JSON; external edits can be reimported through `import_pattern_json`, which saves
a checkpoint first. Native JSON is separate from version-1 garment recipe JSON.
Do not invent the native schema: export and inspect it on the installed version.
After import, inspect geometry and sewing, refresh indices, and explicitly rebind
references. Round-trip import does not promise preservation of all seam mappings.
Version 0.6 defaults to restoring known resolution, layer, solidify and fabric
assignment through unique names. Replacement imports need the explicit
`preserve_settings=False` opt-out; full physical state still requires a project
checkpoint. See [the v0.6 guide](live-test-improvements.md).

## Avatar placement, lining and constraints

Use `list_arrangements`, then `arrange_patterns` with the returned index. Choose
`Flat` or `Curved` and use `inspect_arrangement` to read resulting properties.
Optional orientation and `[x,y,offset]` values are native integer API settings;
their interpretation must be checked against the installed version. No named
orientation codes or automatic front/back matching are guessed. Setter completion
plus property read-back does not verify collision-free placement.
Use v0.6 `arrange_patterns_verified` or `arrange_patterns_by_name` to capture
checkpointed before/after mesh movement evidence. Unchanged geometry is reported
explicitly; successful property setters alone never certify placement.

`get_pattern_layer` and `set_pattern_layers` expose simulation layers from 0 to
20 with read-back verification. `clone_pattern_layer` performs the native under
or over layer clone, checks the added piece and assigns a name. Use this as a
lining starting point, then inspect its sewing, layer order and cloth behavior.
`set_pattern_constraints` accepts freeze, strengthen and solidify booleans.
Only solidify has documented read-back here; freeze/strengthen are explicitly
listed as unverified settings in the result. Constraints are simulation aids,
not automatic collision repair.

## Named pieces and edges

Bind semantic references to a JSON registry:

```python
bind_pattern_reference(registry_path="C:/garment/refs.json", ref_id="front",
                       pattern_index=0, edge_names={"side": 1, "hem": 2})
bind_pattern_reference(registry_path="C:/garment/refs.json", ref_id="back",
                       pattern_index=1, edge_names={"side": 3, "hem": 2})
resolve_pattern_reference(registry_path="C:/garment/refs.json", ref_id="front")
sew_named_edges(registry_path="C:/garment/refs.json", ref_a="front", edge_a="side",
               ref_b="back", edge_b="side", checkpoint_path="C:/garment/pre-sew.zprj")
```

The registry binds piece name, a signature of the geometry information MD exposes,
and explicit boundary records. It can resolve a changed scene ordinal when there
is one matching piece, and rejects missing, changed or ambiguous matches. These
are not native persistent UUIDs. Signature coverage depends on the returned MD
schema; equal-length geometry may be indistinguishable when point data is absent.
Renames/topology edits require inspection and explicit rebind. Identical pieces
with identical names are ambiguous. Imported copies in another scene can match:
resolve only in the intended project. Registries use atomic file replacement;
coordinate access between clients to avoid concurrent registry updates.

## Reports and bounded fitting passes

`measure_patterns` returns 2D boundary lengths. Optional targets contain
`pattern_index`, `line_index`, `target_length`, and `tolerance` in native units.
`ok` means measurements succeeded; `passes` means supplied targets passed.
An empty target list does not validate fit. Measurements are not 3D body
circumference, strain, pressure or penetration sensors.

`capture_fit_report` takes a new/empty output directory, pattern indices,
optional targets and explicit seam pairs, optional caller observations, and
1–8 previews. It returns actual MCP image content and writes `fit-report.json`.
No simulation occurs. Caller observations are labeled as such. The report always
sets `fit_certified=false`; Codex must inspect the images before proposing edits.
Version 0.6 also exports mesh metrics by default and supports caller-selected
close-ups from saved images through `create_fit_closeups`.

`run_fitting_pass` preflights measurements/seam inputs, saves `before.zprj` and
its manifest, selects simulation quality/mode with read-back, runs **one** pass,
then returns updated measurements, images and the saved report. Quality codes:
0=normal, 1=animation, 2=fitting; mode 0=CPU, 1=GPU. Requested quality remains
active afterward. `simulation_steps` is passed to native `Simulate(int)`, bounded
1–200; installed-version semantics still need verification. A timeout does not
cancel simulation. Inspect state before retrying.

`apply_fit_adjustments` uses the reference registry and explicit adjustments:

```json
[
  {"ref_id":"front","action":"layer","parameters":{"layer":1}},
  {"ref_id":"back","action":"resolution","parameters":{"particle_distance":10,"mesh_type":"Triangle"}},
  {"ref_id":"front","action":"move_2d","parameters":{"offset_x":25,"offset_y":0}}
]
```

All references, actions and limits are checked before checkpointing. Move offsets
are bounded per axis by `max_move` (default 100; maximum 1000 native units); layers
are 0–20; particle distances 0.8–100. Execution stops on first failure, preserves
completed results, and updates references for verified changes. Failed registry
writes retain the inline updated references and mutation result. These corrections
do not resize pattern boundaries or adjust 3D avatar placement. Do not repeat
partially completed plans or invent automatic body-fit optimization.

## Recovery and operation history

`create_scene_checkpoint` saves a new `.zprj` and adjacent `.checkpoint.json`
containing a SHA-256 file hash, piece count and ordered piece names.
`restore_checkpoint` accepts that manifest and a fresh `preserve_current_path`.
It refuses changed checkpoint bytes, saves the current scene first, then uses
explicit nonappend project import options and verifies count/names. Preservation
also receives a manifest. This is a recovery mechanism, not a guarantee that
every avatar, simulation cache, fabric or cloth property has been revalidated.
If restoration fails, inspect the scene and the preservation checkpoint.

`get_operation_history` reads up to 100 registered runtime operations for the
current server process; `save_operation_history` persists it as JSON. Entries
record UTC start time, operation ID, elapsed time, parameter keys/digest and
completed/failed/uncertain status. No scripts or replayable inputs are stored.
Bridge failures remain uncertain and are never automatically retried. The
journal excludes legacy raw Python wrappers and local-only recipe operations,
is lost on process exit unless saved, and is separate for Codex and Hermes.
Since v0.7 registered runtime operations also save durable per-process JSONL
events automatically. `read_operation_journal` can inspect incomplete or uncertain
operations after a restart; it never replays them. Legacy raw Python tools remain
outside that journal. See [journal storage and limits](remaining-gaps.md).

Use the cycle: checkpoint → create/inspect → name edges → arrange/layer →
diagnose sewing → bounded simulation → inspect images/measurements → explicitly
correct → repeat. Save/export only once the visible result is satisfactory.
