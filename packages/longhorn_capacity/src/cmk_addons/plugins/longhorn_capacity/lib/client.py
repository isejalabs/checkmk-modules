"""Query Longhorn's node/disk state. Standard library only.

Two sources, giving the same disk readings:

- **Kubernetes API** (preferred): the Longhorn CRDs `nodes.longhorn.io`, `volumes.longhorn.io` and
  `instancemanagers.longhorn.io`, read with a ServiceAccount token that may only `get`/`list` them
  (`fetch_kubernetes`). The volumes and instance managers are only available from this source.
- **Longhorn API** (legacy): `GET <endpoint>/v1/nodes` through the Longhorn UI route, which has no authentication of
  its own (`fetch_node_disks`).

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
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

_MAX_BODY = 5_000_000

# The Longhorn CRD version served by Longhorn 1.5 and newer (v1beta1 is no longer served since 1.10).
_CRD_PATH = "/apis/longhorn.io/v1beta2/namespaces/{namespace}/{resource}"


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
    """Whether a Longhorn condition's status is "True", or None if it is missing/malformed.

    The Longhorn API returns conditions as a map keyed by type, the CRDs as a list of objects with a `type`.
    """
    if isinstance(conditions, list):
        conditions = {c.get("type"): c for c in conditions if isinstance(c, dict)}
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
    body, error = _get(f"{endpoint.rstrip('/')}/v1/nodes", timeout=timeout, urlopen=urlopen)
    if body is None:
        return [_failed(error or "unknown error")]
    return parse_nodes(body)


def _get(
    url: str,
    *,
    timeout: float,
    urlopen: Callable[..., Any],
    headers: dict[str, str] | None = None,
    context: ssl.SSLContext | None = None,
) -> tuple[bytes | None, str | None]:
    """The response body, or None and the reason. Never raises for a network or server problem.

    The reason never contains the request headers, so a bearer token cannot leak into the agent output.
    """
    request = urllib.request.Request(url, method="GET", headers=headers or {})
    kwargs: dict[str, Any] = {"timeout": timeout}
    if context is not None:
        kwargs["context"] = context
    try:
        with urlopen(request, **kwargs) as response:
            return response.read(_MAX_BODY), None
    except urllib.error.HTTPError as exc:
        with exc:  # release the connection of the error response
            pass
        return None, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, f"connection failed: {exc.reason}"
    except (TimeoutError, OSError) as exc:
        return None, f"connection failed: {exc}"


# --- Kubernetes API (the Longhorn CRDs) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeReading:
    """One Longhorn volume, or a failed reading of the whole list (`ok = False`, `name` empty)."""

    name: str
    ok: bool
    state: str | None = None
    robustness: str | None = None
    node: str | None = None
    size: int | None = None
    actual_size: int | None = None
    replicas: int | None = None
    pvc: str | None = None
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass(frozen=True)
class InstanceManagerReading:
    """One Longhorn instance manager, or a failed reading of the whole list (`ok = False`, `name` empty)."""

    name: str
    ok: bool
    node: str | None = None
    state: str | None = None
    data_engine: str | None = None
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass(frozen=True)
class KubernetesReadings:
    disks: list[DiskReading]
    volumes: list[VolumeReading]
    instance_managers: list[InstanceManagerReading]


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _items(body: bytes) -> list[dict[str, Any]] | None:
    """The `items` of a Kubernetes list response, or None if the body is not one."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _name(obj: dict[str, Any]) -> str | None:
    metadata = obj.get("metadata")
    return _str(metadata.get("name")) if isinstance(metadata, dict) else None


def _section(obj: dict[str, Any], key: str) -> dict[str, Any]:
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def parse_crd_nodes(body: bytes) -> list[DiskReading]:
    """Turn a `nodes.longhorn.io` list into one reading per disk, or a single failed reading.

    The values are the same as those of `/v1/nodes` (the Longhorn API is built from these objects), so the services
    and their history carry over: `spec.disks` holds the configured disk (path, reserved bytes, whether scheduling is
    allowed), `status.diskStatus` under the same disk name the measured values and conditions.
    """
    nodes = _items(body)
    if nodes is None:
        return [_failed("unexpected response: no node list")]

    readings: list[DiskReading] = []
    for node in nodes:
        node_name = _name(node)
        if node_name is None:
            continue
        spec = _section(node, "spec")
        status = _section(node, "status")
        node_ready = _condition_ok(status.get("conditions"), "Ready")
        disks = spec.get("disks")
        if not isinstance(disks, dict):
            continue
        disk_status = _section(status, "diskStatus")
        for disk_name, disk in disks.items():
            if not isinstance(disk, dict):
                continue
            measured = disk_status.get(disk_name)
            measured = measured if isinstance(measured, dict) else {}
            readings.append(
                DiskReading(
                    node=node_name,
                    disk=str(disk_name),
                    ok=True,
                    path=_str(disk.get("path")) or _str(measured.get("diskPath")),
                    zone=_str(status.get("zone")),
                    storage_maximum=_bytes(measured.get("storageMaximum")),
                    storage_available=_bytes(measured.get("storageAvailable")),
                    storage_reserved=_bytes(disk.get("storageReserved")),
                    storage_scheduled=_bytes(measured.get("storageScheduled")),
                    allow_scheduling=disk.get("allowScheduling") if isinstance(disk.get("allowScheduling"), bool) else None,
                    disk_ready=_condition_ok(measured.get("conditions"), "Ready"),
                    disk_schedulable=_condition_ok(measured.get("conditions"), "Schedulable"),
                    node_ready=node_ready,
                    collected_at=_str(measured.get("healthDataLastCollectedAt")),
                )
            )
    if not readings:
        return [_failed("unexpected response: no disks found")]
    return readings


