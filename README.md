# marvelous-designer-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) server that lets an
LLM (e.g. Claude) drive **Marvelous Designer** (and CLO, which shares the same
Python API) by running code inside MD's embedded Python interpreter.

It does **not** need the C++ SDK, a plugin build, or a CLO-SET API key — just MD's
built-in Python Editor.

## How it works

```
LLM  ──MCP(stdio)──▶  MCP server (this repo, FastMCP)
                          │  JSON-per-line over TCP 127.0.0.1:7421
                          ▼
                      md_listener.py  (you paste it into MD's Python Editor)
                          │  exec() in MD's interpreter
                          ▼
                      MD Python API:  import_api / export_api / fabric_api /
                                      pattern_api / utility_api / ...
```

MD's embedded Python (3.11) does **not** schedule background threads, so the
listener is a plain blocking accept loop that runs on MD's GUI thread. **While the
listener is running, MD's window is unresponsive** — that's expected. Stop it with
the `shutdown_listener` tool (or by closing MD).

## Requirements

- Marvelous Designer 2026 (or CLO 2026) — `Plugins ▸ Python Editor` must be available
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) on the machine running the MCP server
- The MCP server and MD run on the **same machine** (the listener binds `127.0.0.1`)

## Setup

```powershell
git clone https://github.com/ysk424/marvelous-designer-mcp.git
cd marvelous-designer-mcp
uv sync
```

## Running

### 1. Start the listener inside Marvelous Designer

