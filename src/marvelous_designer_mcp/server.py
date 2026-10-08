import base64
import json
import hashlib
import time
import uuid
import os
import tempfile
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from . import bridge, operations, recipes, advanced, geometry, native_controls, mesh_analysis, drafting
from .config import MD_HOST, MD_PORT

mcp = FastMCP("marvelous-designer")
_OPERATION_SOURCE = Path(operations.__file__).read_text(encoding="utf-8")
_RECIPE_SOURCE = Path(recipes.__file__).read_text(encoding="utf-8")
_ADVANCED_SOURCE = Path(advanced.__file__).read_text(encoding="utf-8")
_GEOMETRY_SOURCE = Path(geometry.__file__).read_text(encoding="utf-8")
_NATIVE_SOURCE = Path(native_controls.__file__).read_text(encoding="utf-8")
_history = deque(maxlen=100)
_history_lock = Lock()
_journal_lock = Lock()
_server_instance_id = uuid.uuid4().hex
_journal_directory = Path(os.environ.get('MD_MCP_HISTORY_DIR',str(
    Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'marvelous-designer-mcp'/'journals')))
_journal_path = _journal_directory/(_server_instance_id+'.jsonl')


def _persist_operation(entry):
    record={'server_instance_id':_server_instance_id,**entry}
    with _journal_lock:
        _journal_directory.mkdir(parents=True,exist_ok=True)
        with _journal_path.open('a',encoding='utf-8') as file:
            file.write(json.dumps(record,allow_nan=False)+'\n')
            file.flush()
            os.fsync(file.fileno())


def _md_exec(code: str, *, timeout: float | None = None) -> dict:
    """Run `code` inside MD via execute_python and flatten the listener's envelope.

    The listener returns {stdout, stderr, result, error}; this returns
    {"ok": True, "result": ...} or {"ok": False, "error": ...} (plus "stdout" if any).
    """
    try:
        resp = bridge.call("execute_python", {"code": code}, timeout=timeout)
    except bridge.BridgeError as e:
        return {"ok": False, "error": f"bridge: {e}"}
    if not isinstance(resp, dict):
        return {"ok": True, "result": resp}
    out: dict = {"ok": False, "error": resp["error"]} if resp.get("error") else {"ok": True, "result": resp.get("result")}
    if resp.get("stdout"):
        out["stdout"] = resp["stdout"]
    if resp.get("stderr"):
        out["stderr"] = resp["stderr"]
    return out


def _execute_md_operation(operation: str, *, timeout: float | None = None, **params) -> dict:
    try:
        encoded = json.dumps(params, allow_nan=False, ensure_ascii=True)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    code = _OPERATION_SOURCE + '\n' + _RECIPE_SOURCE + '\n' + _ADVANCED_SOURCE + '\n' + _GEOMETRY_SOURCE + '\n' + _NATIVE_SOURCE + f"\nresult = run_operation({operation!r}, json.loads({encoded!r}))\n"
    response = _md_exec(code, timeout=timeout)
    if response.get("ok") and isinstance(response.get("result"), dict):
        outcome = response["result"]
        for stream in ("stdout", "stderr"):
            if response.get(stream):
                outcome[stream] = response[stream]
        return outcome
    return response


