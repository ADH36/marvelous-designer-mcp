# Remaining gap implementations (v0.7)

This release adds 15 tools to the v0.6 source catalog, bringing the total to
**90**. It extends the v0.5 live report's recommendations with concrete geometric
analysis, construction drafts, explicit redrape and recoverable fabric state.

**No automated tests, smoke runs, server startup or live MD calls were performed
for v0.7, at the user's request.** Only source review, static documentation/syntax
processing and package installation were performed. Implementation is not proof
of correct native behavior or production fit. The v0.5 live report remains
evidence about that earlier version.

## Gap-to-implementation map

| Gap | Implementation | Remaining boundary |
|---|---|---|
| Custom arrangement setters may not move cloth | Explicit `commit_redrape=True` on guarded arrangement, plus `redrape_garment` | Native redrape can reset the whole garment; actual movement is checked, not assumed. Per-piece rigid 3D transforms remain unsupported. |
| No geometric body clearance evidence | `analyze_mesh_fit` with a triangle BVH and ray parity | Sampled geometric evidence; not a native collision sensor or exhaustive triangle-intersection test. |
| No deformation evidence | `analyze_mesh_deformation` against an explicit rest mesh | Edge elongation, not MD material stress/pressure. Stable vertex order is the caller's responsibility. |
| No darts | `draft_dart` produces an equal-leg cut-out dart | Draft a new polygon and inspect/sew actual legs; native internal darts remain unsupported. |
| No seam allowance drafting | `draft_seam_allowance` produces stitch/cutting outlines | Straight polygons only; complex offsets fail rather than silently altering topology. Not a native allowance property. |
| No closure planning | `draft_closure_layout` produces plackets, button centers or zipper centerlines | Layout only; native accessory creation/attachment is not documented here. |
| No zipper control | `inspect_zipper_style`, `set_zipper_style` with native read-back | Existing styles only; does not instantiate a zipper. |
| Sleeves not matched to bodice | `plan_sleeve_cap` measures actual cap and armhole lengths and explicit ease | Length planning; cap reshaping, seam orientation and body fit require further explicit edits. |
| No bounded draft geometry edits | `transform_native_pattern_json` edits selected exported XY records | File-scoped affine editing, not native grading or 3D placement; inspect seams after scaling. |
| Native identity persistence unknown | `compare_native_pattern_exports` compares two actual exports | Observed file IDs, not certified persistent IDs or a replacement for geometry-bound references. |
| Native JSON loses fabric detail | `.zfab` backup/restore around JSON import; standalone preset recovery tools | Native presets restore more state, but physical equivalence, colorways and cache are not certified. |
| API availability differs across installations | `inspect_capabilities` returns real availability/docstrings | Availability does not prove behavior; unsupported mutations stop before execution. |
| Operation evidence disappears after restart | Durable JSONL journals and `read_operation_journal` | Unknown completion stays uncertain and requires scene inspection; never automatically replayed. |

## Placement and redrape

`arrange_patterns_verified(..., commit_redrape=True)` explicitly calls the installed
`ReDrape3DArrangement(ImportExportOption)` after setting properties, then refreshes
the 3D window and compares before/after garment OBJ exports. False remains the
default. The signature and option fields were captured in the v0.5 live session;
its zero-translation redrape trial did not move that scene. This release does
not claim redrape reliably applies every custom arrangement offset.

`redrape_garment(output_dir, translation=[x,y,z])` exposes the same whole-garment
path independently. Translation is bounded to 1000 native units per axis.
Both paths checkpoint first, capture evidence, fail unchanged/noncomparable
geometry by default, and never retry automatically. Redrape may discard simulated
drape; it is not a rigid transform limited to the selected pieces.

## Geometric fit diagnostics

```python
analyze_mesh_fit(
    garment_path="C:/garment/fit/mesh-after/garment.obj",
    avatar_path="C:/garment/avatar-triangulated.obj", clearance=2,
    max_samples=2000, report_path="C:/garment/clearance.json")
analyze_mesh_deformation(
    rest_path="C:/garment/rest.obj", current_path="C:/garment/current.obj",
    stretch_limit_percent=10, report_path="C:/garment/elongation.json")
```

These tools read local files and do not call MD. Both meshes must use the same
coordinate system, unit scale and avatar pose. Analysis requires triangles;
concave polygons are not guessed into triangles. OBJ negative vertex indices
are supported. Mesh/file counts are bounded.

Clearance measures nearest triangle distance for evenly sampled garment
vertices using a bounding volume hierarchy. Closed avatars with no degenerate
faces can receive inside-candidate classifications when two independent ray
parity queries agree. Edge-count manifold closure is not proof against avatar
self-intersection. Open/ambiguous surfaces provide unsigned distance only.
Samples near the surface are not automatically penetrations. Unsampled vertices
and triangle interiors can hide intersections; no native collision thickness or
force is inferred. Worst samples include vertex indices and nearby avatar faces.

