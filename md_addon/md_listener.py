"""Marvelous Designer side MCP listener (serial API calls on MD's main thread).

MD's embedded Python (3.11) does not give CPU to background daemon threads, so
the old thread-based server never actually bound a socket. Version 0.8 delegates
to cooperative_listener: nonblocking sockets and Windows message dispatch while
idle. API calls remain synchronous. Idle responsiveness was observed on one
Windows/MD 2026.0.315 host; this does not establish responsiveness during calls.
The MCP shutdown_listener tool stops the loop; no operation is automatically replayed.

How to start it (paste into MD's Python Editor, or use scripts/md_start_listener.py):

    import sys
    sys.path.insert(0, r"C:\\path\\to\\marvelous-designer-mcp\\md_addon")
    import importlib, md_listener
    importlib.reload(md_listener)          # pick up edits without restarting MD
    md_listener.serve_forever()

Wire protocol: one JSON object per line over TCP 127.0.0.1:7421.
    Request:  {"id": str, "method": str, "params": dict}
    Response: {"id": str, "result": any}  or  {"id": str, "error": str}

Note: MD's API is exposed as importable modules (import_api, export_api,
fabric_api, pattern_api, utility_api, ...), NOT as globals. Code sent via
execute_python must `import` whatever it needs and bind its return value to a
name called `result`.
"""
from __future__ import annotations

import contextlib
import io
import json
import socket
import time
import traceback

HOST = "127.0.0.1"
PORT = 7421
MAX_REQUEST_BYTES = 1024 * 1024
REQUEST_READ_TIMEOUT = 5.0

# Persists across execute_python calls within one listener session.
_persistent_globals: dict = {"__name__": "__md_mcp__"}


def _handle_ping(_params: dict) -> dict:
    return {"pong": True}


class _BoundedOutput(io.StringIO):
    def write(self, value):
        super().write(value[:max(0, 65536 - self.tell())])
        return len(value)


def _execute_code(code, namespace) -> dict:
    out, err = _BoundedOutput(), _BoundedOutput()
    result = None
    error = None
    try:
        # Don't accidentally return the previous call's result when this code
        # doesn't assign one.
        namespace.pop("result", None)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(code, namespace)
        result = namespace.get("result")
    except Exception:
        error = traceback.format_exc()
    payload = {"stdout": out.getvalue(), "stderr": err.getvalue(), "result": result, "error": error}
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError):
        payload["result"] = repr(result)[:65536]
    return payload


def _handle_execute_python(params: dict) -> dict:
    code = params.get('code', '')
    if not isinstance(code, str):
        raise ValueError('code must be a string')
    return _execute_code(code, _persistent_globals)


HANDLERS = {
    "ping": _handle_ping,
    "execute_python": _handle_execute_python,
}


def _read_line(conn: socket.socket) -> bytes | None:
    """Read one bounded JSON line so an idle local client cannot hold MD's GUI."""
    buf = bytearray()
    deadline = time.monotonic() + REQUEST_READ_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"request line timed out after {REQUEST_READ_TIMEOUT:g}s")
        conn.settimeout(remaining)
        chunk = conn.recv(min(65536, MAX_REQUEST_BYTES + 1 - len(buf)))
        if not chunk:
            return None
        buf.extend(chunk)
        newline = buf.find(b"\n")
        if newline >= 0:
            if newline > MAX_REQUEST_BYTES:
                raise ValueError(f"request line exceeds {MAX_REQUEST_BYTES} bytes")
            return bytes(buf[:newline])
        if len(buf) > MAX_REQUEST_BYTES:
            raise ValueError(f"request line exceeds {MAX_REQUEST_BYTES} bytes")


def _serve_conn(conn: socket.socket) -> bool:
    """Handle one request on one connection. Returns False iff shutdown was requested.

    The MCP-side bridge opens a fresh connection per call, so one request per
    connection is all that's needed (and avoids odd Windows socket-reuse issues).
    """
    with conn:
        try:
            line = _read_line(conn)
        except (socket.timeout, TimeoutError, ValueError) as e:
            resp = {"id": None, "error": str(e)}
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True
        if line is None:
            return True  # client connected then closed without sending anything
        try:
            req = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            conn.sendall((json.dumps({"id": None, "error": f"bad json: {e}"}) + "\n").encode())
            return True

        if not isinstance(req, dict):
            conn.sendall((json.dumps({"id": None, "error": "request must be a JSON object"}) + "\n").encode())
            return True

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        if not isinstance(req_id, str) or not req_id:
            conn.sendall((json.dumps({"id": None, "error": "request id must be a non-empty string"}) + "\n").encode())
            return True
        if not isinstance(method, str) or not method:
            conn.sendall((json.dumps({"id": req_id, "error": "request method must be a non-empty string"}) + "\n").encode())
            return True
        if params is None:
            params = {}
        if not isinstance(params, dict):
            conn.sendall((json.dumps({"id": req_id, "error": "request params must be a JSON object"}) + "\n").encode())
            return True

        if method == "shutdown":
            conn.sendall((json.dumps({"id": req_id, "result": {"bye": True}}) + "\n").encode())
            return False

        handler = HANDLERS.get(method)
        if handler is None:
            resp = {"id": req_id, "error": f"unknown method: {method}"}
        else:
            try:
                resp = {"id": req_id, "result": handler(params)}
            except Exception:
                resp = {"id": req_id, "error": traceback.format_exc()}
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        return True


def serve_forever() -> None:
    """Use serial native API execution with cooperative idle Windows dispatch."""
    import sys
    import cooperative_listener
    cooperative_listener.serve_forever(sys.modules[__name__])


def serve_blocking_legacy() -> None:
    """Blocking listener loop. Call this from MD's Python Editor.

    Blocks the MD GUI until a client sends {"method": "shutdown"}.
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(4)
    except OSError as e:
        # Most commonly: port already in use (a previous listener is still running).
        raise RuntimeError(f"[md-mcp] cannot bind {HOST}:{PORT} ({e}) -- is a listener already running?") from e

    print(f"[md-mcp] listening (blocking) on {HOST}:{PORT} -- MD GUI frozen until a client sends 'shutdown'")
    try:
        with srv:
            while True:
                conn, _addr = srv.accept()
                try:
                    keep_going = _serve_conn(conn)
                except Exception:
                    traceback.print_exc()
                    keep_going = True
                if not keep_going:
                    break
    finally:
        print("[md-mcp] stopped")


if __name__ == "__main__":
    serve_forever()