def _size(value: Any) -> int | None:
    """Volume sizes are strings in `spec.size` and numbers in `status.actualSize`."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return _bytes(value)


def parse_crd_volumes(body: bytes) -> list[VolumeReading]:
    """Turn a `volumes.longhorn.io` list into one reading per volume. An empty list is valid (no volumes)."""
    volumes = _items(body)
    if volumes is None:
        return [VolumeReading(name="", ok=False, error="unexpected response: no volume list")]
    readings: list[VolumeReading] = []
    for volume in volumes:
        name = _name(volume)
        if name is None:
            continue
        spec = _section(volume, "spec")
        status = _section(volume, "status")
        kube = _section(status, "kubernetesStatus")
        namespace, pvc_name = _str(kube.get("namespace")), _str(kube.get("pvcName"))
        replicas = spec.get("numberOfReplicas")
        readings.append(
            VolumeReading(
                name=name,
                ok=True,
                state=_str(status.get("state")),
                robustness=_str(status.get("robustness")),
                node=_str(status.get("currentNodeID")),
                size=_size(spec.get("size")),
                actual_size=_size(status.get("actualSize")),
                replicas=replicas if isinstance(replicas, int) and not isinstance(replicas, bool) else None,
                pvc=f"{namespace}/{pvc_name}" if namespace and pvc_name else None,
            )
        )
    return readings


def parse_crd_instance_managers(body: bytes) -> list[InstanceManagerReading]:
    """Turn an `instancemanagers.longhorn.io` list into one reading per instance manager, or a failed reading."""
    managers = _items(body)
    if managers is None:
        return [InstanceManagerReading(name="", ok=False, error="unexpected response: no instance manager list")]
    readings: list[InstanceManagerReading] = []
    for manager in managers:
        name = _name(manager)
        if name is None:
            continue
        spec = _section(manager, "spec")
        status = _section(manager, "status")
        readings.append(
            InstanceManagerReading(
                name=name,
                ok=True,
                node=_str(spec.get("nodeID")),
                state=_str(status.get("currentState")),
                data_engine=_str(spec.get("dataEngine")),
            )
        )
    if not readings:
        return [InstanceManagerReading(name="", ok=False, error="unexpected response: no instance managers found")]
    return readings


def ssl_context(ca_cert: str | None) -> ssl.SSLContext:
    """Verify the API server against the given PEM CA (the cluster's own CA), or the system trust store without one."""
    if ca_cert:
        return ssl.create_default_context(cadata=ca_cert)
    return ssl.create_default_context()


def fetch_kubernetes(
    api_server: str,
    token: str,
    *,
    namespace: str = "longhorn-system",
    context: ssl.SSLContext | None = None,
    timeout: float = 10.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> KubernetesReadings:
    """Read the Longhorn nodes, volumes and instance managers from the Kubernetes API. Never raises for a network or
    server problem: each list that could not be read becomes a single failed reading with the reason.

    `token` is a bearer token of a ServiceAccount that may `get`/`list` the three resources in `namespace`.
    """
    base = api_server.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get(resource: str) -> tuple[bytes | None, str | None]:
        url = base + _CRD_PATH.format(namespace=namespace, resource=resource)
        return _get(url, timeout=timeout, urlopen=urlopen, headers=headers, context=context)

    body, error = get("nodes")
    disks = parse_crd_nodes(body) if body is not None else [_failed(error or "unknown error")]
    body, error = get("volumes")
    volumes = (
        parse_crd_volumes(body) if body is not None else [VolumeReading(name="", ok=False, error=error or "unknown error")]
    )
    body, error = get("instancemanagers")
    instance_managers = (
        parse_crd_instance_managers(body)
        if body is not None
        else [InstanceManagerReading(name="", ok=False, error=error or "unknown error")]
    )
    return KubernetesReadings(disks=disks, volumes=volumes, instance_managers=instance_managers)
