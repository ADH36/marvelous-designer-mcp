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

| Tool | What it does |
|---|---|
| `ping` | Check the listener is reachable. |
| `execute_python(code)` | Run arbitrary Python in MD's interpreter. `import` the `*_api` modules; bind your return value to a name called `result`. Returns `{stdout, stderr, result, error}`. |
| `shutdown_listener()` | Stop the listener loop and release the MD GUI. |
| `scene_info()` | Project name/path, MD version, pattern & fabric counts. |
| `list_patterns()` | Pattern pieces: index, name, assigned fabric index. |
| `list_fabrics()` | Fabrics: index, name (+ fabric-style list). |
| `assign_fabric(fabric_index, pattern_index, assignment_mode=1)` | Assign fabric to the current colorway (1), all unlinked (2), or all linked (3). |
| `import_project(path)` | Open a `.zprj` / `.zpac` / `.obj` / `.fbx` / ... by absolute path (`import_api.ImportFile`). |
| `export_project(path)` | Save the scene as a `.zprj` (`export_api.ExportZPrjW`). |
| `simulate(steps=1)` | `utility_api.Simulate(int)`. |
| `md_api(module, contains="")` | List public module attributes; substring-filtered. Inspect a callable's `__doc__` before using it. |
| `preview_garment(output_dir, ...)` | Return turntable PNG images directly as MCP image content. |
| `export_turntable_images(path, ...)` | Write evenly spaced garment previews with verified output files. |
| `export_custom_views(output_dir, ...)` | Export existing saved custom views without the standard snapshot dialog. |
| `export_obj(path, ...)` | Export garment OBJ with explicit scale, mesh, avatar and UV options. |
| `inspect_pattern(pattern_index)` | Inspect boundary edges, points, fabric and mesh resolution. |
| `rename_pattern(pattern_index, name)` | Rename a piece and verify its name. |
| `mirror_pattern(pattern_index, with_sewing=False)` | Create a symmetric piece and return refreshed indices. |
| `select_patterns(pattern_indices, ...)` | Select multiple pieces and verify selection. |
| `set_pattern_resolution(pattern_indices, particle_distance, mesh_type)` | Batch-edit particle distance and Triangle/Quad mesh type with read-back checks. |
| `list_seams()` | List sewing groups. |
| `sew_edges(pattern_a, line_a, pattern_b, line_b, ...)` | Sew validated boundary edges with explicit directions. |
| `assign_fabric_batch(fabric_index, pattern_indices, assignment_mode=1)` | Batch-assign a fabric and report partial failures. |
| `create_fabric_from_textures(path, base_texture, ...)` | Create a .zfab preset from existing texture maps. |
| `save_checkpoint(path, ...)` | Save and verify a .zprj checkpoint. |
| `garment_workflow(output_dir, ...)` | Checkpoint, optionally append avatar/garment assets, simulate, save, export mesh and previews. |
| `create_pattern(points, name, ...)`, `create_rectangle(width, height, name, ...)` | Create validated straight-edge pattern geometry. |
| `diagnose_sewing(seam_pairs, ...)` | Compare explicit boundary pairs and return mismatches, reused edges and a sewing map. |
| `import_fabric(path)`, `replace_fabric(fabric_index, path)` | Import .zfab/.jfab or replace an existing fabric using .zfab. |
| `build_skirt_recipe(...)` | Generate a two-panel skirt block from measurements without changing MD. |
| `save_garment_recipe(path, recipe)`, `load_garment_recipe(path)` | Persist and validate data-only JSON recipes. |
| `apply_garment_recipe(recipe, ..., dry_run=True)` | Review a recipe or checkpoint, append pieces, sew, apply fabric and export. |
| `animation_state()`, `configure_animation(start_frame, end_frame)` | Read/set animation ranges with read-back checks. |
| `record_animation(start_frame, end_frame, checkpoint_path)` | Checkpoint then record cloth animation. |
| `export_alembic(path, ...)` | Export an existing garment animation cache to Alembic. |
| `save_export_profile(...)`, `export_obj_with_profile(path, profile_path)` | Save explicit destination calibration and reuse validated scale/axes. |
| `batch_garment_workflows(jobs, output_dir)` | Replace the scene per .zprj job, checkpoint and export, then write a batch report. |

Anything not covered by a wrapper: use `execute_python` directly.

Version 0.4 exposes 42 tools. See [garment/export contracts](docs/features.md) and
[recipe, animation and batch examples](docs/recipes.md) for checkpoint guidance
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
```

These tests exercise TCP framing, API contracts, mutation preflight, partial
failures, workflow ordering and actual MCP image content without requiring a
Marvelous Designer installation. `uv sync` installs the SDK and Pillow needed
for the integration tests. Plain Python without the SDK skips those tests.

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