def _md_operation(operation: str, *, timeout: float | None = None, **params) -> dict:
    # Record identifiers and a digest, never arbitrary scripts or complete inputs.
    entry = {'operation_id': uuid.uuid4().hex, 'operation': operation,
             'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'started',
             'parameter_keys': sorted(params)}
    try:
        entry['parameter_sha256'] = hashlib.sha256(json.dumps(params, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError):
        pass
    with _history_lock:
        _history.append(entry)
    try:
        _persist_operation(dict(entry))
    except OSError as exc:
        with _history_lock:
            entry.update(status='failed',error='Cannot persist start record')
        return {'ok':False,'error':'Operation journal is unavailable: '+str(exc),
                'operation_id':entry['operation_id'],'partial_change_possible':False}
    started = time.monotonic()
    try:
        outcome = _execute_md_operation(operation, timeout=timeout, **params)
    except Exception:
        with _history_lock:
            entry.update(status='uncertain', elapsed_seconds=round(time.monotonic()-started, 3))
        try:
            _persist_operation(dict(entry))
        except OSError:
            pass
        raise
    with _history_lock:
        entry.update(status='completed' if outcome.get('ok') else
                     ('uncertain' if str(outcome.get('error', '')).startswith('bridge:') else 'failed'),
                     elapsed_seconds=round(time.monotonic()-started, 3),
                     partial_change_possible=outcome.get('partial_change_possible', False))
    outcome['operation_id'] = entry['operation_id']
    outcome['journal_path'] = str(_journal_path)
    try:
        _persist_operation(dict(entry))
    except OSError as exc:
        outcome['journal_error']=str(exc)
        outcome['journal_completion_persisted']=False
    return outcome


def _resize_turntable(outcome: dict, width: int, height: int) -> dict:
    if outcome.get("ok"):
        try:
            render_sizes = []
            for filename in outcome['files']:
                with Image.open(filename) as image:
                    if image.format != 'PNG':
                        raise ValueError('MD returned a non-PNG turntable image')
                    render_sizes.append(list(image.size))
                    if image.size != (width, height):
                        resized = ImageOps.pad(image.convert('RGBA'), (width, height),
                                               method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
                        resized.save(filename, format='PNG')
            outcome['render_sizes'] = render_sizes
            outcome['output_size'] = [width, height]
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            outcome['ok'] = False
            outcome['error'] = 'Preview image processing failed: ' + str(exc)
    return outcome


@mcp.tool()
def ping() -> dict:
    """Verify the MD listener is reachable. Returns whatever the listener echoes back."""
    try:
        return {"ok": True, "host": MD_HOST, "port": MD_PORT, "result": bridge.call("ping")}
    except bridge.BridgeError as e:
        return {"ok": False, "host": MD_HOST, "port": MD_PORT, "error": str(e)}


@mcp.tool()
def execute_python(code: str) -> dict:
    """Execute arbitrary Python inside Marvelous Designer's interpreter.

    MD's API is exposed as importable modules (import_api, export_api, fabric_api,
    pattern_api, utility_api, ...), NOT as globals — so `import` what you need.
    Bind the value you want back to a name called `result`. Inspect API function
    __doc__ strings for signatures; wrong/no-argument probing can invoke valid
    overloads and mutate the scene.

    Returns: {"stdout": str, "stderr": str, "result": any, "error": str|None}.
    """
    try:
        return bridge.call("execute_python", {"code": code})
    except bridge.BridgeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def shutdown_listener() -> dict:
    """Stop the MD-side listener's blocking loop and release the Marvelous Designer GUI."""
    try:
        return {"ok": True, "result": bridge.call("shutdown")}
    except bridge.BridgeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def scene_info() -> dict:
    """Summary of the current MD scene: project name/path, MD version, pattern & fabric counts."""
    return _md_exec(
        "import utility_api, pattern_api, fabric_api\n"
        "result = {\n"
        "    'project_name': utility_api.GetProjectName(),\n"
        "    'project_path': utility_api.GetProjectFilePath(),\n"
        "    'md_version': [utility_api.GetMajorVersion(), utility_api.GetMinorVersion(), utility_api.GetPatchVersion()],\n"
        "    'pattern_count': pattern_api.GetPatternCount(),\n"
        "    'fabric_count': fabric_api.GetFabricCount(True),\n"
        "    'fabric_styles': fabric_api.GetFabricStyleNameList(),\n"
        "}\n"
    )


@mcp.tool()
def list_patterns() -> dict:
    """List pattern pieces in the current scene: index, name, assigned fabric index."""
    return _md_exec(
        "import pattern_api\n"
        "result = [\n"
        "    {'index': i, 'name': pattern_api.GetPatternPieceName(i), 'fabric_index': pattern_api.GetPatternPieceFabricIndex(i)}\n"
        "    for i in range(pattern_api.GetPatternCount())\n"
        "]\n"
    )


@mcp.tool()
def list_fabrics() -> dict:
    """List fabrics in the current scene: index and name (plus the fabric-style name list)."""
    return _md_exec(
        "import fabric_api\n"
        "result = {\n"
        "    'fabrics': [{'index': i, 'name': fabric_api.GetFabricName(i)} for i in range(fabric_api.GetFabricCount(True))],\n"
        "    'styles': fabric_api.GetFabricStyleNameList(),\n"
        "}\n"
    )


@mcp.tool()
def assign_fabric(fabric_index: int, pattern_index: int, assignment_mode: int = 1,
                  face: int | None = None) -> dict:
    """Assign fabric with colorway mode 1=current, 2=all unlinked, 3=all linked.

    face is a deprecated alias for the numeric assignment mode, not a surface face.
    Default mode 1 limits changes to the current colorway.
    """
    return _md_operation('assign_fabric_batch', fabric_index=fabric_index,
                         pattern_indices=[pattern_index], assignment_mode=assignment_mode, face=face)


@mcp.tool()
def import_project(path: str) -> dict:
    """Open an MD project / garment / mesh file (.zprj, .zpac, .obj, .fbx, ...) by absolute path.

    Uses the generic import_api.ImportFile (dispatches by extension); the *W variant
    handles non-ASCII Windows paths. Returns which call ran and its bool result.

    Caveat: if MD raises a modal dialog (e.g. "save current project?"), the listener
    deadlocks because the GUI thread is stuck in our accept loop — close MD to recover.
    """
    code = (
        "import import_api\n"
        f"path = {path!r}\n"
        "result = None\n"
        "for name in ('ImportFileW', 'ImportFile'):\n"
        "    fn = getattr(import_api, name, None)\n"
        "    if fn is None:\n"
        "        continue\n"
        "    try:\n"
        "        result = {'ok': bool(fn(path)), 'used': name, 'path': path}\n"
        "        break\n"
        "    except TypeError as e:\n"
        "        result = {'ok': False, 'signature_error': str(e), 'tried': name}\n"
        "if result is None:\n"
        "    result = {'ok': False, 'error': 'no ImportFile variant in import_api', 'path': path}\n"
    )
    return _md_exec(code)


@mcp.tool()
def export_project(path: str) -> dict:
    """Save the current scene as a .zprj project file at the given absolute path.

    Uses export_api.ExportZPrjW(path, False) (False disables thumbnail creation),
    falling back to ExportZPrj(path). Returns the path string MD
    reports, or the raw signature error if the call shape was wrong.
    """
    code = (
        "import export_api\n"
        f"path = {path!r}\n"
        "result = None\n"
        "fnW = getattr(export_api, 'ExportZPrjW', None)\n"
        "if fnW is not None:\n"
        "    try:\n"
        "        result = {'ok': True, 'used': 'ExportZPrjW', 'returned': fnW(path, False)}\n"
        "    except TypeError as e:\n"
        "        result = {'ok': False, 'signature_error': str(e), 'tried': 'ExportZPrjW'}\n"
        "if result is None or not result.get('ok'):\n"
        "    fn = getattr(export_api, 'ExportZPrj', None)\n"
        "    if fn is not None:\n"
        "        try:\n"
        "            result = {'ok': True, 'used': 'ExportZPrj', 'returned': fn(path)}\n"
        "        except TypeError as e:\n"
        "            result = {'ok': False, 'signature_error': str(e), 'tried': 'ExportZPrj'}\n"
        "if result is None:\n"
        "    result = {'ok': False, 'error': 'no ExportZPrj variant in export_api', 'path': path}\n"
    )
    return _md_exec(code)


@mcp.tool()
def simulate(steps: int = 1) -> dict:
    """Run cloth simulation via utility_api.Simulate(int).

    `steps` is the int the MD API expects (its exact meaning — frame/step count or a
    mode — is version-dependent; 1 is a reasonable default). Returns the bool MD reports.
    """
    code = (
        "import utility_api\n"
        f"steps = {int(steps)}\n"
        "try:\n"
        "    result = {'ok': True, 'returned': utility_api.Simulate(steps), 'steps': steps}\n"
        "except TypeError as e:\n"
        "    result = {'ok': False, 'signature_error': str(e)}\n"
    )
    return _md_exec(code)


@mcp.tool()
def md_api(module: str, contains: str = "") -> dict:
    """List public attributes of an installed MD API module, optionally filtered by name.

    Modules include import_api, export_api, fabric_api, pattern_api and utility_api.
    `contains` filters names by case-insensitive substring.

    To learn a function's signature, inspect its __doc__ via execute_python first.
    Wrong/no-argument probing can invoke valid overloads, so don't probe setters.
    """
    code = (
        "import importlib\n"
        f"mod = importlib.import_module({module!r})\n"
        f"sub = {contains.lower()!r}\n"
        "names = [n for n in dir(mod) if not n.startswith('_')]\n"
        "if sub:\n"
        "    names = [n for n in names if sub in n.lower()]\n"
        "result = sorted(names)\n"
    )
    return _md_exec(code)


@mcp.tool()
def inspect_pattern(pattern_index: int) -> dict:
    """Inspect a pattern's name, fabric, mesh resolution, points and boundary edges.

    Use the returned edge indices with sew_edges. Lengths are in MD API native
    units. Geometry schema and index bounds are checked before sewing.
    """
    return _md_operation("inspect_pattern", pattern_index=pattern_index)


@mcp.tool()
def rename_pattern(pattern_index: int, name: str) -> dict:
    """Rename a pattern piece and read its name back to verify the change."""
    return _md_operation("rename_pattern", pattern_index=pattern_index, name=name)


@mcp.tool()
def mirror_pattern(pattern_index: int, with_sewing: bool = False) -> dict:
    """Create a symmetric pattern, optionally including sewing.

    Save a checkpoint first. Returns all pieces after the change; refresh indices
    before further edits. This adds geometry to the current scene.
    """
    return _md_operation("mirror_pattern", pattern_index=pattern_index, with_sewing=with_sewing)


@mcp.tool()
def select_patterns(pattern_indices: list[int], keep_previous: bool = False) -> dict:
    """Select multiple pattern pieces and return the verified selection."""
    return _md_operation("select_patterns", pattern_indices=pattern_indices, keep_previous=keep_previous)


@mcp.tool()
def set_pattern_resolution(pattern_indices: list[int], particle_distance: float,
                           mesh_type: Literal["Triangle", "Quad"] = "Triangle") -> dict:
    """Set particle distance and mesh type for a batch of patterns, then read back.

    Distance uses MD native units and must be at least 0.8. All indices are checked
    before mutation. A partial failure reports completed pieces and failed_index;
    inspect that piece before retrying. No automatic rollback occurs.
    """
    return _md_operation("set_pattern_resolution", pattern_indices=pattern_indices,
                         particle_distance=particle_distance, mesh_type=mesh_type)


@mcp.tool()
def list_seams() -> dict:
    """List the sewing groups in the current scene by index and name."""
    return _md_operation("list_seams")


@mcp.tool()
def sew_edges(pattern_a: int, line_a: int, pattern_b: int, line_b: int,
              direction_a: bool = True, direction_b: bool = False) -> dict:
    """Sew two boundary edges. True means forward and False means backward.

    Call inspect_pattern for valid edge indices and lengths. Save a checkpoint
    before sewing. Directions are explicit; this does not guess seam orientation
    or equalize edge lengths. Invalid endpoints are rejected before mutation.
    """
    return _md_operation("sew_edges", pattern_a=pattern_a, line_a=line_a,
                         pattern_b=pattern_b, line_b=line_b,
                         direction_a=direction_a, direction_b=direction_b)


@mcp.tool()
def assign_fabric_batch(fabric_index: int, pattern_indices: list[int], assignment_mode: int = 1,
                       face: int | None = None) -> dict:
    """Assign one fabric to multiple pieces with preflight bounds checks.

    Modes: 1=current colorway, 2=all unlinked, 3=all linked. face is a deprecated
    numeric alias. Reports read-back indices and completed pieces on failure.
    """
    return _md_operation("assign_fabric_batch", fabric_index=fabric_index,
                         pattern_indices=pattern_indices, assignment_mode=assignment_mode, face=face)


@mcp.tool()
def create_fabric_from_textures(path: str, base_texture: str, normal_texture: str = "",
                               displacement_texture: str = "", opacity_texture: str = "",
                               roughness_texture: str = "", metalness_texture: str = "",
                               overwrite: bool = False) -> dict:
    """Create a .zfab preset from existing texture maps, verifying the output file.

    Use absolute paths. Optional maps may be empty. This creates a preset file;
    it does not assign or import the preset into the current scene.
    """
    return _md_operation("create_fabric_from_textures", path=path, base_texture=base_texture,
                         normal_texture=normal_texture, displacement_texture=displacement_texture,
                         opacity_texture=opacity_texture, roughness_texture=roughness_texture,
                         metalness_texture=metalness_texture, overwrite=overwrite)


@mcp.tool()
def export_obj(path: str, scale: float = 1.0, thin: bool = True, single_object: bool = False,
               include_avatar: bool = False, unified_uv: bool = True, overwrite: bool = False,
               timeout: float = 120.0) -> dict:
    """Export garment OBJ with explicit options to avoid an export dialog.

    path must be an absolute .obj path in a dedicated empty output folder (unless
    overwrite=True), because MD also writes material/texture files. Scale is an
    explicit multiplier; coordinate axes retain MD's API defaults. Verify scale
    with your DAZ/Unity/Blender destination. Returns verified output paths.
    """
    return _md_operation("export_obj", timeout=timeout, path=path, scale=scale,
                         thin=thin, single_object=single_object, include_avatar=include_avatar,
                         unified_uv=unified_uv, overwrite=overwrite)


@mcp.tool()
def export_turntable_images(path: str, image_count: int = 4, width: int = 1024, height: int = 1024,
                            start_index: int = 0, overwrite: bool = False, timeout: float = 120.0) -> dict:
    """Export 1–72 evenly spaced turntable images using an absolute PNG path prefix.

    Uses the current scene/colorway and view settings. Verifies files were written.
    Use preview_garment to return image content directly to the MCP client.
    """
    outcome = _md_operation("export_turntable_images", timeout=timeout, path=path,
                            image_count=image_count, width=width, height=height,
                            start_index=start_index, overwrite=overwrite)
    return _resize_turntable(outcome, width, height)


@mcp.tool()
def export_custom_views(output_dir: str, width: int = 1024, height: int = 1024,
                        prefix: str = "view", overwrite: bool = False, timeout: float = 120.0) -> dict:
    """Export saved MD custom views into an absolute output directory.

    Requires custom views already configured in MD. Empty output is reported as a
    failure. Uses ExportCustomViewSnapshot, avoiding ExportSnapshot3D's dialog.
    """
    return _md_operation("export_custom_views", timeout=timeout, output_dir=output_dir,
                         width=width, height=height, prefix=prefix, overwrite=overwrite)


@mcp.tool()
def preview_garment(output_dir: str, image_count: int = 4, width: int = 1024, height: int = 1024,
                    prefix: str = "preview", overwrite: bool = False, timeout: float = 120.0) -> CallToolResult:
    """Generate up to 8 turntable views and return PNG image content to the agent.

    Use an absolute output directory and a new filename prefix. Dimensions are
    limited to 2048 for MCP previews. Uses current camera/render settings.
    Exported images remain on disk; unsupported/oversized images report an error.
    """
    if (type(image_count) is not int or not 1 <= image_count <= 8 or
            type(width) is not int or type(height) is not int or
            not 64 <= width <= 2048 or not 64 <= height <= 2048 or
            not prefix or any(c in prefix for c in '/\\\x00') or prefix in (".", "..")):
        outcome = {"ok": False, "error": "Preview requires 1–8 images, dimensions 64–2048, and a filename prefix"}
    else:
        outcome = export_turntable_images(str(Path(output_dir) / (prefix + ".png")), image_count,
                                           width, height, 0, overwrite, timeout)
    content = []
    if outcome.get("ok"):
        try:
            if len(outcome["files"]) > 8:
                raise ValueError("MD returned too many preview files")
            for path in outcome["files"]:
                file = Path(path)
                if file.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Preview exceeds 8 MiB; use smaller dimensions: " + path)
                data = file.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("MD returned a non-PNG preview: " + path)
                content.append(ImageContent(type="image", mimeType="image/png",
                                            data=base64.b64encode(data).decode("ascii")))
        except (OSError, ValueError) as exc:
            outcome["ok"] = False
            outcome["error"] = str(exc)
            content = []
    content.insert(0, TextContent(type="text", text=json.dumps(outcome, ensure_ascii=False)))
    return CallToolResult(content=content, isError=not outcome.get("ok", False))


@mcp.tool()
def save_checkpoint(path: str, overwrite: bool = False, timeout: float = 120.0) -> dict:
    """Save a .zprj checkpoint without a thumbnail dialog and verify the file."""
    return _md_operation("save_checkpoint", timeout=timeout, path=path, overwrite=overwrite)


@mcp.tool()
def garment_workflow(output_dir: str, avatar_path: str = "", garment_path: str = "",
                     simulation_steps: int = 0, preview_count: int = 4, scale: float = 1.0,
                     overwrite: bool = False, timeout: float = 300.0) -> dict:
    """Checkpoint, optionally append .avt/.zpac assets, simulate, save, export OBJ and previews.

    Requires an empty absolute output directory unless overwrite=True. Assets are
    appended using explicit import options. A checkpoint precedes mutations.
    simulation_steps is passed directly to MD's Simulate(int); 0 skips simulation.
    Stops on first failure and reports completed stages. Mutations are not rolled
    back. A timeout leaves completion uncertain: inspect state before retrying.
    """
    outcome = _md_operation("garment_workflow", timeout=timeout, output_dir=output_dir,
                            avatar_path=avatar_path, garment_path=garment_path,
                            simulation_steps=simulation_steps, preview_count=preview_count,
                            scale=scale, overwrite=overwrite)
    for stage in outcome.get('stages', []):
        if stage['stage'] == 'previews' and stage.get('ok'):
            _resize_turntable(stage, 1024, 1024)
            if not stage['ok']:
                outcome['ok'] = False
                outcome['error'] = stage['error']
    return outcome


def _local_call(callback) -> dict:
    try:
        return callback()
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return {'ok': False, 'error': str(exc)}


def _read_json(path: str, max_bytes: int = 1024 * 1024) -> dict:
    path = operations._path(path, '.json', must_exist=True)
    if Path(path).stat().st_size > max_bytes:
        raise ValueError('JSON document exceeds the permitted size')
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('Duplicate JSON key: ' + key)
            obj[key] = value
        return obj
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=invalid)


def _write_json(path: str, value: dict, overwrite: bool = False, max_bytes: int = 1024 * 1024) -> dict:
    path = operations._path(path, '.json')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    if len(encoded.encode('utf-8')) > max_bytes:
        raise ValueError('JSON document exceeds the permitted size')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=Path(path).parent, delete=False) as file:
                temporary = file.name
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    else:
        with open(path, 'x', encoding='utf-8') as file:
            file.write(encoded)
    return {'ok': True, 'path': path, 'bytes': Path(path).stat().st_size}


