import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cmk_addons.plugins.rustfs_quota.lib.client import BucketQuota, fetch_bucket_quota, parse_quota_stats

ACCESS_KEY = "AKTESTKEY"
SECRET_KEY = "SECRET-THAT-MUST-NEVER-LEAK"


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []

    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/xml") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        _Handler.seen.append({k.lower(): v for k, v in self.headers.items()} | {"path": self.path})
        bucket = self.path.rsplit("/", 1)[-1]
        if f"Credential={ACCESS_KEY}/" not in self.headers.get("Authorization", ""):
            return self._send(403, b"<Error><Code>InvalidAccessKeyId</Code></Error>")
        if bucket == "denied":
            return self._send(403, b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>")
        if bucket == "boom":
            return self._send(500, b"<Error><Code>InternalError</Code></Error>")
        if bucket == "garbage":
            return self._send(200, b"this is not json", "text/plain")
        if bucket == "slow":
            time.sleep(1.5)
        if bucket == "noquota":
            return self._send(200, json.dumps({"bucket": bucket, "quota_limit": 0, "current_usage": 5}).encode(), "application/json")
        if bucket == "wrongbucket":
            return self._send(200, json.dumps({"bucket": "other", "quota_limit": 1, "current_usage": 1}).encode(), "application/json")
        payload = {
            "bucket": bucket,
            "quota_limit": 10737418240,
            "current_usage": 3145752,
            "remaining_quota": 10734272488,
            "usage_percentage": 0.0293,
        }
        self._send(200, json.dumps(payload).encode(), "application/json")


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

    def fetch(self, bucket: str, **kwargs) -> BucketQuota:
        return fetch_bucket_quota(self.endpoint + "/", ACCESS_KEY, SECRET_KEY, bucket, timeout=kwargs.pop("timeout", 5), **kwargs)

    def test_reading(self) -> None:
        reading = self.fetch("dev-kopiur-backup")
        self.assertTrue(reading.ok)
        self.assertEqual((reading.quota_limit, reading.current_usage), (10737418240, 3145752))
        self.assertIsNone(reading.error)

    def test_request_is_signed_and_hits_the_route(self) -> None:
        _Handler.seen.clear()
        self.fetch("dev-kopiur-backup")
        request = _Handler.seen[-1]
        self.assertEqual(request["path"], "/rustfs/admin/v3/quota-stats/dev-kopiur-backup")
        self.assertTrue(request["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKTESTKEY/"))
        self.assertIn("SignedHeaders=host;x-amz-content-sha256;x-amz-date", request["authorization"])
        self.assertNotIn(SECRET_KEY, json.dumps(request))

    def test_no_quota(self) -> None:
        reading = self.fetch("noquota")
        self.assertTrue(reading.ok)
        self.assertEqual((reading.quota_limit, reading.current_usage), (0, 5))

    def test_failures_are_reported_not_zero(self) -> None:
        for bucket, expected in (
            ("denied", "HTTP 403 AccessDenied"),
            ("boom", "HTTP 500 InternalError"),
            ("garbage", "unexpected response: not JSON"),
            ("wrongbucket", "unexpected response: wrong bucket"),
        ):
            with self.subTest(bucket=bucket):
                reading = self.fetch(bucket)
                self.assertFalse(reading.ok)
                self.assertEqual(reading.error, expected)
                self.assertIsNone(reading.current_usage)

    def test_timeout(self) -> None:
        reading = self.fetch("slow", timeout=0.3)
        self.assertFalse(reading.ok)
        self.assertIn("connection failed", reading.error or "")

    def test_wrong_credentials(self) -> None:
        reading = fetch_bucket_quota(self.endpoint, "OTHERKEY", SECRET_KEY, "b", timeout=5)
        self.assertFalse(reading.ok)
        self.assertEqual(reading.error, "HTTP 403 InvalidAccessKeyId")

    def test_unreachable_endpoint(self) -> None:
        reading = fetch_bucket_quota("http://127.0.0.1:9", ACCESS_KEY, SECRET_KEY, "b", timeout=2)
        self.assertFalse(reading.ok)
        self.assertIn("connection failed", reading.error or "")

    def test_secret_never_appears_in_any_result(self) -> None:
        for bucket in ("dev-kopiur-backup", "denied", "boom", "garbage", "noquota"):
            self.assertNotIn(SECRET_KEY, self.fetch(bucket).to_json())


class ParseQuotaStats(unittest.TestCase):
    def test_rejects_bad_numbers(self) -> None:
        for body in (
            {"bucket": "b", "current_usage": -1},
            {"bucket": "b", "current_usage": "5"},
            {"bucket": "b", "current_usage": True},
            {"bucket": "b", "current_usage": 1, "quota_limit": "x"},
            {"bucket": "b"},
            [],
        ):
            with self.subTest(body=body):
                self.assertFalse(parse_quota_stats("b", json.dumps(body).encode()).ok)

    def test_null_quota_means_no_quota(self) -> None:
        reading = parse_quota_stats("b", json.dumps({"bucket": "b", "current_usage": 7, "quota_limit": None}).encode())
        self.assertTrue(reading.ok)
        self.assertEqual(reading.quota_limit, 0)


if __name__ == "__main__":
    unittest.main()
