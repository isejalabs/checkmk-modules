import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cmk_addons.plugins.longhorn_capacity.lib.client import (
    DiskReading,
    fetch_kubernetes,
    fetch_node_disks,
    parse_crd_instance_managers,
    parse_crd_nodes,
    parse_crd_volumes,
    parse_nodes,
)

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


def _condition(kind: str, status: bool) -> dict:
    return {"type": kind, "status": "True" if status else "False", "message": "", "reason": ""}


def _crd_node(name: str, ready: bool = True) -> dict:
    """A nodes.longhorn.io object shaped like Longhorn 1.13's (fields not used by the client left out)."""
    return {
        "metadata": {"name": name, "namespace": "longhorn-system"},
        "spec": {
            "allowScheduling": True,
            "disks": {
                "default-disk-1": {
                    "allowScheduling": True,
                    "diskType": "filesystem",
                    "path": "/var/mnt/longhorn",
                    "storageReserved": 2 * GIB,
                }
            },
            "name": name,
        },
        "status": {
            "conditions": [_condition("Ready", ready), _condition("Schedulable", True)],
            "diskStatus": {
                "default-disk-1": {
                    "conditions": [_condition("Ready", True), _condition("Schedulable", True)],
                    "diskPath": "/var/mnt/longhorn",
                    "healthDataLastCollectedAt": "2026-11-09T21:21:23Z",
                    "storageAvailable": 15 * GIB,
                    "storageMaximum": 20 * GIB,
                    "storageScheduled": 3 * GIB,
                }
            },
            "zone": "pve1",
        },
    }


def _crd_volume(name: str, state: str = "attached", robustness: str = "healthy") -> dict:
    return {
        "metadata": {"name": name},
        "spec": {"numberOfReplicas": 2, "size": str(2 * GIB)},
        "status": {
            "actualSize": 1 * GIB,
            "currentNodeID": "work-01" if state == "attached" else "",
            "kubernetesStatus": {"namespace": "unifi", "pvcName": "unifi-mongodb"},
            "robustness": robustness,
            "state": state,
        },
    }


def _crd_instance_manager(name: str, node: str, state: str = "running") -> dict:
    return {
        "metadata": {"name": name},
        "spec": {"dataEngine": "v1", "nodeID": node, "type": "aio"},
        "status": {"currentState": state},
    }


class _Handler(BaseHTTPRequestHandler):
    response: dict = {"status": 200, "body": b"{}", "content_type": "application/json"}
    # Responses by path, for the Kubernetes API tests; a path not in here gets `response`.
    by_path: dict[str, dict] = {}
    seen: list[str] = []
    headers_seen: list[dict] = []

    def log_message(self, *args) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802
        _Handler.seen.append(self.path)
        _Handler.headers_seen.append(dict(self.headers))
        if self.path == "/slow/v1/nodes":
            time.sleep(1.5)
        resp = _Handler.by_path.get(self.path, _Handler.response)
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


_CRD = "/apis/longhorn.io/v1beta2/namespaces/longhorn-system/"


class KubernetesAgainstStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.api_server = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def tearDown(self) -> None:
        _Handler.by_path = {}

    def serve(self, resource: str, body: dict, status: int = 200) -> None:
        _Handler.by_path[_CRD + resource] = {
            "status": status,
            "body": json.dumps(body).encode(),
            "content_type": "application/json",
        }

    def serve_all(self) -> None:
        self.serve("nodes", {"kind": "NodeList", "items": [_crd_node("work-01"), _crd_node("work-02", ready=False)]})
        self.serve("volumes", {"kind": "VolumeList", "items": [_crd_volume("pvc-1"), _crd_volume("pvc-2", "detached", "unknown")]})
        self.serve(
            "instancemanagers",
            {"kind": "InstanceManagerList", "items": [_crd_instance_manager("im-1", "work-01"), _crd_instance_manager("im-2", "work-02", "error")]},
        )

    def test_reads_the_three_lists_with_the_token(self) -> None:
        _Handler.seen.clear()
        _Handler.headers_seen.clear()
        self.serve_all()
        readings = fetch_kubernetes(self.api_server, "s3cr3t", timeout=5)
        self.assertEqual(_Handler.seen, [_CRD + "nodes", _CRD + "volumes", _CRD + "instancemanagers"])
        self.assertTrue(all(h.get("Authorization") == "Bearer s3cr3t" for h in _Handler.headers_seen))

        self.assertEqual([(d.node, d.path) for d in readings.disks], [("work-01", "/var/mnt/longhorn"), ("work-02", "/var/mnt/longhorn")])
        disk = readings.disks[0]
        self.assertEqual(
            (disk.storage_maximum, disk.storage_available, disk.storage_reserved, disk.storage_scheduled),
            (20 * GIB, 15 * GIB, 2 * GIB, 3 * GIB),
        )
        self.assertTrue(disk.ok and disk.disk_ready and disk.disk_schedulable and disk.node_ready and disk.allow_scheduling)
        self.assertEqual(disk.zone, "pve1")
        self.assertEqual(disk.collected_at, "2026-11-09T21:21:23Z")
        self.assertFalse(readings.disks[1].node_ready)

        volume = readings.volumes[0]
        self.assertEqual((volume.name, volume.state, volume.robustness), ("pvc-1", "attached", "healthy"))
        self.assertEqual((volume.size, volume.actual_size, volume.replicas), (2 * GIB, 1 * GIB, 2))
        self.assertEqual(volume.pvc, "unifi/unifi-mongodb")
        self.assertEqual(volume.node, "work-01")

        self.assertEqual([(m.name, m.node, m.state) for m in readings.instance_managers], [("im-1", "work-01", "running"), ("im-2", "work-02", "error")])

    def test_same_disk_reading_as_the_longhorn_api(self) -> None:
        # The services keep their items and history when a host switches from the UI route to the Kubernetes API.
        self.serve_all()
        from_crd = fetch_kubernetes(self.api_server, "t", timeout=5).disks[0]
        from_api = parse_nodes(json.dumps({"data": [_node("work-01")]}).encode())[0]
        self.assertEqual(from_crd.node, from_api.node)
        self.assertEqual(from_crd.path, from_api.path)
        for field in ("storage_maximum", "storage_available", "storage_reserved", "storage_scheduled", "disk_ready", "node_ready"):
            self.assertEqual(getattr(from_crd, field), getattr(from_api, field), field)

    def test_forbidden_is_reported_per_list_without_the_token(self) -> None:
        self.serve_all()
        self.serve("volumes", {"kind": "Status", "code": 403}, status=403)
        readings = fetch_kubernetes(self.api_server, "s3cr3t", timeout=5)
        self.assertTrue(all(d.ok for d in readings.disks))
        self.assertEqual(len(readings.volumes), 1)
        self.assertFalse(readings.volumes[0].ok)
        self.assertEqual(readings.volumes[0].error, "HTTP 403")
        self.assertNotIn("s3cr3t", readings.volumes[0].to_json())

    def test_unreachable_api_server_fails_every_list(self) -> None:
        readings = fetch_kubernetes("http://127.0.0.1:9", "t", timeout=2)
        for failed in (readings.disks[0], readings.volumes[0], readings.instance_managers[0]):
            self.assertFalse(failed.ok)
            self.assertIn("connection failed", failed.error or "")


class ParseCrds(unittest.TestCase):
    def test_not_a_list(self) -> None:
        self.assertEqual(parse_crd_nodes(b"{}")[0].error, "unexpected response: no node list")
        self.assertEqual(parse_crd_volumes(b"nope")[0].error, "unexpected response: no volume list")
        self.assertEqual(parse_crd_instance_managers(b"[]")[0].error, "unexpected response: no instance manager list")

    def test_no_volumes_is_valid_but_no_instance_managers_is_not(self) -> None:
        self.assertEqual(parse_crd_volumes(b'{"items": []}'), [])
        self.assertFalse(parse_crd_instance_managers(b'{"items": []}')[0].ok)
        self.assertFalse(parse_crd_nodes(b'{"items": []}')[0].ok)

    def test_skips_malformed_objects(self) -> None:
        body = json.dumps({"items": ["x", {"metadata": {}}, {"metadata": {"name": "n"}, "spec": {"disks": "x"}}, _crd_node("work-01")]})
        readings = parse_crd_nodes(body.encode())
        self.assertEqual([r.node for r in readings], ["work-01"])

    def test_disk_without_status_has_no_values(self) -> None:
        node = _crd_node("work-01")
        node["status"]["diskStatus"] = {}
        reading = parse_crd_nodes(json.dumps({"items": [node]}).encode())[0]
        self.assertTrue(reading.ok)
        self.assertIsNone(reading.storage_maximum)
        self.assertIsNone(reading.disk_ready)

    def test_volume_without_pvc(self) -> None:
        volume = _crd_volume("pvc-1")
        volume["status"]["kubernetesStatus"] = {}
        self.assertIsNone(parse_crd_volumes(json.dumps({"items": [volume]}).encode())[0].pvc)


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
