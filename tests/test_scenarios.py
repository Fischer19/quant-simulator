import csv
import io
import unittest

from quant_sim.backtest import DEFAULT_COST
from quant_sim.market import SYMBOLS, csv_text, load_snapshot
from quant_sim.scenarios import BENCHMARK, CHOICES, HORIZON, MIN_SPACING, ScenarioStudy, is_selloff


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_snapshot()
        cls.study = ScenarioStudy(cls.snapshot)

    def test_full_exports_share_history_from_2015(self):
        days = []
        for symbol in SYMBOLS:
            rows = csv.DictReader(io.StringIO(csv_text(self.snapshot, symbol)))
            dates = [row["date"] for row in rows]
            self.assertEqual(dates[0], "2015-01-02")
            self.assertEqual(dates[-1], self.snapshot["as_of"])
            self.assertNotIn("2018-12-05", dates)
            self.assertNotIn("2025-01-09", dates)
            days.append(dates)
        self.assertTrue(all(dates == days[0] for dates in days))

    def test_matches_are_ranked_distinct_and_fully_resolved_at_reference(self):
        study = self.study
        self.assertIsNotNone(study.reference)
        self.assertTrue(is_selloff(study.profiles[study.reference]))
        self.assertFalse(any(is_selloff(profile) for index, profile in study.profiles.items() if index > study.reference))
        self.assertEqual(len(study.matches), 3)
        scores = [score for _, score in study.matches]
        self.assertEqual(scores, sorted(scores))
        prior = []
        for index, _ in study.matches:
            self.assertTrue(is_selloff(study.profiles[index]))
            self.assertLessEqual(index + HORIZON, study.reference)
            self.assertTrue(all(abs(index - earlier) >= MIN_SPACING for earlier in prior))
            prior.append(index)

    def test_matching_does_not_use_days_after_reference(self):
        study = self.study
        prefix = {
            **self.snapshot,
            "as_of": study.days[study.reference],
            "bars": {symbol: bars[:study.reference + 1] for symbol, bars in study.bars.items()},
        }
        earlier = ScenarioStudy(prefix)
        self.assertEqual(study.matches, earlier.matches)

    def test_horizons_for_each_symbol_and_strategy(self):
        for symbol in SYMBOLS:
            for strategy, _ in CHOICES:
                with self.subTest(symbol=symbol, strategy=strategy):
                    result = self.study.result(symbol, strategy)
                    self.assertGreaterEqual(len(result["actual"]["path"]), 1)
                    self.assertLessEqual(len(result["actual"]["path"]), HORIZON + 1)
                    for scenario in result["scenarios"]:
                        self.assertEqual(len(scenario["path"]), HORIZON + 1)
                        self.assertEqual(scenario["path"][0], 0)
                        self.assertEqual(scenario["path"][-1], scenario["return"])
                        self.assertLessEqual(scenario["drawdown"], 0)

    def test_benchmark_enters_on_next_open_after_the_selloff(self):
        path = self.study.result("QQQ", BENCHMARK)["scenarios"][0]["path"]
        first = self.study.matches[0][0]
        next_bar = self.study.bars["QQQ"][first + 1]
        self.assertAlmostEqual(path[1], (1 - DEFAULT_COST / 10_000) * next_bar["close"] / next_bar["open"] - 1)


if __name__ == "__main__":
    unittest.main()
