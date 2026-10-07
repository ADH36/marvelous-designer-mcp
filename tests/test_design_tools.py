"""Recipe geometry, mutation boundaries, destination profiles and independent batches."""
import asyncio
import copy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_operations import FakeMD
from marvelous_designer_mcp import operations as ops, recipes

try:
    from marvelous_designer_mcp import server
except ModuleNotFoundError as exc:
    if exc.name != 'mcp':
        raise
    server = None


class DesignMD(FakeMD):
    def __init__(self):
        super().__init__()
        self.created_points = {}
        self.fabric_names = ['Cotton', 'Denim']
        self.fail_create = False
        self.fail_load = None
        self.start, self.end, self.current = 0.0, 100.0, 0.0
        self.pattern.CreatePatternWithPoints = self.create
        self.pattern.GetPatternInputInformation = self.geometry
        self.fabric.GetFabricCount = lambda current: len(self.fabric_names)
        self.fabric.GetFabricName = lambda i: self.fabric_names[i]
        self.fabric.AddFabricW = self.add_fabric
        self.fabric.ReplaceFabric = self.replace
        self.export.ExportAlembicW = self.alembic
        self.modules['ApiTypes'].ImportZPRJOption = lambda: SimpleNamespace(bAppend=True, bLoadGarment=False,
            bLoadAvatar=False, bLoadSceneAndProps=False, bLoadRenderProperties=False, bLoadCustomView=False)
        self.modules['import_api'].ImportZprjW = self.load_project
        self.modules['utility_api'] = SimpleNamespace(Simulate=self.simulate,
            GetStartAnimationFrame=lambda: self.start, GetEndAnimationFrame=lambda: self.end,
            GetCurrentAnimationFrame=lambda: self.current,
            SetStartAnimationFrame=lambda n: self.set_frame('start', n),
            SetEndAnimationFrame=lambda n: self.set_frame('end', n), RunAnimationRecording=self.record)

    def new_options(self):
        result = super().new_options()
        result.bExportAnimation = False
        result.axisX, result.axisY, result.axisZ = 0, 1, 2
        result.bInvertX = result.bInvertY = result.bInvertZ = False
        return result

    def create(self, points):
        if self.fail_create:
            raise RuntimeError('Creation failed')
        self.events.append(('create', points))
        index = len(self.names)
        self.created_points[index] = points
        self.names.append('Pattern')
        self.distances.append(20.0)
        self.mesh_types.append('Triangle')
        self.fabrics.append(0)
        return index

    def geometry(self, index):
        if index not in self.created_points:
            return super().geometry(index)
        points = self.created_points[index]
        edges = [{'Line index': str(i), 'Line length': math.dist(p[:2], points[(i+1) % len(points)][:2])}
                 for i, p in enumerate(points)]
        return json.dumps({'Pattern InputInformation': [{'Pattern index': '0', 'Pattern name': self.names[index], 'LineList': edges}]})

    def add_fabric(self, path):
        self.events.append(('add_fabric', path))
        self.fabric_names.append(Path(path).stem)
        return len(self.fabric_names)-1

    def replace(self, index, path):
        self.fabric_names[index] = Path(path).stem
        self.events.append(('replace_fabric', index))
        return True

    def set_frame(self, key, value):
        self.events.append(('frame', key, value))
        setattr(self, key, value)

    def record(self, start, end):
        self.events.append(('record', start, end))
        self.current = end

    def alembic(self, path, option):
        self.events.append(('alembic', option.bExportAnimation))
        Path(path).write_bytes(b'Ogawa cache fixture')
        return [path]

    def load_project(self, path, option):
        self.events.append(('load', path, option.bAppend))
        if path == self.fail_load:
            return False
        self.names = ['Loaded garment']
        self.distances, self.mesh_types, self.fabrics = [20.0], ['Triangle'], [0]
        self.created_points, self.seams = {}, []
        return True


