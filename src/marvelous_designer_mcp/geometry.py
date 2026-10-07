"""Mesh evidence and guarded placement, using already inspected MD APIs only."""
import hashlib
import json
import math
import os

if globals().get('__package__') == 'marvelous_designer_mcp':
    from .operations import _path, export_obj, inspect_pattern
    from .recipes import recipe_number
    from .advanced import (arrange_patterns, list_arrangements, create_scene_checkpoint,
                           export_pattern_json, _arrangement_inputs, _OPERATIONS)


def obj_metrics(path):
    path = _path(path, '.obj', must_exist=True)
    low, high, total = [float('inf')]*3, [float('-inf')]*3, [0.0]*3
    vertices = faces = 0
    topology = hashlib.sha256()
    positions = hashlib.sha256()
    with open(path, encoding='utf-8', errors='strict') as mesh:
        for line in mesh:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == 'v':
                if len(fields) < 4:
                    raise ValueError('Incomplete OBJ vertex')
                xyz = [float(value) for value in fields[1:4]]
                if not all(math.isfinite(value) for value in xyz):
                    raise ValueError('Nonfinite OBJ vertex')
                vertices += 1
                positions.update(json.dumps(xyz, separators=(',', ':')).encode())
                for axis, value in enumerate(xyz):
                    low[axis] = min(low[axis], value)
                    high[axis] = max(high[axis], value)
                    total[axis] += value
            elif fields[0] == 'f':
                faces += 1
                # Vertex connectivity only: changes in UV/normal numbering are irrelevant.
                topology.update((' '.join(value.split('/')[0] for value in fields[1:])+'\n').encode())
    if not vertices or not faces:
        raise ValueError('OBJ contains no polygonal mesh')
    return {'path':path, 'vertices':vertices, 'faces':faces,
            'bounds':{'min':low, 'max':high}, 'centroid':[v/vertices for v in total],
            'extent':[high[i]-low[i] for i in range(3)],
            'topology_sha256':topology.hexdigest(), 'positions_sha256':positions.hexdigest(),
            'units':'export scale 1; native MD units; verify destination calibration',
            'scope':'all visible exported garment pieces, excludes avatar; no collision/strain sensing'}


def compare_meshes(before_path, after_path, movement_threshold=0.1):
    threshold = recipe_number(movement_threshold, 'movement_threshold', True)
    before, after = obj_metrics(before_path), obj_metrics(after_path)
    compatible = (before['vertices']==after['vertices'] and before['faces']==after['faces']
                  and before['topology_sha256']==after['topology_sha256'])
    bounds_delta = {key:[after['bounds'][key][i]-before['bounds'][key][i] for i in range(3)]
                    for key in ('min', 'max')}
    max_displacement = rms = None
    if compatible:
        def positions(path):
            with open(path, encoding='utf-8') as mesh:
                for line in mesh:
                    fields = line.split()
                    if fields and fields[0]=='v':
                        yield [float(v) for v in fields[1:4]]
        maximum, squared, count = 0.0, 0.0, 0
        for first, second in zip(positions(before['path']), positions(after['path'])):
            distance_squared = sum((a-b)**2 for a,b in zip(first,second))
            maximum = max(maximum, distance_squared)
            squared += distance_squared
            count += 1
        max_displacement, rms = math.sqrt(maximum), math.sqrt(squared/count)
    return {'ok':True, 'before':before, 'after':after, 'comparable_topology':compatible,
            'bounds_delta':bounds_delta, 'movement_threshold':threshold,
            'max_vertex_displacement':max_displacement, 'rms_vertex_displacement':rms,
            'movement_detected':None if not compatible else max_displacement>threshold,
            'correspondence':'export vertex order plus connectivity; native vertex IDs are unavailable',
            'placement_certified':False, 'fit_certified':False}


def capture_mesh_snapshot(path):
    exported = export_obj(path, scale=1.0, thin=True, single_object=True, include_avatar=False)
    metrics = obj_metrics(path)
    return {'ok':True, 'export':exported, 'metrics':metrics}


