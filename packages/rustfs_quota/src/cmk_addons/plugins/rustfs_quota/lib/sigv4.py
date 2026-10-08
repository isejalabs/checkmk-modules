"""AWS Signature Version 4 request signing, standard library only.

RustFS (like S3) authenticates a request by a signature computed from the secret key, instead of by receiving the
secret. The client builds a canonical description of the request, derives a signing key from the secret key with a
chain of HMACs, signs a "string to sign" with it, and sends the result in the `Authorization` header together with the
signed headers. The server repeats the computation with its copy of the secret and compares. The secret key itself never
travels over the wire.

Only what the RustFS admin API needs is implemented: requests without a body, signed headers `host`,
`x-amz-content-sha256` and `x-amz-date`. See https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv.html
"""

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, unquote, urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"
# SHA-256 of an empty body, the payload hash of every request this module signs.
EMPTY_PAYLOAD_SHA256 = hashlib.sha256(b"").hexdigest()
_UNRESERVED = "-_.~"


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive the signing key: HMAC chain over date, region, service and the literal `aws4_request`."""
    key = _hmac(("AWS4" + secret_key).encode(), date_stamp)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def canonical_query_string(query: str) -> str:
    """Query parameters percent-encoded and sorted by name, then value."""
    pairs = parse_qsl(query, keep_blank_values=True)
    encoded = sorted((quote(k, safe=_UNRESERVED), quote(v, safe=_UNRESERVED)) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in encoded)


def authorization(
    *,
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: Mapping[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
) -> str:
    """Compute the `Authorization` header value for the given (already canonical) request parts.

    `headers` are exactly the headers to sign; names are lowercased and values trimmed as the algorithm demands.
    `amz_date` is the request time as `YYYYMMDDTHHMMSSZ`.
    """
    normalized = {name.lower(): " ".join(value.split()) for name, value in headers.items()}
    names = sorted(normalized)
    canonical_headers = "".join(f"{name}:{normalized[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
    )
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([ALGORITHM, amz_date, scope, _sha256_hex(canonical_request)])
    signature = hmac.new(
        signing_key(secret_key, date_stamp, region, service), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"


def sign_request(
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    *,
    region: str = "us-east-1",
    service: str = "s3",
    now: datetime | None = None,
) -> dict[str, str]:
    """Headers to send with a body-less request to `url`: `Host`, `x-amz-date`, `x-amz-content-sha256`, `Authorization`.

    RustFS ignores the region, so the usual default is used. `now` is for tests.
    """
    parts = urlsplit(url)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    host = parts.netloc.rpartition("@")[2]
    signed = {
        "host": host,
        "x-amz-content-sha256": EMPTY_PAYLOAD_SHA256,
        "x-amz-date": amz_date,
    }
    value = authorization(
        method=method,
        canonical_uri=quote(unquote(parts.path or "/"), safe="/" + _UNRESERVED),
        canonical_query=canonical_query_string(parts.query),
        headers=signed,
        payload_hash=EMPTY_PAYLOAD_SHA256,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        service=service,
        amz_date=amz_date,
    )
    return {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": EMPTY_PAYLOAD_SHA256,
        "Authorization": value,
    }