Deformation measures mesh edge length change relative to a caller-supplied rest
mesh with exactly matching vertex count and ordered face connectivity. Do not
call a previously deformed garment an undeformed rest reference. Export ordering
and coordinate alignment still require the caller's verification. Compression
and elongation extremes and worst edges are recorded; no physical pressure or
stress is computed.

`capture_fit_report` and `run_fitting_pass` accept optional `avatar_mesh_path` and
`rest_mesh_path` to include these analyses alongside images. Inputs are validated
before the MD workflow. Geometry diagnostics have their own `ok` field and can
be unavailable even when capture/simulation succeeds. Mesh capture must be enabled.
Every report retains `fit_certified=false` and labels the diagnostics as geometric.

## Construction drafts and explicit editing

`draft_dart(points, edge_index, intake, depth)` inserts a centered cut-out V in a
straight boundary. It checks that the intake fits, the tip is inside the original
polygon and the resulting polygon is valid. It returns equal leg length and
proposed draft indices. Create a new pattern, inspect native edge order, then
sew the legs. The function does not mutate the current garment.

`draft_seam_allowance(points, width, miter_limit=5)` returns the original stitch
outline and an offset cutting outline. It rejects extreme miters and invalid
or self-intersecting outlines. Use it for cutting drafts; simply sewing the
expanded outer outline would change garment dimensions.

`draft_closure_layout(start, end, placket_width, kind="buttons")` computes a
placket polygon and evenly spaced button centers, or a zipper centerline. All
values are explicit native draft units. It does not fabricate native button or
zipper JSON structures. Existing zipper styles can be inspected/edited with
documented enums, dimensions and read-back, after a checkpoint.

`plan_sleeve_cap` accepts lists of `{pattern_index,line_index}` endpoints,
measures actual API lengths, computes requested cap ease and reports mismatch.
A suggested uniform scale is planning information, not an automatic fitted-cap
edit. Sleeve width, cuff, cap curvature and other seams need reassessment after
changes. Equal total length alone does not establish a sewable cap.

`transform_native_pattern_json` selects native IDs in an exported file and
applies scale, rotation and translation to recognized XY `Position` records,
including repeated point/control records consistently. Source files are preserved.
It refuses graded documents rather than guessing grading-rule updates. Sewing
length parameters and accessory dimensions must be reinspected after transforms;
this is not production size grading. Reimport separately with a checkpoint and
explicitly rebind references. `compare_native_pattern_exports` records IDs
retained/added/removed and native geometry equality in two files only.

## Native fabric recovery and capability inspection

JSON import now backs up referenced fabrics as native `.zfab` presets by default,
retains a hashed manifest, and restores unique fabric names before reapplying
known pattern settings. This uses the documented native preset export/replace
functions, not an incomplete reconstruction from geometry JSON. Set
`preserve_fabric_presets=False` explicitly to use only v0.6 known-property
restoration. Full scene replacement still requires `preserve_settings=False`.

`backup_fabric_presets` and `restore_fabric_presets` expose the same recovery
mechanism independently. Changed hashes or ambiguous names stop restoration.
The project checkpoint remains the full-state authority: preset export/replacement
success and name read-back do not certify every physical parameter or colorway.

`inspect_capabilities` only imports modules and reads function availability and
docstrings; it invokes none of the inspected native functions. Documented native
controls preflight relevant installed docstrings before mutation. Their published
signatures are sourced from the [official MD API list](https://developer.marvelousdesigner.com/list.html);
unknown setters, accessory schemas and sensor names are not invented.

## Durable operation evidence

Registered runtime operations persist start/completion events to a unique JSONL
file per MCP server instance. On Windows the default directory is
`%LOCALAPPDATA%/marvelous-designer-mcp/journals`; otherwise the fallback is the
system temporary directory. `MD_MCP_HISTORY_DIR` selects a durable location.
Journal paths are returned with operations and in `get_operation_history`.
Entries retain parameter names/digests, IDs, elapsed time and statuses; they
exclude scripts and replayable input payloads.

If the start record cannot be saved, the MD operation does not execute. If a
completion record cannot be saved, the returned operation result includes a
journal error; do not repeat a mutation based on missing evidence. A torn append
or start without completion remains uncertain. `read_operation_journal` is local,
bounded, and never replays operations or infers successful completion.
Legacy raw `execute_python`/project wrappers are outside this operation journal.

## Loading and validation status

The editable package is synchronized locally. Reconnect Codex/Hermes MCP server
processes to discover the 90 tools. The MD launcher/TCP protocol is unchanged;
the running MD scene/listener was not touched. Validation remains intentionally
deferred. Native per-piece transforms, native accessory instantiation, full
colorway/cache preservation and native pressure/stress sensing remain API-dependent
boundaries; they are not presented as completed or certified features.
