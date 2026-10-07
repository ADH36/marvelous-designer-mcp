"""Complex garment controls, supplied to MD as standard-library-only source."""
import copy
import hashlib
import json
import math
import os

if globals().get('__package__') == 'marvelous_designer_mcp':
    from .operations import (_function, _integer, _number, _path, _prepare_file, _verify_files,
                             _pattern_indices, _lines, rename_pattern, save_checkpoint,
                             set_pattern_resolution, sew_edges, assign_fabric_batch, _OPERATIONS, _batch_load_option)
    from .recipes import recipe_number, recipe_polygon, recipe_keys


def advanced_vertices(vertices, closed=True):
    if not isinstance(vertices, list) or not (3 if closed else 2) <= len(vertices) <= 1024:
        raise ValueError('Provide 3–1024 closed or 2–1024 open [x,y,type] vertices')
    result = []
    for vertex in vertices:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            raise ValueError('Vertices must be [x,y,type]')
        x, y, kind = vertex
        if type(kind) is not int or kind not in (0,2,3):
            raise ValueError('Vertex type must be 0=straight, 2=spline or 3=Bezier')
        result.append((recipe_number(x,'x'), recipe_number(y,'y'), kind))
    if len({v[:2] for v in result}) != len(result):
        raise ValueError('Vertex coordinates must be unique; omit a repeated closing vertex')
    if closed:
        recipe_polygon([list(v[:2]) for v in result])
    return result


def _geometry_hash(index):
    info, _ = _lines(index)
    record = info['Pattern InputInformation'][0]
    # Do not bind to scene ordinals, material assignments or mesh resolution.
    geometry = {key:value for key,value in record.items()
                if key == 'LineList' or 'point' in key.lower() or 'vertex' in key.lower()}
    return hashlib.sha256(json.dumps(geometry,sort_keys=True,allow_nan=False).encode()).hexdigest()


def _new_piece(name, callback):
    if not isinstance(name,str) or not name.strip() or '\x00' in name:
        raise ValueError('A nonempty pattern name is required')
    count = _function('pattern_api','GetPatternCount')
    get_name = _function('pattern_api','GetPatternPieceName')
    _function('pattern_api','SetPatternPieceName')
    before = count()
    names = [get_name(i) for i in range(before)]
    try:
        returned = callback()
        after = count()
        if after != before+1 or [get_name(i) for i in range(before)] != names:
            raise RuntimeError('Creation changed unexpected pattern indices; inspect the scene')
        if type(returned) is int and returned != before:
            raise RuntimeError('MD returned an unexpected new pattern index')
        result = rename_pattern(before,name)
        if not result['ok']:
            raise RuntimeError('New piece name was not retained')
        return {'ok':True,'index':before,'name':name,'before_count':before,'after_count':after,'refresh_indices':True}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'partial_change_possible':True,'before_count':before,'after_count':count()}


def create_curved_pattern(vertices, name):
    points = advanced_vertices(vertices)
    fn = _function('pattern_api','CreatePatternWithPoints')
    result = _new_piece(name,lambda:fn(points))
    result['vertex_types'] = [p[2] for p in points]
    result['verification'] = 'count/name/API index; actual curve shape needs visual inspection'
    return result


def create_internal_shape(pattern_index, vertices, closed=False):
    _pattern_indices([pattern_index])
    if type(closed) is not bool:
        raise ValueError('closed must be boolean')
    points = advanced_vertices(vertices,closed)
    fn = _function('pattern_api','CreateInternalShapeWithPoints')
    try:
        index = fn(pattern_index,points,closed)
        if type(index) is not int or index < 0:
            raise RuntimeError('MD did not return a valid internal-shape index')
        return {'ok':True,'pattern_index':pattern_index,'internal_shape_index':index,
                'verification':'MD returned index; inspect internal geometry before sewing'}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'partial_change_possible':True}


