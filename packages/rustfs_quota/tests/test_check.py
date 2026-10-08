import unittest

try:
    from cmk.agent_based.v2 import IgnoreResults, Metric, Result, State
    from cmk.plugins.lib.df import FILESYSTEM_DEFAULT_PARAMS

    from cmk_addons.plugins.rustfs_quota.agent_based import rustfs_quota as plugin
except ImportError:  # pragma: no cover - needs a Checkmk site's Python
    plugin = None

GIB = 1024**3


def reading(used: int, limit: int = 10 * GIB) -> dict:
    return {"bucket": "b", "ok": True, "quota_limit": limit, "current_usage": used, "error": None}


@unittest.skipIf(plugin is None, "needs the Python of a Checkmk site")
class CheckPlugin(unittest.TestCase):
    def evaluate(self, item_reading: dict, params=None, store=None, this_time=None) -> list:
        return list(
            plugin.evaluate_bucket("b", params or FILESYSTEM_DEFAULT_PARAMS, item_reading, {} if store is None else store, this_time)
        )

    def states(self, results: list) -> set:
        return {r.state for r in results if isinstance(r, Result)}

    def test_parse_and_discovery(self) -> None:
        section = plugin.parse_rustfs_quota(
            [['{"bucket": "a", "ok": true, "current_usage": 1, "quota_limit": 2, "error": null}'], ["garbage"], ['{"no": "bucket"}'], ['{"bucket": "c", "ok": false, "error": "x"}']]
        )
        self.assertEqual(sorted(section), ["a", "c"])
        self.assertEqual([s.item for s in plugin.discover_rustfs_quota(section)], ["a", "c"])

    def test_ok(self) -> None:
        results = self.evaluate(reading(3 * GIB))
        self.assertEqual(self.states(results), {State.OK})
        self.assertTrue(any(isinstance(r, Metric) and r.name == "fs_used" for r in results))
        self.assertTrue(any(isinstance(r, Metric) and r.name == "fs_size" for r in results))

    def test_warn_and_crit_levels(self) -> None:
        self.assertIn(State.WARN, self.states(self.evaluate(reading(int(8.5 * GIB)))))
        self.assertIn(State.CRIT, self.states(self.evaluate(reading(int(9.5 * GIB)))))

    def test_over_quota_is_critical(self) -> None:
        self.assertIn(State.CRIT, self.states(self.evaluate(reading(11 * GIB))))

    def test_no_quota_reports_usage_only(self) -> None:
        results = self.evaluate(reading(5 * GIB, limit=0))
        self.assertEqual(self.states(results), {State.OK})
        self.assertIn("no quota set", [r for r in results if isinstance(r, Result)][0].summary)

    def test_failed_reading_is_unknown_never_zero(self) -> None:
        results = self.evaluate({"bucket": "b", "ok": False, "error": "HTTP 403 AccessDenied"})
        self.assertEqual(self.states(results), {State.UNKNOWN})
        self.assertIn("HTTP 403 AccessDenied", results[0].summary)
        self.assertFalse([r for r in results if isinstance(r, Metric)])

    def test_missing_item_yields_nothing(self) -> None:
        self.assertEqual(list(plugin.check_rustfs_quota("missing", FILESYSTEM_DEFAULT_PARAMS, {})), [])

    def test_trend_and_time_left(self) -> None:
        """Growing usage over several samples gives a trend and a time until the quota is full."""
        params = {**FILESYSTEM_DEFAULT_PARAMS, "trend_range": 24, "trend_perfdata": True, "trend_showtimeleft": True}
        store: dict = {}
        start = 1_800_000_000.0
        outputs = []
        for hours in range(0, 7):
            results = self.evaluate(reading(int((1 + 0.5 * hours) * GIB)), params, store, start + hours * 3600)
            outputs.append(" | ".join(r.summary for r in results if isinstance(r, Result)) + " || " + ",".join(r.name for r in results if isinstance(r, Metric)))
        last = outputs[-1]
        self.assertIn("trend", last.lower())
        self.assertTrue("until" in last.lower() or "time left" in last.lower(), last)


if __name__ == "__main__":
    unittest.main()