`scripts/md_start_listener.py` starts a blocking socket listener on
`127.0.0.1:7421`. **MD's GUI freezes while it runs — that's the ready state.**
Stop it later with the `shutdown_listener` tool (or by closing MD). See
[Why does MD freeze?](#why-does-md-freeze-while-the-listener-runs) below.

**Option C — auto-register (one shot, recommended).** Run

```powershell
uv run python scripts/install_md_plugin.py
```

once. It writes the launcher into MD's `pluginSettings.json`, so on the next MD
launch `Plugins ▸ Plug-in ▸ md_start_listener` is there waiting to be clicked.

**Option B — register manually.** `Plugins ▸ Plug-in Manager ▸ +ADD`, pick
`scripts/md_start_listener.py`. Same outcome as Option C, just hand-driven.

**Option A — paste into the Python Editor.** Open `Plugins ▸ Python Editor`, paste
**the whole contents of `scripts/md_start_listener.py`**, run it. (Paste it in
one go from a text editor — pasting line by line can mangle indentation.) Good
for development; the editor's console shows prints. Because pasted code has no
`__file__`, set `MD_MCP_ADDON_DIR` in the environment before launching MD, pointing
to this checkout's `md_addon` directory. Registering the launcher as a plug-in
does not require this setting.

Either way, status and errors are appended to `~\md_mcp_listener.log` (handy in
plugin mode, where stdout may not be visible).

### 2. Start the MCP server

```powershell
uv run python -m marvelous_designer_mcp
```

Or let Claude Desktop launch it (below).

### 3. Claude Desktop config

Add to `claude_desktop_config.json`
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "marvelous-designer": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\you\\git\\marvelous-designer-mcp",
        "python",
        "-m",
        "marvelous_designer_mcp"
      ]
    }
  }
}
```

Restart Claude Desktop. The MD listener must already be running (step 1) for the
tools to work.

## Tools

<!-- TOOLS:START -->

**Total: 75 MCP tools (v0.6.0).**

Every registered tool is listed individually below. Required inputs are shown;
the MCP schema supplies optional settings and defaults.

| Tool | Required inputs | What it does |
|---|---|---|
| `animation_state` | — | Read the current animation frame and start/end range. |
| `apply_fit_adjustments` | `registry_path`, `adjustments`, `checkpoint_path` | Checkpoint and apply explicit bounded move_2d, layer or resolution corrections to named pieces. |
| `apply_garment_recipe` | `recipe` | Review or apply an explicit garment recipe. Dry-run is the default. |
| `arrange_patterns` | `pattern_indices`, `arrangement_index` | Assign patterns to an installed avatar arrangement point and read the resulting properties. |
| `arrange_patterns_by_name` | `pattern_indices`, `arrangement_name`, `output_dir` | Resolve an exact discovered avatar arrangement name and apply it with mesh evidence. |
| `arrange_patterns_verified` | `pattern_indices`, `arrangement_index`, `output_dir` | Checkpoint/apply arrangement and compare actual exported garment meshes before and after. |
| `assign_fabric` | `fabric_index`, `pattern_index` | Assign fabric with colorway mode 1=current, 2=all unlinked, 3=all linked. |
| `assign_fabric_batch` | `fabric_index`, `pattern_indices` | Assign one fabric to multiple pieces with preflight bounds checks. |
| `batch_garment_workflows` | `jobs`, `output_dir` | Process 1–50 .zprj projects independently with checkpoints and a JSON report. |
| `bind_pattern_reference` | `registry_path`, `ref_id`, `pattern_index`, `edge_names` | Persist a piece reference and named boundary edges using name plus geometry signature. |
| `build_bodice_block` | `bust_cm`, `length_cm`, `shoulder_width_cm`, `neck_width_cm`, `armhole_depth_cm` | Draft local front/back bodice spline templates with explicit cm scale and Y direction. |
| `build_skirt_recipe` | `waist_cm`, `length_cm`, `hem_cm` | Build a data-only two-panel skirt block with matching side seams. |
| `build_sleeve_block` | `bicep_cm`, `cuff_cm`, `length_cm`, `cap_height_cm` | Draft a local spline sleeve template; cap-to-armhole matching and fit remain explicit. |
| `capture_fit_report` | `output_dir`, `pattern_indices` | Return garment images, edge target comparisons, seam diagnostics and a saved fit report. |
| `capture_mesh_snapshot` | `path` | Export the visible garment OBJ and record bounds, centroid, counts and geometry digests. |
| `clone_pattern_layer` | `pattern_index`, `name` | Create an over/under layer clone for lining and verify the added piece. |
| `compare_mesh_snapshots` | `before_path`, `after_path` | Compare two local OBJ exports for mesh movement; no MD calls or fit certification. |
| `configure_animation` | `start_frame`, `end_frame` | Set an animation frame range with read-back checks. Does not simulate. |
| `copy_pattern` | `pattern_index`, `name` | Copy a pattern with a 2D offset and verify the added piece's index/name. |
| `create_curved_pattern` | `vertices`, `name` | Create a named native pattern with [x,y,type] vertices: 0 straight, 2 spline, 3 Bezier. |
| `create_fabric_from_textures` | `path`, `base_texture` | Create a .zfab preset from existing texture maps, verifying the output file. |
| `create_fit_closeups` | `report_path`, `output_dir`, `regions` | Crop caller-selected regions from saved fitting images into labeled MCP close-up images. |
| `create_internal_shape` | `pattern_index`, `vertices` | Create an internal line or closed construction shape using [x,y,type] vertices. |
| `create_pattern` | `points`, `name` | Create a named straight-edge polygon from [x,y] points in MD native units. |
| `create_rectangle` | `width`, `height`, `name` | Create a rectangle in MD native units; boundary starts at the origin. |
| `create_scene_checkpoint` | `path` | Save a fresh .zprj plus .checkpoint.json manifest with SHA-256 and pattern count/names. |
| `diagnose_sewing` | `seam_pairs` | Compare explicit boundary pairs, report length mismatches/reused edges and a sewing map. |
| `execute_python` | `code` | Execute arbitrary Python inside Marvelous Designer's interpreter. |
| `export_alembic` | `path` | Export garment animation to a fresh Alembic file using explicit options. |
| `export_custom_views` | `output_dir` | Export saved MD custom views into an absolute output directory. |
| `export_obj` | `path` | Export garment OBJ with explicit options to avoid an export dialog. |
| `export_obj_with_profile` | `path`, `profile_path` | Export OBJ using a saved, destination-validated profile and explicit options. |
| `export_pattern_json` | `path` | Export verified MD-native geometry JSON for external editing and round-trip import. |
| `export_project` | `path` | Save the current scene as a .zprj project file at the given absolute path. |
| `export_turntable_images` | `path` | Export 1–72 evenly spaced turntable images using an absolute PNG path prefix. |
| `garment_workflow` | `output_dir` | Checkpoint, optionally append .avt/.zpac assets, simulate, save, export OBJ and previews. |
| `get_operation_history` | — | Read recent registered MD operation statuses/IDs/digests for this server process (up to 100). |
| `get_pattern_layer` | `pattern_index` | Read a pattern's simulation layer. |
| `import_fabric` | `path` | Import an existing absolute .zfab/.jfab path; verify its index and name. |
| `import_pattern_json` | `path`, `checkpoint_path` | Checkpoint/import native JSON and restore known resolution, layer, solidify and fabric assignments. |
| `import_project` | `path` | Open an MD project / garment / mesh file (.zprj, .zpac, .obj, .fbx, ...) by absolute path. |
| `inspect_arrangement` | `pattern_index` | Read a pattern's native avatar arrangement properties. |
| `inspect_native_pattern_geometry` | `pattern_index`, `export_path` | Export native geometry/control points/IDs alongside the actual API boundary edge map. |
| `inspect_pattern` | `pattern_index` | Inspect a pattern's name, fabric, mesh resolution, points and boundary edges. |
| `list_arrangements` | — | List installed avatar arrangement points with their native properties. |
| `list_fabrics` | — | List fabrics in the current scene: index and name (plus the fabric-style name list). |
| `list_patterns` | — | List pattern pieces in the current scene: index, name, assigned fabric index. |
| `list_seams` | — | List the sewing groups in the current scene by index and name. |
| `load_garment_recipe` | `path` | Load and validate a data-only recipe JSON, with no scene changes. |
| `md_api` | `module` | List public attributes of an installed MD API module, optionally filtered by name. |
| `measure_patterns` | `pattern_indices` | Measure 2D boundary lengths and compare explicit edge targets/tolerances in native units. |
| `mirror_pattern` | `pattern_index` | Create a symmetric pattern, optionally including sewing. |
| `move_pattern_2d` | `pattern_index`, `x`, `y` | Move a piece in the 2D editor and verify its position; uses native units. |
| `ping` | — | Verify the MD listener is reachable. Returns whatever the listener echoes back. |
| `preview_garment` | `output_dir` | Generate up to 8 turntable views and return PNG image content to the agent. |
| `record_animation` | `start_frame`, `end_frame`, `checkpoint_path` | Checkpoint then run MD animation recording for an explicit frame range. |
| `rename_pattern` | `pattern_index`, `name` | Rename a pattern piece and read its name back to verify the change. |
| `replace_fabric` | `fabric_index`, `path` | Replace an existing fabric using a .zfab file. Verify appearance separately. |
| `resolve_pattern_reference` | `registry_path`, `ref_id` | Resolve a saved piece/edge reference to current indices, rejecting stale or ambiguous matches. |
| `restore_checkpoint` | `manifest_path`, `preserve_current_path` | Verify a checkpoint hash, preserve the current scene, load and verify count/names. |
| `run_fitting_pass` | `output_dir`, `pattern_indices` | Checkpoint, set verified quality/mode, run one bounded simulation pass and return images/report. |
| `save_checkpoint` | `path` | Save a .zprj checkpoint without a thumbnail dialog and verify the file. |
| `save_export_profile` | `path`, `destination`, `scale`, `axis_codes`, `invert_axes`, `calibration_note` | Save explicit destination scale/axes. No destination defaults are guessed. |
| `save_garment_recipe` | `path`, `recipe` | Validate and save a reusable JSON recipe; refuses overwrite by default. |
| `save_operation_history` | `path` | Persist the current process's operation journal to JSON; contains digests, not replayable code. |
| `scene_info` | — | Summary of the current MD scene: project name/path, MD version, pattern & fabric counts. |
| `select_patterns` | `pattern_indices` | Select multiple pattern pieces and return the verified selection. |
| `set_pattern_constraints` | `pattern_indices` | Set freeze, strengthen or solidify; only solidify has documented state read-back. |
| `set_pattern_layers` | `pattern_indices`, `layer` | Set simulation layers from 0 through 20 with read-back checks and partial progress. |
| `set_pattern_resolution` | `pattern_indices`, `particle_distance` | Set particle distance and mesh type for a batch of patterns, then read back. |
| `sew_edges` | `pattern_a`, `line_a`, `pattern_b`, `line_b` | Sew two boundary edges. True means forward and False means backward. |
| `sew_internal_edges` | `pattern_a`, `line_a`, `pattern_b`, `line_b` | Sew boundary/internal or internal/internal edges using explicit child indices. |
| `sew_named_edges` | `registry_path`, `ref_a`, `edge_a`, `ref_b`, `edge_b`, `checkpoint_path` | Resolve named edges together, checkpoint and sew validated endpoints. |
| `shutdown_listener` | — | Stop the MD-side listener's blocking loop and release the Marvelous Designer GUI. |
| `simulate` | — | Run cloth simulation via utility_api.Simulate(int). |

<!-- TOOLS:END -->

Anything not covered by a wrapper: use `execute_python` directly.

Version 0.6 exposes 75 tools. See [live-test gap fixes](docs/live-test-improvements.md), [garment/export contracts](docs/features.md) and
[recipe, animation and batch examples](docs/recipes.md), and
[complex garment controls](docs/complex-design.md) for checkpoint guidance
and validation limits. Restart the MCP client
connection after updating so Codex, Hermes or another client discovers the tools.

The old `face` keyword for fabric assignment remains a deprecated numeric alias;
it never meant front/back/side. Mode 0 is rejected, mode 3 is now supported, and
new calls default to the current colorway (1) instead of all unlinked (2).

## Development

Run the protocol tests from the repository root with:

```powershell
$env:PYTHONPATH = "src;md_addon"
uv run python -m unittest discover -s tests -v
uv run python scripts/update_tool_catalog.py --check
```

These tests exercise TCP framing, API contracts, mutation preflight, partial
failures, workflow ordering and actual MCP image content without requiring a
Marvelous Designer installation. `uv sync` installs the SDK and Pillow needed
for the integration tests. Plain Python without the SDK skips those tests.
The tool catalog is generated from FastMCP schemas; after adding tools, run
`uv run python scripts/update_tool_catalog.py` to update every row and the total.
The v0.5 live session exercised 42/67 tools, including all 25 new v0.5 names,
and produced a simulated skirt and sleeveless pocket top. It found placement,
coordinate and JSON settings gaps. Version 0.6 addresses those findings in code;
**no tests or live MD calls were run for v0.6, at the user's request**.
See [the implementation and remaining limits](docs/live-test-improvements.md).
Generate the catalog without importing the server using
`python scripts/update_tool_catalog.py --static` when execution is intentionally deferred.

## Why does MD freeze while the listener runs?

MD's embedded Python (3.11) doesn't give CPU to background threads — a daemon
thread spawned from a script is `is_alive() == True` but never actually
executes. So the socket server has to run on whatever thread the Python Editor
uses, which is MD's GUI thread. Empirically (verified live), API calls work
fine while the GUI is frozen because they are synchronous main-thread calls;
the GUI just doesn't repaint or accept input until the listener returns.

The "right" fix is a C++ plugin (Qt-based, worker thread + queued connection
back to the main thread). We built and tested that for CLO 3D's plugin model
in `cpp_plugin/` — but **MD's loader scans only `.py` files** and ignores
`.dll`s entirely (verified with a zero-dep probe DLL). The C++ code is kept
for CLO 3D and for the day MD adds a `.dll` loader; see `cpp_plugin/README.md`.

## Caveats

- **MD freezes while the listener runs.** Use `shutdown_listener` when you want the
  GUI back. For an LLM-driven workflow this is usually fine — Claude does the work.
- **Modal-dialog deadlock.** Any API call that pops a modal dialog (unsaved-changes
  prompt, error popup, file picker) hangs the listener forever, because MD's GUI
  thread is stuck in our loop. Recovery: close MD. The wrappers pick dialog-free
  call variants where possible.
- **Incomplete client requests are bounded.** The listener closes a connection
  that does not finish its newline-terminated request within 5 seconds or whose
  request exceeds 1 MiB. This keeps an idle or oversized local client from
  holding the GUI thread indefinitely.
- **Heavy ops are slow.** Exporting a large `.zprj` (e.g. with an embedded Alembic
  animation), simulation and rendering can take a long time; the bridge timeout
  defaults to 120s — raise `MD_MCP_TIMEOUT` (seconds) for very long renders.
- **localhost only.** The listener binds `127.0.0.1`; it is not exposed to the network.
- Generated MD code must `import` the `*_api` module(s) it uses (they are not
  globals) and bind output to `result`. The API is pybind11-based.

## Config (environment variables)

| Var | Default | Meaning |
|---|---|---|
| `MD_MCP_HOST` | `127.0.0.1` | listener host the bridge connects to |
| `MD_MCP_PORT` | `7421` | listener port |
| `MD_MCP_TIMEOUT` | `120.0` | bridge socket timeout, seconds |
| `MD_MCP_MAX_RESPONSE_BYTES` | `16777216` | maximum listener response line size |

The listener-side request read timeout and 1 MiB request limit are defined in
`md_addon/md_listener.py` (`REQUEST_READ_TIMEOUT` and `MAX_REQUEST_BYTES`).
Listener responses are limited to 16 MiB by default. Raise
`MD_MCP_MAX_RESPONSE_BYTES` if a workflow returns larger values.

(The listener side's host/port are constants in `md_addon/md_listener.py` — keep
them in sync if you change the defaults.)

## Uninstall

There's nothing installed inside MD — just stop the listener (`shutdown_listener`
or close MD) and remove the Claude Desktop config entry. Delete the repo to remove
the rest.

## License

MIT — see `LICENSE`.