def sew_internal_edges(pattern_a,line_a,pattern_b,line_b,child_a=None,child_b=None,direction_a=True,direction_b=False):
    if type(direction_a) is not bool or type(direction_b) is not bool:
        raise ValueError('Sewing directions must be boolean')
    _pattern_indices([pattern_a] if pattern_a==pattern_b else [pattern_a,pattern_b])
    if child_a is None and child_b is None:
        raise ValueError('At least one endpoint must reference an internal shape')
    for pattern,child,line in ((pattern_a,child_a,line_a),(pattern_b,child_b,line_b)):
        _integer(line,'line index')
        if child is None:
            _,edges = _lines(pattern)
            if line not in {e['index'] for e in edges}:
                raise ValueError('Boundary edge does not exist')
        else:
            _integer(child,'child index')
            length = _function('pattern_api','GetLineLength')(pattern,child,line)
            if not math.isfinite(length) or length<=0:
                raise ValueError('MD did not report a valid internal-edge length')
    if (pattern_a,child_a,line_a)==(pattern_b,child_b,line_b):
        raise ValueError('Cannot sew an edge to itself')
    fn = _function('pattern_api','AddSeamlinePairGroup')
    if child_a is None:
        args=(pattern_a,line_a,pattern_b,child_b,line_b,direction_a,direction_b)
    elif child_b is None:
        args=(pattern_b,line_b,pattern_a,child_a,line_a,direction_b,direction_a)
    else:
        args=(pattern_a,child_a,line_a,pattern_b,child_b,line_b,direction_a,direction_b)
    try:
        success=bool(fn(*args))
        return {'ok':success,'endpoints':[[pattern_a,child_a,line_a],[pattern_b,child_b,line_b]],
                'partial_change_possible':not success,
                'verification':'boundary bounds and API internal lengths; internal schema is not fully decoded'}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'partial_change_possible':True}


def move_pattern_2d(pattern_index,x,y):
    _pattern_indices([pattern_index])
    x,y=recipe_number(x,'x'),recipe_number(y,'y')
    fn=_function('pattern_api','SetPatternPiecePos')
    getter=_function('pattern_api','GetPatternPiecePos')
    previous=list(getter(pattern_index))
    fn(pattern_index,x,y)
    actual=list(getter(pattern_index))
    return {'ok':len(actual)==2 and math.isclose(actual[0],x,abs_tol=0.01) and math.isclose(actual[1],y,abs_tol=0.01),
            'index':pattern_index,'previous':previous,'position':actual,'scope':'2D editor position, not avatar placement'}


def copy_pattern(pattern_index,name,offset_x=100.0,offset_y=0.0):
    _pattern_indices([pattern_index])
    x,y=recipe_number(offset_x,'offset_x'),recipe_number(offset_y,'offset_y')
    fn=_function('pattern_api','CopyPatternPieceMove')
    return _new_piece(name,lambda:fn(pattern_index,x,y))


def export_pattern_json(path,overwrite=False):
    path=_path(path,'.json')
    fn=_function('pattern_api','ExportPatternJSON')
    before=_prepare_file(path,overwrite)
    if not fn(path):
        raise RuntimeError('MD rejected native pattern JSON export')
    result=_verify_files([path],os.path.dirname(path),before,'.json')
    with open(path,encoding='utf-8') as file:
        json.load(file)
    result['format']='MD-native pattern JSON; distinct from garment recipe JSON'
    return result