def _finish_previews(outcome):
    for stage in outcome.get('stages', []):
        if stage['stage'] == 'previews' and stage.get('ok'):
            _resize_turntable(stage, 1024, 1024)
            if not stage['ok']:
                outcome['ok'] = False
                outcome['error'] = stage['error']
    return outcome


def _attach_report(outcome, path):
    report = _local_call(lambda: _write_json(path, outcome))
    outcome['report'] = report
    if not report['ok']:
        # File/report failures must not erase completed scene mutations and stages.
        outcome['ok'] = False
        outcome['report_error'] = report['error']
        outcome['partial_change_possible'] = True
    return outcome


@mcp.tool()
def create_pattern(points: list[list[float]], name: str, coordinate_scale: float = 1.0) -> dict:
    """Create a named straight-edge polygon from [x,y] points in MD native units.

    coordinate_scale is explicit; 1 preserves input coordinates. Rejects degenerate
    and self-intersecting polygons before MD. Save a checkpoint before scene edits.
    """
    return _md_operation('create_pattern', points=points, name=name, coordinate_scale=coordinate_scale)


@mcp.tool()
def create_rectangle(width: float, height: float, name: str,
                     origin_x: float = 0.0, origin_y: float = 0.0) -> dict:
    """Create a rectangle in MD native units; boundary starts at the origin."""
    def create():
        w = recipes.recipe_number(width, 'width', True)
        h = recipes.recipe_number(height, 'height', True)
        x = recipes.recipe_number(origin_x, 'origin_x')
        y = recipes.recipe_number(origin_y, 'origin_y')
        return create_pattern([[x,y], [x+w,y], [x+w,y+h], [x,y+h]], name)
    return _local_call(create)


@mcp.tool()
def diagnose_sewing(seam_pairs: list[dict], tolerance_percent: float = 5.0) -> dict:
    """Compare explicit boundary pairs, report length mismatches/reused edges and a sewing map.

    Each pair contains pattern_a, line_a, pattern_b, line_b. Read-only; it does not
    infer existing seam endpoints or certify fit or seam orientation.
    """
    return _md_operation('diagnose_sewing', seam_pairs=seam_pairs, tolerance_percent=tolerance_percent)


@mcp.tool()
def import_fabric(path: str) -> dict:
    """Import an existing absolute .zfab/.jfab path; verify its index and name."""
    return _md_operation('import_fabric', path=path)


@mcp.tool()
def replace_fabric(fabric_index: int, path: str) -> dict:
    """Replace an existing fabric using a .zfab file. Verify appearance separately."""
    return _md_operation('replace_fabric', fabric_index=fabric_index, path=path)


@mcp.tool()
def build_skirt_recipe(waist_cm: float, length_cm: float, hem_cm: float,
                       ease_cm: float = 2.0, native_units_per_cm: float = 10.0,
                       vertical_direction: Literal['down','up'] = 'down') -> dict:
    """Build a data-only two-panel skirt block with matching side seams.

    Does not change MD. native_units_per_cm is explicit calibration (10 for mm).
    down uses negative Y, as observed in MD 2026; up preserves the old positive-Y draft.
    This is a starting block without darts, openings, waistband or seam allowance;
    avatar arrangement and fit validation remain required.
    """
    return _local_call(lambda: {'ok': True, 'recipe': recipes.skirt_recipe(waist_cm, length_cm, hem_cm, ease_cm, native_units_per_cm, vertical_direction),
                              'coordinate_convention':{'vertical_direction':vertical_direction,'downward_y_sign':-1 if vertical_direction=='down' else 1}})


