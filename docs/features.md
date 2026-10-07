# Garment tools (v0.3)

The server exposes 26 tools, including 15 new garment and export tools. All paths
must be absolute. The client and MD run on the same machine. API calls still run
on MD's GUI thread; these features do not remove the listener's GUI blocking.

## Previews

`preview_garment(output_dir, image_count=4, width=1024, height=1024)` writes PNGs
and returns real MCP image content to the agent. It uses the current camera,
colorway and scene; it does not simulate or alter garment geometry. An existing
output prefix is refused unless `overwrite=True`. MCP previews allow 1–8 images,
dimensions of 64–2048, and at most 8 MiB per image.

Use `export_turntable_images(path, image_count=4, ...)` for 1–72 views written to
disk. `path` is a PNG filename prefix, such as `C:/renders/shirt/view.png`.
`export_custom_views(output_dir, prefix="view", ...)` exports views already
saved in MD; no saved views results in a clear error.

MD 2026.0.315 returned no files from the turntable overload with explicit paths
and dimensions. The count-only overload successfully produced PNGs in MD's
temporary directory. The server falls back only after an empty result, checks
source freshness, copies the files to the requested location, then uses Pillow
to resize/pad them without stretching. Results report `used`, `render_sizes` and
`output_size`. The fallback numbers files using `start_index`; its camera angles
follow MD's count-only API. The normal path overload receives `start_index`
directly. The general `ExportSnapshot3D` API is intentionally not called: its
documented dialog can block this listener.

## OBJ export

```text
export_obj(
  path="C:/exports/shirt/garment.obj",
  scale=1.0, thin=True, single_object=False,
  include_avatar=False, unified_uv=True
)
```

Use a dedicated empty directory because MD may create OBJ, MTL and texture
sidecars. `overwrite=True` allows an existing directory. Options are passed
explicitly to `ExportOBJW(path, ImportExportOption)` to avoid an export dialog.
Every returned file must exist, be nonempty and be newly written or changed;
an OBJ primary file must contain vertices and polygon faces. The response reports
their counts. An old file, header-only mesh or empty MD return value is not
treated as success.

`scale` is a multiplier, not an automatic DAZ/Unity/Blender unit preset. Axes use
MD's option defaults. Verify size and orientation at the destination before
turning those choices into a reusable preset.

## Pattern editing and sewing

1. Call `inspect_pattern(index)` for point/edge information and current settings.
2. Save a `.zprj` using `save_checkpoint` before topology changes.
3. Use `rename_pattern`, `select_patterns`, `mirror_pattern`, or
   `set_pattern_resolution` as needed.
4. Call `inspect_pattern` again after adding/mirroring pieces. Indices can change.
5. Call `sew_edges(pattern_a, line_a, pattern_b, line_b, direction_a, direction_b)`.
   Directions are explicit: `True` is forward, `False` is backward. The wrapper
   does not infer seam orientation or equalize lengths.

Boundary indices are checked against geometry before sewing. This wrapper
covers boundary-to-boundary sewing; internal-line overloads remain available
through Python. On this MD build the indexed geometry getter labels its single
returned record with a local `Pattern index: 0` even for global piece 1. The
wrapper uses the getter's requested selector and validates the returned name.
Unknown geometry schemas fail explicitly.

Particle distance uses MD API native units, with a documented minimum of 0.8.
Mesh types are `Triangle` or `Quad`. Batch edits validate all indices before
mutation and read settings back. Failed batches report `completed`,
`failed_index`, and whether a partial change is possible. They do not roll back.

## Fabrics

`assign_fabric_batch(fabric_index, pattern_indices, face=2)` checks indices before
assignment and reports the fabric index read back for each completed piece.
The face argument remains MD's raw face code; this read-back does not verify
face-specific appearance or physical drape.

`create_fabric_from_textures(path, base_texture, ...)` creates a `.zfab` file.
Normal, displacement, opacity, roughness and metalness maps are optional absolute
paths. It verifies texture existence and the generated preset. This saves a
preset file; it does not import or assign it to the scene. Physical fabric tuning
and automatic material recognition are future features.

## Reusable workflow

```text
garment_workflow(
  output_dir="C:/exports/shirt-run-01",
  avatar_path="C:/assets/body.avt",
  garment_path="C:/assets/shirt.zpac",
  simulation_steps=0, preview_count=4, scale=1.0
)
```

An empty output directory is required by default. Both asset paths are optional;
omitting them processes the current scene. Assets are appended with explicit
import options (`bAdd=True`). The stages are:

1. Save `before.zprj` before any scene mutation.
2. Append optional avatar and garment assets.
3. Simulate only when `simulation_steps > 0`.
4. Save `garment.zprj`.
5. Export `mesh/garment.obj` and its sidecars.
6. Export previews (set `preview_count=0` to skip them).

The simulation integer is passed directly to MD's `Simulate(int)`; its meaning
is not redefined by this wrapper. Defaults skip simulation. A failed stage stops
later stages and reports completed/failed stages. Imports, simulation and other
scene edits are not rolled back. A socket timeout does not cancel MD: inspect
the scene and output files before retrying.

## Validation

Signatures and option attributes were inspected live on MD 2026.0.315. Live
checks exercised checkpoint saving, pattern/edge inspection, seam listing, OBJ
export, turntable previews with MCP image content, texture preset creation and
a current-scene workflow with imports/simulation skipped. Existing custom views
were absent, so their export was not tested live.

Mutating pattern/sewing/batch operations and asset imports are covered by
stateful API-contract tests. Their signatures are verified, but they were not
applied to the user's active garment during feature validation. This prevents
test edits from interfering with that scene. Visual previews show the existing
scene; they do not certify fit or seam quality.

Official references: [API list](https://developer.marvelousdesigner.com/list.html),
[import/export options](https://developer.marvelousdesigner.com/optiontype.html).
