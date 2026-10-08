# rustfs_quota

Checkmk special agent and check plugin for the **bucket quotas of a RustFS**: for every configured bucket one service showing the used space against the bucket's quota, with levels, growth trend and the time until the quota is full.

See the [Changelog](CHANGELOG.md). The Checkmk objects around this package (Password Store entries, the API-only host, the rules) are managed with Terraform, see [ADR 0015 in isejalabs/homelab](https://github.com/isejalabs/homelab/blob/main/docs/decisions/0015-checkmk-configuration-as-code.md) and the Terraform modules [`checkmk-password`](https://github.com/isejalabs/terraform-modules/tree/main/modules/checkmk-password) and [`checkmk-rustfs-monitoring`](https://github.com/isejalabs/terraform-modules/tree/main/modules/checkmk-rustfs-monitoring).

## How it works

- **Special agent** (`libexec/agent_rustfs_quota`): per bucket it calls `GET /rustfs/admin/v3/quota-stats/<bucket>` and writes the section `rustfs_quota`, one JSON object per line: the reading, or `"ok": false` with a reason (`HTTP 403 AccessDenied`, `connection failed: ...`, `unexpected response: ...`). The identity needs only the bucket-scoped permission `s3:GetBucketQuota`.
- **Check** (`agent_based/rustfs_quota.py`): one service `RustFS bucket <name>` per bucket. A bucket's quota is treated like the size of a filesystem, so Checkmk's own filesystem helper provides the used-space levels (default 80%/90%), the growth trend and the time until the quota is full, and the existing ruleset **Filesystems (used space and growth)** configures these services with the bucket name as the item. Metrics are the usual `fs_used`, `fs_size`, `fs_used_percent`, `growth`, `trend` and `trend_hoursleft`.
- **Failures are never zero.** A reading that could not be obtained makes the service UNKNOWN with the reason; a bucket that vanishes from the output shows as not found. A bucket without quota (`quota_limit` 0) reports its usage only. On the first execution of a new service the trend counters are initializing: the used-space result is kept and the trend appears from the second execution.

## Rule contract (v0)

The rule for the ruleset `special_agents:rustfs_quota` has exactly these parameters, which the Terraform module `checkmk-rustfs-monitoring` writes:

| Parameter | Value |
| --- | --- |
| `endpoint` | RustFS base URL without a trailing slash, for example `https://rustfs.example.net:9000` |
| `access_key` | the monitoring identity's access key |
| `secret_key` | a Password Store reference, never the secret itself |
| `buckets` | list of bucket names |

Put the rule on a host **without a Checkmk agent and without an IP address** (`tag_agent = special-agents`, `no-ip`). On a host with the normal agent a special-agent rule replaces its agent connection.

## Secret handling

Checkmk renders the Password Store reference as `<id>:<path to the merged password file>` on the agent's command line; the agent resolves it itself with `cmk.utils.password_store.lookup`, so neither the reference's target nor the secret is exposed. The secret is only used to compute the request signature and is never part of a reading, an error text or the section. `cmk.utils.password_store` is a Checkmk-internal module (also used by Checkmk's own agents): re-check after Checkmk upgrades.

## AWS Signature Version 4

RustFS authenticates requests like S3: the client does not send its secret but a signature computed from it. `lib/sigv4.py` implements the algorithm with the standard library (about 100 lines): a canonical description of the request is hashed and signed with a key derived from the secret key through a chain of HMAC-SHA256 over date, region, service and `aws4_request`; the result goes into the `Authorization` header together with `x-amz-date` and `x-amz-content-sha256`, and the server repeats the computation. It is verified against the published AWS test vector for the algorithm, cross-checked against botocore (an independent implementation) in the unit tests, and was accepted by the real RustFS. The signing code is isolated in one module so it can be swapped for botocore if needed.

## Polling interval

Checkmk runs the agent once per check cycle of the host, by default every minute, and each run makes one request per bucket; RustFS logs every quota request as a warning-level event. With the default interval, 12 buckets give about 17,000 requests a day. The monitoring plan calls for a 15-minute interval, which is set with the rule **Normal check interval for service checks** for the `Check_MK` service of the API-only host (900 seconds). This is not part of the package.

## Development

`scripts/mkp.sh` tests and builds the package inside a throwaway Checkmk Raw container (needs docker; the image is about 3 GB):

```sh
scripts/mkp.sh test  rustfs_quota   # unit tests with the site's Python
scripts/mkp.sh build rustfs_quota   # dist/rustfs_quota-<version>.mkp
```

The tests that need no Checkmk (SigV4, client, parsing) also run with a plain `PYTHONPATH=src python3 -m unittest discover -s tests -t .` from this folder; the check tests and the botocore cross-check are skipped there.

## Installing

Install a released `.mkp` on the Checkmk site with `mkp add <file>` and `mkp enable rustfs_quota` (then `omd restart apache` to load the ruleset); remove it with `mkp disable` and `mkp remove`. On the homelab this is done through Salt with a pinned version. The package requires Checkmk 2.4.0 or newer.