def import_pattern_json(path,checkpoint_path,preserve_settings=True):
    path=_path(path,'.json',must_exist=True)
    if os.path.getsize(path)>10*1024*1024:
        raise ValueError('Native pattern JSON exceeds 10 MiB')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('Duplicate native JSON key: '+key)
            result[key]=value
        return result
    def nonfinite(value):
        raise ValueError('Nonfinite native JSON constant: '+value)
    with open(path,encoding='utf-8') as file:
        document=json.load(file,object_pairs_hook=unique,parse_constant=nonfinite)
    if not isinstance(document,(dict,list)) or not document:
        raise ValueError('Native pattern JSON must contain structured geometry')
    if isinstance(document,dict) and ('code' in document or ('schema_version' in document and 'pieces' in document)):
        raise ValueError('Supply an MD-native export, not a recipe or executable code')
    fn=_function('pattern_api','ImportPatternJSON')
    _function('pattern_api','GetPatternCount')
    if type(preserve_settings) is not bool:
        raise ValueError('preserve_settings must be boolean')
    settings = _capture_import_settings(document) if preserve_settings else []
    settings_path=_path(checkpoint_path,'.zprj')+'.settings.json'
    if preserve_settings and os.path.exists(settings_path):
        raise ValueError('Settings recovery file already exists; choose a fresh checkpoint path')
    checkpoint=save_checkpoint(checkpoint_path)
    try:
        if preserve_settings:
            with open(settings_path,'x',encoding='utf-8') as file:
                json.dump({'settings':settings,'scope':'known properties only'},file,indent=2)
        success=bool(fn(path))
        if not success:
            raise RuntimeError('MD rejected native JSON import; scene may have changed')
        restored = _restore_import_settings(settings) if preserve_settings else []
        return {'ok':success,'checkpoint':checkpoint,'pattern_count':_function('pattern_api','GetPatternCount')(),
                'partial_change_possible':not success,
                'refresh_indices':True,'restored_settings':restored,'preserve_settings':preserve_settings,
                'settings_recovery_path':settings_path if preserve_settings else None,
                'full_state_preserved':False,
                'unverified_state':['physical fabric parameters','colorways','freeze','strengthen','simulation cache'],
                'verification':'known settings reapplied/read back by unique names; inspect geometry and rebind references'}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'partial_change_possible':True,
                'settings_recovery_path':settings_path if preserve_settings else None}


def _capture_import_settings(document):
    # Native IDs are observable, but their persistence is not established. Require
    # an exact set of unique names rather than guessing index correspondence.
    count=_function('pattern_api','GetPatternCount')()
    name=_function('pattern_api','GetPatternPieceName')
    names=[name(i) for i in range(count)]
    pieces=document.get('PatternList') if isinstance(document,dict) else None
    if not isinstance(pieces,list) or any(not isinstance(p,dict) for p in pieces):
        raise ValueError('Settings preservation requires an MD PatternList export')
    incoming=[p.get('Name') for p in pieces]
    if (any(not isinstance(n,str) or not n for n in names+incoming)
            or len(set(names))!=len(names) or len(set(incoming))!=len(incoming)
            or set(names)!=set(incoming)):
        raise ValueError('Settings preservation requires exactly the current unique pattern names; use preserve_settings=False for replacement')
    fabric_name=_function('fabric_api','GetFabricName')
    states=[]
    for i,n in enumerate(names):
        mesh=_function('pattern_api','GetMeshCountByType')(i)
        mesh_type=mesh.get('Mesh Type')
        if mesh_type not in ('Triangle','Quad'):
            raise ValueError('Unrecognized mesh type; settings cannot be preserved safely')
        states.append({'name':n,'particle_distance':_function('pattern_api','GetParticleDistanceOfPattern')(i),
                       'mesh_type':mesh_type,'layer':_function('pattern_api','GetPatternLayer')(i),
                       'solidify':bool(_function('pattern_api','IsPatternPieceSolidify')(i)),
                       'fabric_name':fabric_name(_function('pattern_api','GetPatternPieceFabricIndex')(i))})
    for fn in ('SetParticleDistanceOfPattern','SetMeshType','SetPatternLayer','SetPatternPieceSolidify'):
        _function('pattern_api',fn)
    _function('fabric_api','GetFabricCount')
    _function('fabric_api','AssignFabricToPattern')
    return states