@mcp.tool()
def save_garment_recipe(path: str, recipe: dict, overwrite: bool = False) -> dict:
    """Validate and save a reusable JSON recipe; refuses overwrite by default."""
    return _local_call(lambda: _write_json(path, recipes.validate_recipe(recipe), overwrite))


@mcp.tool()
def load_garment_recipe(path: str) -> dict:
    """Load and validate a data-only recipe JSON, with no scene changes."""
    return _local_call(lambda: {'ok': True, 'recipe': recipes.validate_recipe(_read_json(path))})


@mcp.tool()
def apply_garment_recipe(recipe: dict, output_dir: str = '', dry_run: bool = True,
                         timeout: float = 300.0) -> dict:
    """Review or apply an explicit garment recipe. Dry-run is the default.

    Application requires an empty absolute directory, saves before.zprj, appends
    pieces, verifies boundary lengths/order, sews, optionally imports/assigns fabric,
    saves project/OBJ/previews. Stops at failure; no rollback or automatic simulation.
    """
    def apply():
        normalized = recipes.validate_recipe(recipe)
        if dry_run:
            return {'ok': True, 'dry_run': True, 'recipe': normalized,
                    'actions': ['checkpoint', 'create patterns', 'validate/sew provided pairs',
                                'optional fabric import/assignment', 'save project', 'export OBJ', 'optional previews'],
                    'fit': 'not arranged or simulated'}
        outcome = _finish_previews(_md_operation('apply_garment_recipe', timeout=timeout,
                                                 recipe=normalized, output_dir=output_dir))
        if Path(output_dir).is_absolute() and Path(output_dir).is_dir() and outcome.get('stages'):
            _attach_report(outcome, str(Path(output_dir) / 'recipe-result.json'))
        return outcome
    return _local_call(apply)


@mcp.tool()
def animation_state() -> dict:
    """Read the current animation frame and start/end range."""
    return _md_operation('animation_state')


@mcp.tool()
def configure_animation(start_frame: float, end_frame: float) -> dict:
    """Set an animation frame range with read-back checks. Does not simulate."""
    return _md_operation('configure_animation', start_frame=start_frame, end_frame=end_frame)


@mcp.tool()
def record_animation(start_frame: float, end_frame: float, checkpoint_path: str,
                      timeout: float = 300.0) -> dict:
    """Checkpoint then run MD animation recording for an explicit frame range.

    Recording changes the cloth cache. A timeout does not cancel it; inspect state
    before retrying. Completion alone does not certify visible motion quality.
    """
    return _md_operation('record_animation', timeout=timeout, start_frame=start_frame,
                         end_frame=end_frame, checkpoint_path=checkpoint_path)


@mcp.tool()
def export_alembic(path: str, scale: float = 1.0, include_avatar: bool = False,
                    overwrite: bool = False, timeout: float = 120.0) -> dict:
    """Export garment animation to a fresh Alembic file using explicit options.

    Existing animation/cache is required. File verification does not certify that
    the cache contains motion; inspect the animation at the destination.
    """
    return _md_operation('export_alembic', timeout=timeout, path=path, scale=scale,
                         include_avatar=include_avatar, overwrite=overwrite)


