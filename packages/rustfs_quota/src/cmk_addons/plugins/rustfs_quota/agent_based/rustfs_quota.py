"""Check plugin: quota and usage of RustFS buckets, one service per bucket.

A bucket's quota is treated like the size of a filesystem and its usage like used space, so the checks of Checkmk's
own filesystem helper apply: used-space levels, growth trend and time until the quota is full. The ruleset
"Filesystems (used space and growth)" configures these services as it does for filesystems, with the bucket name as the
item. A reading that could not be obtained is UNKNOWN, never zero.
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
    Metric,
    Result,
    Service,
    State,
    StringTable,
    get_value_store,
    render,
)
from cmk.plugins.lib.df import FILESYSTEM_DEFAULT_PARAMS, df_check_filesystem_single

MIB = 1024 * 1024

Section = Mapping[str, Mapping[str, Any]]


def parse_rustfs_quota(string_table: StringTable) -> Section:
    """One JSON object per row, keyed by bucket. Rows that are not valid readings are ignored."""
    section: dict[str, Mapping[str, Any]] = {}
    for row in string_table:
        try:
            reading = json.loads(" ".join(row))
        except ValueError:
            continue
        if isinstance(reading, dict) and isinstance(reading.get("bucket"), str):
            section[reading["bucket"]] = reading
    return section


agent_section_rustfs_quota = AgentSection(name="rustfs_quota", parse_function=parse_rustfs_quota)


def discover_rustfs_quota(section: Section) -> DiscoveryResult:
    for bucket in sorted(section):
        yield Service(item=bucket)


def evaluate_bucket(
    item: str,
    params: Mapping[str, Any],
    reading: Mapping[str, Any],
    value_store: MutableMapping[str, Any],
    this_time: float | None = None,
) -> CheckResult:
    """Evaluate one bucket's reading. Separate from the check function so that tests can pass the value store and time."""
    if not reading.get("ok"):
        yield Result(
            state=State.UNKNOWN,
            summary=f"Quota statistics unavailable: {reading.get('error') or 'unknown error'}",
        )
        return

    used = int(reading.get("current_usage") or 0)
    limit = int(reading.get("quota_limit") or 0)
    if limit <= 0:
        yield Result(state=State.OK, summary=f"Used: {render.bytes(used)}, no quota set")
        yield Metric("fs_used", used / MIB)
        return

    try:
        yield from df_check_filesystem_single(
            value_store, item, limit / MIB, (limit - used) / MIB, 0.0, None, None, params, this_time
        )
    except IgnoreResultsError as exc:
        # The growth trend needs a second sample (the helper raises on the first execution of a new service). Keep the
        # used-space results that were already yielded and only note that the trend is not available yet.
        yield IgnoreResults(str(exc))


def check_rustfs_quota(item: str, params: Mapping[str, Any], section: Section) -> CheckResult:
    reading = section.get(item)
    if reading is None:
        return  # Checkmk reports the vanished item as "not found in monitoring data"
    yield from evaluate_bucket(item, params, reading, get_value_store())


check_plugin_rustfs_quota = CheckPlugin(
    name="rustfs_quota",
    service_name="RustFS bucket %s",
    discovery_function=discover_rustfs_quota,
    check_function=check_rustfs_quota,
    check_ruleset_name="filesystem",
    check_default_parameters=FILESYSTEM_DEFAULT_PARAMS,
)