def _restore_import_settings(states):
    from collections import defaultdict
    patterns, fabrics=defaultdict(list),defaultdict(list)
    for i in range(_function('pattern_api','GetPatternCount')()):
        patterns[_function('pattern_api','GetPatternPieceName')(i)].append(i)
    for i in range(_function('fabric_api','GetFabricCount')(False)):
        fabrics[_function('fabric_api','GetFabricName')(i)].append(i)
    planned=[]
    if len(states)!=sum(len(v) for v in patterns.values()):
        raise RuntimeError('Imported pattern count changed; use the checkpoint to recover')
    for state in states:
        if len(patterns[state['name']])!=1 or len(fabrics[state['fabric_name']])!=1:
            raise RuntimeError('Pattern/fabric name mapping became missing or ambiguous; recover the checkpoint')
        planned.append((state,patterns[state['name']][0],fabrics[state['fabric_name']][0]))
    completed=[]
    for state,index,fabric_index in planned:
        actions=(lambda:set_pattern_resolution([index],state['particle_distance'],state['mesh_type']),
                 lambda:set_pattern_layers([index],state['layer']),
                 lambda:set_pattern_constraints([index],solidify=state['solidify']),
                 lambda:assign_fabric_batch(fabric_index,[index],assignment_mode=1))
        for action in actions:
            if not action().get('ok'):
                raise RuntimeError('Imported settings read-back failed; recover the checkpoint')
        completed.append({'index':index,**state,'fabric_index':fabric_index,'readback_verified':True})
    return completed


def list_arrangements():
    items=_function('pattern_api','GetArrangementList')()
    if not isinstance(items,(list,tuple)):
        raise RuntimeError('Unsupported arrangement-list response')
    return {'ok':True,'arrangements':[{'index':i,'properties':dict(item)} for i,item in enumerate(items)]}


def inspect_arrangement(pattern_index):
    _pattern_indices([pattern_index])
    return {'ok':True,'pattern_index':pattern_index,
            'properties':dict(_function('pattern_api','GetArrangementOfPattern')(pattern_index))}


def _arrangement_inputs(pattern_indices,arrangement_index,shape_style,orientation,position):
    _pattern_indices(pattern_indices)
    arrangements=list_arrangements()['arrangements']
    _integer(arrangement_index,'arrangement_index',maximum=len(arrangements)-1)
    if shape_style not in ('Flat','Curved'):
        raise ValueError('shape_style must be Flat or Curved')
    set_arr=_function('pattern_api','SetArrangement')
    set_shape=_function('pattern_api','SetArrangementShapeStyleW','SetArrangementShapeStyle')
    _function('pattern_api','GetArrangementOfPattern')
    orient_fn=_function('pattern_api','SetArrangementOrientation') if orientation is not None else None
    if orientation is not None:
        _integer(orientation,'orientation')
    pos_fn=_function('pattern_api','SetArrangementPosition') if position is not None else None
    if position is not None and (not isinstance(position,list) or len(position)!=3 or any(type(v) is not int for v in position)):
        raise ValueError('Arrangement position must be three integer API codes/offsets')
    return arrangements,set_arr,set_shape,orient_fn,pos_fn


def arrange_patterns(pattern_indices,arrangement_index,shape_style='Flat',orientation=None,position=None):
    arrangements,set_arr,set_shape,orient_fn,pos_fn=_arrangement_inputs(
        pattern_indices,arrangement_index,shape_style,orientation,position)
    completed=[]
    for index in pattern_indices:
        try:
            set_arr(index,arrangement_index)
            set_shape(index,shape_style)
            if orient_fn:
                orient_fn(index,orientation)
            if pos_fn:
                pos_fn(index,*position)
            completed.append(inspect_arrangement(index))
        except Exception as exc:
            return {'ok':False,'error':str(exc),'completed':completed,'failed_index':index,'partial_change_possible':True}
    return {'ok':True,'completed':completed,'requested_arrangement':arrangements[arrangement_index],
            'placement_verified':False,'movement_verified':False,
            'verification':'property read-back only; use arrange_patterns_verified for actual mesh movement evidence'}


