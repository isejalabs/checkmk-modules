import datetime
import unittest
from unittest import mock

from cmk_addons.plugins.rustfs_quota.lib import sigv4

try:
    import botocore.auth
    import botocore.awsrequest
    import botocore.credentials
except ImportError:  # pragma: no cover - botocore is only needed for the cross-check
    botocore = None

ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
MOMENT = datetime.datetime(2015, 8, 30, 12, 36, 0, tzinfo=datetime.timezone.utc)


class AwsTestVector(unittest.TestCase):
    def test_get_vanilla(self) -> None:
        """The `get-vanilla` case of the AWS Signature Version 4 test suite."""
        value = sigv4.authorization(
            method="GET",
            canonical_uri="/",
            canonical_query="",
            headers={"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"},
            payload_hash=sigv4.EMPTY_PAYLOAD_SHA256,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            region="us-east-1",
            service="service",
            amz_date="20150830T123600Z",
        )
        self.assertEqual(
            value,
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
            "SignedHeaders=host;x-amz-date, "
            "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
        )

    def test_signing_key_vector(self) -> None:
        """Published example: derived key for 20120215, us-east-1, iam."""
        key = sigv4.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam")
        self.assertEqual(key.hex(), "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d")


class SignRequest(unittest.TestCase):
    def test_headers_and_signed_header_list(self) -> None:
        headers = sigv4.sign_request(
            "GET", "https://fiona.example.net:9000/rustfs/admin/v3/quota-stats/b1", "AK", "SK", now=MOMENT
        )
        self.assertEqual(headers["Host"], "fiona.example.net:9000")
        self.assertEqual(headers["x-amz-date"], "20150830T123600Z")
        self.assertIn("SignedHeaders=host;x-amz-content-sha256;x-amz-date", headers["Authorization"])
        self.assertIn("Credential=AK/20150830/us-east-1/s3/aws4_request", headers["Authorization"])
        self.assertNotIn("SK", headers["Authorization"])

    def test_query_parameters_are_sorted(self) -> None:
        self.assertEqual(sigv4.canonical_query_string("b=2&a=1&a=0"), "a=0&a=1&b=2")
        self.assertEqual(sigv4.canonical_query_string("list-type=2"), "list-type=2")
        self.assertEqual(sigv4.canonical_query_string("x=a b"), "x=a%20b")

    def test_userinfo_is_not_part_of_the_host(self) -> None:
        headers = sigv4.sign_request("GET", "https://user:pw@host.example:9000/x", "AK", "SK", now=MOMENT)
        self.assertEqual(headers["Host"], "host.example:9000")


@unittest.skipIf(botocore is None, "botocore not available for the cross-check")
class CrossCheckWithBotocore(unittest.TestCase):
    """The same request signed by botocore, an independent implementation, must give the same Authorization."""

    def _botocore_authorization(self, url: str) -> str:
        class FrozenDatetime(datetime.datetime):
            @classmethod
            def utcnow(cls):  # type: ignore[override]
                return cls(2026, 10, 8, 12, 0, 0)

        request = botocore.awsrequest.AWSRequest(
            method="GET", url=url, headers={"x-amz-content-sha256": sigv4.EMPTY_PAYLOAD_SHA256}
        )
        auth = botocore.auth.SigV4Auth(botocore.credentials.Credentials("AK", "SK"), "s3", "us-east-1")
        with mock.patch.object(botocore.auth.datetime, "datetime", FrozenDatetime):
            auth.add_auth(request)
        return request.headers["Authorization"]

    def test_matches_botocore(self) -> None:
        moment = datetime.datetime(2026, 10, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
        for url in (
            "https://fiona.example.net:9000/rustfs/admin/v3/quota-stats/dev-kopiur-backup",
            "https://fiona.example.net:9000/rustfs/admin/v3/info",
            "http://127.0.0.1:18099/bucket?list-type=2",
            "https://fiona.example.net/rustfs/admin/v3/quota-stats/b.with.dots",
        ):
            with self.subTest(url=url):
                mine = sigv4.sign_request("GET", url, "AK", "SK", now=moment)["Authorization"]
                self.assertEqual(mine, self._botocore_authorization(url))


if __name__ == "__main__":
    unittest.main()
