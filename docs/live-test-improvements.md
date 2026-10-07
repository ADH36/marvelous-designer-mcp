# Changes informed by the v0.5 live garment test (v0.6)

The MD 2026.0.315 session built and simulated a two-panel skirt and a sleeveless
top with three internal pocket seams. It exercised 42/67 tools, including all
25 v0.5 additions, with no unexpected API/transport failures. Successful calls
still produced incorrect initial placement and wrinkles. Native JSON import
reset particle distance from 15 to 20. These findings inform the changes below.

**This implementation was not tested. No automated tests, smoke runs, server
startup or live MD calls were performed for v0.6, as requested by the user.**
The earlier live result is evidence about v0.5, not certification of v0.6.

## Mesh evidence and placement

Eight new tools bring the source catalog to **75**:

| Tool | Purpose |
|---|---|
| `capture_mesh_snapshot` | Export garment-only OBJ with bounds, centroid, vertex/face counts and digests |
| `compare_mesh_snapshots` | Compare saved OBJ geometry locally, without calling MD |
| `arrange_patterns_verified` | Save a project checkpoint, apply arrangement, export before/after meshes and report movement |
| `arrange_patterns_by_name` | Resolve an exact discovered arrangement name, then use the guarded placement path |
| `inspect_native_pattern_geometry` | Export native IDs/control points alongside actual API edges |
| `build_bodice_block` | Draft local front/back spline templates with explicit dimensions and coordinates |
| `build_sleeve_block` | Draft a local sleeve with an explicit cap height and cuff |
| `create_fit_closeups` | Crop caller-selected regions from saved preview images into MCP images |

`arrange_patterns` still performs the established native setter calls, but now
explicitly reports `placement_verified=false` and `movement_verified=false`.
Read-back alone cannot establish 3D movement. Use the new guarded path:

```python
list_arrangements()
arrange_patterns_by_name(
    pattern_indices=[0], arrangement_name="Leg_Skirt_Front",
    output_dir="C:/garment/placement-01")
```

The name is an exact value discovered on the installed avatar; it is not a
universal preset. Missing/ambiguous names fail. The evidence directory must be
new/empty. It contains `before.zprj`, its hash manifest, before/after meshes,
and `placement-report.json`. Native setters are not automatically retried or
followed by speculative redrape calls. Unchanged or noncomparable geometry
returns `ok=false` by default. Use `require_movement=false` only when explicitly
allowing an already-correct/no-op placement.

Movement uses exported vertex order and face connectivity; it is not a native
vertex identity guarantee. Changed topology makes per-vertex comparison
unavailable; bounds are still reported. Metrics cover all visible exported
garment pieces. They neither identify a particular body region nor prove
collision-free placement. A direct operation that reliably commits custom
orientation/offset changes remains an installed-API research gap.

## Geometry editing with known settings recovery

`import_pattern_json(path, checkpoint_path, preserve_settings=True)` now captures
particle distance, mesh type, layer, solidify and current-colorway fabric name
before mutation. Preservation requires the same unique pattern names as the
current scene. It checkpoints, imports, matches unique names, reapplies settings
and verifies available read-back. Fabric indices are rediscovered by name rather
than reused after import. Missing or ambiguous mapping stops recovery and
reports the checkpoint; there is no automatic destructive rollback.

A `.zprj.settings.json` sidecar retains the captured values. Names, topology or
fabric mapping failures require inspection and project checkpoint recovery.
`preserve_settings=False` explicitly opts into replacement without recovery.
This is an intentional stricter default than v0.5. Existing scripts importing
unrelated/new pieces need the explicit opt-out.

Physical fabric parameters, colorways, freeze/strengthen and simulation cache
are not certified by these getters. Preserve the full project checkpoint.
`inspect_native_pattern_geometry` exposes native `PatternList.ID` and spline
controls for investigation, but does not claim IDs persist through import or
that native line order equals the API edge map. Bind seams to inspected API
indices: the live test's 11 spline input vertices became eight boundary edges.

## Coordinates and construction templates

New skirt recipes default to negative Y down, with an explicit
`vertical_direction="up"` compatibility option. Dimensions use the supplied
`native_units_per_cm` (10 for the tested millimetre export). Existing saved
recipes remain unchanged. Arrangement points still require discovery on the
actual avatar; draft origin does not automatically establish avatar placement.

`build_bodice_block` accepts bust, length, shoulder width, neck width, armhole
depth, front/back neck depth and ease in centimetres. `build_sleeve_block`
accepts bicep, cuff, length, cap height and ease. Both return local `[x,y,type]`
vertices for `create_curved_pattern`, explicit unit/direction metadata and a
construction sequence. They do not mutate MD. Inspect native curves and edge
lengths, bind references, checkpoint, sew and arrange explicitly. These are
draft templates, not production slopers; sleeve-cap matching is not automatic.
Darts, fitted grading, closures and seam allowance remain future construction
features requiring verified native API/schema support.

## Fit reports and close-ups

`capture_fit_report` now records garment mesh metrics by default.
`run_fitting_pass` records before/after mesh evidence around its one simulation
pass. Both accept `capture_mesh=False` when OBJ evidence is intentionally omitted.
Meshes live in separate subdirectories to avoid collisions with image files.
The simulation argument retains its native `Simulate(int)` meaning; it is not
advertised as a verified frame/time count.

```python
create_fit_closeups(
    report_path="C:/garment/fit-01/fit-report.json",
    output_dir="C:/garment/closeups-01",
    regions=[{"name":"pocket", "view_index":0, "box":[0.35,0.35,0.65,0.65]}])
```

Crop boxes are caller-selected normalized image coordinates. Source images
must belong to the saved report directory; filenames are generated independently
of region labels. The tool returns images plus `closeups.json` and never calls
MD. Enlarging a crop cannot recover detail absent from the original preview.
Reports retain `fit_certified=false`; collision, pressure, strain and wrinkle
measurement remain gaps rather than inferred sensor readings.

## Loading the updated source

The Codex/Hermes configuration uses this editable checkout. Reconnect their MCP
server processes to load the updated source and discover 75 tools. This update
changes the MCP Python layer, not the registered MD launcher or TCP protocol;
no native plugin rebuild is needed. The MD scene/listener was left untouched.
Package metadata is synchronized with `uv sync --locked` during installation;
that command is not a test. Broader regression/live validation is intentionally
deferred, including other MD versions, GPU, Bezier, clone drape, batches,
animation and timeout recovery.