def get_pattern_layer(pattern_index):
    _pattern_indices([pattern_index])
    return {'ok':True,'index':pattern_index,'layer':_function('pattern_api','GetPatternLayer')(pattern_index)}


def set_pattern_layers(pattern_indices,layer):
    _pattern_indices(pattern_indices)
    _integer(layer,'layer',maximum=20)
    setter=_function('pattern_api','SetPatternLayer')
    getter=_function('pattern_api','GetPatternLayer')
    completed=[]
    for index in pattern_indices:
        try:
            setter(index,layer)
            actual=getter(index)
            if actual!=layer:
                raise RuntimeError('MD did not retain the requested layer')
            completed.append({'index':index,'layer':actual})
        except Exception as exc:
            return {'ok':False,'error':str(exc),'completed':completed,'failed_index':index,'partial_change_possible':True}
    return {'ok':True,'completed':completed}


def set_pattern_constraints(pattern_indices,freeze=None,strengthen=None,solidify=None):
    _pattern_indices(pattern_indices)
    requested={k:v for k,v in {'freeze':freeze,'strengthen':strengthen,'solidify':solidify}.items() if v is not None}
    if not requested or any(type(v) is not bool for v in requested.values()):
        raise ValueError('Provide at least one boolean freeze/strengthen/solidify setting')
    names={'freeze':'SetPatternFreeze','strengthen':'SetPatternStrengthen','solidify':'SetPatternPieceSolidify'}
    setters={k:_function('pattern_api',names[k]) for k in requested}
    getter=_function('pattern_api','IsPatternPieceSolidify') if solidify is not None else None
    completed=[]
    for index in pattern_indices:
        try:
            for key,value in requested.items():
                setters[key](index,value)
            readback={'solidify':bool(getter(index))} if getter else {}
            if getter and readback['solidify']!=solidify:
                raise RuntimeError('Solidify read-back did not match')
            completed.append({'index':index,'requested':requested,'readback':readback})
        except Exception as exc:
            return {'ok':False,'error':str(exc),'completed':completed,'failed_index':index,'partial_change_possible':True}
    return {'ok':True,'completed':completed,'unverified_settings':[k for k in requested if k!='solidify']}


def clone_pattern_layer(pattern_index,name,under=True,offset_x=100.0,offset_y=0.0):
    _pattern_indices([pattern_index])
    if type(under) is not bool:
        raise ValueError('under must be boolean')
    x,y=recipe_number(offset_x,'offset_x'),recipe_number(offset_y,'offset_y')
    fn=_function('pattern_api','LayerClonePatternPieceMove')
    result=_new_piece(name,lambda:fn(pattern_index,x,y,under))
    result['under']=under
    return result


def bind_pattern_reference(ref_id,pattern_index,edge_names):
    if not isinstance(ref_id,str) or not ref_id.strip() or len(ref_id)>128 or '\x00' in ref_id:
        raise ValueError('Reference id must be a nonempty string of at most 128 characters')
    _pattern_indices([pattern_index])
    if not isinstance(edge_names,dict):
        raise ValueError('edge_names must map names to boundary indices')
    _,edges=_lines(pattern_index)
    valid={e['index']:e for e in edges}
    if len(set(edge_names.values()))!=len(edge_names):
        raise ValueError('Each edge alias must identify a different edge')
    aliases={}
    for name,index in edge_names.items():
        if not isinstance(name,str) or not name.strip() or len(name)>128 or '\x00' in name or type(index) is not int or index not in valid:
            raise ValueError('Invalid named boundary edge')
        aliases[name]=valid[index]
    return {'ok':True,'reference':{'schema_version':1,'ref_id':ref_id,
            'pattern_name':_function('pattern_api','GetPatternPieceName')(pattern_index),
            'geometry_hash':_geometry_hash(pattern_index),'edges':aliases}}


