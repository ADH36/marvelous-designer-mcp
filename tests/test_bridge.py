"""Tests for bridge framing and response validation without a live MD process."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from marvelous_designer_mcp import bridge


class FakeSocket:
    def __init__(self, response: bytes) -> None:
        self.response = bytearray(response)
        self.sent = bytearray()
        self.closed = False
        self.timeouts=[]

    def settimeout(self,value):
        self.timeouts.append(value)

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request_id = "request-test"
        self.uuid_patch = patch.object(bridge.uuid, "uuid4", return_value=SimpleNamespace(hex=self.request_id))
        self.uuid_patch.start()
        self.addCleanup(self.uuid_patch.stop)

    def call_with_response(self, response: bytes, **kwargs: object) -> tuple[object, FakeSocket]:
        fake = FakeSocket(response)
        with patch.object(bridge.socket, "create_connection", return_value=fake):
            result = bridge.call("ping", timeout=2, **kwargs)
        return result, fake

    def test_request_and_correlated_response_round_trip(self) -> None:
        response = json.dumps({"id": self.request_id, "result": {"pong": True}}).encode() + b"\n"
        result, fake = self.call_with_response(response)
        self.assertEqual(result, {"pong": True})
        self.assertTrue(fake.closed)
        sent=json.loads(fake.sent)
        deadline=sent.pop('expires_at_unix')
        self.assertGreater(deadline,time.time())
        self.assertLessEqual(deadline,time.time()+2)
        self.assertEqual(sent,{"id": self.request_id, "method": "ping", "params": {}})
        self.assertTrue(all(0<value<=2 for value in fake.timeouts))

    def test_rejects_mismatched_response_id(self) -> None:
        response = b'{"id":"wrong","result":true}\n'
        with self.assertRaisesRegex(bridge.BridgeError, "response ID does not match"):
            self.call_with_response(response)

    def test_rejects_non_object_response(self) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, "expected a JSON object"):
            self.call_with_response(b"[]\n")

    def test_rejects_missing_result(self) -> None:
        response = json.dumps({"id": self.request_id}).encode() + b"\n"
        with self.assertRaisesRegex(bridge.BridgeError, "missing result"):
            self.call_with_response(response)

    def test_surfaces_listener_error(self) -> None:
        response = json.dumps({"id": self.request_id, "error": "bad request"}).encode() + b"\n"
        with self.assertRaisesRegex(bridge.BridgeError, "bad request"):
            self.call_with_response(response)

    def test_rejects_response_over_limit(self) -> None:
        fake = FakeSocket(b"123456789")
        with self.assertRaisesRegex(bridge.BridgeError, "response exceeds 8 bytes"):
            bridge._recv_line(fake, max_bytes=8)

    def test_wraps_socket_errors(self) -> None:
        with patch.object(bridge.socket, "create_connection", side_effect=OSError("connection refused")):
            with self.assertRaisesRegex(bridge.BridgeError, "listener I/O failed: connection refused"):
                bridge.call("ping", timeout=2)

    def test_rejects_nonpositive_timeout(self) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, "timeout must be positive"):
            bridge.call("ping", timeout=0)


if __name__ == "__main__":
    unittest.main()
