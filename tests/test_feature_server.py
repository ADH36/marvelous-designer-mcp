"""Exercise generated MD scripts and image content through the real MCP SDK."""
import asyncio
import base64
import json
from io import BytesIO
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_operations import FakeMD, PNG

try:
    from marvelous_designer_mcp import server
except ModuleNotFoundError as exc:
    if exc.name != 'mcp':
        raise
    server = None


@unittest.skipIf(server is None, 'Install project dependencies for MCP SDK integration tests')
class FeatureServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = FakeMD()
        patcher = patch.dict('sys.modules', self.md.modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        bridge_patch = patch.object(server.bridge, 'call', side_effect=self.execute)
        self.bridge_mock = bridge_patch.start()
        self.addCleanup(bridge_patch.stop)

    def execute(self, method, params, *, timeout=None):
        self.assertEqual(method, 'execute_python')
        namespace = {}
        exec(params['code'], namespace)
        return {'result': namespace['result'], 'stdout': '', 'stderr': '', 'error': None}

    def test_all_feature_tools_register_with_typed_schemas(self):
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        for name in ('preview_garment', 'export_obj', 'sew_edges', 'garment_workflow'):
            self.assertIn(name, tools)
        self.assertEqual(tools['set_pattern_resolution'].inputSchema['properties']['mesh_type']['enum'], ['Triangle', 'Quad'])

    def test_script_encoding_handles_quotes_unicode_and_failure_status(self):
        result = server.rename_pattern(0, "Sleeve's ü\nresult = False")
        self.assertTrue(result['ok'])
        self.assertEqual(self.md.names[0], "Sleeve's ü\nresult = False")
        self.assertFalse(server.rename_pattern(99, 'bad')['ok'])

    def test_nonfinite_parameters_are_rejected_before_transport(self):
        self.assertFalse(server.set_pattern_resolution([0], math.nan)['ok'])
        self.bridge_mock.assert_not_called()

    def test_preview_returns_actual_mcp_image_content(self):
        result = server.preview_garment(str(self.root), image_count=2)
        self.assertFalse(result.isError)
        images = [block for block in result.content if block.type == 'image']
        self.assertEqual(len(images), 2)
        with server.Image.open(BytesIO(base64.b64decode(images[0].data))) as image:
            self.assertEqual(image.size, (1024, 1024))
        self.assertTrue(json.loads(result.content[0].text)['ok'])

    def test_invalid_preview_does_not_reach_md(self):
        self.assertTrue(server.preview_garment(str(self.root), image_count=100).isError)
        self.bridge_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