def resolve_pattern_reference(reference):
    recipe_keys(reference,('schema_version','ref_id','pattern_name','geometry_hash','edges'),
                ('schema_version','ref_id','pattern_name','geometry_hash','edges'),'pattern reference')
    if type(reference['schema_version']) is not int or reference['schema_version']!=1:
        raise ValueError('Unknown reference schema')
    if not all(isinstance(reference[k],str) and reference[k] for k in ('ref_id','pattern_name','geometry_hash')):
        raise ValueError('Invalid reference identity')
    if not isinstance(reference['edges'],dict):
        raise ValueError('Invalid reference edge map')
    candidates=[]
    count=_function('pattern_api','GetPatternCount')()
    getter=_function('pattern_api','GetPatternPieceName')
    for index in range(count):
        if getter(index)==reference['pattern_name'] and _geometry_hash(index)==reference['geometry_hash']:
            candidates.append(index)
    if len(candidates)!=1:
        raise ValueError('Reference is missing, stale or ambiguous; inspect and explicitly rebind')
    _,edges=_lines(candidates[0])
    actual={e['index']:e for e in edges}
    for alias,edge in reference['edges'].items():
        if not isinstance(alias,str) or not isinstance(edge,dict) or edge.get('index') not in actual or edge!=actual[edge['index']]:
            raise ValueError('Named edge no longer matches the bound geometry')
    return {'ok':True,'ref_id':reference['ref_id'],'pattern_index':candidates[0],
            'edges':reference['edges'],'scope':'name plus geometry signature; not a native MD UUID'}


def sew_named_edges(reference_a,edge_a,reference_b,edge_b,direction_a,direction_b,checkpoint_path):
    if type(direction_a) is not bool or type(direction_b) is not bool:
        raise ValueError('Sewing directions must be boolean')
    first,second=resolve_pattern_reference(reference_a),resolve_pattern_reference(reference_b)
    if edge_a not in first['edges'] or edge_b not in second['edges']:
        raise ValueError('Unknown named edge')
    endpoints=(first['pattern_index'],first['edges'][edge_a]['index'],second['pattern_index'],second['edges'][edge_b]['index'])
    if endpoints[:2]==endpoints[2:]:
        raise ValueError('Cannot sew an edge to itself')
    _function('pattern_api','AddSeamlinePairGroup')
    checkpoint=save_checkpoint(checkpoint_path)
    try:
        result=sew_edges(*endpoints,direction_a,direction_b)
        result['checkpoint']=checkpoint
        if not result['ok']:
            result['partial_change_possible']=True
        return result
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'partial_change_possible':True}


def measure_patterns(pattern_indices,targets=None):
    _pattern_indices(pattern_indices)
    targets=[] if targets is None else targets
    if not isinstance(targets,list) or len(targets)>500:
        raise ValueError('targets must be a list of at most 500 edge measurements')
    patterns=[]
    lookup={}
    for index in pattern_indices:
        _,edges=_lines(index)
        lookup[index]={e['index']:e['length'] for e in edges}
        patterns.append({'index':index,'name':_function('pattern_api','GetPatternPieceName')(index),'edges':edges})
    comparisons=[]
    for target in targets:
        recipe_keys(target,('pattern_index','line_index','target_length','tolerance'),
                    ('pattern_index','line_index','target_length','tolerance'),'measurement target')
        index,line=target['pattern_index'],target['line_index']
        _integer(index,'pattern_index')
        _integer(line,'line_index')
        if index not in lookup or line not in lookup[index]:
            raise ValueError('Measurement target is outside the requested pattern edges')
        expected=_number(target['target_length'],'target_length')
        tolerance=_number(target['tolerance'],'tolerance',inclusive=True)
        length=lookup[index][line]
        if not math.isfinite(length) or length<=0:
            raise RuntimeError('Invalid MD edge measurement')
        comparisons.append({**target,'actual_length':length,'difference':length-expected,'passes':abs(length-expected)<=tolerance})
    return {'ok':True,'patterns':patterns,'targets':comparisons,'passes':all(c['passes'] for c in comparisons),
            'units':'MD native units','scope':'2D boundary lengths; not 3D body circumference or penetration'}


