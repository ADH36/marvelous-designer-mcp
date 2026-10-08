"""Documented/native controls with preflight, checkpoints and explicit evidence."""
import importlib
import json
import math
import os

if globals().get('__package__') == 'marvelous_designer_mcp':
    from .operations import (_function,_integer,_path,_prepare_file,_verify_files,
                             _pattern_indices,_lines,replace_fabric,save_checkpoint,_OPERATIONS)
    from .advanced import _file_hash,create_scene_checkpoint
    from .geometry import capture_mesh_snapshot,compare_meshes
    from .recipes import recipe_number,recipe_keys


def _signature_function(module,name,*type_tokens):
    fn=_function(module,name)
    doc=getattr(fn,'__doc__','') or ''
    if not doc or any(token not in doc for token in type_tokens):
        raise RuntimeError('Installed signature is not established for '+module+'.'+name+'; inspect md_api/docstrings')
    return fn


def inspect_capabilities():
    requested={
        'pattern_api':['CreatePatternWithPoints','CreateInternalShapeWithPoints','SetArrangement',
                       'SetArrangementPosition','SetArrangementOrientation','MovePatternPoint',
                       'GetAddlThicknessCollisionValue','SetAddlThicknessCollision'],
        'fabric_api':['ExportZFabW','ReplaceFabric','GetFabricInfo'],
        'utility_api':['ReDrape3DArrangement','Refresh3DWindow','GetClothPositions',
                       'GetZipperStyleName','SetZipperStyleTeethWidth','GetZipperStyleTeethWidth']}
    records=[]
    for module,names in requested.items():
        try:
            api=importlib.import_module(module)
        except ImportError as exc:
            records.append({'module':module,'available':False,'error':str(exc)})
            continue
        for name in names:
            value=getattr(api,name,None)
            records.append({'module':module,'name':name,'available':callable(value),
                            'doc':(getattr(value,'__doc__','') or '')[:6000] if callable(value) else None})
    return {'ok':True,'capabilities':records,'invoked_api_functions':False,
            'scope':'availability/docstrings only; no feature certification or guessed setter names'}


def backup_fabric_presets(output_dir,fabric_indices):
    folder=_path(output_dir)
    if os.path.exists(folder) and (not os.path.isdir(folder) or os.listdir(folder)):
        raise ValueError('Fabric backups require a new or empty directory')
    if not isinstance(fabric_indices,list) or not fabric_indices or len(set(fabric_indices))!=len(fabric_indices):
        raise ValueError('Provide unique fabric indices')
    count=_function('fabric_api','GetFabricCount')(False)
    name=_function('fabric_api','GetFabricName')
    export=_signature_function('fabric_api','ExportZFabW','str','int')
    names=[]
    for index in fabric_indices:
        _integer(index,'fabric_index',maximum=count-1)
        names.append(name(index))
    scene_names=[name(i) for i in range(count)]
    if any(not n or scene_names.count(n)!=1 for n in names):
        raise ValueError('Backup/restore requires unique nonempty fabric names')
    os.makedirs(folder,exist_ok=True)
    records=[]
    for number,(index,n) in enumerate(zip(fabric_indices,names)):
        path=os.path.join(folder,'fabric_%03d.zfab'%number)
        before=_prepare_file(path)
        returned=export(path,index)
        _verify_files(returned,folder,before,'.zfab')
        if not os.path.isfile(path) or not os.path.getsize(path):
            raise RuntimeError('MD did not create the requested fabric preset')
        records.append({'name':n,'original_index':index,'path':path,'sha256':_file_hash(path)})
    manifest={'schema_version':1,'fabrics':records}
    manifest_path=os.path.join(folder,'fabric-manifest.json')
    with open(manifest_path,'x',encoding='utf-8') as file:
        json.dump(manifest,file,indent=2)
    return {'ok':True,'manifest':manifest,'manifest_path':manifest_path,
            'scope':'MD-native presets retain more fabric state than geometry JSON; physical equivalence still needs inspection'}


