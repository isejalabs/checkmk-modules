import unittest

try:
    from cmk.agent_based.v2 import Metric, Result, State
    from cmk.plugins.lib.df import FILESYSTEM_DEFAULT_PARAMS

    from cmk_addons.plugins.longhorn_capacity.agent_based import longhorn_capacity as plugin
except ImportError:  # pragma: no cover - needs a Checkmk site's Python
    plugin = None

GIB = 1024**3


def reading(**overrides) -> dict:
    base = {
        "node": "work-01",
        "disk": "default-disk-1",
        "ok": True,
        "path": "/var/mnt/longhorn",
        "zone": "pve1",
        "storage_maximum": 20 * GIB,
        "storage_available": 15 * GIB,
        "storage_reserved": 2 * GIB,
        "storage_scheduled": 3 * GIB,
        "allow_scheduling": True,
        "disk_ready": True,
        "disk_schedulable": True,
        "node_ready": True,
        "collected_at": "2026-10-16 06:00:00 +0200 CEST",
        "error": None,
    }
    base.update(overrides)
    return base


@unittest.skipIf(plugin is None, "needs the Python of a Checkmk site")
class ParseAndDiscovery(unittest.TestCase):
    def test_parse_and_discovery(self) -> None:
        import json

        rows = [
            [json.dumps(reading(node="a", disk="d1", path="/x"))],
            ["garbage"],
            [json.dumps({"no": "node"})],
            [json.dumps(reading(node="b", disk="d1", path="/y", ok=False, error="x"))],
        ]
        section = plugin.parse_longhorn_capacity(rows)
        self.assertEqual(sorted(section), ["a /x", "b /y"])
        for discover in (
            plugin.discover_longhorn_capacity_disk,
            plugin.discover_longhorn_capacity_scheduled,
            plugin.discover_longhorn_capacity_health,
        ):
            self.assertEqual(sorted(s.item for s in discover(section)), ["a /x", "b /y"])


@unittest.skipIf(plugin is None, "needs the Python of a Checkmk site")
class DiskUsage(unittest.TestCase):
    def evaluate(self, item_reading: dict, params=None, store=None, this_time=None) -> list:
        return list(
            plugin.evaluate_disk_usage(
                "work-01 /var/mnt/longhorn", params or FILESYSTEM_DEFAULT_PARAMS, item_reading,
                {} if store is None else store, this_time,
            )
        )

    def states(self, results: list) -> set:
        return {r.state for r in results if isinstance(r, Result)}

    def test_ok(self) -> None:
        results = self.evaluate(reading())
        self.assertEqual(self.states(results), {State.OK})
        self.assertTrue(any(isinstance(r, Metric) and r.name == "fs_used" for r in results))

    def test_warn_and_crit_levels(self) -> None:
        # 20 GiB disk, default filesystem levels 80%/90%
        self.assertIn(State.WARN, self.states(self.evaluate(reading(storage_available=3 * GIB))))  # 85% used
        self.assertIn(State.CRIT, self.states(self.evaluate(reading(storage_available=1 * GIB))))  # 95% used

    def test_failed_reading_is_unknown_never_zero(self) -> None:
        results = self.evaluate(reading(ok=False, error="connection failed: refused"))
        self.assertEqual(self.states(results), {State.UNKNOWN})
        self.assertIn("connection failed: refused", results[0].summary)
        self.assertFalse([r for r in results if isinstance(r, Metric)])

    def test_missing_size_is_unknown(self) -> None:
        results = self.evaluate(reading(storage_maximum=None))
        self.assertEqual(self.states(results), {State.UNKNOWN})

    def test_missing_item_yields_nothing(self) -> None:
        self.assertEqual(list(plugin.check_longhorn_capacity_disk("missing", FILESYSTEM_DEFAULT_PARAMS, {})), [])


@unittest.skipIf(plugin is None, "needs the Python of a Checkmk site")
class DiskScheduled(unittest.TestCase):
    def evaluate(self, item_reading: dict, params=None, store=None, this_time=None) -> list:
        return list(
            plugin.evaluate_disk_scheduled(
                "work-01 /var/mnt/longhorn", params or FILESYSTEM_DEFAULT_PARAMS, item_reading,
                {} if store is None else store, this_time,
            )
        )

    def states(self, results: list) -> set:
        return {r.state for r in results if isinstance(r, Result)}

    def test_ok_schedulable_capacity_distinct_from_physical(self) -> None:
        # schedulable = 20 - 2 = 18 GiB, scheduled = 3 GiB -> 16.7% used, well under levels even though
        # physical usage (storage_available=15 of 20) would be a different number entirely.
        results = self.evaluate(reading())
        self.assertEqual(self.states(results), {State.OK})

    def test_overcommitted_scheduling_can_warn_while_physical_use_is_fine(self) -> None:
        # Physical disk only 25% used (15 of 20 GiB available), but almost everything schedulable is
        # already allocated to replicas -> the two services genuinely disagree, which is the point.
        results = self.evaluate(reading(storage_scheduled=16 * GIB))  # 16 of 18 GiB schedulable = ~89%
        self.assertIn(State.WARN, self.states(results))

    def test_no_schedulable_capacity_is_unknown(self) -> None:
        results = self.evaluate(reading(storage_maximum=2 * GIB, storage_reserved=2 * GIB))
        self.assertEqual(self.states(results), {State.UNKNOWN})

    def test_failed_reading_is_unknown(self) -> None:
        results = self.evaluate(reading(ok=False, error="HTTP 500"))
        self.assertEqual(self.states(results), {State.UNKNOWN})
        self.assertIn("HTTP 500", results[0].summary)


@unittest.skipIf(plugin is None, "needs the Python of a Checkmk site")
class DiskHealth(unittest.TestCase):
    def evaluate(self, item_reading: dict) -> list:
        return list(plugin.evaluate_disk_health(item_reading))

    def test_healthy(self) -> None:
        results = self.evaluate(reading())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, State.OK)
        self.assertIn("healthy", results[0].summary)

    def test_node_not_ready_is_critical(self) -> None:
        results = self.evaluate(reading(node_ready=False))
        self.assertEqual(results[0].state, State.CRIT)
        self.assertIn("Node", results[0].summary)

    def test_disk_not_ready_is_critical(self) -> None:
        results = self.evaluate(reading(disk_ready=False))
        self.assertEqual(results[0].state, State.CRIT)
        self.assertIn("Disk", results[0].summary)

    def test_not_schedulable_is_warning(self) -> None:
        results = self.evaluate(reading(disk_schedulable=False))
        self.assertEqual(results[0].state, State.WARN)

    def test_scheduling_disallowed_is_warning(self) -> None:
        results = self.evaluate(reading(allow_scheduling=False))
        self.assertEqual(results[0].state, State.WARN)

    def test_unknown_condition_is_unknown_not_ok(self) -> None:
        results = self.evaluate(reading(node_ready=None))
        self.assertEqual(results[0].state, State.UNKNOWN)

    def test_failed_reading_is_unknown_never_ok(self) -> None:
        results = self.evaluate(reading(ok=False, error="HTTP 500"))
        self.assertEqual(results[0].state, State.UNKNOWN)
        self.assertIn("HTTP 500", results[0].summary)


if __name__ == "__main__":
    unittest.main()