def _file_hash(path):
    value=hashlib.sha256()
    with open(path,'rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def _scene_signature():
    count=_function('pattern_api','GetPatternCount')()
    name=_function('pattern_api','GetPatternPieceName')
    return {'pattern_count':count,'pattern_names':[name(i) for i in range(count)]}


def create_scene_checkpoint(path):
    signature=_scene_signature()
    saved=save_checkpoint(path)
    return {'ok':True,'files':saved['files'],'manifest':{'schema_version':1,'path':_path(path,'.zprj'),
            'sha256':_file_hash(path),**signature}}


def restore_checkpoint(manifest,preserve_current_path):
    recipe_keys(manifest,('schema_version','path','sha256','pattern_count','pattern_names'),
                ('schema_version','path','sha256','pattern_count','pattern_names'),'checkpoint manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version']!=1:
        raise ValueError('Unsupported checkpoint manifest')
    _integer(manifest['pattern_count'],'pattern_count')
    if not isinstance(manifest['pattern_names'],list) or len(manifest['pattern_names'])!=manifest['pattern_count'] or any(not isinstance(n,str) for n in manifest['pattern_names']):
        raise ValueError('Invalid checkpoint scene signature')
    path=_path(manifest['path'],'.zprj',must_exist=True)
    backup=_path(preserve_current_path,'.zprj')
    if os.path.normcase(path)==os.path.normcase(backup):
        raise ValueError('Preservation path must differ from the checkpoint being restored')
    if _file_hash(path)!=manifest['sha256']:
        raise ValueError('Checkpoint file changed since manifest creation; restore refused')
    load=_function('import_api','ImportZprjW','ImportZprj')
    option=_batch_load_option()
    preserved=create_scene_checkpoint(backup)
    try:
        if not load(path,option):
            raise RuntimeError('MD rejected checkpoint load')
        actual=_scene_signature()
        expected={k:manifest[k] for k in ('pattern_count','pattern_names')}
        return {'ok':actual==expected,'preserved':preserved,'scene':actual,'expected':expected,
                'partial_change_possible':actual!=expected,
                'verification':'file hash, MD load status, pattern count/names; fit is not certified','refresh_references':True}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'preserved':preserved,'partial_change_possible':True}


def prepare_fitting_pass(checkpoint_path,simulation_steps,quality=2,simulation_mode=0):
    _integer(simulation_steps,'simulation_steps',minimum=1,maximum=200)
    _integer(quality,'quality',maximum=2)
    _integer(simulation_mode,'simulation_mode',maximum=1)
    setter=_function('utility_api','SetSimulationQuality')
    getter=_function('utility_api','GetSimulationQuality')
    simulate=_function('utility_api','Simulate')
    checkpoint=create_scene_checkpoint(checkpoint_path)
    stages=[]
    try:
        setter(quality,simulation_mode)
        actual=list(getter())
        if actual!=[quality,simulation_mode]:
            raise RuntimeError('Simulation quality read-back differs from the request')
        stages.append({'stage':'simulation_quality','ok':True,'actual':actual})
        success=bool(simulate(simulation_steps))
        stages.append({'stage':'simulate','ok':success,'steps_argument':simulation_steps})
        return {'ok':success,'checkpoint':checkpoint,'stages':stages,'partial_change_possible':not success,
                'scope':'one bounded pass; inspect results before correcting'}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'stages':stages,'partial_change_possible':True}