def _validate_profile(profile):
    recipes.recipe_keys(profile, ('schema_version', 'destination', 'scale', 'axis_options', 'validated', 'calibration_note'),
                        ('schema_version', 'destination', 'scale', 'axis_options', 'validated', 'calibration_note'), 'export profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ValueError('Unsupported export profile version')
    if profile['destination'] not in ('DAZ', 'Blender', 'Unity', 'custom'):
        raise ValueError('Unknown destination')
    recipes.recipe_number(profile['scale'], 'scale', True)
    axes = profile['axis_options']
    recipes.recipe_keys(axes, ('axisX','axisY','axisZ','bInvertX','bInvertY','bInvertZ'),
                        ('axisX','axisY','axisZ','bInvertX','bInvertY','bInvertZ'), 'axis options')
    for key in ('axisX','axisY','axisZ'):
        operations._integer(axes[key], key)
    if len({axes[k] for k in ('axisX','axisY','axisZ')}) != 3:
        raise ValueError('Axes must use three distinct installed MD axis codes')
    if any(type(axes[k]) is not bool for k in ('bInvertX','bInvertY','bInvertZ')) or type(profile['validated']) is not bool:
        raise ValueError('Profile flags must be booleans')
    if not isinstance(profile['calibration_note'], str) or not profile['calibration_note'].strip():
        raise ValueError('Record scale/orientation calibration or explain what remains unverified')
    return profile


@mcp.tool()
def save_export_profile(path: str, destination: Literal['DAZ','Blender','Unity','custom'],
                         scale: float, axis_codes: list[int], invert_axes: list[bool],
                         calibration_note: str, validated: bool = False, overwrite: bool = False) -> dict:
    """Save explicit destination scale/axes. No destination defaults are guessed.

    axis_codes are MD numeric codes for X/Y/Z; invert_axes are three boolean flags.
    Mark validated only after checking scale/orientation at the destination.
    """
    def save():
        if len(axis_codes) != 3 or len(invert_axes) != 3:
            raise ValueError('Provide three axis codes and three inversion flags')
        profile = {'schema_version': 1, 'destination': destination, 'scale': scale,
                   'axis_options': dict(zip(('axisX','axisY','axisZ','bInvertX','bInvertY','bInvertZ'), axis_codes+invert_axes)),
                   'validated': validated, 'calibration_note': calibration_note}
        return _write_json(path, _validate_profile(profile), overwrite)
    return _local_call(save)


@mcp.tool()
def export_obj_with_profile(path: str, profile_path: str, thin: bool = True,
                            single_object: bool = False, include_avatar: bool = False,
                            unified_uv: bool = True, overwrite: bool = False, timeout: float = 120.0) -> dict:
    """Export OBJ using a saved, destination-validated profile and explicit options."""
    def export():
        profile = _validate_profile(_read_json(profile_path))
        if not profile['validated']:
            raise ValueError('Profile is unverified; calibrate it at the destination first')
        outcome = _md_operation('export_obj', timeout=timeout, path=path, scale=profile['scale'],
                                axis_options=profile['axis_options'], thin=thin, single_object=single_object,
                                include_avatar=include_avatar, unified_uv=unified_uv, overwrite=overwrite)
        outcome['profile'] = profile
        return outcome
    return _local_call(export)


@mcp.tool()
def batch_garment_workflows(jobs: list[dict], output_dir: str, timeout: float = 600.0) -> dict:
    """Process 1–50 .zprj projects independently with checkpoints and a JSON report.

    Jobs contain project_path and optional simulation_steps (default 0), preview_count
    (default 4), scale (default 1). Checkpoints the original, replaces the scene per
    job, stops at first failure, and leaves the last loaded job active. No rollback.
    """
    def batch():
        prepared = _md_operation('prepare_garment_batch', timeout=timeout, jobs=jobs, output_dir=output_dir)
        if not prepared.get('ok'):
            return prepared
        outcome = {'ok': True, 'results': [], 'checkpoint': prepared['checkpoint'],
                   'scene_after': 'last loaded job; original preserved in checkpoint'}
        for position, job in enumerate(prepared['jobs']):
            result = _finish_previews(_md_operation('process_garment_batch_job', timeout=timeout,
                job=job, output_dir=str(Path(output_dir) / ('job_'+str(position).zfill(3)))))
            outcome['results'].append({'job': position, 'project_path': job['project_path'], **result})
            if not result['ok']:
                outcome['ok'] = False
                outcome['error'] = result.get('error', 'Batch job failed')
                outcome['failed_job'] = position
                outcome['partial_change_possible'] = True
                break
        if Path(output_dir).is_absolute() and Path(output_dir).is_dir() and 'results' in outcome:
            _attach_report(outcome, str(Path(output_dir) / 'batch-result.json'))
        return outcome
    return _local_call(batch)


@mcp.tool()
def create_curved_pattern(vertices: list[list[float | int]], name: str) -> dict:
    """Create a named native pattern with [x,y,type] vertices: 0 straight, 2 spline, 3 Bezier.

    Validates the vertex polygon, not the full curve or Bezier handles. Inspect the
    resulting curves visually; coordinates use MD native units.
    """
    return _md_operation('create_curved_pattern', vertices=vertices, name=name)


@mcp.tool()
def create_internal_shape(pattern_index: int, vertices: list[list[float | int]], closed: bool = False) -> dict:
    """Create an internal line or closed construction shape using [x,y,type] vertices."""
    return _md_operation('create_internal_shape', pattern_index=pattern_index, vertices=vertices, closed=closed)


@mcp.tool()
def sew_internal_edges(pattern_a: int, line_a: int, pattern_b: int, line_b: int,
                       child_a: int | None = None, child_b: int | None = None,
                       direction_a: bool = True, direction_b: bool = False) -> dict:
    """Sew boundary/internal or internal/internal edges using explicit child indices."""
    return _md_operation('sew_internal_edges', pattern_a=pattern_a, line_a=line_a,
                         pattern_b=pattern_b, line_b=line_b, child_a=child_a, child_b=child_b,
                         direction_a=direction_a, direction_b=direction_b)


@mcp.tool()
def move_pattern_2d(pattern_index: int, x: float, y: float) -> dict:
    """Move a piece in the 2D editor and verify its position; uses native units."""
    return _md_operation('move_pattern_2d', pattern_index=pattern_index, x=x, y=y)


@mcp.tool()
def copy_pattern(pattern_index: int, name: str, offset_x: float = 100.0, offset_y: float = 0.0) -> dict:
    """Copy a pattern with a 2D offset and verify the added piece's index/name."""
    return _md_operation('copy_pattern', pattern_index=pattern_index, name=name, offset_x=offset_x, offset_y=offset_y)


@mcp.tool()
def export_pattern_json(path: str, overwrite: bool = False) -> dict:
    """Export verified MD-native geometry JSON for external editing and round-trip import."""
    return _md_operation('export_pattern_json', path=path, overwrite=overwrite)


@mcp.tool()
def import_pattern_json(path: str, checkpoint_path: str, preserve_settings: bool = True,
                        preserve_fabric_presets: bool = True) -> dict:
    """Checkpoint/import native JSON and restore known resolution, layer, solidify and fabric assignments.

    Preservation requires exactly the current unique pattern names and uniquely
    named fabrics. False explicitly allows replacement without settings recovery.
    Native .zfab backups are restored by default; opt out with preserve_fabric_presets=False.
    Physical equivalence, colorways and simulation caches remain uncertified.
    """
    return _md_operation('import_pattern_json', path=path, checkpoint_path=checkpoint_path,
                         preserve_settings=preserve_settings,preserve_fabric_presets=preserve_fabric_presets)


@mcp.tool()
def list_arrangements() -> dict:
    """List installed avatar arrangement points with their native properties."""
    return _md_operation('list_arrangements')


@mcp.tool()
def inspect_arrangement(pattern_index: int) -> dict:
    """Read a pattern's native avatar arrangement properties."""
    return _md_operation('inspect_arrangement', pattern_index=pattern_index)


@mcp.tool()
def arrange_patterns(pattern_indices: list[int], arrangement_index: int,
                     shape_style: Literal['Flat', 'Curved'] = 'Flat',
                     orientation: int | None = None, position: list[int] | None = None) -> dict:
    """Assign patterns to an installed avatar arrangement point and read the resulting properties.

    Discover indices first. orientation and [x,y,offset] position are explicit native
    API values; their interpretation needs installed-version confirmation. Inspect
    placement visually before simulation. Property read-back does not establish
    mesh movement; use arrange_patterns_verified to record before/after mesh evidence.
    """
    return _md_operation('arrange_patterns', pattern_indices=pattern_indices, arrangement_index=arrangement_index,
                         shape_style=shape_style, orientation=orientation, position=position)


@mcp.tool()
def get_pattern_layer(pattern_index: int) -> dict:
    """Read a pattern's simulation layer."""
    return _md_operation('get_pattern_layer', pattern_index=pattern_index)


@mcp.tool()
def capture_mesh_snapshot(path: str) -> dict:
    """Export the visible garment OBJ and record bounds, centroid, counts and geometry digests."""
    return _md_operation('capture_mesh_snapshot',path=path)


@mcp.tool()
def compare_mesh_snapshots(before_path: str, after_path: str, movement_threshold: float = 0.1) -> dict:
    """Compare two local OBJ exports for mesh movement; no MD calls or fit certification."""
    return _local_call(lambda: geometry.compare_meshes(before_path,after_path,movement_threshold))


@mcp.tool()
def arrange_patterns_verified(pattern_indices: list[int], arrangement_index: int, output_dir: str,
                              shape_style: Literal['Flat','Curved'] = 'Flat',
                              orientation: int | None = None, position: list[int] | None = None,
                              movement_threshold: float = 0.1, require_movement: bool = True,
                              commit_redrape: bool = False) -> dict:
    """Checkpoint/apply arrangement and compare actual exported garment meshes before and after.

    Unchanged or noncomparable meshes fail when require_movement=True. False allows
    an intentional no-op with explicit movement evidence. commit_redrape=True
    explicitly requests whole-garment redrape after setters; this can reset drape.
    No guessed per-piece 3D setter is attempted. Movement does not certify fit.
    """
    return _md_operation('arrange_patterns_verified',pattern_indices=pattern_indices,
                         arrangement_index=arrangement_index,output_dir=output_dir,shape_style=shape_style,
                         orientation=orientation,position=position,movement_threshold=movement_threshold,
                         require_movement=require_movement,commit_redrape=commit_redrape)


@mcp.tool()
def arrange_patterns_by_name(pattern_indices: list[int], arrangement_name: str, output_dir: str,
                             shape_style: Literal['Flat','Curved'] = 'Flat',
                             movement_threshold: float = 0.1, require_movement: bool = True) -> dict:
    """Resolve an exact discovered avatar arrangement name and apply it with mesh evidence."""
    return _md_operation('arrange_patterns_by_name',pattern_indices=pattern_indices,
                         arrangement_name=arrangement_name,output_dir=output_dir,shape_style=shape_style,
                         movement_threshold=movement_threshold,require_movement=require_movement)


@mcp.tool()
def inspect_native_pattern_geometry(pattern_index: int, export_path: str) -> dict:
    """Export native geometry/control points/IDs alongside the actual API boundary edge map."""
    return _md_operation('inspect_native_pattern_geometry',pattern_index=pattern_index,export_path=export_path)


@mcp.tool()
def inspect_capabilities() -> dict:
    """Inspect installed native function availability/docstrings without invoking those functions."""
    return _md_operation('inspect_capabilities')


@mcp.tool()
def redrape_garment(output_dir: str, translation: list[float] | None = None,
                    movement_threshold: float = 0.1, require_movement: bool = True) -> dict:
    """Checkpoint and explicitly redrape the whole garment with before/after mesh evidence.

    Uses the installed ReDrape3DArrangement option signature captured in the live
    session. This may reset drape; it is not a per-pattern rigid transform. Bounds
    translation to 1000 native units per axis and never retries unchanged output.
    """
    return _md_operation('redrape_garment',output_dir=output_dir,translation=translation,
                         movement_threshold=movement_threshold,require_movement=require_movement)


@mcp.tool()
def backup_fabric_presets(output_dir: str, fabric_indices: list[int]) -> dict:
    """Export native fabric presets with unique names and SHA-256 recovery manifests."""
    return _md_operation('backup_fabric_presets',output_dir=output_dir,fabric_indices=fabric_indices)


@mcp.tool()
def restore_fabric_presets(manifest_path: str, checkpoint_path: str) -> dict:
    """Verify preset backup hashes, checkpoint and restore uniquely named existing fabrics."""
    return _local_call(lambda: _md_operation('restore_fabric_presets',
                                            manifest=_read_json(manifest_path),checkpoint_path=checkpoint_path))


@mcp.tool()
def inspect_zipper_style(style_index: int) -> dict:
    """Read an existing zipper style's documented settings; does not create or attach a zipper."""
    return _md_operation('inspect_zipper_style',style_index=style_index)


@mcp.tool()
def set_zipper_style(style_index: int, properties: dict, checkpoint_path: str) -> dict:
    """Checkpoint/edit existing zipper style settings with installed signatures and read-back.

    Properties: function_type 0–2, asset_type 0–6, teeth_type 0–1, positive teeth_width
    and tape_thickness in mm, positive weight in grams. Does not place accessories.
    """
    return _md_operation('set_zipper_style',style_index=style_index,properties=properties,checkpoint_path=checkpoint_path)


@mcp.tool()
def plan_sleeve_cap(armhole_edges: list[dict], cap_edges: list[dict],
                    ease_percent: float = 0.0, tolerance_percent: float = 3.0) -> dict:
    """Measure actual armhole/cap edges and calculate explicit sleeve ease and length correction.

    Endpoints are {pattern_index,line_index}; no mutation or automatic cap reshaping.
    """
    return _md_operation('plan_sleeve_cap',armhole_edges=armhole_edges,cap_edges=cap_edges,
                         ease_percent=ease_percent,tolerance_percent=tolerance_percent)


def _save_analysis(result,report_path):
    if report_path:
        result['report_file']=_write_json(report_path,result)
    return result


@mcp.tool()
def analyze_mesh_fit(garment_path: str, avatar_path: str, clearance: float = 2.0,
                      max_samples: int = 2000, report_path: str = '') -> dict:
    """Analyze sampled garment-to-avatar surface clearance and closed-mesh inside candidates locally.

    Requires triangulated OBJ meshes in identical coordinates/units. Open or
    ambiguous avatars yield unsigned distances. Sampling can miss triangle
    intersections; these are geometric diagnostics, not MD collision sensors.
    """
    return _local_call(lambda: _save_analysis(mesh_analysis.analyze_clearance(
        garment_path,avatar_path,clearance,max_samples),report_path))


@mcp.tool()
def analyze_mesh_deformation(rest_path: str, current_path: str,
                              stretch_limit_percent: float = 10.0, report_path: str = '') -> dict:
    """Measure geometric edge elongation against an explicit matching reference OBJ locally.

    Requires matching exported vertex order and triangle connectivity. Does not
    infer MD stress, pressure or material strain, and never certifies garment fit.
    """
    return _local_call(lambda: _save_analysis(mesh_analysis.analyze_deformation(
        rest_path,current_path,stretch_limit_percent),report_path))


@mcp.tool()
def draft_dart(points: list[list[float]], edge_index: int, intake: float, depth: float,
                position_ratio: float = 0.5) -> dict:
    """Draft a straight-polygon cut-out dart with equal legs and proposed seam indices locally.

    Does not mutate MD. Create a new pattern, inspect actual boundary edges, then
    sew verified legs. This is a cut-out dart, not an undocumented native dart command.
    """
    return _local_call(lambda: drafting.draft_dart(points,edge_index,intake,depth,position_ratio))


@mcp.tool()
def draft_seam_allowance(points: list[list[float]], width: float, miter_limit: float = 5.0) -> dict:
    """Generate a local straight-polygon cutting outline with explicit allowance and miter bounds."""
    return _local_call(lambda: drafting.seam_allowance(points,width,miter_limit))


@mcp.tool()
def draft_closure_layout(start: list[float], end: list[float], placket_width: float,
                          spacing: float = 60.0, end_margin: float = 15.0,
                          kind: Literal['buttons','zipper'] = 'buttons') -> dict:
    """Draft local placket geometry and button/zipper centerlines; no native accessory placement."""
    return _local_call(lambda: drafting.closure_layout(start,end,placket_width,spacing,end_margin,kind))


@mcp.tool()
def transform_native_pattern_json(path: str, output_path: str, pattern_ids: list[str],
                                   scale_x: float = 1.0, scale_y: float = 1.0,
                                   rotation_degrees: float = 0.0, translation_x: float = 0.0,
                                   translation_y: float = 0.0, pivot: list[float] | None = None) -> dict:
    """Edit exported native XY positions with explicit scaling, rotation and translation locally.

    Select IDs from this file. Refuses graded documents and preserves original
    files. Reimport separately with a checkpoint, reinspect sewing and rebind.
    This changes 2D draft geometry, not 3D avatar placement or native grading rules.
    """
    def transform():
        if Path(operations._path(path,'.json',must_exist=True)).resolve()==Path(operations._path(output_path,'.json')).resolve():
            raise ValueError('Choose a new output path; the source export must be preserved')
        result=drafting.transform_native_document(_read_json(path,max_bytes=10*1024*1024),pattern_ids,scale_x,scale_y,
            rotation_degrees,translation_x,translation_y,pivot)
        document=result.pop('document')
        result['output_file']=_write_json(output_path,document,max_bytes=10*1024*1024)
        return result
    return _local_call(transform)


@mcp.tool()
def compare_native_pattern_exports(before_path: str, after_path: str) -> dict:
    """Compare native pattern IDs/names/geometry in two exports without claiming global ID stability."""
    def compare():
        def index(path):
            document=_read_json(path,max_bytes=10*1024*1024)
            if not isinstance(document,dict) or not isinstance(document.get('PatternList'),list):
                raise ValueError('Supply native PatternList exports')
            records={}
            for piece in document['PatternList']:
                if not isinstance(piece,dict) or not isinstance(piece.get('ID'),str) or not piece['ID']:
                    raise ValueError('Native piece has no usable ID')
                if piece['ID'] in records:
                    raise ValueError('Duplicate native pattern ID')
                records[piece['ID']]=piece
            return records
        first,second=index(before_path),index(after_path)
        common=sorted(first.keys() & second.keys())
        records=[]
        for identity in common:
            a,b=first[identity],second[identity]
            records.append({'id':identity,'before_name':a.get('Name'),'after_name':b.get('Name'),
                            'native_geometry_equal':a.get('ShapeInfo')==b.get('ShapeInfo'),
                            'internal_geometry_equal':a.get('InternalLineList')==b.get('InternalLineList')})
        return {'ok':True,'same_ids':records,'removed_ids':sorted(first.keys()-second.keys()),
                'added_ids':sorted(second.keys()-first.keys()),'all_pattern_ids_retained':first.keys()==second.keys(),
                'native_id_persistence_certified':False,'scope':'observed identity in these two files only; aliases still require geometry checks'}
    return _local_call(compare)


@mcp.tool()
def build_bodice_block(bust_cm: float, length_cm: float, shoulder_width_cm: float,
                      neck_width_cm: float, armhole_depth_cm: float,
                      front_neck_depth_cm: float = 8.0, back_neck_depth_cm: float = 3.0,
                      ease_cm: float = 4.0, native_units_per_cm: float = 10.0,
                      vertical_direction: Literal['down','up'] = 'down') -> dict:
    """Draft local front/back bodice spline templates with explicit cm scale and Y direction.

    Returns vertices for create_curved_pattern, not sewn geometry or a fitted
    sloper. Inspect actual native edges before binding seams and avatar placement.
    """
    measurements=dict(bust_cm=bust_cm,length_cm=length_cm,shoulder_width_cm=shoulder_width_cm,
                      neck_width_cm=neck_width_cm,armhole_depth_cm=armhole_depth_cm,
                      front_neck_depth_cm=front_neck_depth_cm,back_neck_depth_cm=back_neck_depth_cm,ease_cm=ease_cm)
    return _local_call(lambda: recipes.garment_block('bodice',measurements,native_units_per_cm,vertical_direction))


@mcp.tool()
def build_sleeve_block(bicep_cm: float, cuff_cm: float, length_cm: float, cap_height_cm: float,
                      ease_cm: float = 4.0, native_units_per_cm: float = 10.0,
                      vertical_direction: Literal['down','up'] = 'down') -> dict:
    """Draft a local spline sleeve template; cap-to-armhole matching and fit remain explicit."""
    measurements=dict(bicep_cm=bicep_cm,cuff_cm=cuff_cm,length_cm=length_cm,cap_height_cm=cap_height_cm,ease_cm=ease_cm)
    return _local_call(lambda: recipes.garment_block('sleeve',measurements,native_units_per_cm,vertical_direction))


@mcp.tool()
def create_fit_closeups(report_path: str, output_dir: str, regions: list[dict], output_size: int = 1024) -> CallToolResult:
    """Crop caller-selected regions from saved fitting images into labeled MCP close-up images.

    Each region: {name, view_index, box:[left,top,right,bottom]} with normalized
    coordinates 0–1. Caller selects garment regions; no automatic collision sensing.
    """
    try:
        report_file=Path(operations._path(report_path,'.json',must_exist=True))
        report=_read_json(str(report_file))
        if not isinstance(report,dict) or not isinstance(report.get('preview'),dict):
            raise ValueError('Supply a saved fitting report with preview metadata')
        sources=report['preview'].get('files',[])
        if not isinstance(sources,list) or any(not isinstance(p,str) for p in sources):
            raise ValueError('Preview metadata must list image paths')
        sources=[Path(p) for p in sources if str(p).lower().endswith('.png')]
        if not sources:
            raise ValueError('The saved report has no preview PNG files')
        folder=Path(operations._path(output_dir))
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise ValueError('Close-ups require a new or empty output directory')
        operations._integer(output_size,'output_size',minimum=128,maximum=2048)
        if not isinstance(regions,list) or not 1<=len(regions)<=16:
            raise ValueError('Supply 1–16 close-up regions')
        planned=[]
        for region in regions:
            recipes.recipe_keys(region,('name','view_index','box'),('name','view_index','box'),'close-up region')
            name=region['name']
            if not isinstance(name,str) or not name.strip() or len(name)>128:
                raise ValueError('Region name must be 1–128 characters')
            index=operations._integer(region['view_index'],'view_index',maximum=len(sources)-1)
            box=region['box']
            if not isinstance(box,list) or len(box)!=4:
                raise ValueError('box must be [left,top,right,bottom]')
            box=[recipes.recipe_number(v,'box coordinate') for v in box]
            if not 0<=box[0]<box[2]<=1 or not 0<=box[1]<box[3]<=1:
                raise ValueError('Normalized crop bounds must be ordered within 0–1')
            source=sources[index].resolve()
            if not source.is_relative_to(report_file.parent.resolve()):
                raise ValueError('Preview file must be inside the report directory')
            if not source.is_file() or source.stat().st_size>8*1024*1024:
                raise ValueError('Source PNG is missing or exceeds 8 MiB')
            planned.append((name,source,box,index))
        folder.mkdir(parents=True,exist_ok=True)
        images=[]
        crops=[]
        for number,(name,source,box,index) in enumerate(planned):
            with Image.open(source) as original:
                if original.format!='PNG':
                    raise ValueError('Preview source is not a PNG')
                w,h=original.size
                pixel_box=(int(box[0]*w),int(box[1]*h),int(box[2]*w),int(box[3]*h))
                if pixel_box[0]>=pixel_box[2] or pixel_box[1]>=pixel_box[3]:
                    raise ValueError('Crop is smaller than one pixel')
                cropped=ImageOps.contain(original.crop(pixel_box).convert('RGB'),(output_size,output_size))
                path=folder/f'closeup_{number:03d}.png'
                cropped.save(path)
            crops.append({'name':name,'source':str(source),'view_index':index,'box':box,'path':str(path)})
            images.append(ImageContent(type='image',data=base64.b64encode(path.read_bytes()).decode(),mimeType='image/png'))
        outcome={'ok':True,'regions':crops,'fit_certified':False,'region_source':'caller selected'}
        _write_json(str(folder/'closeups.json'),outcome)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(outcome)),*images])
    except (OSError,ValueError,TypeError,KeyError,Image.DecompressionBombError) as exc:
        return _fit_error({'ok':False,'error':str(exc),'partial_output_possible':True})


