"""Experimental idle Windows message pump; native API calls remain synchronous."""
import ctypes
import hashlib
import json
import math
import os
import select
import socket
import time
import traceback
import uuid

_running = False
_cache = {}
_status = {}
MAX_CLIENTS = 8
POLL_SECONDS = 0.02
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class WindowsPump:
    def __init__(self):
        from ctypes import wintypes as w
        class MSG(ctypes.Structure):
            _fields_ = [('hwnd', w.HWND), ('message', w.UINT), ('wParam', w.WPARAM),
                        ('lParam', w.LPARAM), ('time', w.DWORD), ('pt', w.POINT), ('lPrivate', w.DWORD)]
        self.MSG = MSG
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.PeekMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        self.user.PeekMessageW.restype = w.BOOL
        self.user.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user.TranslateMessage.restype = w.BOOL
        self.user.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user.DispatchMessageW.restype = ctypes.c_ssize_t
        self.user.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user.PostQuitMessage.restype = None

    def pump(self):
        message = self.MSG()
        deadline = time.monotonic() + 0.008
        for _ in range(100):
            if time.monotonic() >= deadline or not self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                break
            if message.message == 0x0012:  # preserve WM_QUIT for the enclosing host loop
                self.user.PostQuitMessage(int(message.wParam))
                return False
            self.user.TranslateMessage(ctypes.byref(message))
            self.user.DispatchMessageW(ctypes.byref(message))
            _status['dispatched_messages'] += 1
        return True


def _execute_runtime(params, legacy, deadline=None):
    digest, source = params.get('runtime_sha256'), params.get('runtime_source')
    operation, arguments = params.get('operation'), params.get('arguments')
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('Supply a runtime SHA-256')
    if not isinstance(operation, str) or not isinstance(arguments, dict):
        raise ValueError('Supply an operation name and argument object')
    namespace = _cache.get(digest)
    if namespace is None:
        if source is None:
            return {'runtime_cache_miss': True, 'executed': False}
        if not isinstance(source, str) or hashlib.sha256(source.encode()).hexdigest() != digest:
            raise ValueError('Runtime source digest mismatch')
        namespace = {'__name__': '__md_runtime__'}
        # Isolated globals keep arbitrary execute_python calls out of the runtime.
        outcome = legacy._execute_code(compile(source, '<md-mcp-runtime>', 'exec'), namespace)
        if outcome['error']:
            return outcome
        if not callable(namespace.get('run_operation')):
            raise ValueError('Runtime has no operation dispatcher')
        _cache.clear()  # bound memory; another version can reinstall without replaying
        _cache[digest] = namespace
    if deadline is not None and time.time() >= deadline:
        return {'error':'Request expired before native execution', 'executed':False, 'result':None}
    namespace['_request_operation'], namespace['_request_arguments'] = operation, arguments
    try:
        return legacy._execute_code('result = run_operation(_request_operation, _request_arguments)', namespace)
    finally:
        namespace.pop('_request_operation', None)
        namespace.pop('_request_arguments', None)


def _dispatch(line, legacy):
    request_id = None
    try:
        req = json.loads(line.decode('utf-8'))
        if not isinstance(req, dict):
            raise ValueError('request must be a JSON object')
        request_id, method = req.get('id'), req.get('method')
        params = req.get('params', {})
        if params is None:
            params = {}
        if not isinstance(request_id, str) or not request_id:
            raise ValueError('request id must be a non-empty string')
        if not isinstance(method, str) or not method or not isinstance(params, dict):
            raise ValueError('Supply a method string and params object')
        deadline = req.get('expires_at_unix')
        if deadline is not None:
            if isinstance(deadline,bool) or not isinstance(deadline,(float,int)) or not math.isfinite(deadline):
                raise ValueError('expires_at_unix must be finite')
            if time.time() >= deadline:
                return {'id':request_id,'error':'request expired before execution; not executed'}, True
        if method == 'shutdown':
            return {'id': request_id, 'result': {'bye': True}}, False
        if method in ('ping', 'status'):
            return {'id': request_id, 'result': {'pong': True, **_status, 'runtime_cache_entries': len(_cache)}}, True
        if method != 'execute_operation' and method not in legacy.HANDLERS:
            return {'id': request_id, 'error': 'unknown method: ' + method}, True
        started = time.monotonic()
        _status['active_method'] = method
        try:
            result = _execute_runtime(params, legacy, deadline) if method == 'execute_operation' else legacy.HANDLERS[method](params)
            return {'id': request_id, 'result': result}, True
        finally:
            _status.update(active_method=None, last_method=method,
                           last_operation_seconds=round(time.monotonic() - started, 4))
    except Exception:
        return {'id': request_id, 'error': traceback.format_exc()}, True


