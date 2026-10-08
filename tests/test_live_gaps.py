"""Regressions for the v0.8 live report; native fakes model the observed failures."""
import asyncio
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import test_advanced_tools as advanced_tests
from test_advanced_tools import AdvancedMD,server
from md_transport import install_transport
from marvelous_designer_mcp import mesh_analysis,construction,drafting


class RecoveryRegressions(unittest.TestCase):
    setUp=advanced_tests.AdvancedOperationsTests.setUp
    call=advanced_tests.AdvancedOperationsTests.call
    def test_duplicate_default_fabric_import_preserves_nondefault_settings(self):
        self.md.distances=[15,12]
        self.md.layers={0:1,1:2}
        self.md.solidified={0:True,1:True}
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['fabric_mapping'],{'0':2})
        self.assertEqual(self.md.fabric_names,['Cotton','Denim','Cotton'])
        self.assertEqual(self.md.distances,[15,12])
        self.assertEqual(self.md.layers,{0:1,1:2})
        self.assertEqual(self.md.solidified,{0:True,1:True})
        self.assertEqual(self.md.fabrics,[2,2])
        self.assertFalse(result['full_state_preserved'])

    def test_import_mapping_follows_names_after_native_reordering(self):
        self.md.distances=[15,12]
        original=self.md.pattern.ImportPatternJSON
        def reordered(path):
            original(path)
            self.md.names.reverse()
            return True
        self.md.pattern.ImportPatternJSON=reordered
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.distances,[12,15])

    def test_import_split_group_stops_before_preset_replacement(self):
        original=self.md.pattern.ImportPatternJSON
        def split(path):
            original(path)
            self.md.fabrics=[0,2]
            return True
        self.md.pattern.ImportPatternJSON=split
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_stage'],'fabric_mapping')
        self.assertTrue(result['partial_change_possible'])
        self.assertTrue(Path(result['settings_recovery_path']).exists())
        self.assertFalse(any(e[0]=='replace_fabric' for e in self.md.events))

    def test_import_collapsed_original_fabrics_are_not_overwritten(self):
        self.md.fabrics=[0,1]
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertIn('collapsed',result['error'])
        self.assertFalse(any(e[0]=='replace_fabric' for e in self.md.events))

    def backup(self,indices=None):
        return self.call('backup_fabric_presets',output_dir=str(self.root/'backup'),fabric_indices=indices or [0])['manifest']

    def test_preset_filename_does_not_replace_saved_name(self):
        manifest=self.backup()
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.md.fabric_names[0],'Cotton')
        self.assertTrue(result['completed'][0]['name_restored'])
        self.assertEqual(self.md.fabrics,[0,0])

    def test_duplicate_names_resolve_using_distinct_pattern_associations(self):
        self.md.fabric_names=['Cotton','Cotton']
        self.md.fabrics=[0,1]
        manifest=self.backup([0,1])
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual([r['fabric_index'] for r in result['completed']],[0,1])

    def test_legacy_ambiguous_names_require_explicit_mapping(self):
        manifest=self.backup()
        manifest['schema_version']=1
        manifest['fabrics'][0].pop('pattern_names')
        self.md.fabric_names[1]='Cotton'
        self.md.events.clear()
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'bad.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.md.events,[])
        result=self.call('restore_fabric_presets',manifest=manifest,fabric_mapping={'0':0},checkpoint_path=str(self.root/'good.zprj'))
        self.assertTrue(result['ok'],result)

    def test_changed_backup_refuses_checkpoint_and_mutation(self):
        manifest=self.backup()
        Path(manifest['fabrics'][0]['path']).write_bytes(b'tampered')
        self.md.events.clear()
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.md.events,[])

    def test_name_readback_failure_retains_checkpoint_and_completed_replacement(self):
        manifest=self.backup()
        def ignored(index,name):
            """SetFabricNameW(arg0: int, arg1: str) -> None"""
        self.md.fabric.SetFabricNameW=ignored
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_stage'],'name_restore')
        self.assertEqual(result['completed'][0]['name'],'fabric_000')
        self.assertTrue(result['partial_change_possible'])
        self.assertTrue(Path(result['checkpoint']['files'][0]).exists())

    def test_unsupported_name_signature_rejects_before_checkpoint(self):
        manifest=self.backup()
        self.md.fabric.SetFabricNameW=lambda i,n:None
        self.md.events.clear()
        result=self.call('restore_fabric_presets',manifest=manifest,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.md.events,[])

    def test_nonzero_redrape_offset_is_rejected_without_mutation(self):
        folder=self.root/'redrape'
        result=self.call('redrape_garment',output_dir=str(folder),translation=[0,0,25])
        self.assertFalse(result['ok'])
        self.assertIn('unsupported',result['error'])
        self.assertEqual(result['code'],'unsupported_translation')
        self.assertFalse(result['partial_change_possible'])
        self.assertFalse(result['checkpoint_created'])
        self.assertEqual(self.md.events,[])
        self.assertFalse(folder.exists())

    def test_missing_preset_rename_signature_is_preflighted_before_json_import(self):
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        self.md.fabric.SetFabricNameW=lambda i,n:None
        self.md.events.clear()
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.md.events,[])

    def test_zipper_fixture_properties_checkpoint_and_read_back(self):
        props={'Name':'Fixture zipper','FunctionType':0,'AssetType':0,'TeethType':0,'TeethWidth':2.0,'Weight':1.0,'TapeThickness':.3}
        api=self.md.modules['utility_api']
        for key,value in props.items():
            setattr(api,'GetZipperStyle'+key,lambda i,k=key:props[k])
            if key!='Name':
                def setter(i,v,k=key):
                    """Setter(arg0: int, arg1: float) -> None"""
                    props[k]=v
                setattr(api,'SetZipperStyle'+key,setter)
        result=self.call('set_zipper_style',style_index=0,properties={'teeth_width':3.5},checkpoint_path=str(self.root/'zipper.zprj'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['completed'][0]['value'],3.5)
        self.assertTrue(Path(result['checkpoint']['files'][0]).exists())

    def test_settings_partial_failure_retains_completed_actions(self):
        self.md.fail_layer=1
        source=str(self.root/'native.json')
        self.call('export_pattern_json',path=source)
        result=self.call('import_pattern_json',path=source,checkpoint_path=str(self.root/'before.zprj'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_stage'],'pattern_settings')
        self.assertTrue(result['restored_settings'][0]['readback_verified'])
        self.assertEqual(result['restored_settings'][1]['completed_actions'][-1]['action'],'layer')
        self.assertFalse(result['restored_settings'][1]['readback_verified'])


class GeometryRegressions(unittest.TestCase):
    base=[(0,0,0),(4,0,0),(0,4,0)]
    crossing=[(1,.5,-1),(1,.5,1),(1,2,0)]
    touching=[(0,0,0),(4,0,0),(0,-4,0)]
    coplanar=[(1,1,0),(3,1,0),(1,3,0)]

    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def mesh(self,name,triangles):
        lines=[]
        for n,points in enumerate(triangles):
            lines.append('o Piece_%d'%n)
            lines.extend('v %s'%(' '.join(map(str,p))) for p in points)
            lines.append('f %d %d %d'%(n*3+1,n*3+2,n*3+3))
        path=self.root/name
        path.write_text('\n'.join(lines))
        return str(path)

    def test_crossing_is_separate_from_boundary_contact_and_coplanar_overlap(self):
        for triangle,expected in ((self.crossing,'proper_crossing'),(self.touching,'contact'),(self.coplanar,'coplanar_overlap')):
            self.assertEqual(mesh_analysis.classify_triangle_pair(self.base,triangle,1e-6),expected)
        self.assertIsNone(mesh_analysis.classify_triangle_pair(self.base,[(x,y,z+3) for x,y,z in self.base],1e-6))

    def test_classification_is_stable_under_translation_and_face_orientation(self):
        a=[(x+100000,y-100000,z+2000) for x,y,z in self.base]
        b=[(x+100000,y-100000,z+2000) for x,y,z in self.crossing]
        self.assertEqual(mesh_analysis.classify_triangle_pair(a,b[::-1],1e-6),'proper_crossing')

    def test_normal_adjacent_sewn_contact_is_suppressed(self):
        path=self.root/'square.obj'
        path.write_text('v 0 0 0\nv 4 0 0\nv 0 4 0\nv 4 4 0\nf 1 2 3\nf 2 4 3\n')
        result=mesh_analysis.analyze_intersections(str(path))
        self.assertEqual(result['intersecting_or_touching_pairs'],0)
        self.assertEqual(result['suppressed_contact_counts'],{'mesh_adjacent_contact':1})

    def test_caller_seam_pair_only_suppresses_contact(self):
        for triangle,expected in ((self.touching,0),(self.crossing,1),(self.coplanar,1)):
            path=self.mesh('seam.obj',[self.base,triangle])
            result=mesh_analysis.analyze_intersections(path,known_seam_triangle_pairs=[[0,1]])
            self.assertEqual(result['intersecting_or_touching_pairs'],expected,result)

    def test_crossing_retains_piece_and_explicit_body_region_provenance(self):
        a=self.mesh('garment.obj',[self.base])
        b=self.mesh('body.obj',[self.crossing])
        result=mesh_analysis.analyze_intersections(a,b,body_regions=[{'name':'upper torso','min':[-1,-1,-2],'max':[5,5,2]}])
        self.assertEqual(result['classification_counts'],{'proper_crossing':1})
        item=result['examples'][0]
        self.assertEqual(item['garment_source']['object'],'Piece_0')
        self.assertEqual(item['body_regions'],['upper torso'])
        self.assertEqual(result['review_status'],'needs_review')

    def test_budget_exit_never_claims_clean_result(self):
        path=self.mesh('overlap.obj',[self.base]*30)
        result=mesh_analysis.analyze_intersections(path,max_candidates=100)
        self.assertFalse(result['analysis_complete'])
        self.assertEqual(result['review_status'],'incomplete')
        self.assertEqual(result['triangle_pairs_tested'],100)

    def test_localized_svg_views_refuse_changed_mesh(self):
        path=self.mesh('crossing.obj',[self.base,self.crossing])
        result=mesh_analysis.analyze_intersections(path)
        views=mesh_analysis.export_intersection_review(result,str(self.root/'views'))
        self.assertTrue(Path(views['views'][0]['path']).read_text().count('projection')==3)
        Path(path).write_text(Path(path).read_text()+'\n# changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            mesh_analysis.export_intersection_review(result,str(self.root/'changed'))

    def test_rigid_movement_has_zero_elongation(self):
        a=self.mesh('rest.obj',[self.base])
        b=self.mesh('moved.obj',[[(x+10,y-20,z+4) for x,y,z in self.base]])
        result=mesh_analysis.analyze_deformation(a,b)
        self.assertEqual(result['edges_over_limit'],0)
        self.assertEqual(result['maximum_change_percent'],0)

    def test_open_avatar_never_infers_signed_clearance(self):
        a=self.mesh('garment.obj',[[(x,y,z+1) for x,y,z in self.base]])
        b=self.mesh('body.obj',[self.base])
        result=mesh_analysis.analyze_clearance(a,b)
        self.assertFalse(result['avatar_closed_manifold_by_edge_count'])
        self.assertTrue(all(p['signed_distance'] is None for p in result['worst_samples']))


@unittest.skipUnless(server is not None,'Install MCP SDK dependencies')
class ServerRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.md=AdvancedMD()
        modules=patch.dict('sys.modules',self.md.modules)
        modules.start()
        self.addCleanup(modules.stop)
        install_transport(self,server)

    def test_cached_runtime_source_sent_once_and_globals_isolated(self):
        self.assertTrue(server.rename_pattern(0,'First')['ok'])
        self.assertTrue(server.rename_pattern(0,'Second')['ok'])
        self.assertIn('runtime_source',self.transport.requests[0][1])
        self.assertNotIn('runtime_source',self.transport.requests[1][1])
        self.transport('execute_python',{'code':'run_operation = None; result = True'})
        self.assertTrue(server.rename_pattern(0,'Third')['ok'])

    def test_explicit_legacy_fallback_executes_each_operation_once(self):
        self.transport.legacy=True
        self.assertTrue(server.rename_pattern(0,'Legacy')['ok'])
        self.assertEqual([m for m,_ in self.transport.requests],['execute_operation','execute_python'])
        self.assertTrue(server.rename_pattern(0,'Legacy2')['ok'])
        self.assertEqual(self.transport.requests[-1][0],'execute_python')

    def test_cache_miss_reinstalls_only_when_not_executed(self):
        self.assertTrue(server.rename_pattern(0,'First')['ok'])
        self.transport.cache.clear()
        self.assertTrue(server.rename_pattern(0,'After cache loss')['ok'])
        self.assertNotIn('runtime_source',self.transport.requests[-2][1])
        self.assertIn('runtime_source',self.transport.requests[-1][1])

    def test_bridge_io_failure_is_uncertain_and_never_replayed(self):
        with patch.object(server.bridge,'call',side_effect=server.bridge.BridgeError('listener I/O failed: lost reply')) as call:
            result=server.rename_pattern(0,'Uncertain')
        self.assertFalse(result['ok'])
        self.assertEqual(call.call_count,1)
        self.assertEqual(server.get_operation_history(1)['operations'][0]['status'],'uncertain')

    def test_torn_and_invalid_journals_normalize_started_entries(self):
        for suffix in ('','{torn','[]','x'*17000):
            path=self.root/'journal.jsonl'
            path.write_text(json.dumps({'operation_id':'op','status':'started'})+'\n'+suffix)
            result=server.read_operation_journal(str(path))
            self.assertEqual(result['operations'][0]['status'],'uncertain')
            self.assertTrue(result['requires_state_inspection'])
            self.assertFalse(result['automatic_replay'])
            self.assertEqual(result['ok'],not suffix)

    def test_journal_retains_completed_evidence_before_torn_tail(self):
        path=self.root/'journal.jsonl'
        path.write_text('{"operation_id":"op","status":"started"}\n{"operation_id":"op","status":"completed"}\n{torn')
        result=server.read_operation_journal(str(path))
        self.assertEqual(result['operations'][0]['status'],'completed')
        self.assertFalse(result['automatic_replay'])

    def test_strict_capture_rejects_small_native_image_without_upscaling(self):
        result=server.preview_garment(str(self.root/'images'),image_count=1,require_native_size=True)
        status=json.loads(result.content[0].text)
        self.assertTrue(result.isError)
        self.assertEqual(status['render_sizes'],[[1,1]])
        with server.Image.open(status['files'][0]) as image:
            self.assertEqual(image.size,(1,1))

    def test_saved_custom_views_return_native_resolution_metadata(self):
        def capture(folder,width,height,prefix):
            path=Path(folder)/(prefix+'.png')
            server.Image.new('RGB',(width,height)).save(path)
            return [str(path)]
        self.md.export.ExportCustomViewSnapshotW=capture
        result=server.preview_garment(str(self.root/'views'),capture_mode='custom_views',require_native_size=True)
        status=json.loads(result.content[0].text)
        self.assertFalse(result.isError,status)
        self.assertTrue(status['native_size_verified'])
        self.assertFalse(status['resampled'])
        self.assertEqual(status['capture_mode'],'custom_views')

    def test_fit_review_separates_incomplete_scan_from_capture_ok(self):
        outcome={'ok':True,'geometric_diagnostics':{'self_intersections':{'ok':False,'analysis_complete':False,'intersecting_or_touching_pairs':0}}}
        server._summarize_fit_review(outcome)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['review_status'],'incomplete')
        self.assertFalse(outcome['fit_certified'])

    def test_fit_review_does_not_call_unsigned_clearance_clean(self):
        outcome={'ok':True,'geometric_diagnostics':{'clearance':{'ok':True,'all_garment_vertices_sampled':False,'avatar_closed_manifold_by_edge_count':False}}}
        server._summarize_fit_review(outcome)
        self.assertEqual(outcome['review_status'],'needs_review')
        self.assertTrue(outcome['review_limits'])

    def test_invalid_detail_capture_options_do_not_mutate(self):
        result=server.run_fitting_pass(str(self.root/'fit'),[0],preview_width=9000)
        self.assertTrue(result.isError)
        self.assertEqual(self.md.events,[])

    def test_list_fabrics_uses_global_indices_and_reports_used_assignments(self):
        self.md.fabrics=[1,1]
        self.md.fabric.GetFabricCount=lambda current:1 if current else 2
        self.md.fabric.GetFabricStyleNameList=lambda:list(self.md.fabric_names)
        result=server.list_fabrics()
        self.assertEqual(len(result['result']['fabrics']),2)
        self.assertEqual(result['result']['used_assignments'][0]['fabric_index'],1)

    def test_all_new_tools_have_typed_schemas(self):
        tools={t.name:t for t in asyncio.run(server.mcp.list_tools())}
        names='analyze_mesh_deformation analyze_mesh_fit analyze_surface_intersections arrange_patterns_by_name arrange_patterns_verified assess_design_evidence backup_fabric_presets build_bodice_block build_sleeve_block capture_mesh_snapshot compare_mesh_snapshots compare_native_pattern_exports create_fit_closeups draft_closure_layout draft_dart draft_matched_sleeve_cap draft_seam_allowance draft_size_variants export_construction_svg inspect_capabilities inspect_native_pattern_geometry inspect_zipper_style listener_status plan_reference_migration plan_sleeve_cap read_operation_journal redrape_garment restore_fabric_presets set_zipper_style transform_native_pattern_json'.split()
        self.assertEqual(len(names),30)
        for name in names:
            self.assertEqual(tools[name].inputSchema['type'],'object')
        self.assertIn('fabric_mapping',tools['restore_fabric_presets'].inputSchema['properties'])
        self.assertIn('known_seam_triangle_pairs',tools['analyze_surface_intersections'].inputSchema['properties'])

    def test_listener_metadata_separates_old_listener_from_host_evidence(self):
        with patch.object(server.bridge,'call',return_value={'listener_version':'0.8.0','dispatched_messages':42,'cached_runtime_supported':True}):
            result=server.listener_status()
        self.assertEqual(result['server_version'],server.__version__)
        self.assertTrue(result['listener_restart_required'])
        self.assertFalse(result['restart_required_for_v08'])
        self.assertTrue(result['ui_pump_health']['message_dispatch_observed'])
        self.assertEqual(result['release_ui_evidence']['md_version'],'2026.0.315')

    def test_listener_unavailable_returns_actionable_structured_status(self):
        with patch.object(server.bridge,'call',side_effect=server.bridge.BridgeError('listener I/O failed: refused')):
            result=server.listener_status()
        self.assertFalse(result['ok'])
        self.assertFalse(result['listener_available'])
        self.assertFalse(result['partial_change_possible'])
        self.assertIn('registered MD listener',result['next_step'])


class ConstructionEvidenceTests(unittest.TestCase):
    def test_matched_cap_solver_and_impossible_cap(self):
        cap=construction.matched_sleeve_cap(420,260,180,550,60,200,5,32,.05,'down')
        self.assertAlmostEqual(cap['draft_cap_length'],441,delta=.05)
        self.assertEqual(len(cap['cap_draft_edge_indices']),32)
        self.assertFalse(cap['native_edge_indices_verified'])
        with self.assertRaises(ValueError):
            construction.matched_sleeve_cap(100,260,180,550,60,160)

    def test_size_variants_are_bounded_affine_drafts(self):
        result=construction.size_variants([[0,0],[100,0],[100,-200],[0,-200]],[{'name':'L','scale_x':1.1,'scale_y':1.2}])
        self.assertAlmostEqual(result['variants'][0]['boundary_lengths'][1],240)
        self.assertFalse(result['native_grading_rules_created'])
        with self.assertRaises(ValueError):
            construction.size_variants([[0,0],[100,0],[0,100]],[{'name':'Bad','scale_x':10,'scale_y':1}])

    def test_dart_legs_equal_and_cutting_allowance_expands_bounds(self):
        points=[[0,0],[400,0],[400,-500],[0,-500]]
        dart=drafting.draft_dart(points,0,30,100)
        boundary=dart['points']
        a,b=dart['draft_leg_indices']
        self.assertAlmostEqual(math.dist(boundary[a],boundary[a+1]),math.dist(boundary[b],boundary[b+1]))
        allowance=drafting.seam_allowance(points,10)
        self.assertLess(min(p[0] for p in allowance['cutting_outline']),0)

    def test_construction_svg_scale_and_xml_escaping(self):
        svg,meta=construction.construction_svg([[0,0],[400,0],[400,-500],[0,-500]],markers=[{'label':'<button & notch>','position':[200,-50]}])
        self.assertEqual(meta['width_mm'],420)
        self.assertEqual(meta['height_mm'],520)
        self.assertIn('&lt;button &amp; notch&gt;',svg)

    def test_evidence_claims_require_references_and_remain_uncertified(self):
        check={'category':'placement','name':'Placement','status':'pass','evidence':'mesh-comparison.json','note':''}
        result=construction.assess_evidence([check])
        self.assertEqual(result['review_status'],'needs_review')
        self.assertFalse(result['fit_certified'])
        check['evidence']=''
        with self.assertRaises(ValueError):
            construction.assess_evidence([check])


if __name__=='__main__':
    unittest.main()
