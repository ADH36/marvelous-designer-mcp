"""Protocol tests for the MD-side listener; these don't require Marvelous Designer."""

from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch

import md_listener


class FakeConnection:
    def __init__(self, request: bytes = b"") -> None:
        self.request = bytearray(request)
        self.response = bytearray()

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def settimeout(self, _timeout: float) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        if not self.request:
            return b""
        chunk = bytes(self.request[:_size])
        del self.request[:_size]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.response.extend(data)


class TimedOutSocket(FakeConnection):
    def recv(self, _size: int) -> bytes:
        raise socket.timeout("timed out")


class ListenerTests(unittest.TestCase):
    def exchange(self, request: bytes) -> tuple[dict, bool]:
        conn = FakeConnection(request)
        keep_going = md_listener._serve_conn(conn)
        self.assertTrue(conn.response, "listener did not return a response")
        response = json.loads(bytes(conn.response).split(b"\n", 1)[0])
        return response, keep_going

    def test_ping_round_trip(self) -> None:
        response, keep_going = self.exchange(b'{"id":"test-1","method":"ping"}\n')
        self.assertTrue(keep_going)
        self.assertEqual(response, {"id": "test-1", "result": {"pong": True}})

    def test_rejects_non_object_json(self) -> None:
        response, keep_going = self.exchange(b'["ping"]\n')
        self.assertTrue(keep_going)
        self.assertEqual(response["id"], None)
        self.assertEqual(response["error"], "request must be a JSON object")

    def test_rejects_bad_json(self) -> None:
        response, _ = self.exchange(b'{bad json}\n')
        self.assertEqual(response["id"], None)
        self.assertIn("bad json:", response["error"])

    def test_rejects_missing_or_invalid_fields(self) -> None:
        cases = (
            (b'{"method":"ping"}\n', "request id must be a non-empty string"),
            (b'{"id":"x"}\n', "request method must be a non-empty string"),
            (b'{"id":"x","method":"ping","params":[]}\n', "request params must be a JSON object"),
        )
        for request, expected_error in cases:
            with self.subTest(request=request):
                response, _ = self.exchange(request)
                self.assertEqual(response["error"], expected_error)

    def test_unknown_method_returns_correlated_error(self) -> None:
        response, _ = self.exchange(b'{"id":"test-2","method":"not_a_method"}\n')
        self.assertEqual(response, {"id": "test-2", "error": "unknown method: not_a_method"})

    def test_shutdown_acknowledges_and_stops_loop(self) -> None:
        response, keep_going = self.exchange(b'{"id":"test-3","method":"shutdown"}\n')
        self.assertFalse(keep_going)
        self.assertEqual(response, {"id": "test-3", "result": {"bye": True}})

    def test_oversized_request_is_rejected(self) -> None:
        with patch.object(md_listener, "MAX_REQUEST_BYTES", 8):
            response, keep_going = self.exchange(b"123456789")
        self.assertTrue(keep_going)
        self.assertIn("request line exceeds 8 bytes", response["error"])

    def test_incomplete_request_times_out(self) -> None:
        conn = TimedOutSocket()
        with patch.object(md_listener, "REQUEST_READ_TIMEOUT", 0.01):
            keep_going = md_listener._serve_conn(conn)
        self.assertTrue(keep_going)
        response = json.loads(bytes(conn.response).split(b"\n", 1)[0])
        self.assertIn("timed out", response["error"])

    def test_execute_without_result_does_not_reuse_previous_result(self) -> None:
        with patch.dict(md_listener._persistent_globals, {"__name__": "__md_mcp__"}, clear=True):
            first = md_listener._handle_execute_python({"code": "result = 42"})
            second = md_listener._handle_execute_python({"code": "other_value = 7"})
        self.assertEqual(first["result"], 42)
        self.assertIsNone(second["result"])


if __name__ == "__main__":
    unittest.main()