def serve_forever(legacy):
    """Pump only between calls; slow reads/writes never hold the GUI thread."""
    global _running
    if _running:
        raise RuntimeError('Listener already running; stop before restarting')
    pump = WindowsPump() if os.name == 'nt' and os.environ.get('MD_MCP_UI_PUMP', '1') != '0' else None
    clients = {}
    _status.clear()
    _status.update(listener_version='0.8.0', listener_id=uuid.uuid4().hex,
                   ui_mode='windows_idle_pump' if pump else 'blocking_compatibility',
                   ui_pump_live_validated=False, dispatched_messages=0, active_method=None,
                   last_operation_seconds=None, cached_runtime_supported=True, idle_poll_seconds=POLL_SECONDS)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        srv.bind((legacy.HOST, legacy.PORT))
        srv.listen(MAX_CLIENTS)
        srv.setblocking(False)
        _running = True
        print('[md-mcp] listening; %s; native calls remain synchronous' % _status['ui_mode'])
        stopping = False
        while True:
            if pump and not pump.pump():
                break
            if stopping and not clients:
                break
            reads = ([] if stopping else [srv]) + [c for c, s in clients.items() if s['output'] is None]
            writes = [c for c, s in clients.items() if s['output'] is not None]
            ready, writable, _ = select.select(reads, writes, [], POLL_SECONDS)
            if srv in ready:
                conn, _addr = srv.accept()
                conn.setblocking(False)
                if len(clients) >= MAX_CLIENTS:
                    conn.close()
                else:
                    clients[conn] = {'input': bytearray(), 'output': None, 'offset': 0,
                                     'deadline': time.monotonic() + legacy.REQUEST_READ_TIMEOUT}
                ready.remove(srv)
            for conn in ready:
                state = clients.get(conn)
                if state is None:
                    continue
                # A queued request expired during a long API call: do not execute it.
                if time.monotonic() > state['deadline']:
                    conn.close()
                    del clients[conn]
                    continue
                try:
                    data = conn.recv(min(65536, legacy.MAX_REQUEST_BYTES + 1 - len(state['input'])))
                    if not data:
                        conn.close()
                        del clients[conn]
                        continue
                    state['input'].extend(data)
                    newline = state['input'].find(b'\n')
                    if len(state['input']) > legacy.MAX_REQUEST_BYTES and (newline < 0 or newline > legacy.MAX_REQUEST_BYTES):
                        response, keep = {'id': None, 'error': 'request exceeds limit; not executed'}, True
                    elif newline >= 0:
                        response, keep = _dispatch(bytes(state['input'][:newline]), legacy)
                    else:
                        continue
                    encoded = (json.dumps(response, allow_nan=False) + '\n').encode()
                    if len(encoded) > MAX_RESPONSE_BYTES:
                        encoded = (json.dumps({'id': response.get('id'), 'error': 'response exceeds limit; operation may have completed'}) + '\n').encode()
                    state.update(output=encoded, input=None, deadline=time.monotonic() + 5.0)
                    if not keep:
                        stopping = True
                        for other in list(clients):
                            if other is not conn:
                                other.close()
                                del clients[other]
                        break
                except BlockingIOError:
                    pass
                except (OSError, ValueError):
                    conn.close()
                    clients.pop(conn, None)
            for conn in writable:
                state = clients.get(conn)
                if state is None:
                    continue
                try:
                    sent = conn.send(memoryview(state['output'])[state['offset']:state['offset'] + 65536])
                    state['offset'] += sent
                    if sent == 0 or state['offset'] == len(state['output']):
                        conn.close()
                        del clients[conn]
                except BlockingIOError:
                    pass
                except OSError:
                    conn.close()
                    clients.pop(conn, None)
            for conn, state in list(clients.items()):
                if time.monotonic() > state['deadline']:
                    conn.close()
                    del clients[conn]
    finally:
        for conn in clients:
            conn.close()
        srv.close()
        _running = False
        print('[md-mcp] stopped')
