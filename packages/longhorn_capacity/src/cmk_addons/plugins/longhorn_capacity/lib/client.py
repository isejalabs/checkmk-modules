"""Query Longhorn's node/disk API. Standard library only; the Longhorn API itself has no authentication.

`GET <endpoint>/v1/nodes` returns one object per node, each with a `disks` map keyed by Longhorn's internal disk
name (for example `default-disk-082000000000`). The fields used here, per disk:

- `storageMaximum`, `storageAvailable`: physical capacity and free space of the underlying filesystem, in bytes.
- `storageReserved`: bytes Longhorn keeps unschedulable (for example OS/system headroom).
- `storageScheduled`: sum of the *scheduled* (allocated) size of replicas on this disk — Longhorn's own
  bookkeeping, not the physical bytes actually written (that is `storageMaximum - storageAvailable`).
- `allowScheduling`, `conditions.Ready`, `conditions.Schedulable`: whether Longhorn will place replicas here and
  whether it currently considers the disk healthy.
- `healthDataLastCollectedAt`: when Longhorn itself last refreshed this disk's health/capacity data.

A reading that cannot be obtained is reported as such (`ok = False` with a reason), never as zero. Unlike a
per-bucket RustFS request, one failed call to `/v1/nodes` loses every node/disk at once: there is no fixed, known
set of items to report individually as failed, so the agent emits a single top-level error reading instead (see
`agent_longhorn_capacity`).
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

_MAX_BODY = 5_000_000


@dataclass(frozen=True)
class DiskReading:
    """One disk on one node, or a failed reading (`ok = False`, every other field `None`)."""

    node: str
    disk: str
    ok: bool
    path: str | None = None
    zone: str | None = None
    storage_maximum: int | None = None
    storage_available: int | None = None
    storage_reserved: int | None = None
    storage_scheduled: int | None = None
    allow_scheduling: bool | None = None
    disk_ready: bool | None = None
    disk_schedulable: bool | None = None
    node_ready: bool | None = None
    collected_at: str | None = None
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _failed(reason: str, node: str = "", disk: str = "") -> DiskReading:
    return DiskReading(node=node, disk=disk, ok=False, error=reason)


def _bytes(value: Any) -> int | None:
    """A non-negative whole number of bytes, or None. Rejects booleans and strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _condition_ok(conditions: Any, name: str) -> bool | None:
    """Whether a Longhorn condition's status is "True", or None if it is missing/malformed."""
    if not isinstance(conditions, dict):
        return None
    condition = conditions.get(name)
    if not isinstance(condition, dict):
        return None
    status = condition.get("status")
    if status not in ("True", "False", "Unknown"):
        return None
    return status == "True"


def parse_nodes(body: bytes) -> list[DiskReading]:
    """Turn a `/v1/nodes` response body into one reading per disk, or a single failed reading."""
    try:
        data = json.loads(body)
    except ValueError:
        return [_failed("unexpected response: not JSON")]
    nodes = data.get("data") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return [_failed("unexpected response: no node list")]

    readings: list[DiskReading] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_name = node.get("name") or node.get("id")
        if not isinstance(node_name, str) or not node_name:
            continue
        node_ready = _condition_ok(node.get("conditions"), "Ready")
        zone = node.get("zone") if isinstance(node.get("zone"), str) else None
        disks = node.get("disks")
        if not isinstance(disks, dict):
            continue
        for disk_name, disk in disks.items():
            if not isinstance(disk, dict):
                continue
            readings.append(
                DiskReading(
                    node=node_name,
                    disk=str(disk_name),
                    ok=True,
                    path=disk.get("path") if isinstance(disk.get("path"), str) else None,
                    zone=zone,
                    storage_maximum=_bytes(disk.get("storageMaximum")),
                    storage_available=_bytes(disk.get("storageAvailable")),
                    storage_reserved=_bytes(disk.get("storageReserved")),
                    storage_scheduled=_bytes(disk.get("storageScheduled")),
                    allow_scheduling=disk.get("allowScheduling") if isinstance(disk.get("allowScheduling"), bool) else None,
                    disk_ready=_condition_ok(disk.get("conditions"), "Ready"),
                    disk_schedulable=_condition_ok(disk.get("conditions"), "Schedulable"),
                    node_ready=node_ready,
                    collected_at=disk.get("healthDataLastCollectedAt")
                    if isinstance(disk.get("healthDataLastCollectedAt"), str)
                    else None,
                )
            )
    if not readings:
        return [_failed("unexpected response: no disks found")]
    return readings


def fetch_node_disks(
    endpoint: str,
    *,
    timeout: float = 10.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> list[DiskReading]:
    """Request every node's disks from one Longhorn API endpoint. Never raises for a network or server problem.

    `endpoint` is the base URL the special agent is configured with, for example the environment's existing
    Longhorn UI route (`http://longhorn.<env>.iseja.net`) — the frontend service proxies `/v1/*` to the backend
    and carries no authentication of its own, same as a browser opening the dashboard.
    """
    url = f"{endpoint.rstrip('/')}/v1/nodes"
    request = urllib.request.Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        with exc:  # release the connection of the error response
            pass
        return [_failed(f"HTTP {exc.code}")]
    except urllib.error.URLError as exc:
        return [_failed(f"connection failed: {exc.reason}")]
    except (TimeoutError, OSError) as exc:
        return [_failed(f"connection failed: {exc}")]
    return parse_nodes(body)