def _fabric_restore_plan(manifest):
    recipe_keys(manifest,('schema_version','fabrics'),('schema_version','fabrics'),'fabric manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version']!=1 or not isinstance(manifest['fabrics'],list) or not 1<=len(manifest['fabrics'])<=500:
        raise ValueError('Invalid fabric backup manifest')
    count=_function('fabric_api','GetFabricCount')(False)
    name=_function('fabric_api','GetFabricName')
    names=[name(i) for i in range(count)]
    _signature_function('fabric_api','ReplaceFabric','int','str')
    plan=[]
    seen=set()
    for record in manifest['fabrics']:
        recipe_keys(record,('name','original_index','path','sha256'),('name','original_index','path','sha256'),'fabric record')
        n=record['name']
        if not isinstance(n,str) or n in seen or names.count(n)!=1:
            raise ValueError('Fabric mapping is missing, duplicate or ambiguous')
        seen.add(n)
        path=_path(record['path'],'.zfab',must_exist=True)
        if _file_hash(path)!=record['sha256']:
            raise ValueError('Fabric backup hash changed')
        plan.append((names.index(n),record))
    return plan


def _restore_fabric_presets(manifest):
    plan=_fabric_restore_plan(manifest)
    completed=[]
    for index,record in plan:
        result=replace_fabric(index,record['path'])
        completed.append(result)
        if not result.get('ok') or result.get('name')!=record['name']:
            return {'ok':False,'error':'Fabric preset replacement/name verification failed',
                    'completed':completed,'partial_change_possible':True}
    return {'ok':True,'completed':completed,'physical_parameters_certified':False,
            'verification':'native preset hashes before restore and MD replacement/name read-back; not physical sensor equivalence'}


def restore_fabric_presets(manifest,checkpoint_path):
    _fabric_restore_plan(manifest)
    checkpoint=save_checkpoint(checkpoint_path)
    try:
        result=_restore_fabric_presets(manifest)
        result['checkpoint']=checkpoint
        return result
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'partial_change_possible':True}


def _redrape_options(translation=None):
    translation=[0.0,0.0,0.0] if translation is None else translation
    if not isinstance(translation,list) or len(translation)!=3:
        raise ValueError('translation must be [x,y,z]')
    translation=[recipe_number(v,'translation') for v in translation]
    if any(abs(v)>1000 for v in translation):
        raise ValueError('Translation is bounded to 1000 per axis')
    redrape=_signature_function('utility_api','ReDrape3DArrangement','ImportExportOption')
    refresh=_function('utility_api','Refresh3DWindow')
    option=_function('ApiTypes','ImportExportOption')()
    values={'bMoveGarment':True,'bSizeAndPoseFromAvatar':False,'bAutoTranslate':False,'scale':1.0,
            'translationValueX':translation[0],'translationValueY':translation[1],'translationValueZ':translation[2]}
    for name,value in values.items():
        if not hasattr(option,name):
            raise ValueError('Installed redrape option is unavailable: '+name)
        setattr(option,name,value)
    return redrape,refresh,option,translation


def redrape_garment(output_dir,translation=None,movement_threshold=0.1,require_movement=True):
    folder=_path(output_dir)
    if os.path.exists(folder) and (not os.path.isdir(folder) or os.listdir(folder)):
        raise ValueError('Redrape requires a new or empty evidence directory')
    recipe_number(movement_threshold,'movement_threshold',True)
    if type(require_movement) is not bool:
        raise ValueError('require_movement must be boolean')
    redrape,refresh,option,translation=_redrape_options(translation)
    checkpoint=create_scene_checkpoint(os.path.join(folder,'before.zprj'))
    with open(os.path.join(folder,'before.checkpoint.json'),'x',encoding='utf-8') as file:
        json.dump(checkpoint['manifest'],file,indent=2)
    before=capture_mesh_snapshot(os.path.join(folder,'before','garment.obj'))
    try:
        returned=redrape(option)
        if returned is False:
            raise RuntimeError('MD rejected redrape')
        refresh()
        after=capture_mesh_snapshot(os.path.join(folder,'after','garment.obj'))
        comparison=compare_meshes(before['metrics']['path'],after['metrics']['path'],movement_threshold)
        verified=comparison['movement_detected'] is True
        result={'ok':verified or not require_movement,'checkpoint':checkpoint,'translation':translation,
                'mesh_comparison':comparison,'movement_verified':verified,'placement_certified':False,
                'scope':'explicit whole-garment native redrape; may reset drape and is not per-piece rigid translation',
                'partial_change_possible':True}
        if require_movement and not verified:
            result['error']='Redrape did not establish movement. Inspect the scene; no retry was performed.'
        with open(os.path.join(folder,'redrape-report.json'),'x',encoding='utf-8') as file:
            json.dump(result,file,indent=2)
        return result
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'partial_change_possible':True}


