import json
import socket
import uuid
import time
import math
from threading import Lock
from typing import Any

from .config import MD_HOST, MD_MAX_RESPONSE_BYTES, MD_PORT, MD_TIMEOUT


class BridgeError(RuntimeError):
    pass


_call_lock = Lock()


def _recv_line(sock: socket.socket, *, max_bytes: int = MD_MAX_RESPONSE_BYTES, deadline: float | None = None) -> bytes:
    """Read one bounded JSON line from the listener."""
    if max_bytes < 1:
        raise BridgeError("maximum response size must be positive")
    buf = bytearray()
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError('response deadline exceeded; execution may still be running')
            sock.settimeout(remaining)
        chunk = sock.recv(min(65536, max_bytes + 1 - len(buf)))
        if not chunk:
            raise BridgeError("connection closed before response")
        buf.extend(chunk)
        newline = buf.find(b"\n")
        if newline >= 0:
            if newline > max_bytes:
                raise BridgeError(f"response exceeds {max_bytes} bytes")
            return bytes(buf[:newline])
        if len(buf) > max_bytes:
            raise BridgeError(f"response exceeds {max_bytes} bytes")


def call(method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
    """Send a JSON request to the MD listener and return the result.

    Wire format: one JSON object per line.
    Request:  {"id": str, "method": str, "params": dict}
    Response: {"id": str, "result": any} or {"id": str, "error": str}
    """
    effective_timeout = MD_TIMEOUT if timeout is None else timeout
    if not math.isfinite(effective_timeout) or effective_timeout <= 0:
        raise BridgeError("timeout must be positive")
    payload = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {},
        "expires_at_unix": time.time() + effective_timeout,
    }
    data = (json.dumps(payload) + "\n").encode("utf-8")
    if len(data) - 1 > 1024 * 1024:
        raise BridgeError('request exceeds the listener 1 MiB limit; not sent')

    if not _call_lock.acquire(blocking=False):
        raise BridgeError('another request is active in this server; this request was not sent')
    try:
        deadline = time.monotonic() + effective_timeout
        with socket.create_connection((MD_HOST, MD_PORT), timeout=effective_timeout) as sock:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            sock.sendall(data)
            line = _recv_line(sock, deadline=deadline)
    except OSError as e:
        raise BridgeError(f"listener I/O failed: {e}") from e
    finally:
        _call_lock.release()

    try:
        resp = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BridgeError(f"invalid JSON from listener: {e}") from e

    if not isinstance(resp, dict):
        raise BridgeError("invalid response from listener: expected a JSON object")
    if resp.get("id") != payload["id"]:
        raise BridgeError("response ID does not match request ID")
    if "error" in resp:
        raise BridgeError(str(resp["error"]))
    if "result" not in resp:
        raise BridgeError("invalid response from listener: missing result")
    return resp.get("result")
