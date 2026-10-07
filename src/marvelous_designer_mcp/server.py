import base64
import json
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from . import bridge, operations
from .config import MD_HOST, MD_PORT

mcp = FastMCP("marvelous-designer")
_OPERATION_SOURCE = Path(operations.__file__).read_text(encoding="utf-8")


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


def _md_operation(operation: str, *, timeout: float | None = None, **params) -> dict:
    try:
        encoded = json.dumps(params, allow_nan=False, ensure_ascii=True)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    code = _OPERATION_SOURCE + f"\nresult = run_operation({operation!r}, json.loads({encoded!r}))\n"
    response = _md_exec(code, timeout=timeout)
    if response.get("ok") and isinstance(response.get("result"), dict):
        outcome = response["result"]
        for stream in ("stdout", "stderr"):
            if response.get(stream):
                outcome[stream] = response[stream]
        return outcome
    return response


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
def assign_fabric(fabric_index: int, pattern_index: int, face: int = 2) -> dict:
    """Assign a fabric to a pattern piece via fabric_api.AssignFabricToPattern(fabric, pattern, face).

    `face` is the MD third int argument (commonly 0=front, 1=back, 2=both); default 2.
    On a signature mismatch the raw TypeError is returned so the real meaning can be found.
    """
    code = (
        "import fabric_api\n"
        f"fi, pi, fc = {int(fabric_index)}, {int(pattern_index)}, {int(face)}\n"
        "try:\n"
        "    result = {'ok': bool(fabric_api.AssignFabricToPattern(fi, pi, fc))}\n"
        "except TypeError as e:\n"
        "    result = {'ok': False, 'signature_error': str(e)}\n"
    )
    return _md_exec(code)


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
    """List the functions of an MD API module (import_api, export_api, fabric_api, pattern_api,
    utility_api, ...). `contains` filters names by case-insensitive substring.

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
def assign_fabric_batch(fabric_index: int, pattern_indices: list[int], face: int = 2) -> dict:
    """Assign one fabric to multiple pieces with preflight bounds checks.

    face is the raw MD face code. Reports read-back pattern fabric indices and
    completed pieces on failure; face-specific visual appearance is not verified.
    """
    return _md_operation("assign_fabric_batch", fabric_index=fabric_index,
                         pattern_indices=pattern_indices, face=face)


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
