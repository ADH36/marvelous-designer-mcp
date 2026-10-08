> Current contracts and validation: [v0.8.1 fixes](live-v08-fixes.md). The release-specific counts and deferred-test notes below are historical.

# Recipes and automation (v0.4)

Version 0.4 adds 16 tools, for 42 total. These tools use the existing Python
listener; reconnect the MCP client after `uv sync`. New MD-dependent operations
have stateful contract and generated-script tests. Their signatures follow the
official API examples, but this release's new mutations have not been tested on
a live MD scene. Missing installed APIs return an explicit failure.

## Pattern creation

`create_pattern(points, name, coordinate_scale=1)` accepts 3–1024 `[x,y]` vertices
for a straight-edge polygon. Coordinates are MD native units; scale is an
explicit multiplier. Duplicate vertices, zero area and self-intersections fail
before calling MD. Omit a repeated closing point. `create_rectangle` uses the
same validated path with positive width/height and an optional origin.

Save a checkpoint before creating pieces. Creation verifies the pattern count
and assigned name; if it fails after mutation, inspect `partial_change_possible`
and refresh indices. The tool does not position pieces around an avatar.

## Sewing diagnostics

```json
[
  {"pattern_a": 0, "line_a": 1, "pattern_b": 1, "line_b": 3}
]
```

Pass explicit boundary pairs to `diagnose_sewing`. It reads actual MD lengths,
reports absolute differences and percentages relative to the longer edge, flags
reused endpoints/self-seams, and returns a `sewing_map` of connections. Default
tolerance is 5%. `ok` means the report was produced; `passes` means all provided
pairs passed the checks. This does not infer all existing seam endpoints,
validate seam orientation, or certify avatar fit.

## Fabric library

`import_fabric` accepts an existing absolute `.zfab` or `.jfab` path and verifies
the new index and name. `replace_fabric` accepts `.zfab`, checks the target index,
then reports MD's result and the resulting name. Inspect appearance separately.

Fabric assignment uses `assignment_mode`, not a surface face: 1=current colorway,
2=all colorways with unlinked materials, 3=all linked. The old `face` keyword is
a deprecated alias for those numeric modes. Zero is invalid; conflicting mode
and alias values fail. Default mode is now 1. Batch assignment verifies the
assigned fabric index and reports partial completion on failure.

## Reusable recipes

```text
build_skirt_recipe(waist_cm=75, length_cm=60, hem_cm=120, ease_cm=2,
                  native_units_per_cm=10)
save_garment_recipe(path="C:/recipes/skirt.json", recipe=<returned recipe>)
load_garment_recipe(path="C:/recipes/skirt.json")
apply_garment_recipe(recipe=<loaded recipe>)                 # dry-run
apply_garment_recipe(recipe=<loaded recipe>,
                    output_dir="C:/exports/skirt-run-01", dry_run=False)
```

The builder creates two matching trapezoidal skirt panels with side seams.
`native_units_per_cm=10` is the explicit cm-to-mm calibration; change it if your
installed coordinate convention differs. This is a starting block, without
darts, waistband, closures, seam allowances or automatic avatar arrangement.
It is not a finished fitted skirt.

Since v0.6, `build_skirt_recipe` defaults to `vertical_direction="down"`, which
extends length toward negative Y as observed in the MD 2026 live trial. Use
`vertical_direction="up"` to reproduce the earlier positive-Y draft. Saved
recipes already contain coordinates and are not silently rewritten. New
`build_bodice_block` and `build_sleeve_block` return inspectable curved draft
templates; see [the v0.6 guide](live-test-improvements.md).

The version-1 JSON schema is data-only: a name, pieces with unique ids and polygon
points, explicit boundary seam pairs/directions, optional fabric path/mode,
export settings and preview count. Measurement metadata is retained. Points
are already in MD native units. Unknown fields, invalid seam references,
duplicate JSON keys, nonfinite numbers and unsupported versions are rejected.
Documents are limited to 1 MiB. No recipe code is executed. See
[the sample recipe](../examples/recipes/skirt.json).

Application defaults to a local dry-run and needs no running MD instance.
`dry_run=False` requires a new or empty absolute output directory. It appends
pieces to the current scene; it does not clear existing geometry. Stages:

1. Save `before.zprj`.
2. Create and name pieces; retain the actual new scene indices.
3. Check boundary index/length sequences, diagnose pairs, and sew explicit pairs.
4. Optionally import/assign the recipe fabric to the created pieces.
5. Save `garment.zprj`, OBJ/sidecars, and optional previews.
6. Write `recipe-result.json` with stage results.

Recipe seam indices follow the supplied polygon boundary order. A differing MD
length/index sequence stops sewing. Equal-length edges can make orientation
ambiguous; inspect the actual sewing visually before simulation. No automatic
simulation occurs. First failure stops the run and reports created pieces;
earlier edits remain in the scene. Checkpoint restore is manual.

## Animation

`animation_state` reads current/start/end frames. `configure_animation` sets a
nonnegative ordered frame range and verifies it. It does not simulate.
`record_animation(start_frame, end_frame, checkpoint_path)` saves a new `.zprj`
before calling `RunAnimationRecording`. The scene needs a suitable animated
avatar and cloth setup. A completed API call does not establish visible motion
quality. A timeout does not cancel recording; inspect before retrying.

`export_alembic` uses explicit options including `bExportAnimation=True`, checks
fresh nonempty `.abc` output, and defaults to garment-only export. Use an empty
output directory. This is file validation, not a cache decoder or a guarantee
that the exported cache contains motion. Inspect it in the destination.

## Destination profiles

`save_export_profile` saves a JSON profile for DAZ, Blender, Unity or custom.
Supply scale, three distinct installed MD axis codes, three inversion flags,
and a calibration note. Axis meanings are not inferred from the destination.
Profiles default to `validated=False`; set true only after checking a known-size
mesh and orientation in the destination. `export_obj_with_profile` refuses
unvalidated profiles, verifies the option attributes exist, and passes the
stored settings to MD. These are reusable user-calibrated presets, not bundled
claims of correct destination units, rigging or cloth physics.

## Independent project batches

```json
[
  {"project_path": "C:/projects/shirt.zprj", "preview_count": 4},
  {"project_path": "C:/projects/skirt.zprj", "simulation_steps": 0, "scale": 1}
]
```

`batch_garment_workflows(jobs, output_dir)` preflights 1–50 `.zprj` jobs and uses
an empty output directory. It saves `original.zprj`, loads each project with
`bAppend=False` and explicit content/render/view options, then runs the existing
checkpoint/export workflow in `job_000`, `job_001`, etc. Jobs do not accumulate
garments from previous scenes. Simulation defaults to skipped, previews to 4
(0–8 supported), and scale to 1. First failure stops later jobs. Results include
completed/failed stages and a `batch-result.json` report. The last loaded job
remains active; the original project is preserved in its checkpoint.

Official references: [API list](https://developer.marvelousdesigner.com/list.html),
[pattern/animation examples](https://developer.marvelousdesigner.com/scenario.html),
[import/export options](https://developer.marvelousdesigner.com/optiontype.html).
