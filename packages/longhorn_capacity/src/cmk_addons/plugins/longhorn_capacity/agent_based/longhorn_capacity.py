"""Check plugin: per-node, per-disk capacity and health of a Longhorn deployment.

Three services per disk, all keyed by the same item (`"<node> <path>"`):

- **"Longhorn disk <node> <path>"** — actual physical usage of the disk (`storageMaximum` as size,
  `storageAvailable` as free space). This is the real bytes-on-disk number, independent of what Longhorn has
  scheduled onto it.
- **"Longhorn disk <node> <path> scheduled"** — Longhorn's own allocation bookkeeping: schedulable capacity
  (`storageMaximum - storageReserved`) against bytes already committed to replicas (`storageScheduled`). This
  can run high from thin provisioning well before the physical check does; kept as a separate service on
  purpose; the plan explicitly wants allocated/scheduled bytes distinct from actual physical use.
- **"Longhorn disk <node> <path> health"** — Longhorn's own disk/node readiness and scheduling flags
  (`conditions.Ready`/`Schedulable`, `allowScheduling`, the node's own `Ready` condition). Independent of
  capacity: a disk can be full but healthy, or have plenty of room but be marked not-Ready.

Both capacity services are modelled as filesystems and share Checkmk's own filesystem check (used-space levels,
growth trend, time until full) through the existing ruleset "Filesystems (used space and growth)", with the
service's item as the discriminator — the same approach `rustfs_quota` uses for bucket quotas.

A reading that could not be obtained is UNKNOWN, never zero or OK. One failed `/v1/nodes` call loses every
node/disk at once (see `lib/client.py`): the agent then writes a single error reading with no node/disk
identity, which does not match any already-discovered item, so every existing service for this host reports
"not found in monitoring data" until the next successful call — the same vanishing-service behavior Checkmk
gives any agent section that stops reporting an item.
"""

import json
from collections.abc import Mapping, MutableMapping
from typing import Any

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    IgnoreResults,
    IgnoreResultsError,
    Result,
    Service,
    State,
    StringTable,
    get_value_store,
)
from cmk.plugins.lib.df import FILESYSTEM_DEFAULT_PARAMS, df_check_filesystem_single

MIB = 1024 * 1024

Section = Mapping[str, Mapping[str, Any]]


def _item(reading: Mapping[str, Any]) -> str | None:
    node = reading.get("node")
    if not isinstance(node, str) or not node:
        return None
    path = reading.get("path")
    disk = reading.get("disk")
    label = path if isinstance(path, str) and path else disk
    if not isinstance(label, str) or not label:
        return None
    return f"{node} {label}"


def parse_longhorn_capacity(string_table: StringTable) -> Section:
    """One JSON object per row, keyed by `"<node> <path>"`. Rows without a usable item are ignored."""
    section: dict[str, Mapping[str, Any]] = {}
    for row in string_table:
        try:
            reading = json.loads(" ".join(row))
        except ValueError:
            continue
        if not isinstance(reading, dict):
            continue
        item = _item(reading)
        if item is not None:
            section[item] = reading
    return section


agent_section_longhorn_capacity = AgentSection(name="longhorn_capacity", parse_function=parse_longhorn_capacity)


def _discover(section: Section) -> DiscoveryResult:
    for item in sorted(section):
        yield Service(item=item)


discover_longhorn_capacity_disk = _discover
discover_longhorn_capacity_scheduled = _discover
discover_longhorn_capacity_health = _discover


def _unavailable(reading: Mapping[str, Any]) -> CheckResult | None:
    """A Result if the reading could not be obtained at all, otherwise None."""
    if not reading.get("ok"):
        yield Result(
            state=State.UNKNOWN,
            summary=f"Capacity data unavailable: {reading.get('error') or 'unknown error'}",
        )


def evaluate_disk_usage(
    item: str,
    params: Mapping[str, Any],
    reading: Mapping[str, Any],
    value_store: MutableMapping[str, Any],
    this_time: float | None = None,
) -> CheckResult:
    """Physical usage: size = storageMaximum, free = storageAvailable. Separate from the check function for tests."""
    unavailable = list(_unavailable(reading) or [])
    if unavailable:
        yield from unavailable
        return
    maximum = reading.get("storage_maximum")
    available = reading.get("storage_available")
    if not isinstance(maximum, int) or not isinstance(available, int) or maximum <= 0:
        yield Result(state=State.UNKNOWN, summary="Capacity data unavailable: no storage size reported")
        return
    try:
        yield from df_check_filesystem_single(
            value_store, item, maximum / MIB, available / MIB, 0.0, None, None, params, this_time
        )
    except IgnoreResultsError as exc:
        yield IgnoreResults(str(exc))


