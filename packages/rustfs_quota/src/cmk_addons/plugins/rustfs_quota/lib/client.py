"""Query the RustFS bucket quota statistics. Standard library only; the request is signed with `sigv4`.

The route `GET /rustfs/admin/v3/quota-stats/<bucket>` returns the bucket's quota limit and its logical usage, for
example `{"bucket": "b", "quota_limit": 10737418240, "current_usage": 3145752, "remaining_quota": 10734272488,
"usage_percentage": 0.0293}`. It needs the bucket-scoped `s3:GetBucketQuota` permission only.

A reading that cannot be obtained is reported as such (`ok = False` with a reason), never as zero.
"""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from cmk_addons.plugins.rustfs_quota.lib.sigv4 import sign_request

_ERROR_CODE = re.compile(rb"<Code>([A-Za-z0-9_.-]{1,64})</Code>")
_MAX_BODY = 1_000_000


@dataclass(frozen=True)
class BucketQuota:
    """The outcome of one quota-statistics request."""

    bucket: str
    ok: bool
    quota_limit: int | None = None  # bytes; 0 or None means no quota
    current_usage: int | None = None  # bytes, logical
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _failed(bucket: str, reason: str) -> BucketQuota:
    return BucketQuota(bucket=bucket, ok=False, error=reason)


def _number(value: Any) -> int | None:
    """A non-negative whole number, or None. Rejects booleans and strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def parse_quota_stats(bucket: str, body: bytes) -> BucketQuota:
    """Turn a response body into a reading, or into a failed reading if it is not what was expected."""
    try:
        data = json.loads(body)
    except ValueError:
        return _failed(bucket, "unexpected response: not JSON")
    if not isinstance(data, dict):
        return _failed(bucket, "unexpected response: not a JSON object")
    if data.get("bucket") != bucket:
        return _failed(bucket, "unexpected response: wrong bucket")
    usage = _number(data.get("current_usage"))
    if usage is None:
        return _failed(bucket, "unexpected response: no usage")
    limit_raw = data.get("quota_limit")
    limit = 0 if limit_raw is None else _number(limit_raw)
    if limit is None:
        return _failed(bucket, "unexpected response: invalid quota")
    return BucketQuota(bucket=bucket, ok=True, quota_limit=limit, current_usage=usage)


def fetch_bucket_quota(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    *,
    timeout: float = 10.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    now: datetime | None = None,
) -> BucketQuota:
    """Request the quota statistics of one bucket. Never raises for a network or server problem.

    `endpoint` is the base URL, for example `https://fiona.example.net:9000`. The secret key is only used for the
    signature and is never part of the returned reading or of an error text.
    """
    url = f"{endpoint.rstrip('/')}/rustfs/admin/v3/quota-stats/{quote(bucket, safe='')}"
    headers = sign_request("GET", url, access_key, secret_key, now=now)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        with exc:  # release the connection of the error response
            code = _ERROR_CODE.search(exc.read(2048) or b"")
        return _failed(bucket, f"HTTP {exc.code}" + (f" {code.group(1).decode()}" if code else ""))
    except urllib.error.URLError as exc:
        return _failed(bucket, f"connection failed: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return _failed(bucket, f"connection failed: {exc}")
    return parse_quota_stats(bucket, body)