class RecipeValidationTests(unittest.TestCase):
    def test_skirt_measurements_and_matching_side_lengths(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        front = recipe['pieces'][0]['points']
        self.assertEqual(front[1][0]-front[0][0], 385)
        self.assertEqual(front[2][1], 600)
        self.assertEqual(front[2][0]-front[3][0], 600)
        self.assertEqual(len(recipe['seams']), 2)

    def test_scale_is_explicit(self):
        self.assertEqual(recipes.skirt_recipe(75, 60, 120, native_units_per_cm=1)['pieces'][0]['points'][2][1], 60)

    def test_invalid_polygons(self):
        for points in ([[0,0],[1,1],[0,1],[1,0]], [[0,0],[1,0],[2,0]], [[0,0],[1,0],[0,0]],
                       [[0,0],[1,0],[0, math.nan]], [[0,0],[1,0]], [[0,0],[True,1],[1,0]]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                recipes.recipe_polygon(points)

    def test_concave_polygon_supported(self):
        self.assertEqual(len(recipes.recipe_polygon([[0,0],[4,0],[4,4],[2,2],[0,4]])), 5)

    def test_unknown_fields_and_schema_are_rejected(self):
        for key, value in (('code', 'malicious code'), ('schema_version', True), ('schema_version', 9)):
            recipe = recipes.skirt_recipe(75, 60, 120)
            recipe[key] = value
            with self.assertRaises(ValueError):
                recipes.validate_recipe(recipe)

    def test_bad_seam_and_duplicate_ids(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        recipe['seams'][0]['line_a'] = 9
        with self.assertRaises(ValueError):
            recipes.validate_recipe(recipe)
        recipe = recipes.skirt_recipe(75, 60, 120)
        recipe['pieces'][1]['id'] = 'front'
        with self.assertRaises(ValueError):
            recipes.validate_recipe(recipe)

    def test_reused_edges_are_rejected(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        recipe['seams'].append(copy.deepcopy(recipe['seams'][0]))
        with self.assertRaises(ValueError):
            recipes.validate_recipe(recipe)

    def test_recipe_is_not_mutated_by_validation(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        original = copy.deepcopy(recipe)
        recipes.validate_recipe(recipe)
        self.assertEqual(recipe, original)


class DesignOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = DesignMD()
        patcher = patch.dict('sys.modules', self.md.modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, operation, **params):
        return ops.run_operation(operation, params)

    def test_create_polygon_and_scaled_readback(self):
        result = self.call('create_pattern', points=[[0,0],[20,0],[0,20]], name="Panel's ü", coordinate_scale=10)
        self.assertTrue(result['ok'])
        self.assertEqual(result['index'], 2)
        self.assertEqual(self.md.created_points[2][1], (200.0,0.0,0))
        self.assertEqual(self.md.names[2], "Panel's ü")

    def test_invalid_polygon_never_calls_md(self):
        self.assertFalse(self.call('create_pattern', points=[[0,0],[1,0],[2,0]], name='bad')['ok'])
        self.assertEqual(self.md.events, [])

    def test_creation_failure_is_marked_partial(self):
        self.md.fail_create = True
        result = self.call('create_pattern', points=[[0,0],[1,0],[0,1]], name='bad')
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_change_possible'])

    def test_reordered_indices_do_not_rename_existing_piece(self):
        original_create = self.md.create
        def reorder(points):
            original_create(points)
            self.md.names[0], self.md.names[2] = self.md.names[2], self.md.names[0]
        self.md.pattern.CreatePatternWithPoints = reorder
        result = self.call('create_pattern', points=[[0,0],[1,0],[0,1]], name='New panel')
        self.assertFalse(result['ok'])
        self.assertNotIn('New panel', self.md.names)

    def test_diagnostics_are_read_only_and_find_mismatch(self):
        result = self.call('diagnose_sewing', seam_pairs=[{'pattern_a':0,'line_a':0,'pattern_b':1,'line_b':1}])
        self.assertTrue(result['ok'])
        self.assertFalse(result['passes'])
        self.assertAlmostEqual(result['pairs'][0]['difference_percent'], 100/3)
        self.assertEqual(self.md.events, [])

    def test_diagnostics_flag_reused_edges(self):
        pair = {'pattern_a':0,'line_a':0,'pattern_b':1,'line_b':0}
        result = self.call('diagnose_sewing', seam_pairs=[pair,pair])
        self.assertFalse(result['passes'])
        self.assertEqual(len(result['reused_edges']), 2)

    def test_diagnostics_invalid_endpoint_fails(self):
        self.assertFalse(self.call('diagnose_sewing', seam_pairs=[{'pattern_a':0,'line_a':99,'pattern_b':1,'line_b':0}])['ok'])

    def test_fabric_import_and_replace(self):
        fabric = self.root / 'Silk.zfab'
        fabric.write_bytes(b'preset')
        result = self.call('import_fabric', path=str(fabric))
        self.assertTrue(result['ok'])
        self.assertEqual(result['fabric_index'], 2)
        self.assertTrue(self.call('assign_fabric_batch', fabric_index=2, pattern_indices=[0], assignment_mode=3)['ok'])
        self.assertTrue(self.call('replace_fabric', fabric_index=1, path=str(fabric))['ok'])

    def test_fabric_import_false_or_old_index_is_not_success(self):
        fabric = self.root / 'preset.zfab'
        fabric.write_bytes(b'preset')
        self.md.fabric.AddFabricW = lambda path: 0
        self.assertFalse(self.call('import_fabric', path=str(fabric))['ok'])

    def test_assignment_modes_alias_and_readback(self):
        self.assertTrue(self.call('assign_fabric_batch', fabric_index=1, pattern_indices=[0], face=3)['ok'])
        self.assertFalse(self.call('assign_fabric_batch', fabric_index=1, pattern_indices=[0], face=0)['ok'])
        self.assertFalse(self.call('assign_fabric_batch', fabric_index=1, pattern_indices=[0], assignment_mode=2, face=3)['ok'])
        self.md.fabric.AssignFabricToPattern = lambda *args: True
        self.assertFalse(self.call('assign_fabric_batch', fabric_index=0, pattern_indices=[0])['ok'])

    def test_recipe_checkpoint_precedes_creation_and_sewing(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        recipe['preview_count'] = 0
        result = self.call('apply_garment_recipe', recipe=recipe, output_dir=str(self.root / 'run'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.md.events[0][0], 'checkpoint')
        self.assertEqual(result['created_patterns'], {'front':2,'back':3})
        self.assertEqual(len(self.md.seams), 2)
        self.assertNotIn('simulate', [e[0] for e in self.md.events])

    def test_invalid_recipe_preflight_before_checkpoint(self):
        recipe = recipes.skirt_recipe(75, 60, 120)
        recipe['seams'][0]['piece_a'] = 'missing'
        self.assertFalse(self.call('apply_garment_recipe', recipe=recipe, output_dir=str(self.root / 'run'))['ok'])
        self.assertEqual(self.md.events, [])

    def test_recipe_creation_failure_stops_exports(self):
        self.md.fail_create = True
        result = self.call('apply_garment_recipe', recipe=recipes.skirt_recipe(75,60,120), output_dir=str(self.root / 'run'))
        self.assertFalse(result['ok'])
        self.assertEqual([s['stage'] for s in result['stages']], ['checkpoint_before','create:front'])

    def test_recipe_order_mismatch_stops_sewing(self):
        self.md.pattern.GetPatternInputInformation = lambda i: FakeMD.geometry(self.md, i)
        result = self.call('apply_garment_recipe', recipe=recipes.skirt_recipe(75,60,120), output_dir=str(self.root / 'run'))
        self.assertFalse(result['ok'])
        self.assertIn('edge ordering', result['error'])
        self.assertEqual(self.md.seams, [])

    def test_animation_range_readback_and_order(self):
        result = self.call('configure_animation', start_frame=200,end_frame=300)
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.events[0], ('frame','end',300.0))
        self.assertFalse(self.call('configure_animation', start_frame=20,end_frame=10)['ok'])

    def test_animation_recording_checkpoints_before_call(self):
        result = self.call('record_animation', start_frame=0,end_frame=30,checkpoint_path=str(self.root/'before.zprj'))
        self.assertTrue(result['ok'])
        self.assertEqual([e[0] for e in self.md.events], ['checkpoint','record'])

    def test_alembic_explicit_options_and_stale_rejection(self):
        path = self.root/'motion.abc'
        self.assertTrue(self.call('export_alembic', path=str(path))['ok'])
        self.assertEqual(self.md.events[0], ('alembic',True))
        self.md.export.ExportAlembicW = lambda p, option: [p]
        self.assertFalse(self.call('export_alembic', path=str(path), overwrite=True)['ok'])

    def test_batch_replaces_scene_and_checkpoints_original(self):
        jobs = []
        for name in ('one','two'):
            project = self.root/(name+'.zprj')
            project.write_bytes(b'project')
            jobs.append({'project_path':str(project),'preview_count':0})
        result = self.call('batch_garment_workflows', jobs=jobs, output_dir=str(self.root/'batch'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.md.events[0][0], 'checkpoint')
        self.assertEqual([e[2] for e in self.md.events if e[0]=='load'], [False,False])
        self.assertEqual(len(self.md.names), 1)
        self.assertEqual(len(result['results']), 2)

    def test_batch_all_inputs_checked_before_scene_change(self):
        self.assertFalse(self.call('batch_garment_workflows', jobs=[{'project_path':str(self.root/'missing.zprj')}],output_dir=str(self.root/'batch'))['ok'])
        self.assertEqual(self.md.events, [])

    def test_batch_stops_at_failed_load(self):
        path = self.root/'bad.zprj'
        path.write_bytes(b'project')
        self.md.fail_load = str(path)
        result = self.call('batch_garment_workflows', jobs=[{'project_path':str(path)}]*2,output_dir=str(self.root/'batch'))
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['results']), 1)


@unittest.skipIf(server is None, 'Install MCP SDK integration dependencies')
class DesignServerTests(unittest.TestCase):
    # Integration cases use the same stateful fake through generated MD source.
    def setUp(self):
        DesignOperationTests.setUp(self)
        def execute(method, params, *, timeout=None):
            namespace = {}
            exec(params['code'], namespace)
            return {'result': namespace['result'], 'error':None}
        patcher = patch.object(server.bridge,'call',side_effect=execute)
        self.bridge_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_registered_design_schemas(self):
        tools = {t.name:t for t in asyncio.run(server.mcp.list_tools())}
        self.assertEqual(len(tools), 67)
        self.assertTrue(tools['apply_garment_recipe'].inputSchema['properties']['dry_run']['default'])
        self.assertEqual(tools['assign_fabric'].inputSchema['properties']['assignment_mode']['default'], 1)

    def test_save_load_and_dry_run_do_not_call_md(self):
        recipe = server.build_skirt_recipe(75,60,120)['recipe']
        path = str(self.root/'skirt.json')
        self.assertTrue(server.save_garment_recipe(path,recipe)['ok'])
        self.assertFalse(server.save_garment_recipe(path,recipe)['ok'])
        loaded = server.load_garment_recipe(path)
        self.assertTrue(loaded['ok'])
        self.assertTrue(server.apply_garment_recipe(loaded['recipe'])['dry_run'])
        self.bridge_mock.assert_not_called()

    def test_duplicate_json_and_code_payload_rejected(self):
        path = self.root/'bad.json'
        path.write_text('{"schema_version":1,"schema_version":2}')
        self.assertFalse(server.load_garment_recipe(str(path))['ok'])
        path.write_text('{"schema_version":1,"code":"print(123)"}')
        self.assertFalse(server.load_garment_recipe(str(path))['ok'])
        self.bridge_mock.assert_not_called()

    def test_recipe_generated_source_and_report(self):
        recipe = recipes.skirt_recipe(75,60,120)
        recipe['preview_count'] = 0
        result = server.apply_garment_recipe(recipe,str(self.root/'run'),dry_run=False)
        self.assertTrue(result['ok'], result)
        self.assertTrue(Path(result['report']['path']).exists())

    def test_report_failure_preserves_mutation_results(self):
        recipe = recipes.skirt_recipe(75,60,120)
        recipe['preview_count'] = 0
        with patch.object(server, '_write_json', side_effect=OSError('Disk full')):
            result = server.apply_garment_recipe(recipe,str(self.root/'run'),dry_run=False)
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial_change_possible'])
        self.assertEqual(result['created_patterns'], {'front':2,'back':3})
        self.assertTrue(result['stages'][-1]['ok'])
        self.assertEqual(result['report_error'], 'Disk full')

    def test_profile_requires_calibration_and_transfers_axes(self):
        profile = str(self.root/'blender.json')
        params = dict(path=profile,destination='Blender',scale=0.001,axis_codes=[0,2,1],invert_axes=[False,False,True],calibration_note='Test cube size/orientation checked')
        self.assertTrue(server.save_export_profile(**params)['ok'])
        self.assertFalse(server.export_obj_with_profile(str(self.root/'mesh'/'garment.obj'),profile)['ok'])
        self.bridge_mock.assert_not_called()
        self.assertTrue(server.save_export_profile(**params,validated=True,overwrite=True)['ok'])
        result = server.export_obj_with_profile(str(self.root/'mesh'/'garment.obj'),profile)
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.options[-1].axisY, 2)
        self.assertTrue(self.md.options[-1].bInvertZ)

    def test_invalid_profile_is_preflighted(self):
        self.assertFalse(server.save_export_profile(str(self.root/'bad.json'),'Unity',1,[0,0,2],[False]*3,'unverified')['ok'])
        self.bridge_mock.assert_not_called()

    def test_rectangle_invalid_size_does_not_call_md(self):
        self.assertFalse(server.create_rectangle(-20,40,'bad')['ok'])
        self.bridge_mock.assert_not_called()
        self.assertTrue(server.create_rectangle(20,40,'good')['ok'])

    def test_batch_generated_source_and_report(self):
        path = self.root/'one.zprj'
        path.write_bytes(b'project')
        result = server.batch_garment_workflows([{'project_path':str(path),'preview_count':0}],str(self.root/'batch'))
        self.assertTrue(result['ok'],result)
        self.assertTrue(Path(result['report']['path']).exists())

    def test_batch_stops_before_next_load_on_preview_decode_failure(self):
        first, second = self.root/'first.zprj', self.root/'second.zprj'
        first.write_bytes(b'project')
        second.write_bytes(b'project')
        def broken_images(path, count, width, height, start):
            Path(path).write_bytes(b'not a PNG')
            return [path]
        self.md.export.ExportTurntableImagesW = broken_images
        jobs = [{'project_path':str(p),'preview_count':1} for p in (first,second)]
        result = server.batch_garment_workflows(jobs,str(self.root/'batch'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_job'], 0)
        self.assertEqual([e[1] for e in self.md.events if e[0]=='load'], [str(first)])


if __name__ == '__main__':
    unittest.main()
