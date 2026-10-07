"""Stateful API-contract tests for garment features, including partial failures."""
import base64
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from marvelous_designer_mcp import operations as ops

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC')


class FakeMD:
    def __init__(self):
        self.names = ['Front', 'Back']
        self.distances = [20.0, 20.0]
        self.mesh_types = ['Triangle', 'Triangle']
        self.fabrics = [0, 0]
        self.selected = set()
        self.seams = []
        self.events = []
        self.options = []
        self.fail_fabric = None
        self.fail_resolution = None
        self.simulation_success = True
        self.pattern = SimpleNamespace(
            GetPatternCount=lambda: len(self.names),
            GetPatternPieceName=lambda i: self.names[i],
            SetPatternPieceName=lambda i, name: self.names.__setitem__(i, name),
            GetPatternPieceFabricIndex=lambda i: self.fabrics[i],
            GetParticleDistanceOfPattern=lambda i: self.distances[i],
            SetParticleDistanceOfPattern=self.set_distance,
            SetMeshType=lambda i, kind: self.mesh_types.__setitem__(i, kind),
            GetMeshCountByType=lambda i: {'Mesh Type': self.mesh_types[i], 'Face Count': '10'},
            SymmetryPatternPiece=self.mirror,
            SelectPatternViaIndex=self.select,
            GetSelectedPatternViaIndex=lambda i: i in self.selected,
            GetPatternInputInformation=self.geometry,
            GetSeamlinePairGroupCount=lambda: len(self.seams),
            GetSeamlinePairGroupName=lambda i: 'Seam ' + str(i),
            AddSeamlinePairGroup=self.sew,
        )
        self.export = SimpleNamespace(ExportOBJW=self.export_obj, ExportZPrjW=self.checkpoint,
                                      ExportTurntableImagesW=self.turntable, ExportCustomViewSnapshotW=self.custom_views)
        self.fabric = SimpleNamespace(GetFabricCount=lambda current: 2, AssignFabricToPattern=self.assign,
                                      CreateZfabFromTextures=self.create_fabric)
        self.modules = {'pattern_api': self.pattern, 'export_api': self.export, 'fabric_api': self.fabric,
                        'ApiTypes': SimpleNamespace(ImportExportOption=self.new_options),
                        'utility_api': SimpleNamespace(Simulate=self.simulate),
                        'import_api': SimpleNamespace(ImportAvatar=self.import_asset, ImportZpac=self.import_asset)}

    def new_options(self):
        return SimpleNamespace(**{n: False for n in ('bExportGarment', 'bExportAvatar', 'bThin', 'bSingleObject',
                               'bUnifiedUVCoordinates', 'bSaveInZip', 'bSaveColorWays', 'bIncludeHiddenObject', 'bAdd')}, scale=1.0)

    def geometry(self, i):
        return json.dumps({'Pattern InputInformation': [{'Pattern index': str(i), 'LineList': [
            {'Line count': '2'}, {'Line index': '0', 'Line length': 20, 'Line type': 'Straight'},
            {'Line index': '1', 'Line length': 30, 'Line type': 'Straight'}]}]})

    def set_distance(self, i, distance):
        self.events.append(('resolution', i))
        if i == self.fail_resolution:
            raise RuntimeError('resolution failed')
        self.distances[i] = distance

    def mirror(self, i, with_sewing):
        self.events.append(('mirror', i, with_sewing))
        self.names.append(self.names[i] + ' mirrored')
        self.distances.append(self.distances[i])
        self.mesh_types.append(self.mesh_types[i])
        self.fabrics.append(self.fabrics[i])

    def select(self, i, keep):
        if not keep:
            self.selected.clear()
        self.selected.add(i)

    def sew(self, *args):
        self.seams.append(args)
        return True

    def assign(self, fabric, pattern, face):
        self.events.append(('fabric', pattern))
        if pattern == self.fail_fabric:
            return False
        self.fabrics[pattern] = fabric
        return True

    def export_obj(self, path, option):
        self.events.append(('obj', path))
        self.options.append(option)
        file = Path(path)
        file.write_text('o garment\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        file.with_suffix('.mtl').write_text('newmtl cloth\n')
        return [str(file), str(file.with_suffix('.mtl'))]

    def checkpoint(self, path, thumbnail):
        self.events.append(('checkpoint', path, thumbnail))
        Path(path).write_bytes(b'project')
        return path

    def turntable(self, path, count, width, height, start):
        self.events.append(('turntable', count))
        file = Path(path)
        result = []
        for index in range(start, start + count):
            image = file.with_name(file.stem + '_' + str(index) + '.png')
            image.write_bytes(PNG)
            result.append(str(image))
        return result

    def custom_views(self, folder, width, height, prefix):
        return self.turntable(str(Path(folder) / (prefix + '.png')), 2, width, height, 0)

    def create_fabric(self, path, *maps):
        self.events.append(('preset', maps))
        Path(path).write_bytes(b'fabric preset')
        return True

    def simulate(self, steps):
        self.events.append(('simulate', steps))
        return self.simulation_success

    def import_asset(self, path, option):
        self.events.append(('import', path, option.bAdd))
        return True


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = FakeMD()
        patcher = patch.dict('sys.modules', self.md.modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, operation, **params):
        return ops.run_operation(operation, params)

    def test_inspection_provides_valid_sewing_edges(self):
        result = self.call('inspect_pattern', pattern_index=0)
        self.assertTrue(result['ok'])
        self.assertEqual([e['index'] for e in result['edges']], [0, 1])

    def test_rename_reads_back_and_preserves_unicode(self):
        result = self.call('rename_pattern', pattern_index=0, name="Sleeve's ü")
        self.assertEqual(result['name'], "Sleeve's ü")
        self.assertTrue(result['ok'])

    def test_mirror_refreshes_indices(self):
        result = self.call('mirror_pattern', pattern_index=0, with_sewing=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['after_count'], 3)
        self.assertTrue(result['refresh_indices'])

    def test_selection_batch_keeps_all_requested_pieces(self):
        self.assertTrue(self.call('select_patterns', pattern_indices=[0, 1])['ok'])
        self.assertEqual(self.md.selected, {0, 1})

    def test_resolution_readback(self):
        result = self.call('set_pattern_resolution', pattern_indices=[0, 1], particle_distance=5, mesh_type='Quad')
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.distances, [5, 5])
        self.assertEqual(self.md.mesh_types, ['Quad', 'Quad'])

    def test_invalid_batch_is_rejected_before_any_mutation(self):
        result = self.call('set_pattern_resolution', pattern_indices=[0, 20], particle_distance=5)
        self.assertFalse(result['ok'])
        self.assertEqual(self.md.events, [])

    def test_partial_resolution_failure_is_reported(self):
        self.md.fail_resolution = 1
        result = self.call('set_pattern_resolution', pattern_indices=[0, 1], particle_distance=5)
        self.assertFalse(result['ok'])
        self.assertEqual(result['failed_index'], 1)
        self.assertEqual([p['index'] for p in result['completed']], [0])

    def test_invalid_numeric_settings_do_not_mutate(self):
        for value in (math.nan, math.inf, 0.5, True):
            with self.subTest(value=value):
                self.assertFalse(self.call('set_pattern_resolution', pattern_indices=[0], particle_distance=value)['ok'])
        self.assertEqual(self.md.events, [])

    def test_sewing_passes_explicit_directions(self):
        result = self.call('sew_edges', pattern_a=0, line_a=1, pattern_b=1, line_b=0,
                           direction_a=False, direction_b=True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.seams, [(0, 1, 1, 0, False, True)])

    def test_invalid_edge_and_self_seam_are_rejected(self):
        for b, line in ((1, 100), (0, 0)):
            self.assertFalse(self.call('sew_edges', pattern_a=0, line_a=0, pattern_b=b, line_b=line)['ok'])
        self.assertEqual(self.md.seams, [])

    def test_unknown_geometry_schema_does_not_guess_edges(self):
        self.md.pattern.GetPatternInputInformation = lambda i: '{}'
        self.assertFalse(self.call('sew_edges', pattern_a=0, line_a=0, pattern_b=1, line_b=0)['ok'])
        self.assertEqual(self.md.seams, [])

    def test_indexed_geometry_getter_accepts_md_local_zero_index(self):
        def local_index(i):
            info = json.loads(self.md.geometry(i))
            info['Pattern InputInformation'][0]['Pattern index'] = '0'
            info['Pattern InputInformation'][0]['Pattern name'] = self.md.names[i]
            return json.dumps(info)
        self.md.pattern.GetPatternInputInformation = local_index
        self.assertTrue(self.call('inspect_pattern', pattern_index=1)['ok'])
        self.assertTrue(self.call('sew_edges', pattern_a=0, line_a=0, pattern_b=1, line_b=1)['ok'])

    def test_geometry_for_wrong_piece_is_rejected(self):
        info = json.loads(self.md.geometry(1))
        info['Pattern InputInformation'][0]['Pattern name'] = 'Wrong piece'
        self.md.pattern.GetPatternInputInformation = lambda i: json.dumps(info)
        self.assertFalse(self.call('sew_edges', pattern_a=0, line_a=0, pattern_b=1, line_b=1)['ok'])
        self.assertEqual(self.md.seams, [])

    def test_fabric_batch_reports_partial_failure(self):
        self.md.fail_fabric = 1
        result = self.call('assign_fabric_batch', fabric_index=1, pattern_indices=[0, 1])
        self.assertFalse(result['ok'])
        self.assertEqual(result['completed'], [{'index': 0, 'fabric_index': 1}])
        self.assertEqual(self.md.fabrics, [1, 0])

    def test_invalid_fabric_is_preflighted(self):
        self.assertFalse(self.call('assign_fabric_batch', fabric_index=9, pattern_indices=[0])['ok'])
        self.assertEqual(self.md.events, [])

    def test_obj_exports_with_explicit_dialog_free_options(self):
        result = self.call('export_obj', path=str(self.root / "mesh's ü" / 'garment.obj'), scale=0.01, include_avatar=True)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['files']), 2)
        self.assertEqual(self.md.options[0].scale, 0.01)
        self.assertTrue(self.md.options[0].bExportAvatar)
        self.assertFalse(self.md.options[0].bSaveInZip)

    def test_obj_refuses_nonempty_directory_before_api_call(self):
        (self.root / 'existing.mtl').write_text('preserve')
        self.assertFalse(self.call('export_obj', path=str(self.root / 'out.obj'))['ok'])
        self.assertEqual(self.md.events, [])

    def test_stale_export_is_not_reported_as_success(self):
        path = self.root / 'old.obj'
        path.write_text('old mesh')
        self.md.export.ExportOBJW = lambda p, option: [p]
        result = self.call('export_obj', path=str(path), overwrite=True)
        self.assertFalse(result['ok'])
        self.assertIn('unchanged', result['error'])

    def test_export_requires_real_primary_file(self):
        self.md.export.ExportOBJW = lambda p, option: []
        self.assertFalse(self.call('export_obj', path=str(self.root / 'out.obj'))['ok'])

    def test_header_only_obj_is_not_success(self):
        def empty_mesh(path, option):
            Path(path).write_text('o empty\n')
            return [path]
        self.md.export.ExportOBJW = empty_mesh
        result = self.call('export_obj', path=str(self.root / 'out.obj'))
        self.assertFalse(result['ok'])
        self.assertIn('no polygonal geometry', result['error'])

    def test_relative_export_path_is_rejected(self):
        self.assertFalse(self.call('export_obj', path='relative.obj')['ok'])
        self.assertEqual(self.md.events, [])

    def test_turntable_outputs_and_repeat_protection(self):
        params = {'path': str(self.root / 'out.png'), 'image_count': 4}
        result = self.call('export_turntable_images', **params)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['files']), 4)
        self.assertFalse(self.call('export_turntable_images', **params)['ok'])

    def test_turntable_falls_back_to_fresh_temporary_images(self):
        temp = self.root / 'md_temp'
        temp.mkdir()
        self.md.export.ExportTurntableImagesW = lambda *args: []
        self.md.export.ExportTurntableImages = lambda count: self.md.turntable(str(temp / 'output.png'), count, 2500, 2500, 0)
        result = self.call('export_turntable_images', path=str(self.root / 'target' / 'preview.png'), image_count=2)
        self.assertTrue(result['ok'])
        self.assertEqual(result['used'], 'temporary-output-overload')
        self.assertEqual(Path(result['files'][0]).name, 'preview_000.png')
        self.assertEqual(Path(result['files'][0]).read_bytes(), PNG)

    def test_turntable_rejects_cached_temporary_outputs(self):
        image = self.root / 'stale.png'
        image.write_bytes(PNG)
        os.utime(image, (1, 1))
        self.md.export.ExportTurntableImagesW = lambda *args: []
        self.md.export.ExportTurntableImages = lambda count: [str(image)]
        result = self.call('export_turntable_images', path=str(self.root / 'target' / 'preview.png'), image_count=1)
        self.assertFalse(result['ok'])
        self.assertIn('stale', result['error'])

    def test_custom_views_report_missing_views(self):
        self.md.export.ExportCustomViewSnapshotW = lambda *args: []
        result = self.call('export_custom_views', output_dir=str(self.root), prefix='view')
        self.assertFalse(result['ok'])

    def test_checkpoint_disables_thumbnail_and_verifies_file(self):
        result = self.call('save_checkpoint', path=str(self.root / 'save.zprj'))
        self.assertTrue(result['ok'])
        self.assertFalse(self.md.events[0][2])

    def test_fabric_creation_checks_input_and_writes_preset(self):
        texture = self.root / 'cloth.png'
        texture.write_bytes(PNG)
        result = self.call('create_fabric_from_textures', path=str(self.root / 'cloth.zfab'), base_texture=str(texture))
        self.assertTrue(result['ok'])
        self.assertFalse(self.call('create_fabric_from_textures', path=str(self.root / 'other.zfab'),
                                   base_texture=str(self.root / 'missing.png'))['ok'])

    def test_workflow_appends_assets_only_after_checkpoint(self):
        avatar, garment = self.root / 'body.avt', self.root / 'shirt.zpac'
        avatar.write_bytes(b'avatar')
        garment.write_bytes(b'garment')
        result = self.call('garment_workflow', output_dir=str(self.root / 'bundle'),
                           avatar_path=str(avatar), garment_path=str(garment), preview_count=2)
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.events[0][0], 'checkpoint')
        self.assertTrue(all(e[2] for e in self.md.events if e[0] == 'import'))
        self.assertNotIn('simulate', [e[0] for e in self.md.events])

    def test_workflow_stops_after_failed_simulation(self):
        self.md.simulation_success = False
        result = self.call('garment_workflow', output_dir=str(self.root / 'bundle'), simulation_steps=1)
        self.assertFalse(result['ok'])
        self.assertEqual([s['stage'] for s in result['stages']], ['checkpoint_before', 'simulate'])
        self.assertNotIn('obj', [e[0] for e in self.md.events])

    def test_missing_required_api_fails_before_workflow_mutation(self):
        del self.md.export.ExportOBJW
        self.assertFalse(self.call('garment_workflow', output_dir=str(self.root / 'bundle'))['ok'])
        self.assertEqual(self.md.events, [])


if __name__ == '__main__':
    unittest.main()
