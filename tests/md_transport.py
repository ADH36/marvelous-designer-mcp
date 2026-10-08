"""Exercise the actual embedded runtime/cache in server contract tests."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'md_addon'))
import md_listener
import cooperative_listener
from marvelous_designer_mcp import bridge


class RuntimeTransport:
    def __init__(self,legacy=False):
        self.legacy=legacy
        self.cache={}
        self.raw={}
        self.requests=[]

    def __call__(self,method,params,*,timeout=None):
        self.requests.append((method,dict(params)))
        if method=='execute_operation':
            if self.legacy:
                raise bridge.BridgeError('unknown method: execute_operation')
            with patch.object(cooperative_listener,'_cache',self.cache):
                return cooperative_listener._execute_runtime(params,md_listener)
        if method=='execute_python':
            return md_listener._execute_code(params['code'],self.raw)
        raise AssertionError('Unexpected test transport method '+method)


def install_transport(case,server,legacy=False):
    transport=RuntimeTransport(legacy)
    for name in ('_runtime_installed','_legacy_listener'):
        state=patch.object(server,name,False)
        state.start()
        case.addCleanup(state.stop)
    patched=patch.object(server.bridge,'call',side_effect=transport)
    case.bridge_mock=patched.start()
    case.addCleanup(patched.stop)
    case.transport=transport
    case.namespace=transport.raw