@mcp.tool()
def set_pattern_layers(pattern_indices: list[int], layer: int) -> dict:
    """Set simulation layers from 0 through 20 with read-back checks and partial progress."""
    return _md_operation('set_pattern_layers', pattern_indices=pattern_indices, layer=layer)


@mcp.tool()
def set_pattern_constraints(pattern_indices: list[int], freeze: bool | None = None,
                            strengthen: bool | None = None, solidify: bool | None = None) -> dict:
    """Set freeze, strengthen or solidify; only solidify has documented state read-back."""
    return _md_operation('set_pattern_constraints', pattern_indices=pattern_indices,
                         freeze=freeze, strengthen=strengthen, solidify=solidify)


@mcp.tool()
def clone_pattern_layer(pattern_index: int, name: str, under: bool = True,
                        offset_x: float = 100.0, offset_y: float = 0.0) -> dict:
    """Create an over/under layer clone for lining and verify the added piece."""
    return _md_operation('clone_pattern_layer', pattern_index=pattern_index, name=name, under=under,
                         offset_x=offset_x, offset_y=offset_y)


def _registry(path, allow_new=False):
    filename = Path(operations._path(path, '.json'))
    registry = {'schema_version': 1, 'references': {}} if allow_new and not filename.exists() else _read_json(path)
    recipes.recipe_keys(registry, ('schema_version','references'), ('schema_version','references'), 'reference registry')
    if type(registry['schema_version']) is not int or registry['schema_version'] != 1 or not isinstance(registry['references'], dict):
        raise ValueError('Invalid reference registry')
    for key, reference in registry['references'].items():
        if not isinstance(reference, dict) or reference.get('ref_id') != key:
            raise ValueError('Registry ID does not match its reference')
    return registry


