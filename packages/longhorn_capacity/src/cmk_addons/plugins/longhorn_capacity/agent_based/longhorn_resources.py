"""Check plugins: Longhorn failure modes from the CRDs, one service each per Longhorn deployment.

- **"Longhorn volumes"** — CRIT for a faulted volume (no healthy replica left), WARN for an attached volume that is
  degraded (fewer healthy replicas than configured, for example while rebuilding) or whose robustness is unknown, and
  WARN/CRIT for a volume stuck in `attaching` or `detaching` for longer than the levels. A detached volume's
  robustness is always `unknown` in Longhorn, so that alone is not a problem. Every volume is listed in the details
  with its PVC, state, robustness, size and actual size, which is what the Longhorn UI shows per volume.
- **"Longhorn instance managers"** — CRIT for an instance manager that is not `running` (its engines and replicas
  are down on that node), WARN while one is `starting`.

Both are single services rather than one per volume or instance manager: volumes come and go with their PVCs and
instance managers are replaced on every Longhorn upgrade, so per-object services would mostly produce vanished
items. The sections are only written when the agent reads the Kubernetes API; with the legacy Longhorn API source
these services are not discovered.

A list that could not be read is UNKNOWN, never OK.
"""

import json
import time
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Result,
    Service,
    State,
    StringTable,
    get_value_store,
    render,
)

Section = Sequence[Mapping[str, Any]]

# Seconds a volume may stay in attaching/detaching before it counts as stuck (WARN, CRIT). Attaching normally takes
# seconds; a rebuild-before-attach or a slow node can take a few minutes. Fixed for now: no ruleset of its own.
STUCK_LEVELS = (600.0, 1800.0)

_TRANSITIONS = ("attaching", "detaching")
_STUCK_KEY = "transition_since"


def parse_json_rows(string_table: StringTable) -> Section:
    """One JSON object per row; rows that are not an object are ignored. An empty section is valid."""
    rows: list[Mapping[str, Any]] = []
    for row in string_table:
        try:
            value = json.loads(" ".join(row))
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


agent_section_longhorn_volumes = AgentSection(name="longhorn_volumes", parse_function=parse_json_rows)
agent_section_longhorn_instance_managers = AgentSection(
    name="longhorn_instance_managers", parse_function=parse_json_rows
)


def discover_single(section: Section) -> DiscoveryResult:
    yield Service()


def _failure(section: Section) -> Mapping[str, Any] | None:
    return next((row for row in section if not row.get("ok")), None)


def _label(volume: Mapping[str, Any]) -> str:
    pvc = volume.get("pvc")
    return f"{pvc} ({volume.get('name')})" if pvc else str(volume.get("name"))


def _size(value: Any) -> str:
    return render.bytes(value) if isinstance(value, int) else "unknown"


def _stuck_state(seconds: float) -> State:
    warn, crit = STUCK_LEVELS
    if seconds >= crit:
        return State.CRIT
    if seconds >= warn:
        return State.WARN
    return State.OK


def evaluate_volumes(
    section: Section,
    value_store: MutableMapping[str, Any],
    now: float | None = None,
) -> CheckResult:
    """Separate from the check function for tests: `value_store` remembers since when a volume is in transition."""
    failure = _failure(section)
    if failure is not None:
        yield Result(state=State.UNKNOWN, summary=f"Volume data unavailable: {failure.get('error') or 'unknown error'}")
        return

    now = time.time() if now is None else now
    previous: Mapping[str, Any] = value_store.get(_STUCK_KEY, {})
    since: dict[str, list[Any]] = {}
    problems: list[Result] = []
    counts = {"attached": 0, "detached": 0, "other": 0}

    for volume in sorted(section, key=lambda v: str(v.get("name"))):
        name = str(volume.get("name"))
        state = volume.get("state")
        robustness = volume.get("robustness")
        counts[state if state in ("attached", "detached") else "other"] += 1

        if robustness == "faulted":
            problems.append(Result(state=State.CRIT, summary=f"{_label(volume)} is faulted"))
        elif state == "attached" and robustness == "degraded":
            problems.append(Result(state=State.WARN, summary=f"{_label(volume)} is degraded"))
        elif state == "attached" and robustness not in ("healthy", "degraded"):
            problems.append(Result(state=State.WARN, summary=f"{_label(volume)} is attached, robustness {robustness}"))

        if state in _TRANSITIONS:
            first = previous.get(name)
            started = first[1] if isinstance(first, list) and len(first) == 2 and first[0] == state else now
            since[name] = [state, started]
            stuck = _stuck_state(now - started)
            if stuck is not State.OK:
                problems.append(
                    Result(state=stuck, summary=f"{_label(volume)} {state} for {render.timespan(now - started)}")
                )

        yield Result(
            state=State.OK,
            notice=(
                f"{_label(volume)}: {state}, {robustness}, size {_size(volume.get('size'))}, "
                f"actual {_size(volume.get('actual_size'))}, replicas {volume.get('replicas')}, "
                f"node {volume.get('node') or '-'}"
            ),
        )

    value_store[_STUCK_KEY] = since
    yield Result(
        state=State.OK,
        summary=(
            f"{len(section)} volumes: {counts['attached']} attached, {counts['detached']} detached"
            + (f", {counts['other']} other" if counts["other"] else "")
        ),
    )
    yield from problems


def check_longhorn_volumes(section: Section) -> CheckResult:
    yield from evaluate_volumes(section, get_value_store())


def check_longhorn_instance_managers(section: Section) -> CheckResult:
    failure = _failure(section)
    if failure is not None:
        yield Result(
            state=State.UNKNOWN,
            summary=f"Instance manager data unavailable: {failure.get('error') or 'unknown error'}",
        )
        return

    running = 0
    problems: list[Result] = []
    for manager in sorted(section, key=lambda m: str(m.get("name"))):
        state = manager.get("state")
        where = f"{manager.get('name')} on {manager.get('node') or 'unknown node'} ({manager.get('data_engine')})"
        if state == "running":
            running += 1
        elif state == "starting":
            problems.append(Result(state=State.WARN, summary=f"{where} is starting"))
        else:
            problems.append(Result(state=State.CRIT, summary=f"{where} is {state or 'in an unknown state'}"))
        yield Result(state=State.OK, notice=f"{where}: {state}")
    yield Result(state=State.OK, summary=f"{running} of {len(section)} running")
    yield from problems


check_plugin_longhorn_volumes = CheckPlugin(
    name="longhorn_volumes",
    service_name="Longhorn volumes",
    sections=["longhorn_volumes"],
    discovery_function=discover_single,
    check_function=check_longhorn_volumes,
)

check_plugin_longhorn_instance_managers = CheckPlugin(
    name="longhorn_instance_managers",
    service_name="Longhorn instance managers",
    sections=["longhorn_instance_managers"],
    discovery_function=discover_single,
    check_function=check_longhorn_instance_managers,
)
