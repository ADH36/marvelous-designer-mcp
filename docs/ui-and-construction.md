# v0.8: idle UI responsiveness and complex construction support

Version 0.8.0 exposes **97 MCP tools**, including seven new tools. Tests and live
MD calls were not run, as requested. The running MD listener/scene was untouched.

## UI lag implementation

The old listener waits in a blocking socket loop on MD's GUI thread. The new
`md_addon/cooperative_listener.py` polls nonblocking sockets every 20 ms and
dispatches Windows messages between requests, using a nominal 8 ms / 100-message
budget per pass. An individual host message can take longer. It uses
[Windows PeekMessage](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-peekmessagew)
and DispatchMessage through standard-library ctypes, with no Qt dependency or
background native API execution. This host integration remains experimental.

Idle repainting and interaction are the intended improvement. Long native
simulations/exports/modal operations can still pause the window. The pump never
runs inside native calls. Manual edits and dispatched host actions between calls
can change indices/references: reinspect state before mutations. Do not run
another script during an MCP workflow. The launcher rejects recursive listener
startup before reload. WM_QUIT is reposted for MD's enclosing event loop.

Slow request reads and blocked response writes no longer hold the GUI thread.
Eight clients maximum, 1 MiB request / 16 MiB response bounds and five-second
read/write deadlines limit resource use. Bridge requests also include an expiry
timestamp; requests that expire in the socket backlog are rejected before execution.
This does not cancel calls already running. Lost responses still mean execution may have
completed: inspect evidence after recovery and never replay on timeout.

Registered operations cache source by SHA-256 in an isolated MD namespace. After
initial installation, calls send only operation names and arguments, avoiding
repeated full-source transmission/compilation. The cache retains one runtime.
A cache miss explicitly says `executed=false`, permitting one installation retry.
Transport errors never permit automatic replay. An exact old-listener unknown-method
response selects legacy execution compatibility. Reconnect the server after
listener replacement to discard that fallback decision. Diagnostic streams are
bounded to 64 KiB each. The bridge uses an overall deadline and rejects concurrent
requests within one server process before sending them.

## Activate the installed plugin

1. Save the project when convenient.
2. Stop the old listener through `shutdown_listener` in Codex/Hermes.
3. Click the existing registered MD MCP plugin again. Its launcher points to this
   checkout and reloads both listener modules.
4. Reconnect/restart the Codex and Hermes MCP server connections.
5. When live checking is authorized, `listener_status` should report version
   `0.8.0` and `ui_mode=windows_idle_pump`. Metadata is not proof of usable host
   interaction; `ui_pump_live_validated` remains false in this release.

These steps were **not executed** during this update. Set `MD_MCP_UI_PUMP=0` in
MD's environment before listener restart for compatibility mode. This variable
belongs to MD, not the Codex/Hermes server environment. The original loop remains
available as `serve_blocking_legacy()` for troubleshooting.

## New tools

| Tool | Behavior and limits |
|---|---|
| `listener_status` | Listener version, idle UI mode, cache size, last-call duration; no native API invocation. |
| `analyze_surface_intersections` | Triangle BVH search and separating-axis overlap checks, including coplanar/touching cases. Optional second OBJ; omit it for self-check. |
| `draft_matched_sleeve_cap` | Solves cap height within explicit bounds to match measured armhole length plus ease; returns segmented draft and curve definition. |
| `draft_size_variants` | Explicit X/Y scaling around a pivot with boundary measurements. Each size is `{name,scale_x,scale_y}`. |
| `export_construction_svg` | New 1:1 SVG with separate stitch/cutting outlines and labeled notch/button/closure markers. Refuses overwrite. |
| `plan_reference_migration` | Piece mapping proposals from unique names and exact ID-stripped native geometry; never rewrites a reference registry. |
| `assess_design_evidence` | Organizes supplied placement, sewing, clearance, deformation, appearance and recovery checks; never certifies fit. |

## Triangle checks and fit reports

Require triangulated OBJs in identical coordinates/units. Epsilon is absolute in
those units. Touching/coplanar overlaps count conservatively. Self-check excludes
faces sharing any vertex, including adjacent folded faces. Degenerate triangles
are excluded and counted. Reports retain up to 100 pairs and the total detected
count. Closed-volume containment without crossings needs the separate clearance
and inside-candidate analysis.

Defaults: 500,000 candidate comparisons and 30 seconds for traversal/checking
(60 seconds maximum). File reading/BVH building is outside that time budget.
Budget exhaustion returns `ok=false`, `analysis_complete=false` and partial
evidence. Zero hits in an incomplete analysis cannot establish a clean garment.
No native collision thickness, cloth physics, stress or pressure is inferred.

`capture_fit_report` and `run_fitting_pass` accept
`check_surface_intersections=True` for self-checks plus avatar checks when an
explicit avatar OBJ is supplied. Requires `capture_mesh=True`; defaults to false
to keep captures inexpensive. Read nested completion independently of capture success.

## Construction drafts and identity

Measure actual armhole edges with `plan_sleeve_cap`, then supply their total to
`draft_matched_sleeve_cap`. Dimensions use one explicit native unit scale. Supply
bicep/cuff widths, total length, cap-height bounds and ease. Only cap height is
adjusted, using a quadratic profile sampled into 16..128 straight segments.
Matching uses returned segment lengths, not assumed native Bezier behavior.
Out-of-bracket targets fail without changing width. Inspect actual native edge
ordering after creation. Shoulder fit, notch balance and cuff sewing remain explicit.

Size variants are bounded affine drafts (0.5..2 scales; 20 sizes maximum), not
native grading rules or body-specific tailoring. Recheck seams and ease per size.
SVG accepts dart/allowance stitch and cutting outlines plus closure/notch markers.
MD Y is up; SVG Y is down. `units_per_mm=1` means millimetres; supply other scales
explicitly. Print at 100 percent and verify physical scale.

Reference proposals require exact geometry and unique names, followed by actual
API edge inspection and explicit rebinding. Export IDs remain file-scoped.
Geometry equality does not prove native boundary index identity.

Evidence checks require `{category,name,status,evidence,note}`. Category is one
of the six review areas above; status is pass/fail/unverified. A pass requires a
nonempty evidence reference. The tool organizes caller claims without opening
files or running tests; `caller_evidence_complete` is not certification.

## Remaining API and validation boundaries

Reliable per-piece rigid 3D transforms, native button/zipper attachment, native
internal dart/allowance properties and pressure sensing still need verified APIs.
Local construction/SVG output supplies usable drafts without inventing native
schemas. Whole-garment redrape retains its existing scope and mesh evidence.
Full colorway/cache recovery and physical preset equivalence remain unverified;
project checkpoints remain the recovery authority. The
[official MD API list](https://developer.marvelousdesigner.com/list.html) was reviewed
for this update; absent controls were not guessed.

No complex-garment, GPU, lining, animation, batch or UI host validation was added.
The v0.5 prototype remains the last live session. Only source parsing, diff review
and documentation generation were performed; these are not behavioral tests.
Installation does not replace a listener already running inside MD.