def arrange_patterns_verified(pattern_indices, arrangement_index, output_dir,
                              shape_style='Flat', orientation=None, position=None,
                              movement_threshold=0.1, require_movement=True):
    folder = _path(output_dir)
    if os.path.exists(folder) and (not os.path.isdir(folder) or os.listdir(folder)):
        raise ValueError('Placement evidence requires a new or empty output directory')
    recipe_number(movement_threshold, 'movement_threshold', True)
    if type(require_movement) is not bool:
        raise ValueError('require_movement must be boolean')
    # Validate all setter inputs before checkpoint/export; reuse the same validation as the setter.
    _arrangement_inputs(pattern_indices, arrangement_index, shape_style, orientation, position)
    checkpoint = create_scene_checkpoint(os.path.join(folder, 'before.zprj'))
    if not checkpoint.get('ok'):
        return checkpoint
    with open(os.path.join(folder,'before.checkpoint.json'), 'x', encoding='utf-8') as file:
        json.dump(checkpoint['manifest'],file,indent=2)
    before = capture_mesh_snapshot(os.path.join(folder,'before','garment.obj'))
    mutation_started = False
    try:
        mutation_started = True
        applied = arrange_patterns(pattern_indices, arrangement_index, shape_style, orientation, position)
        if not applied.get('ok'):
            return {'ok':False, 'checkpoint':checkpoint, 'arrangement':applied, 'partial_change_possible':True}
        after = capture_mesh_snapshot(os.path.join(folder,'after','garment.obj'))
        comparison = compare_meshes(before['metrics']['path'],after['metrics']['path'],movement_threshold)
        verified = comparison['movement_detected'] is True
        result = {'ok':verified or not require_movement, 'checkpoint':checkpoint, 'arrangement':applied,
                  'mesh_comparison':comparison, 'movement_verified':verified, 'placement_certified':False,
                  'partial_change_possible':True,
                  'scope':'whole garment mesh movement only; intended body region/collisions still require visual review'}
        if require_movement and not verified:
            result['error']='Requested movement was not established. Do not retry blindly; inspect images and checkpoint.'
        with open(os.path.join(folder,'placement-report.json'),'x',encoding='utf-8') as file:
            json.dump(result,file,indent=2)
        return result
    except Exception as exc:
        return {'ok':False, 'error':str(exc), 'checkpoint':checkpoint,
                'partial_change_possible':mutation_started}


def arrange_patterns_by_name(pattern_indices, arrangement_name, output_dir,
                             shape_style='Flat', movement_threshold=0.1, require_movement=True):
    if not isinstance(arrangement_name,str) or not arrangement_name.strip():
        raise ValueError('An exact discovered arrangement name is required')
    candidates = [item['index'] for item in list_arrangements()['arrangements']
                  if any(isinstance(value,str) and value==arrangement_name
                         for value in item['properties'].values())]
    if len(candidates)!=1:
        raise ValueError('Arrangement name is missing or ambiguous; use list_arrangements')
    result = arrange_patterns_verified(pattern_indices,candidates[0],output_dir,shape_style,
                                      movement_threshold=movement_threshold,require_movement=require_movement)
    result['requested_arrangement_name']=arrangement_name
    return result


def inspect_native_pattern_geometry(pattern_index, export_path):
    inspected = inspect_pattern(pattern_index)
    exported = export_pattern_json(export_path)
    with open(export_path,encoding='utf-8') as file:
        document=json.load(file)
    matches = [piece for piece in document.get('PatternList',[]) if piece.get('Name')==inspected['name']]
    if len(matches)!=1:
        raise ValueError('Native export name is missing or ambiguous; use unique scene names')
    native=matches[0]
    lines=native.get('ShapeInfo',{}).get('LineList',[])
    return {'ok':True, 'inspection':inspected, 'export':exported, 'native_id':native.get('ID'),
            'native_geometry':native, 'unit':document.get('Unit'),
            'api_edge_map':inspected['edges'], 'native_boundary_line_count':len(lines),
            'edge_order_equivalence_verified':False, 'native_id_persistence_verified':False,
            'scope':'inspect native control points and API edges; do not infer API indices from input vertices or native IDs'}


_OPERATIONS.update({fn.__name__:fn for fn in (
    capture_mesh_snapshot, arrange_patterns_verified, arrange_patterns_by_name, inspect_native_pattern_geometry,
)})
