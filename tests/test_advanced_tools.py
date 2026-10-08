"""Stateful native-API contracts and end-to-end MCP source execution."""
import asyncio
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_design_tools import DesignMD, server
from md_transport import install_transport
from marvelous_designer_mcp import advanced, operations as ops


class AdvancedMD(DesignMD):
    def __init__(self):
        super().__init__()
        self.positions, self.layers, self.solidified = {}, {}, {}
        self.arrangements, self.internal, self.snapshots = {}, {}, {}
        self.quality = [0, 0]
        self.fail_layer = None
        self.pattern.CreateInternalShapeWithPoints = self.create_internal
        self.pattern.GetLineLength = lambda i,c,e: self.internal[(i,c)][e]
        self.pattern.GetPatternPiecePos = lambda i: self.positions.get(i, [0,0])
        self.pattern.SetPatternPiecePos = lambda i,x,y: self.positions.__setitem__(i, [x,y])
        self.pattern.CopyPatternPieceMove = self.copy_piece
        self.pattern.LayerClonePatternPieceMove = self.clone
        self.pattern.GetArrangementList = lambda: [{'name':'Front'}, {'name':'Back'}]
        self.pattern.GetArrangementOfPattern = lambda i: self.arrangements.get(i, {})
        self.pattern.SetArrangement = lambda i,a: self.arrangements.__setitem__(i, {'arrangement':a})
        self.pattern.SetArrangementShapeStyle = lambda i,s: self.arrangements[i].update(shape=s)
        self.pattern.SetArrangementOrientation = lambda i,o: self.arrangements[i].update(orientation=o)
        self.pattern.SetArrangementPosition = lambda i,x,y,o: self.arrangements[i].update(position=[x,y,o])
        self.pattern.GetPatternLayer = lambda i: self.layers.get(i,0)
        self.pattern.SetPatternLayer = self.set_layer
        self.pattern.SetPatternFreeze = lambda i,v: self.events.append(('freeze',i,v))
        self.pattern.SetPatternStrengthen = lambda i,v: self.events.append(('strengthen',i,v))
        self.pattern.SetPatternPieceSolidify = lambda i,v: self.solidified.__setitem__(i,v)
        self.pattern.IsPatternPieceSolidify = lambda i: self.solidified.get(i,False)
        self.pattern.ExportPatternJSON = self.export_json
        self.pattern.ImportPatternJSON = self.import_json
        self.modules['utility_api'].SetSimulationQuality = self.set_quality
        self.modules['utility_api'].GetSimulationQuality = lambda: self.quality
        self.fabric.ExportZFabW=self.export_preset
        self.fabric.SetFabricNameW=self.set_fabric_name
        # Match installed pybind docstrings; availability alone is not a signature.
        AdvancedMD.export_preset.__doc__='ExportZFabW(arg0: str, arg1: int) -> str'
        AdvancedMD.replace.__doc__='ReplaceFabric(arg0: int, arg1: str) -> bool'
        AdvancedMD.set_fabric_name.__doc__='SetFabricNameW(arg0: int, arg1: str) -> None'

    def export_preset(self,path,index):
        self.events.append(('export_preset',index))
        Path(path).write_bytes(('preset '+str(index)).encode())
        return path

    def set_fabric_name(self,index,name):
        self.fabric_names[index]=name

    def create_internal(self, index, points, closed):
        self.events.append(('internal',index,points,closed))
        self.internal[(index,0)] = [20.0,30.0]
        return 0

    def copy_piece(self, index, x, y):
        self.events.append(('copy',index,x,y))
        result = self.create(copy.deepcopy(self.created_points.get(index, [(0,0,0),(20,0,0),(0,20,0)])))
        return result

    def clone(self, index, x, y, under):
        self.events.append(('clone',index,under))
        self.copy_piece(index,x,y)

    def set_layer(self, index, layer):
        self.events.append(('layer',index,layer))
        if index == self.fail_layer:
            raise RuntimeError('Layer update failed')
        self.layers[index] = layer

    def set_quality(self, quality, mode):
        self.events.append(('quality',quality,mode))
        self.quality = [quality,mode]

    def export_json(self, path):
        Path(path).write_text(json.dumps({'PatternList': [{'Name':n} for n in self.names]}))
        return True

    def import_json(self, path):
        self.events.append(('import_json',path))
        self.names=[p['Name'] for p in json.loads(Path(path).read_text())['PatternList']]
        self.distances=[20.0]*len(self.names)
        self.layers={}
        self.solidified={}
        self.fabric_names.append(self.fabric_names[0])
        self.fabrics=[len(self.fabric_names)-1]*len(self.names)
        return True

    def checkpoint(self, path, thumbnail):
        self.snapshots[path] = copy.deepcopy({key:getattr(self,key) for key in
            ('names','distances','mesh_types','fabrics','created_points','seams','positions','layers','solidified','arrangements')})
        return super().checkpoint(path,thumbnail)

    def load_project(self, path, option):
        if path in self.snapshots:
            self.events.append(('load',path,option.bAppend))
            for key,value in self.snapshots[path].items():
                setattr(self,key,copy.deepcopy(value))
            return True
        return super().load_project(path,option)


class AdvancedOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = AdvancedMD()
        patcher = patch.dict('sys.modules',self.md.modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, operation, **params):
        return ops.run_operation(operation,params)

    def bind(self, index=0, name='front'):
        return self.call('bind_pattern_reference',ref_id=name,pattern_index=index,edge_names={'side':0})['reference']

    def test_curve_types_reach_native_api(self):
        result = self.call('create_curved_pattern',vertices=[[0,0,0],[20,0,2],[20,20,3],[0,20,0]],name='Curved panel')
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.created_points[2][2],(20.0,20.0,3))
        self.assertIn('visual',result['verification'])

    def test_invalid_curve_types_and_crossing_vertices_do_not_mutate(self):
        for vertices in ([[0,0,0],[1,0,1],[0,1,0]], [[0,0,0],[1,1,2],[0,1,2],[1,0,0]]):
            self.assertFalse(self.call('create_curved_pattern',vertices=vertices,name='bad')['ok'])
        self.assertEqual(self.md.events,[])

    def test_internal_shape_and_boundary_internal_overload(self):
        self.assertTrue(self.call('create_internal_shape',pattern_index=0,vertices=[[0,0,0],[20,0,0]])['ok'])
        result = self.call('sew_internal_edges',pattern_a=0,line_a=0,child_a=0,pattern_b=1,line_b=0)
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.seams[-1],(1,0,0,0,0,False,True))

    def test_internal_to_internal_overload(self):
        self.md.internal.update({(0,0):[20.0],(1,2):[20.0]})
        result=self.call('sew_internal_edges',pattern_a=0,child_a=0,line_a=0,pattern_b=1,child_b=2,line_b=0)
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.seams[-1],(0,0,0,1,2,0,True,False))

    def test_invalid_internal_and_self_seams_rejected(self):
        self.md.internal[(0,0)]=[20.0]
        for params in ({'pattern_b':0,'child_b':0}, {'pattern_b':1,'child_b':99}):
            result=self.call('sew_internal_edges',pattern_a=0,child_a=0,line_a=0,line_b=0,**params)
            self.assertFalse(result['ok'])
        self.assertEqual(self.md.seams,[])

    def test_move_is_2d_and_readback_is_checked(self):
        result=self.call('move_pattern_2d',pattern_index=0,x=-20,y=50)
        self.assertTrue(result['ok'])
        self.assertEqual(result['position'],[-20,50])
        self.md.pattern.SetPatternPiecePos=lambda *args: None
        self.assertFalse(self.call('move_pattern_2d',pattern_index=0,x=30,y=40)['ok'])

    def test_copy_and_void_layer_clone_verify_count(self):
        self.assertTrue(self.call('copy_pattern',pattern_index=0,name='Copy')['ok'])
        result=self.call('clone_pattern_layer',pattern_index=0,name='Lining',under=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['index'],3)
        self.assertIn(('clone',0,True),self.md.events)

    def test_native_json_round_trip_checkpoints_before_import(self):
        path=str(self.root/'geometry.json')
        self.assertTrue(self.call('export_pattern_json',path=path)['ok'])
        self.assertFalse(self.call('export_pattern_json',path=path)['ok'])
        result=self.call('import_pattern_json',path=path,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'])
        events=[x[0] for x in self.md.events]
        self.assertLess(events.index('checkpoint'),events.index('import_json'))
        self.assertLess(events.index('export_preset'),events.index('import_json'))
        self.assertTrue(all(p['readback_verified'] for p in result['restored_settings']))

    def test_recipe_json_is_not_native_geometry(self):
        path=self.root/'recipe.json'
        path.write_text('{"schema_version":1,"pieces":[]}')
        self.assertFalse(self.call('import_pattern_json',path=str(path),checkpoint_path=str(self.root/'before.zprj'))['ok'])
        self.assertEqual(self.md.events,[])

    def test_nonfinite_and_duplicate_native_json_are_preflighted(self):
        path=self.root/'geometry.json'
        for document in ('{"Patterns":[NaN]}','{"Patterns":[],"Patterns":[1]}'):
            path.write_text(document)
            self.assertFalse(self.call('import_pattern_json',path=str(path),checkpoint_path=str(self.root/'before.zprj'))['ok'])
        self.assertEqual(self.md.events,[])

    def test_arrangement_discovery_and_explicit_options(self):
        self.assertEqual(len(self.call('list_arrangements')['arrangements']),2)
        result=self.call('arrange_patterns',pattern_indices=[0,1],arrangement_index=1,shape_style='Curved',orientation=3,position=[0,-10,5])
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.arrangements[1],{'arrangement':1,'shape':'Curved','orientation':3,'position':[0,-10,5]})

    def test_bad_arrangement_is_preflighted(self):
        self.assertFalse(self.call('arrange_patterns',pattern_indices=[0,1],arrangement_index=2)['ok'])
        self.assertFalse(self.call('arrange_patterns',pattern_indices=[0,1],arrangement_index=0,position=[0,True,0])['ok'])
        self.assertEqual(self.md.arrangements,{})

    def test_layers_and_partial_progress(self):
        self.md.fail_layer=1
        result=self.call('set_pattern_layers',pattern_indices=[0,1],layer=3)
        self.assertFalse(result['ok'])
        self.assertEqual(result['completed'],[{'index':0,'layer':3}])
        self.assertTrue(result['partial_change_possible'])
        self.assertEqual(self.call('get_pattern_layer',pattern_index=0)['layer'],3)

    def test_constraints_report_unverified_states(self):
        result=self.call('set_pattern_constraints',pattern_indices=[0],freeze=True,strengthen=False,solidify=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['unverified_settings'],['freeze','strengthen'])
        self.assertEqual(result['completed'][0]['readback'],{'solidify':True})

    def test_invalid_layers_and_constraints_never_call_setters(self):
        self.assertFalse(self.call('set_pattern_layers',pattern_indices=[0],layer=21)['ok'])
        self.assertFalse(self.call('set_pattern_constraints',pattern_indices=[0],freeze=1)['ok'])
        self.assertEqual(self.md.events,[])

    def test_reference_survives_ordinal_change(self):
        reference=self.bind()
        self.md.names.reverse()
        result=self.call('resolve_pattern_reference',reference=reference)
        self.assertTrue(result['ok'])
        self.assertEqual(result['pattern_index'],1)

    def test_changed_geometry_and_duplicate_identity_are_rejected(self):
        reference=self.bind()
        self.md.names[1]='Front'
        self.assertFalse(self.call('resolve_pattern_reference',reference=reference)['ok'])
        self.md.names[1]='Back'
        self.md.created_points[0]=[(0,0,0),(40,0,0),(0,40,0)]
        self.assertFalse(self.call('resolve_pattern_reference',reference=reference)['ok'])

    def test_renamed_reference_requires_explicit_rebind(self):
        reference=self.bind()
        self.md.names[0]='Renamed'
        self.assertFalse(self.call('resolve_pattern_reference',reference=reference)['ok'])

    def test_named_sewing_preserves_checkpoint_on_error(self):
        first,second=self.bind(),self.bind(1,'back')
        def fail(*args):
            raise RuntimeError('Native sewing failed')
        self.md.pattern.AddSeamlinePairGroup=fail
        result=self.call('sew_named_edges',reference_a=first,edge_a='side',reference_b=second,edge_b='side',
                         direction_a=True,direction_b=False,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertTrue(Path(result['checkpoint']['files'][0]).exists())
        self.assertTrue(result['partial_change_possible'])

    def test_measurement_ok_is_distinct_from_target_passes(self):
        result=self.call('measure_patterns',pattern_indices=[0],targets=[{'pattern_index':0,'line_index':0,'target_length':25,'tolerance':1}])
        self.assertTrue(result['ok'])
        self.assertFalse(result['passes'])
        self.assertEqual(result['targets'][0]['difference'],-5)

    def test_measurement_targets_validate_indices_and_nonfinite(self):
        for length,line in ((float('nan'),0),(20,99)):
            self.assertFalse(self.call('measure_patterns',pattern_indices=[0],targets=[{'pattern_index':0,'line_index':line,'target_length':length,'tolerance':0}])['ok'])

    def test_restore_verifies_hash_and_preserves_current_first(self):
        saved=self.call('create_scene_checkpoint',path=str(self.root/'saved.zprj'))
        self.md.names[0]='Edited'
        result=self.call('restore_checkpoint',manifest=saved['manifest'],preserve_current_path=str(self.root/'edited.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.names,['Front','Back'])
        self.assertEqual(result['preserved']['manifest']['pattern_names'][0],'Edited')
        self.assertFalse(self.md.events[-1][2])

    def test_changed_checkpoint_hash_refuses_load_and_backup(self):
        saved=self.call('create_scene_checkpoint',path=str(self.root/'saved.zprj'))
        Path(saved['manifest']['path']).write_bytes(b'changed')
        self.md.events.clear()
        self.assertFalse(self.call('restore_checkpoint',manifest=saved['manifest'],preserve_current_path=str(self.root/'backup.zprj'))['ok'])
        self.assertEqual(self.md.events,[])

    def test_bounded_fitting_quality_and_checkpoint_order(self):
        result=self.call('prepare_fitting_pass',checkpoint_path=str(self.root/'before.zprj'),simulation_steps=2,quality=2,simulation_mode=0)
        self.assertTrue(result['ok'],result)
        self.assertEqual([e[0] for e in self.md.events],['checkpoint','quality','simulate'])

    def test_invalid_fitting_pass_never_checkpoints(self):
        self.assertFalse(self.call('prepare_fitting_pass',checkpoint_path=str(self.root/'before.zprj'),simulation_steps=201)['ok'])
        self.assertEqual(self.md.events,[])

    def test_corrections_preflight_all_before_checkpoint(self):
        adjustments=[{'ref_id':'front','action':'move_2d','parameters':{'offset_x':10,'offset_y':0}},
                     {'ref_id':'front','action':'layer','parameters':{'layer':25}}]
        self.assertFalse(self.call('apply_fit_adjustments',references={'front':self.bind()},adjustments=adjustments,checkpoint_path=str(self.root/'before.zprj'))['ok'])
        self.assertEqual(self.md.events,[])

    def test_bounded_corrections_keep_completed_progress(self):
        self.md.fail_layer=1
        adjustments=[{'ref_id':'front','action':'layer','parameters':{'layer':1}},
                     {'ref_id':'back','action':'layer','parameters':{'layer':2}}]
        result=self.call('apply_fit_adjustments',references={'front':self.bind(),'back':self.bind(1,'back')},
                         adjustments=adjustments,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_adjustment'],1)
        self.assertEqual(result['completed'][0]['ref_id'],'front')
        self.assertIn('front',result['updated_references'])


@unittest.skipUnless(server is not None, 'MCP SDK is not installed')
class AdvancedServerTests(unittest.TestCase):
    def setUp(self):
        AdvancedOperationsTests.setUp(self)
        install_transport(self,server)
        with server._history_lock:
            server._history.clear()

    def test_actual_mcp_curve_schema_preserves_integer_vertex_types(self):
        result=asyncio.run(server.mcp.call_tool('create_curved_pattern',{'vertices':[[0,0,0],[20,0,2],[0,20,3]],'name':'Curved'}))
        self.assertEqual(self.md.created_points[2][1][2],2)
        self.assertEqual(self.md.names[2],'Curved')

    def test_registry_persist_resolve_and_named_sewing(self):
        path=str(self.root/'refs.json')
        self.assertTrue(server.bind_pattern_reference(path,'front',0,{'side':0})['ok'])
        self.assertTrue(server.bind_pattern_reference(path,'back',1,{'side':0})['ok'])
        self.assertTrue(server.resolve_pattern_reference(path,'front')['ok'])
        self.assertTrue(server.sew_named_edges(path,'front','side','back','side',str(self.root/'before.zprj'))['ok'])

    def test_capture_report_returns_images_and_separate_target_status(self):
        folder=self.root/'report'
        result=server.capture_fit_report(str(folder),[0],targets=[{'pattern_index':0,'line_index':0,'target_length':25,'tolerance':1}],observations=['Front hem looks uneven'],preview_count=2)
        status=json.loads(result.content[0].text)
        self.assertFalse(result.isError,status)
        self.assertFalse(status['measurements']['passes'])
        self.assertFalse(status['fit_certified'])
        self.assertEqual(len([c for c in result.content if c.type=='image']),2)
        self.assertTrue((folder/'fit-report.json').exists())

    def test_fitting_pass_report_contains_manifest_and_images(self):
        folder=self.root/'fitting'
        result=server.run_fitting_pass(str(folder),[0,1],simulation_steps=2,preview_count=1)
        status=json.loads(result.content[0].text)
        self.assertFalse(result.isError,status)
        self.assertTrue((folder/'before.checkpoint.json').exists())
        self.assertTrue((folder/'fit-report.json').exists())
        self.assertIn(('simulate',2),self.md.events)

    def test_fitting_preflight_failure_does_not_mutate(self):
        result=server.run_fitting_pass(str(self.root/'fitting'),[0],targets=[{'pattern_index':1,'line_index':0,'target_length':20,'tolerance':0}])
        self.assertTrue(result.isError)
        self.assertEqual(self.md.events,[])

    def test_fitting_failure_keeps_checkpoint_and_stages(self):
        self.md.simulation_success=False
        result=server.run_fitting_pass(str(self.root/'fitting'),[0],preview_count=1)
        status=json.loads(result.content[0].text)
        self.assertTrue(result.isError)
        self.assertIn('checkpoint',status)
        self.assertEqual(status['stages'][-1]['stage'],'simulate')
        self.assertTrue(status['partial_change_possible'])

    def test_preview_failure_preserves_fitting_checkpoint_and_stages(self):
        self.md.export.ExportTurntableImagesW=lambda *args: []
        result=server.run_fitting_pass(str(self.root/'fitting'),[0],preview_count=1)
        status=json.loads(result.content[0].text)
        self.assertTrue(result.isError)
        self.assertTrue(status['checkpoint']['ok'])
        self.assertEqual(status['stages'][-1]['stage'],'simulate')
        self.assertIn('preview',status)

    def test_checkpoint_manifest_restore_round_trip(self):
        path=str(self.root/'before.zprj')
        created=server.create_scene_checkpoint(path)
        self.assertTrue(created['ok'],created)
        self.md.names[0]='Edited'
        result=server.restore_checkpoint(created['manifest_file']['path'],str(self.root/'backup.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertTrue((self.root/'backup.checkpoint.json').exists())

    def test_manifest_write_failure_does_not_erase_checkpoint(self):
        with patch.object(server,'_write_json',side_effect=OSError('Disk full')):
            result=server.create_scene_checkpoint(str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertIn('manifest',result)
        self.assertTrue(Path(result['files'][0]).exists())

    def test_adjustment_registry_failure_preserves_completed_changes(self):
        path=str(self.root/'refs.json')
        server.bind_pattern_reference(path,'front',0,{'side':0})
        with patch.object(server,'_write_json',side_effect=OSError('Disk full')):
            result=server.apply_fit_adjustments(path,[{'ref_id':'front','action':'layer','parameters':{'layer':2}}],str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['completed'][0]['ok'])
        self.assertEqual(result['registry_error'],'Disk full')

    def test_journal_bridge_failure_is_uncertain_and_not_retried(self):
        self.bridge_mock.side_effect=server.bridge.BridgeError('Timed out')
        result=server.measure_patterns([0])
        self.assertFalse(result['ok'])
        self.assertEqual(self.bridge_mock.call_count,1)
        entry=server.get_operation_history()['operations'][-1]
        self.assertEqual(entry['status'],'uncertain')
        self.assertEqual(entry['operation_id'],result['operation_id'])

    def test_journal_export_has_digests_without_full_inputs(self):
        server.measure_patterns([0])
        path=str(self.root/'history.json')
        self.assertTrue(server.save_operation_history(path)['ok'])
        entry=json.loads(Path(path).read_text())['operations'][0]
        self.assertEqual(entry['status'],'completed')
        self.assertIn('parameter_sha256',entry)
        self.assertNotIn('pattern_indices',entry)
        self.assertFalse(server.get_operation_history(101)['ok'])

    def test_atomic_registry_replacement_failure_keeps_original(self):
        path=str(self.root/'refs.json')
        server.bind_pattern_reference(path,'front',0,{'side':0})
        before=Path(path).read_bytes()
        with patch.object(server.os,'replace',side_effect=OSError('Replace denied')):
            result=server.bind_pattern_reference(path,'back',1,{'side':0})
        self.assertFalse(result['ok'])
        self.assertEqual(Path(path).read_bytes(),before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['refs.json'])


if __name__=='__main__':
    unittest.main()