@mcp.tool()
def bind_pattern_reference(registry_path: str, ref_id: str, pattern_index: int, edge_names: dict[str, int]) -> dict:
    """Persist a piece reference and named boundary edges using name plus geometry signature.

    Rebinding is explicit. References reject missing, changed or ambiguous geometry;
    they are file-backed checks, not native MD UUIDs.
    """
    def bind():
        registry = _registry(registry_path, allow_new=True)
        outcome = _md_operation('bind_pattern_reference', ref_id=ref_id, pattern_index=pattern_index, edge_names=edge_names)
        if outcome.get('ok'):
            registry['references'][ref_id] = outcome['reference']
            outcome['registry'] = _write_json(registry_path, registry, overwrite=True)
        return outcome
    return _local_call(bind)


@mcp.tool()
def resolve_pattern_reference(registry_path: str, ref_id: str) -> dict:
    """Resolve a saved piece/edge reference to current indices, rejecting stale or ambiguous matches."""
    return _local_call(lambda: _md_operation('resolve_pattern_reference', reference=_registry(registry_path)['references'][ref_id]))


@mcp.tool()
def sew_named_edges(registry_path: str, ref_a: str, edge_a: str, ref_b: str, edge_b: str,
                    checkpoint_path: str, direction_a: bool = True, direction_b: bool = False) -> dict:
    """Resolve named edges together, checkpoint and sew validated endpoints."""
    def sew():
        refs = _registry(registry_path)['references']
        return _md_operation('sew_named_edges', reference_a=refs[ref_a], edge_a=edge_a,
                             reference_b=refs[ref_b], edge_b=edge_b, checkpoint_path=checkpoint_path,
                             direction_a=direction_a, direction_b=direction_b)
    return _local_call(sew)


@mcp.tool()
def measure_patterns(pattern_indices: list[int], targets: list[dict] | None = None) -> dict:
    """Measure 2D boundary lengths and compare explicit edge targets/tolerances in native units."""
    return _md_operation('measure_patterns', pattern_indices=pattern_indices, targets=targets)


def _fit_inputs(output_dir, observations, preview_count):
    folder = Path(operations._path(output_dir))
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError('Fitting output requires a new or empty directory')
    operations._integer(preview_count, 'preview_count', minimum=1, maximum=8)
    observations = [] if observations is None else observations
    if not isinstance(observations, list) or len(observations) > 50 or any(not isinstance(x,str) or len(x)>2000 for x in observations):
        raise ValueError('Provide at most 50 observation strings of 2000 characters each')
    return folder, observations


def _fit_measurements(pattern_indices, targets, seam_pairs):
    measured = measure_patterns(pattern_indices, targets)
    sewing = {'ok': True, 'skipped': True, 'scope': 'no explicit seam pairs supplied'} if seam_pairs is None or seam_pairs == [] else diagnose_sewing(seam_pairs)
    return {'ok': measured.get('ok', False) and sewing.get('ok', False), 'measurements': measured, 'sewing': sewing}


def _fit_geometry_inputs(capture_mesh,avatar_mesh_path,rest_mesh_path):
    if type(capture_mesh) is not bool:
        raise ValueError('capture_mesh must be boolean')
    if (avatar_mesh_path or rest_mesh_path) and not capture_mesh:
        raise ValueError('Avatar/rest diagnostics require capture_mesh=True')
    for path in (avatar_mesh_path,rest_mesh_path):
        if path:
            mesh_analysis.read_triangles(path)


def _fit_geometry_diagnostics(outcome,avatar_mesh_path,rest_mesh_path):
    snapshot=outcome.get('mesh_after',outcome.get('mesh',{}))
    path=snapshot.get('metrics',{}).get('path')
    if not path:
        return
    diagnostics={}
    for name,source,callback in (
        ('clearance',avatar_mesh_path,lambda:mesh_analysis.analyze_clearance(path,avatar_mesh_path)),
        ('edge_elongation',rest_mesh_path,lambda:mesh_analysis.analyze_deformation(rest_mesh_path,path))):
        if source:
            try:
                diagnostics[name]=callback()
            except (OSError,ValueError,TypeError) as exc:
                diagnostics[name]={'ok':False,'error':str(exc),'fit_certified':False}
    outcome['geometric_diagnostics']=diagnostics
    outcome['native_fit_sensors']=False


def _fit_result(outcome, folder, observations, preview_count):
    outcome.update(observations=observations, observations_source='caller supplied; not sensor measurements',
                   fit_certified=False, scope='images, mesh bounds and 2D edge measurements; inspect 3D fit, wrinkles and collisions visually')
    images = []
    if outcome.get('ok'):
        folder.mkdir(parents=True, exist_ok=True)
        preview = preview_garment(str(folder), image_count=preview_count, width=1024, height=1024)
        preview_status = json.loads(preview.content[0].text)
        outcome['preview'] = preview_status
        outcome['ok'] = preview_status.get('ok', False)
        images = [item for item in preview.content if isinstance(item, ImageContent)]
    if folder.is_dir():
        _attach_report(outcome, str(folder / 'fit-report.json'))
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(outcome, ensure_ascii=False)), *images],
                          isError=not outcome.get('ok', False))


def _fit_error(outcome):
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(outcome))], isError=True)


