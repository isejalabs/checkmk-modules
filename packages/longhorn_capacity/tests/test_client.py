import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cmk_addons.plugins.longhorn_capacity.lib.client import DiskReading, fetch_node_disks, parse_nodes

GIB = 1024**3


def _node(name: str, zone: str = "pve1", ready: bool = True, disks: dict | None = None) -> dict:
    return {
        "id": name,
        "name": name,
        "zone": zone,
        "conditions": {"Ready": {"status": "True" if ready else "False"}},
        "disks": disks
        if disks is not None
        else {
            "default-disk-1": {
                "path": "/var/mnt/longhorn",
                "storageMaximum": 20 * GIB,
                "storageAvailable": 15 * GIB,
                "storageReserved": 2 * GIB,
                "storageScheduled": 3 * GIB,
                "allowScheduling": True,
                "conditions": {"Ready": {"status": "True"}, "Schedulable": {"status": "True"}},
                "healthDataLastCollectedAt": "2026-10-16 06:00:00 +0200 CEST",
            }
        },
    }


class _Handler(BaseHTTPRequestHandler):
    response: dict = {"status": 200, "body": b"{}", "content_type": "application/json"}
    seen: list[str] = []

    def log_message(self, *args) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802
        _Handler.seen.append(self.path)
        if self.path == "/slow/v1/nodes":
            time.sleep(1.5)
        resp = _Handler.response
        self.send_response(resp["status"])
        self.send_header("Content-Type", resp["content_type"])
        self.send_header("Content-Length", str(len(resp["body"])))
        self.end_headers()
        self.wfile.write(resp["body"])


class ClientAgainstStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def set_response(self, body: dict | list, status: int = 200) -> None:
        _Handler.response = {"status": status, "body": json.dumps(body).encode(), "content_type": "application/json"}

    def test_reading_two_nodes(self) -> None:
        self.set_response({"data": [_node("work-01"), _node("work-02", zone="pve4")]})
        readings = fetch_node_disks(self.endpoint, timeout=5)
        self.assertTrue(all(r.ok for r in readings))
        self.assertEqual({(r.node, r.disk) for r in readings}, {("work-01", "default-disk-1"), ("work-02", "default-disk-1")})
        reading = next(r for r in readings if r.node == "work-01")
        self.assertEqual(reading.storage_maximum, 20 * GIB)
        self.assertEqual(reading.storage_available, 15 * GIB)
        self.assertEqual(reading.storage_reserved, 2 * GIB)
        self.assertEqual(reading.storage_scheduled, 3 * GIB)
        self.assertTrue(reading.allow_scheduling)
        self.assertTrue(reading.disk_ready)
        self.assertTrue(reading.disk_schedulable)
        self.assertTrue(reading.node_ready)
        self.assertEqual(reading.zone, "pve1")

    def test_requests_the_right_path(self) -> None:
        _Handler.seen.clear()
        self.set_response({"data": [_node("work-01")]})
        fetch_node_disks(self.endpoint, timeout=5)
        self.assertEqual(_Handler.seen[-1], "/v1/nodes")

    def test_node_not_ready(self) -> None:
        self.set_response({"data": [_node("work-01", ready=False)]})
        reading = fetch_node_disks(self.endpoint, timeout=5)[0]
        self.assertFalse(reading.node_ready)

    def test_disk_not_schedulable(self) -> None:
        disks = {
            "d1": {
                "path": "/var/mnt/longhorn",
                "storageMaximum": 10 * GIB,
                "storageAvailable": 1 * GIB,
                "storageReserved": 0,
                "storageScheduled": 9 * GIB,
                "allowScheduling": False,
                "conditions": {"Ready": {"status": "True"}, "Schedulable": {"status": "False"}},
            }
        }
        self.set_response({"data": [_node("work-01", disks=disks)]})
        reading = fetch_node_disks(self.endpoint, timeout=5)[0]
        self.assertTrue(reading.disk_ready)
        self.assertFalse(reading.disk_schedulable)
        self.assertFalse(reading.allow_scheduling)

    def test_failures_are_reported_not_zero(self) -> None:
        cases = [
            ({"status": 500, "body": b"boom", "content_type": "text/plain"}, "HTTP 500"),
            ({"status": 200, "body": b"not json", "content_type": "text/plain"}, "unexpected response: not JSON"),
            ({"status": 200, "body": b"[]", "content_type": "application/json"}, "unexpected response: no node list"),
            ({"status": 200, "body": b'{"data": []}', "content_type": "application/json"}, "unexpected response: no disks found"),
        ]
        for response, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                _Handler.response = response
                readings = fetch_node_disks(self.endpoint, timeout=5)
                self.assertEqual(len(readings), 1)
                self.assertFalse(readings[0].ok)
                self.assertEqual(readings[0].error, expected_error)

    def test_timeout(self) -> None:
        self.set_response({"data": [_node("work-01")]})
        reading = fetch_node_disks(f"{self.endpoint}/slow", timeout=0.3)[0]
        self.assertFalse(reading.ok)
        self.assertIn("connection failed", reading.error or "")

    def test_unreachable_endpoint(self) -> None:
        reading = fetch_node_disks("http://127.0.0.1:9", timeout=2)[0]
        self.assertFalse(reading.ok)
        self.assertIn("connection failed", reading.error or "")


class ParseNodes(unittest.TestCase):
    def test_skips_malformed_nodes_and_disks(self) -> None:
        body = json.dumps(
            {
                "data": [
                    "not a dict",
                    {"name": "", "disks": {}},
                    {"disks": "not a dict"},
                    _node("work-01"),
                ]
            }
        ).encode()
        readings = parse_nodes(body)
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].node, "work-01")

    def test_rejects_negative_or_non_numeric_bytes(self) -> None:
        disks = {
            "d1": {
                "path": "/x",
                "storageMaximum": -1,
                "storageAvailable": "5",
                "storageReserved": True,
                "storageScheduled": 1.5,
            }
        }
        reading = parse_nodes(json.dumps({"data": [_node("work-01", disks=disks)]}).encode())[0]
        self.assertIsNone(reading.storage_maximum)
        self.assertIsNone(reading.storage_available)
        self.assertIsNone(reading.storage_reserved)
        self.assertEqual(reading.storage_scheduled, 1)  # a whole float is accepted and truncated

    def test_to_json_round_trips(self) -> None:
        reading = DiskReading(node="n", disk="d", ok=True, storage_maximum=1, storage_available=1)
        self.assertEqual(json.loads(reading.to_json())["node"], "n")


if __name__ == "__main__":
    unittest.main()