def inspect_zipper_style(style_index):
    _integer(style_index,'style_index')
    properties={}
    for field,suffix in (('name','Name'),('function_type','FunctionType'),('asset_type','AssetType'),
                         ('teeth_type','TeethType'),('teeth_width','TeethWidth'),('weight','Weight'),('tape_thickness','TapeThickness')):
        value=_function('utility_api','GetZipperStyle'+suffix)(style_index)
        if field=='name' and (not isinstance(value,str) or not value):
            raise ValueError('Zipper style index could not be resolved')
        if isinstance(value,float) and not math.isfinite(value):
            raise RuntimeError('Zipper getter returned a nonfinite value')
        properties[field]=value
    return {'ok':True,'style_index':style_index,'properties':properties,
            'scope':'existing zipper style only; not zipper placement or sewing'}


def set_zipper_style(style_index,properties,checkpoint_path):
    current=inspect_zipper_style(style_index)
    spec={'function_type':('FunctionType',0,2),'asset_type':('AssetType',0,6),'teeth_type':('TeethType',0,1),
          'teeth_width':('TeethWidth',None,None),'weight':('Weight',None,None),'tape_thickness':('TapeThickness',None,None)}
    recipe_keys(properties,tuple(spec),(), 'zipper properties')
    if not properties:
        raise ValueError('Provide at least one zipper style property')
    setters={}
    for key,value in properties.items():
        suffix,minimum,maximum=spec[key]
        if minimum is None:
            recipe_number(value,key,True)
        else:
            _integer(value,key,minimum,maximum)
        setters[key]=_signature_function('utility_api','SetZipperStyle'+suffix,'int')
    checkpoint=save_checkpoint(checkpoint_path)
    completed=[]
    try:
        for key,value in properties.items():
            setters[key](style_index,value)
            actual=_function('utility_api','GetZipperStyle'+spec[key][0])(style_index)
            if not math.isclose(actual,value,rel_tol=1e-5,abs_tol=1e-5):
                raise RuntimeError('Zipper style read-back did not match '+key)
            completed.append({'property':key,'value':actual})
        return {'ok':True,'checkpoint':checkpoint,'before':current,'completed':completed,
                'scope':'existing style properties only; no native zipper creation'}
    except Exception as exc:
        return {'ok':False,'error':str(exc),'checkpoint':checkpoint,'completed':completed,'partial_change_possible':True}


def plan_sleeve_cap(armhole_edges,cap_edges,ease_percent=0.0,tolerance_percent=3.0):
    ease=recipe_number(ease_percent,'ease_percent')
    tolerance=recipe_number(tolerance_percent,'tolerance_percent')
    if not 0<=ease<=30 or not 0<=tolerance<=20:
        raise ValueError('Ease must be 0–30 percent and tolerance 0–20 percent')
    endpoints=set()
    def measure(edges):
        if not isinstance(edges,list) or not 1<=len(edges)<=100:
            raise ValueError('Provide 1–100 explicit edges per side')
        result=[]
        for edge in edges:
            recipe_keys(edge,('pattern_index','line_index'),('pattern_index','line_index'),'cap endpoint')
            index,line=edge['pattern_index'],edge['line_index']
            _integer(index,'pattern_index')
            _integer(line,'line_index')
            if (index,line) in endpoints:
                raise ValueError('Cap/armhole endpoints must be unique')
            endpoints.add((index,line))
            _,actual=_lines(index)
            matches=[item for item in actual if item['index']==line]
            if len(matches)!=1 or not math.isfinite(matches[0]['length']) or matches[0]['length']<=0:
                raise ValueError('Edge is missing or has an invalid length')
            result.append({**edge,'length':matches[0]['length']})
        return result
    arm,cap=measure(armhole_edges),measure(cap_edges)
    total_arm,total_cap=sum(e['length'] for e in arm),sum(e['length'] for e in cap)
    target=total_arm*(1+ease/100)
    mismatch=(total_cap-target)/target*100
    return {'ok':True,'armhole_edges':arm,'cap_edges':cap,'armhole_length':total_arm,'cap_length':total_cap,
            'target_cap_length':target,'mismatch_percent':mismatch,'passes':abs(mismatch)<=tolerance,
            'suggested_uniform_cap_scale':target/total_cap,'fit_certified':False,
            'scope':'native measured lengths and requested ease; no automatic curve reshaping, seam orientation or body-fit certification',
            'next_step':'Explicitly revise the sleeve cap, inspect/rebind actual edges, then remeasure before sewing'}


_OPERATIONS.update({fn.__name__:fn for fn in (
    inspect_capabilities,backup_fabric_presets,restore_fabric_presets,redrape_garment,inspect_zipper_style,set_zipper_style,
    plan_sleeve_cap,
)})