def apply_fit_adjustments(references,adjustments,checkpoint_path,max_move=100.0):
    if not isinstance(references,dict) or not isinstance(adjustments,list) or not 1<=len(adjustments)<=100:
        raise ValueError('Provide a reference map and 1–100 explicit adjustments')
    maximum=_number(max_move,'max_move')
    if maximum>1000:
        raise ValueError('max_move cannot exceed 1000 native units')
    current=copy.deepcopy(references)
    for adjustment in adjustments:
        recipe_keys(adjustment,('ref_id','action','parameters'),('ref_id','action','parameters'),'adjustment')
        ref=adjustment['ref_id']
        if not isinstance(ref,str) or ref not in current:
            raise ValueError('Unknown adjustment reference')
        resolve_pattern_reference(current[ref])
        params=adjustment['parameters']
        action=adjustment['action']
        if action=='move_2d':
            recipe_keys(params,('offset_x','offset_y'),('offset_x','offset_y'),'move')
            if max(abs(recipe_number(params[k],k)) for k in params)>maximum:
                raise ValueError('Move exceeds the requested correction bound')
            _function('pattern_api','SetPatternPiecePos')
            _function('pattern_api','GetPatternPiecePos')
        elif action=='layer':
            recipe_keys(params,('layer',),('layer',),'layer')
            _integer(params['layer'],'layer',maximum=20)
            _function('pattern_api','SetPatternLayer')
            _function('pattern_api','GetPatternLayer')
        elif action=='resolution':
            recipe_keys(params,('particle_distance','mesh_type'),('particle_distance','mesh_type'),'resolution')
            distance=_number(params['particle_distance'],'particle_distance',minimum=0.8,inclusive=True)
            if distance>100 or params['mesh_type'] not in ('Triangle','Quad'):
                raise ValueError('Invalid bounded resolution adjustment')
            for name in ('SetParticleDistanceOfPattern','SetMeshType','GetParticleDistanceOfPattern','GetMeshCountByType'):
                _function('pattern_api',name)
        else:
            raise ValueError('Supported corrections: move_2d, layer, resolution')
    checkpoint=create_scene_checkpoint(checkpoint_path)
    completed=[]
    for position,adjustment in enumerate(adjustments):
        ref=adjustment['ref_id']
        try:
            index=resolve_pattern_reference(current[ref])['pattern_index']
            params=adjustment['parameters']
            if adjustment['action']=='move_2d':
                pos=list(_function('pattern_api','GetPatternPiecePos')(index))
                result=move_pattern_2d(index,pos[0]+params['offset_x'],pos[1]+params['offset_y'])
            elif adjustment['action']=='layer':
                result=set_pattern_layers([index],params['layer'])
            else:
                result=set_pattern_resolution([index],**params)
            completed.append({'adjustment':position,'ref_id':ref,**result})
            if not result['ok']:
                raise RuntimeError('Correction read-back failed')
            aliases={k:v['index'] for k,v in current[ref]['edges'].items()}
            current[ref]=bind_pattern_reference(ref,index,aliases)['reference']
        except Exception as exc:
            return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'completed':completed,
                    'failed_adjustment':position,'updated_references':current,'partial_change_possible':True}
    return {'ok':True,'checkpoint':checkpoint,'completed':completed,'updated_references':current,
            'scope':'explicit bounded corrections; no autonomous fitting or body-placement inference'}


_OPERATIONS.update({fn.__name__:fn for fn in (
    create_curved_pattern,create_internal_shape,sew_internal_edges,move_pattern_2d,copy_pattern,
    export_pattern_json,import_pattern_json,list_arrangements,inspect_arrangement,arrange_patterns,
    get_pattern_layer,set_pattern_layers,set_pattern_constraints,clone_pattern_layer,
    bind_pattern_reference,resolve_pattern_reference,sew_named_edges,measure_patterns,
    create_scene_checkpoint,restore_checkpoint,prepare_fitting_pass,apply_fit_adjustments,
)})
