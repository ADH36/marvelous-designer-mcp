import json
import socket
import uuid
from typing import Any

from .config import MD_HOST, MD_MAX_RESPONSE_BYTES, MD_PORT, MD_TIMEOUT


class BridgeError(RuntimeError):
    pass


def _recv_line(sock: socket.socket, *, max_bytes: int = MD_MAX_RESPONSE_BYTES) -> bytes:
    """Read one bounded JSON line from the listener."""
    if max_bytes < 1:
        raise BridgeError("maximum response size must be positive")
    buf = bytearray()
    while True:
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
    payload = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {},
    }
    data = (json.dumps(payload) + "\n").encode("utf-8")

    effective_timeout = MD_TIMEOUT if timeout is None else timeout
    if effective_timeout <= 0:
        raise BridgeError("timeout must be positive")
    try:
        with socket.create_connection((MD_HOST, MD_PORT), timeout=effective_timeout) as sock:
            sock.sendall(data)
            line = _recv_line(sock)
    except OSError as e:
        raise BridgeError(f"listener I/O failed: {e}") from e

    try:
        resp = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as e:
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
