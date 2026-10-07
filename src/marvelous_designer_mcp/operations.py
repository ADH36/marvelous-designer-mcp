"""Self-contained operations executed in MD's interpreter via execute_python.

Only standard-library imports belong here: this source is also sent to MD.
API signatures were inspected on MD 2026.0.315. Missing APIs fail explicitly.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import time

# Preserve the last source paths across execute_python calls. Windows file-write
# timestamps can lag the precise clock; signatures detect cached outputs on reuse.
_md_mcp_turntable_sources = globals().get('_md_mcp_turntable_sources', [])


def _function(module, *names):
    mod = importlib.import_module(module)
    for name in names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError("Required MD API unavailable: " + module + "." + "/".join(names))


def _integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name + " is outside the permitted integer range")
    return value


def _number(value, name, minimum=0, inclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    if value < minimum or (not inclusive and value == minimum):
        raise ValueError(name + " is below the permitted minimum")
    return float(value)


def _path(value, extension=None, must_exist=False):
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        raise ValueError("An absolute path is required")
    value = os.path.abspath(value)
    if extension and os.path.splitext(value)[1].lower() != extension:
        raise ValueError("Expected a " + extension + " path")
    if must_exist and not os.path.isfile(value):
        raise ValueError("Input file does not exist: " + value)
    return value


def _snapshot(folder):
    existing = {}
    if os.path.isdir(folder):
        for root, _, files in os.walk(folder):
            for filename in files:
                path = os.path.join(root, filename)
                stat = os.stat(path)
                existing[os.path.normcase(path)] = (stat.st_mtime_ns, stat.st_size)
    return existing


def _prepare_file(path, overwrite=False, sidecars=False):
    folder = os.path.dirname(path)
    if not overwrite:
        if os.path.exists(path):
            raise ValueError("Output already exists; choose a new path or enable overwrite: " + path)
        # OBJ may create sidecars and textures, so reserve the entire directory.
        if sidecars and os.path.isdir(folder) and os.listdir(folder):
            raise ValueError("Mesh export requires an empty output directory unless overwrite is enabled")
    before = _snapshot(folder)
    os.makedirs(folder, exist_ok=True)
    return before


def _verify_files(returned, folder, before, required_extension=None):
    files = [returned] if isinstance(returned, str) and returned else returned
    if not isinstance(files, (list, tuple)) or not files:
        raise RuntimeError("MD returned no output files")
    verified = []
    root = os.path.normcase(os.path.abspath(folder))
    for filename in files:
        path = _path(filename)
        normalized = os.path.normcase(path)
        if os.path.commonpath([root, normalized]) != root:
            raise RuntimeError("MD returned a file outside the requested output directory")
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("MD did not create a nonempty output: " + path)
        stat = os.stat(path)
        if before.get(normalized) == (stat.st_mtime_ns, stat.st_size):
            raise RuntimeError("MD returned an unchanged existing file: " + path)
        verified.append(path)
    if len(set(os.path.normcase(p) for p in verified)) != len(verified):
        raise RuntimeError("MD returned duplicate output paths")
    if required_extension and not any(os.path.splitext(p)[1].lower() == required_extension for p in verified):
        raise RuntimeError("MD did not return the expected " + required_extension + " output")
    return {"ok": True, "files": verified}


def _pattern_indices(indices):
    if not isinstance(indices, list) or not indices:
        raise ValueError("At least one pattern index is required")
    count = _function("pattern_api", "GetPatternCount")()
    for index in indices:
        _integer(index, "pattern_index", maximum=count - 1)
    if len(set(indices)) != len(indices):
        raise ValueError("Pattern indices must be unique")
    return indices


def _lines(index):
    _pattern_indices([index])
    info = json.loads(_function("pattern_api", "GetPatternInputInformationW", "GetPatternInputInformation")(index))
    records = info.get("Pattern InputInformation", []) if isinstance(info, dict) else []
    # The indexed getter on MD 2026.0.315 reports a local "Pattern index": "0"
    # even for global index 1. The getter's selector, not that field, is authoritative.
    if len(records) != 1 or not isinstance(records[0], dict) or not isinstance(records[0].get("LineList"), list):
        raise RuntimeError("Unsupported MD pattern geometry schema; inspect md_api before sewing")
    expected_name = _function("pattern_api", "GetPatternPieceName")(index)
    if records[0].get('Pattern name', expected_name) != expected_name:
        raise RuntimeError("MD returned geometry for a different pattern")
    edges = [{"index": int(line["Line index"]), "length": float(line["Line length"]),
              "type": line.get("Line type", "")} for line in records[0]["LineList"] if "Line index" in line]
    if not edges:
        raise RuntimeError("Pattern has no inspectable boundary edges")
    return info, edges


def inspect_pattern(pattern_index):
    info, edges = _lines(pattern_index)
    return {"ok": True, "index": pattern_index,
            "name": _function("pattern_api", "GetPatternPieceName")(pattern_index),
            "fabric_index": _function("pattern_api", "GetPatternPieceFabricIndex")(pattern_index),
            "particle_distance": _function("pattern_api", "GetParticleDistanceOfPattern")(pattern_index),
            "mesh": _function("pattern_api", "GetMeshCountByType")(pattern_index),
            "edges": edges, "geometry": info, "units": "MD API native units"}


def rename_pattern(pattern_index, name):
    _pattern_indices([pattern_index])
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        raise ValueError("A nonempty pattern name is required")
    getter = _function("pattern_api", "GetPatternPieceName")
    previous = getter(pattern_index)
    _function("pattern_api", "SetPatternPieceName")(pattern_index, name)
    actual = getter(pattern_index)
    return {"ok": actual == name, "index": pattern_index, "previous_name": previous, "name": actual}


def mirror_pattern(pattern_index, with_sewing=False):
    _pattern_indices([pattern_index])
    fn = _function("pattern_api", "SymmetryPatternPiece")
    count_fn = _function("pattern_api", "GetPatternCount")
    name_fn = _function("pattern_api", "GetPatternPieceName")
    before = count_fn()
    fn(pattern_index, bool(with_sewing))
    after = count_fn()
    return {"ok": after > before, "before_count": before, "after_count": after,
            "patterns": [{"index": i, "name": name_fn(i)} for i in range(after)],
            "refresh_indices": True}


def select_patterns(pattern_indices, keep_previous=False):
    _pattern_indices(pattern_indices)
    fn = _function("pattern_api", "SelectPatternViaIndex")
    selected = _function("pattern_api", "GetSelectedPatternViaIndex")
    for position, index in enumerate(pattern_indices):
        fn(index, bool(keep_previous) or position > 0)
    return {"ok": all(selected(i) for i in pattern_indices),
            "selected_indices": [i for i in range(_function("pattern_api", "GetPatternCount")()) if selected(i)]}


def set_pattern_resolution(pattern_indices, particle_distance, mesh_type="Triangle"):
    _pattern_indices(pattern_indices)
    distance = _number(particle_distance, "particle_distance", minimum=0.8, inclusive=True)
    if mesh_type not in ("Triangle", "Quad"):
        raise ValueError("mesh_type must be Triangle or Quad")
    set_distance = _function("pattern_api", "SetParticleDistanceOfPattern")
    set_mesh = _function("pattern_api", "SetMeshType")
    get_distance = _function("pattern_api", "GetParticleDistanceOfPattern")
    get_mesh = _function("pattern_api", "GetMeshCountByType")
    completed = []
    for index in pattern_indices:
        try:
            set_distance(index, distance)
            set_mesh(index, mesh_type)
            actual = get_distance(index)
            mesh = get_mesh(index)
            if not math.isclose(actual, distance, rel_tol=1e-5) or mesh.get("Mesh Type") != mesh_type:
                raise RuntimeError("MD did not retain the requested resolution settings")
            completed.append({"index": index, "particle_distance": actual, "mesh": mesh})
        except Exception as exc:
            return {"ok": False, "error": str(exc), "completed": completed,
                    "failed_index": index, "partial_change_possible": True}
    return {"ok": True, "completed": completed, "units": "MD API native units"}


def list_seams():
    count = _function("pattern_api", "GetSeamlinePairGroupCount")()
    get_name = _function("pattern_api", "GetSeamlinePairGroupNameW", "GetSeamlinePairGroupName")
    return {"ok": True, "seams": [{"index": i, "name": get_name(i)} for i in range(count)]}


def sew_edges(pattern_a, line_a, pattern_b, line_b, direction_a=True, direction_b=False):
    _integer(line_a, "line_a")
    _integer(line_b, "line_b")
    _, edges_a = _lines(pattern_a)
    _, edges_b = _lines(pattern_b)
    if line_a not in {e["index"] for e in edges_a} or line_b not in {e["index"] for e in edges_b}:
        raise ValueError("Sewing edge index does not exist; inspect_pattern lists valid edges")
    if (pattern_a, line_a) == (pattern_b, line_b):
        raise ValueError("An edge cannot be sewn to itself")
    fn = _function("pattern_api", "AddSeamlinePairGroup")
    count = _function("pattern_api", "GetSeamlinePairGroupCount")
    before = count()
    success = bool(fn(pattern_a, line_a, pattern_b, line_b, bool(direction_a), bool(direction_b)))
    after = count()
    return {"ok": success, "before_count": before, "after_count": after,
            "endpoints": [[pattern_a, line_a], [pattern_b, line_b]],
            "directions": [bool(direction_a), bool(direction_b)]}


def assign_fabric_batch(fabric_index, pattern_indices, face=2):
    _pattern_indices(pattern_indices)
    _integer(face, "face", maximum=2)
    _integer(fabric_index, "fabric_index", maximum=_function("fabric_api", "GetFabricCount")(True) - 1)
    assign = _function("fabric_api", "AssignFabricToPattern")
    get_fabric = _function("pattern_api", "GetPatternPieceFabricIndex")
    completed = []
    for index in pattern_indices:
        try:
            if not assign(fabric_index, index, face):
                raise RuntimeError("MD rejected fabric assignment")
            completed.append({"index": index, "fabric_index": get_fabric(index)})
        except Exception as exc:
            return {"ok": False, "error": str(exc), "completed": completed,
                    "failed_index": index, "partial_change_possible": True}
    return {"ok": True, "completed": completed, "face": face}


def export_obj(path, scale=1.0, thin=True, single_object=False, include_avatar=False,
               unified_uv=True, overwrite=False):
    path = _path(path, ".obj")
    scale = _number(scale, "scale")
    fn = _function("export_api", "ExportOBJW", "ExportOBJ")
    option = _function("ApiTypes", "ImportExportOption")()
    values = {"bExportGarment": True, "bExportAvatar": bool(include_avatar), "bThin": bool(thin),
              "bSingleObject": bool(single_object), "bUnifiedUVCoordinates": bool(unified_uv),
              "bSaveInZip": False, "bSaveColorWays": False, "bIncludeHiddenObject": False, "scale": scale}
    for name, value in values.items():
        if not hasattr(option, name):
            raise RuntimeError("MD export option unavailable: " + name)
        setattr(option, name, value)
    before = _prepare_file(path, overwrite, sidecars=True)
    result = _verify_files(fn(path, option), os.path.dirname(path), before, ".obj")
    geometry = []
    for filename in result['files']:
        if os.path.splitext(filename)[1].lower() == '.obj':
            vertices = faces = 0
            with open(filename, encoding='utf-8', errors='replace') as mesh:
                for line in mesh:
                    line = line.lstrip()
                    vertices += line.startswith('v ')
                    faces += line.startswith('f ')
            if not vertices or not faces:
                raise RuntimeError('MD exported an OBJ with no polygonal geometry')
            geometry.append({'path': filename, 'vertices': vertices, 'faces': faces})
    result['geometry'] = geometry
    result["options"] = values
    return result


def export_turntable_images(path, image_count=4, width=1024, height=1024, start_index=0, overwrite=False):
    global _md_mcp_turntable_sources
    path = _path(path, ".png")
    _integer(image_count, "image_count", minimum=1, maximum=72)
    _integer(width, "width", minimum=64, maximum=8192)
    _integer(height, "height", minimum=64, maximum=8192)
    _integer(start_index, "start_index")
    fn = _function("export_api", "ExportTurntableImagesW", "ExportTurntableImages")
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    if not overwrite and os.path.isdir(folder) and any(n.startswith(stem) for n in os.listdir(folder)):
        raise ValueError("Turntable output prefix already exists; choose a new path or enable overwrite")
    before = _prepare_file(path, overwrite)
    returned = fn(path, image_count, width, height, start_index)
    used = "path-overload"
    if not returned:
        # On MD 2026.0.315 the path overload returned [] while the documented
        # count overload successfully wrote images into MD's temporary folder.
        fallback = _function("export_api", "ExportTurntableImages")
        previous = {}
        for source in _md_mcp_turntable_sources:
            if os.path.isfile(source):
                stat = os.stat(source)
                previous[os.path.normcase(source)] = (stat.st_mtime_ns, stat.st_size)
        started = time.time_ns()
        temporary = fallback(image_count)
        if not isinstance(temporary, (list, tuple)) or len(temporary) != image_count:
            raise RuntimeError("MD returned no complete turntable; check the 3D scene/view settings")
        copies = []
        for position, source in enumerate(temporary):
            source = _path(source, ".png", must_exist=True)
            stat = os.stat(source)
            if (stat.st_mtime_ns < started - 2_000_000_000 or
                    previous.get(os.path.normcase(source)) == (stat.st_mtime_ns, stat.st_size)):
                raise RuntimeError("MD returned a stale temporary turntable image")
            target = os.path.join(folder, stem + '_' + str(start_index + position).zfill(3) + '.png')
            if not overwrite and os.path.exists(target):
                raise ValueError("Turntable destination already exists: " + target)
            copies.append((source, target))
        for source, target in copies:
            shutil.copyfile(source, target)
        _md_mcp_turntable_sources = [source for source, _ in copies]
        returned = [target for _, target in copies]
        used = "temporary-output-overload"
    outcome = _verify_files(returned, folder, before, ".png")
    if len(outcome['files']) != image_count:
        return {**outcome, "ok": False, "error": "MD returned an incomplete turntable"}
    outcome['used'] = used
    return outcome


def export_custom_views(output_dir, width=1024, height=1024, prefix="view", overwrite=False):
    output_dir = _path(output_dir)
    if not isinstance(prefix, str) or not prefix or any(c in prefix for c in '/\\\x00') or prefix in (".", ".."):
        raise ValueError("prefix must be a nonempty filename prefix")
    _integer(width, "width", minimum=64, maximum=8192)
    _integer(height, "height", minimum=64, maximum=8192)
    fn = _function("export_api", "ExportCustomViewSnapshotW", "ExportCustomViewSnapshot")
    if not overwrite and os.path.isdir(output_dir) and any(n.startswith(prefix) for n in os.listdir(output_dir)):
        raise ValueError("Custom-view output prefix already exists; choose a new prefix or enable overwrite")
    before = _snapshot(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    return _verify_files(fn(output_dir, width, height, prefix), output_dir, before)


def save_checkpoint(path, overwrite=False):
    path = _path(path, ".zprj")
    fn = _function("export_api", "ExportZPrjW", "ExportZPrj")
    before = _prepare_file(path, overwrite)
    return _verify_files(fn(path, False), os.path.dirname(path), before, ".zprj")


def create_fabric_from_textures(path, base_texture, normal_texture="", displacement_texture="",
                              opacity_texture="", roughness_texture="", metalness_texture="", overwrite=False):
    path = _path(path, ".zfab")
    textures = [base_texture, normal_texture, displacement_texture, opacity_texture, roughness_texture, metalness_texture]
    textures = [_path(t, must_exist=True) if t else "" for t in textures]
    if not textures[0]:
        raise ValueError("base_texture is required")
    fn = _function("fabric_api", "CreateZfabFromTextures")
    before = _prepare_file(path, overwrite)
    if not fn(path, *textures):
        raise RuntimeError("MD rejected fabric preset creation")
    return _verify_files([path], os.path.dirname(path), before, ".zfab")


def garment_workflow(output_dir, avatar_path="", garment_path="", simulation_steps=0,
                     preview_count=4, scale=1.0, overwrite=False):
    """Append optional assets, then save/simulate/export with checkpoints."""
    output_dir = _path(output_dir)
    _integer(simulation_steps, "simulation_steps")
    _integer(preview_count, "preview_count", maximum=72)
    _number(scale, "scale")
    imports = []
    for path, extension, method in ((avatar_path, ".avt", "ImportAvatar"), (garment_path, ".zpac", "ImportZpac")):
        if path:
            path = _path(path, extension, must_exist=True)
            fn = _function("import_api", method)
            option = _function("ApiTypes", "ImportExportOption")()
            if not hasattr(option, "bAdd"):
                raise RuntimeError("MD append import option unavailable")
            option.bAdd = True
            imports.append((method, fn, path, option))
    if not overwrite and os.path.isdir(output_dir) and os.listdir(output_dir):
        raise ValueError("Workflow requires an empty output directory unless overwrite is enabled")
    _function("export_api", "ExportZPrjW", "ExportZPrj")
    _function("export_api", "ExportOBJW", "ExportOBJ")
    _function("ApiTypes", "ImportExportOption")
    if preview_count:
        _function("export_api", "ExportTurntableImagesW", "ExportTurntableImages")
    simulate_fn = _function("utility_api", "Simulate") if simulation_steps else None
    stages = []

    def stage(name, callback):
        try:
            outcome = callback()
        except Exception as exc:
            outcome = {"ok": False, "error": str(exc)}
        stages.append({"stage": name, **outcome})
        if not outcome.get("ok"):
            raise RuntimeError("Workflow stage failed: " + name)

    try:
        stage("checkpoint_before", lambda: save_checkpoint(os.path.join(output_dir, "before.zprj"), overwrite))
        for name, fn, path, option in imports:
            stage(name, lambda fn=fn, path=path, option=option: {"ok": bool(fn(path, option)), "path": path})
        if simulate_fn:
            stage("simulate", lambda: {"ok": bool(simulate_fn(simulation_steps)), "steps": simulation_steps})
        stage("save_project", lambda: save_checkpoint(os.path.join(output_dir, "garment.zprj"), overwrite))
        stage("export_obj", lambda: export_obj(os.path.join(output_dir, "mesh", "garment.obj"), scale=scale, overwrite=overwrite))
        if preview_count:
            stage("previews", lambda: export_turntable_images(os.path.join(output_dir, "previews", "garment.png"), image_count=preview_count, overwrite=overwrite))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stages": stages, "partial_change_possible": bool(imports) or bool(simulation_steps)}
    return {"ok": True, "stages": stages, "output_dir": output_dir}


_OPERATIONS = {fn.__name__: fn for fn in (
    inspect_pattern, rename_pattern, mirror_pattern, select_patterns, set_pattern_resolution,
    list_seams, sew_edges, assign_fabric_batch, export_obj, export_turntable_images,
    export_custom_views, save_checkpoint, create_fabric_from_textures, garment_workflow,
)}


def run_operation(operation, params):
    try:
        if operation not in _OPERATIONS:
            raise ValueError("Unknown operation: " + str(operation))
        return _OPERATIONS[operation](**params)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