@mcp.tool()
def capture_fit_report(output_dir: str, pattern_indices: list[int], targets: list[dict] | None = None,
                       seam_pairs: list[dict] | None = None, observations: list[str] | None = None,
                       preview_count: int = 4, capture_mesh: bool = True,
                       avatar_mesh_path: str = '', rest_mesh_path: str = '') -> CallToolResult:
    """Return garment images, edge target comparisons, seam diagnostics and a saved fit report.

    Does not simulate. Observations are caller supplied; no automated body collision,
    pressure, strain, wrinkle or fit certification is inferred from measurements.
    Optional explicit avatar/rest OBJ files enable labeled geometric diagnostics.
    """
    try:
        folder, observations = _fit_inputs(output_dir, observations, preview_count)
        _fit_geometry_inputs(capture_mesh,avatar_mesh_path,rest_mesh_path)
        outcome = _fit_measurements(pattern_indices, targets, seam_pairs)
        if capture_mesh and outcome.get('ok'):
            mesh = capture_mesh_snapshot(str(folder/'mesh'/'garment.obj'))
            outcome['mesh'] = mesh
            outcome['ok'] = mesh.get('ok',False)
            _fit_geometry_diagnostics(outcome,avatar_mesh_path,rest_mesh_path)
        return _fit_result(outcome, folder, observations, preview_count)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return _fit_error({'ok': False, 'error': str(exc)})


def _save_manifest(outcome, path):
    if outcome.get('manifest'):
        saved = _local_call(lambda: _write_json(path, outcome['manifest']))
        outcome['manifest_file'] = saved
        if not saved['ok']:
            outcome.update(ok=False, manifest_error=saved['error'])
    return outcome


@mcp.tool()
def run_fitting_pass(output_dir: str, pattern_indices: list[int], simulation_steps: int = 1,
                     quality: int = 2, simulation_mode: int = 0, targets: list[dict] | None = None,
                     seam_pairs: list[dict] | None = None, observations: list[str] | None = None,
                     preview_count: int = 4, timeout: float = 300.0,
                     capture_mesh: bool = True, avatar_mesh_path: str = '',
                     rest_mesh_path: str = '') -> CallToolResult:
    """Checkpoint, set verified quality/mode, run one bounded simulation pass and return images/report.

    steps is the native Simulate(int) argument, limited to 1–200. Quality 0 normal,
    1 animation, 2 fitting; mode 0 CPU, 1 GPU. Leaves requested quality active.
    Inspect results before an explicit correction; timeout does not cancel MD.
    """
    outcome = {'ok': False}
    try:
        folder, observations = _fit_inputs(output_dir, observations, preview_count)
        _fit_geometry_inputs(capture_mesh,avatar_mesh_path,rest_mesh_path)
        preflight = _fit_measurements(pattern_indices, targets, seam_pairs)
        if not preflight['ok']:
            return _fit_error(preflight)
        before_mesh = capture_mesh_snapshot(str(folder/'mesh-before'/'garment.obj')) if capture_mesh else None
        if before_mesh is not None and not before_mesh.get('ok'):
            return _fit_error(before_mesh)
        outcome = _md_operation('prepare_fitting_pass', timeout=timeout, checkpoint_path=str(folder/'before.zprj'),
                                simulation_steps=simulation_steps, quality=quality, simulation_mode=simulation_mode)
        if outcome.get('checkpoint'):
            _save_manifest(outcome['checkpoint'], str(folder/'before.checkpoint.json'))
            if not outcome['checkpoint']['ok']:
                outcome['ok'] = False
        if outcome.get('ok'):
            outcome.update(_fit_measurements(pattern_indices, targets, seam_pairs))
            if capture_mesh:
                after_mesh = capture_mesh_snapshot(str(folder/'mesh-after'/'garment.obj'))
                outcome['mesh_before'],outcome['mesh_after']=before_mesh,after_mesh
                outcome['ok'] = outcome.get('ok',False) and after_mesh.get('ok',False)
                if after_mesh.get('ok'):
                    outcome['mesh_comparison']=geometry.compare_meshes(before_mesh['metrics']['path'],after_mesh['metrics']['path'])
                    _fit_geometry_diagnostics(outcome,avatar_mesh_path,rest_mesh_path)
        return _fit_result(outcome, folder, observations, preview_count)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        outcome.update(ok=False, error=str(exc))
        return _fit_error(outcome)


@mcp.tool()
def apply_fit_adjustments(registry_path: str, adjustments: list[dict], checkpoint_path: str,
                          max_move: float = 100.0) -> dict:
    """Checkpoint and apply explicit bounded move_2d, layer or resolution corrections to named pieces.

    Preflights all adjustments, stops on failure and saves updated references for
    verified completed changes. This does not resize geometry or infer body fit.
    """
    def apply():
        registry = _registry(registry_path)
        manifest_path = str(Path(operations._path(checkpoint_path, '.zprj')).with_suffix('.checkpoint.json'))
        if Path(manifest_path).exists():
            raise ValueError('Correction checkpoint manifest already exists')
        outcome = _md_operation('apply_fit_adjustments', references=registry['references'], adjustments=adjustments,
                                checkpoint_path=checkpoint_path, max_move=max_move)
        if outcome.get('checkpoint'):
            _save_manifest(outcome['checkpoint'], manifest_path)
            if not outcome['checkpoint']['ok']:
                outcome.update(ok=False, partial_change_possible=True)
        if outcome.get('updated_references') is not None:
            registry['references'] = outcome['updated_references']
            saved = _local_call(lambda: _write_json(registry_path, registry, overwrite=True))
            outcome['registry'] = saved
            if not saved['ok']:
                outcome.update(ok=False, registry_error=saved['error'], partial_change_possible=True)
        return outcome
    return _local_call(apply)


@mcp.tool()
def create_scene_checkpoint(path: str) -> dict:
    """Save a fresh .zprj plus .checkpoint.json manifest with SHA-256 and pattern count/names."""
    def create():
        manifest_path = str(Path(operations._path(path, '.zprj')).with_suffix('.checkpoint.json'))
        if Path(manifest_path).exists():
            raise ValueError('Checkpoint manifest already exists')
        return _save_manifest(_md_operation('create_scene_checkpoint', path=path), manifest_path)
    return _local_call(create)


@mcp.tool()
def restore_checkpoint(manifest_path: str, preserve_current_path: str) -> dict:
    """Verify a checkpoint hash, preserve the current scene, load and verify count/names.

    Requires a fresh preservation path. No automatic rollback/retry; successful
    loading and count/name verification do not certify every cloth or avatar detail.
    """
    def restore():
        backup_manifest = str(Path(operations._path(preserve_current_path, '.zprj')).with_suffix('.checkpoint.json'))
        if Path(backup_manifest).exists():
            raise ValueError('Preservation manifest already exists')
        outcome = _md_operation('restore_checkpoint', manifest=_read_json(manifest_path),
                                preserve_current_path=preserve_current_path)
        if outcome.get('preserved'):
            _save_manifest(outcome['preserved'], backup_manifest)
            if not outcome['preserved']['ok']:
                outcome.update(ok=False, partial_change_possible=True)
        return outcome
    return _local_call(restore)


@mcp.tool()
def get_operation_history(limit: int = 25) -> dict:
    """Read recent registered MD operation statuses/IDs/digests for this server process (up to 100)."""
    def history():
        operations._integer(limit, 'limit', minimum=1, maximum=100)
        with _history_lock:
            entries = [dict(entry) for entry in list(_history)[-limit:]]
        return {'ok': True, 'operations': entries,'journal_path':str(_journal_path),
                'scope': 'recent process entries; durable events saved separately; excludes legacy raw Python tools'}
    return _local_call(history)


@mcp.tool()
def save_operation_history(path: str, overwrite: bool = False) -> dict:
    """Persist the current process's operation journal to JSON; contains digests, not replayable code."""
    return _local_call(lambda: _write_json(path, get_operation_history(100), overwrite))


@mcp.tool()
def read_operation_journal(path: str, limit: int = 100) -> dict:
    """Read durable operation events locally; incomplete starts remain uncertain and are never replayed."""
    def read():
        source=Path(operations._path(path,must_exist=True))
        operations._integer(limit,'limit',minimum=1,maximum=1000)
        if source.stat().st_size>10*1024*1024:
            raise ValueError('Journal exceeds 10 MiB; archive older events')
        entries={}
        with source.open(encoding='utf-8') as file:
            for line in file:
                if len(line)>16384:
                    raise ValueError('Journal event exceeds 16 KiB')
                try:
                    event=json.loads(line)
                except json.JSONDecodeError:
                    # A torn final append is diagnostic evidence, not a replay instruction.
                    return {'ok':False,'error':'Journal contains an incomplete/invalid event',
                            'complete_operations':list(entries.values())[-limit:],'requires_state_inspection':True}
                if not isinstance(event,dict) or not isinstance(event.get('operation_id'),str):
                    raise ValueError('Invalid operation event')
                entries[event['operation_id']]=event
        result=list(entries.values())[-limit:]
        for entry in result:
            if entry.get('status')=='started':
                entry.update(status='uncertain',reason='No persisted completion record; inspect MD/checkpoints before further mutation')
        return {'ok':True,'journal_path':str(source),'operations':result,
                'requires_state_inspection':any(e.get('status')=='uncertain' for e in result),
                'automatic_replay':False}
    return _local_call(read)