def evaluate_disk_scheduled(
    item: str,
    params: Mapping[str, Any],
    reading: Mapping[str, Any],
    value_store: MutableMapping[str, Any],
    this_time: float | None = None,
) -> CheckResult:
    """Scheduling headroom: size = storageMaximum - storageReserved, free = that minus storageScheduled."""
    unavailable = list(_unavailable(reading) or [])
    if unavailable:
        yield from unavailable
        return
    maximum = reading.get("storage_maximum")
    reserved = reading.get("storage_reserved")
    scheduled = reading.get("storage_scheduled")
    if not isinstance(maximum, int) or not isinstance(reserved, int) or not isinstance(scheduled, int):
        yield Result(state=State.UNKNOWN, summary="Scheduling data unavailable: no storage size reported")
        return
    schedulable = maximum - reserved
    if schedulable <= 0:
        yield Result(state=State.UNKNOWN, summary="Scheduling data unavailable: no schedulable capacity")
        return
    free = schedulable - scheduled
    try:
        yield from df_check_filesystem_single(
            value_store, item, schedulable / MIB, free / MIB, 0.0, None, None, params, this_time
        )
    except IgnoreResultsError as exc:
        yield IgnoreResults(str(exc))


def evaluate_disk_health(reading: Mapping[str, Any]) -> CheckResult:
    """Longhorn's own readiness/scheduling flags for the disk and its node. Independent of capacity."""
    unavailable = list(_unavailable(reading) or [])
    if unavailable:
        yield from unavailable
        return

    node_ready = reading.get("node_ready")
    disk_ready = reading.get("disk_ready")
    disk_schedulable = reading.get("disk_schedulable")
    allow_scheduling = reading.get("allow_scheduling")

    if node_ready is False:
        yield Result(state=State.CRIT, summary="Node is not Ready")
        return
    if disk_ready is False:
        yield Result(state=State.CRIT, summary="Disk is not Ready")
        return
    if disk_schedulable is False or allow_scheduling is False:
        yield Result(state=State.WARN, summary="Disk is Ready but not schedulable")
        return
    if node_ready is None or disk_ready is None:
        yield Result(state=State.UNKNOWN, summary="Readiness unknown: Longhorn reported no condition status")
        return
    collected_at = reading.get("collected_at")
    detail = f" (data collected {collected_at})" if collected_at else ""
    yield Result(state=State.OK, summary=f"Disk healthy, node Ready, scheduling allowed{detail}")


def check_longhorn_capacity_disk(item: str, params: Mapping[str, Any], section: Section) -> CheckResult:
    reading = section.get(item)
    if reading is None:
        return
    yield from evaluate_disk_usage(item, params, reading, get_value_store())


def check_longhorn_capacity_scheduled(item: str, params: Mapping[str, Any], section: Section) -> CheckResult:
    reading = section.get(item)
    if reading is None:
        return
    yield from evaluate_disk_scheduled(item, params, reading, get_value_store())


def check_longhorn_capacity_health(item: str, section: Section) -> CheckResult:
    reading = section.get(item)
    if reading is None:
        return
    yield from evaluate_disk_health(reading)


check_plugin_longhorn_capacity_disk = CheckPlugin(
    name="longhorn_capacity_disk",
    service_name="Longhorn disk %s",
    sections=["longhorn_capacity"],
    discovery_function=discover_longhorn_capacity_disk,
    check_function=check_longhorn_capacity_disk,
    check_ruleset_name="filesystem",
    check_default_parameters=FILESYSTEM_DEFAULT_PARAMS,
)

check_plugin_longhorn_capacity_scheduled = CheckPlugin(
    name="longhorn_capacity_scheduled",
    service_name="Longhorn disk %s scheduled",
    sections=["longhorn_capacity"],
    discovery_function=discover_longhorn_capacity_scheduled,
    check_function=check_longhorn_capacity_scheduled,
    check_ruleset_name="filesystem",
    check_default_parameters=FILESYSTEM_DEFAULT_PARAMS,
)

check_plugin_longhorn_capacity_health = CheckPlugin(
    name="longhorn_capacity_health",
    service_name="Longhorn disk %s health",
    sections=["longhorn_capacity"],
    discovery_function=discover_longhorn_capacity_health,
    check_function=check_longhorn_capacity_health,
)
