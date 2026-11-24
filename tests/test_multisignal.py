import math
import unittest
from datetime import date, datetime, timedelta
from statistics import stdev
from tempfile import TemporaryDirectory
from pathlib import Path

from quant_sim.context import MarketContext, NEW_YORK, align, observation, read_authorized
from quant_sim.experiments import audit_legacy, bootstrap_paired, causal_events, longest_support, window_returns
from quant_sim.lab import Extension, PolicyLab
from quant_sim.market import SYMBOLS, load_snapshot, next_session
from quant_sim.policies import BY_NUMBER, MATRIX, REGISTRY, Intent, dual_weight, evaluate, fixed, variant, virtual_experts
from quant_sim.portfolio import scheduled, simulate
from quant_sim.strategies import Rule


def bars_from(day, prices):
    bars = []
    for price in prices:
        bars.append({"date": day.isoformat(), "open": price, "close": price, "adj_close": price,
                     "low": price, "high": price, "volume": 100})
        day = next_session(day)
    return bars


class FeatureTests(unittest.TestCase):
    def test_exact_windows_and_relative_adjusted_prices(self):
        prices = [100 + i + (i % 5) for i in range(350)]
        data = bars_from(date(2024, 1, 2), prices)
        snapshot = {"bars": {symbol: data for symbol in SYMBOLS}}
        dates = [date.fromisoformat(bar["date"]) for bar in data]
        public = {"series": {
            "VIX": {"source": "fixture", "unit": "index points", "values": {
                day.isoformat(): 20 + i % 21 for i, day in enumerate(dates)}},
            "RSP": {"source": "fixture", "unit": "adjusted USD", "values": {
                day.isoformat(): 90 + i * 2 for i, day in enumerate(dates)}},
        }, "errors": {}}
        oas = [observation(day, 3 + i / 100, "fixture", "pp") for i, day in enumerate(dates)]
        market = MarketContext(snapshot, public=public, breadth=[], oas=oas)
        frames = market.frames["SPY"]
        self.assertIsNone(frames[250]["dd252"])
        i = 300
        current = frames[i]
        self.assertEqual(current["ma200"], sum(prices[i-199:i+1]) / 200)
        self.assertAlmostEqual(current["mom63"], prices[i] / prices[i-63] - 1)
        self.assertAlmostEqual(current["dd252"], 1 - prices[i] / max(prices[i-251:i+1]))
        self.assertEqual(current["prior_low20"], min(prices[i-20:i]))
        self.assertAlmostEqual(current["rv20"],
                               stdev(prices[j] / prices[j-1] - 1 for j in range(i-19, i+1)) * math.sqrt(252))
        vix_window = [20 + j % 21 for j in range(i-20, i)]
        self.assertEqual(current["vix_peak20"], max(vix_window))
        self.assertAlmostEqual(current["vix_relief20"], 1 - vix_window[-1] / max(vix_window))
        self.assertAlmostEqual(current["oas_delta20_bps"], 20)
        self.assertAlmostEqual(current["breadth_proxy20"],
                               ((90 + (i-1)*2) / prices[i-1]) / ((90 + (i-21)*2) / prices[i-21]) - 1)

    def test_release_after_cutoff_holiday_lag_and_staleness(self):
        last = date(2026, 11, 25)
        days = [last]
        for _ in range(5):
            days.append(next_session(days[-1]))
        delayed = observation(last, 3.9, "fixture", "pp", available=datetime(2026, 11, 27, 14, tzinfo=NEW_YORK))
        values, meta = align(days, [delayed])
        self.assertEqual(days[1], date(2026, 11, 27))
        self.assertEqual(values[:3], [None, None, 3.9])
        self.assertEqual(meta[4]["quality"], "stale")
        self.assertIsNone(values[4])
        self.assertEqual(align(days, [delayed], extra_lag=1)[0][:3], [None, None, 3.9])
        revised = observation(last, 99, "fixture", "pp",
                              available=datetime(2026, 12, 1, 23, tzinfo=NEW_YORK))
        self.assertEqual(align(days, [delayed, revised])[0][:4], values[:4])
        with self.assertRaises(ValueError):
            observation(last, 1., "fixture", "pp", available=datetime(2026, 11, 25, 14, tzinfo=NEW_YORK))

    def test_pit_breadth_membership_denominator(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "breadth.csv"
            file.write_text("date,value,available_at,valid_members,members\n"
                            "2026-11-23,0.6,2026-11-23T16:10:00-05:00,470,500\n")
            values, metadata = align([date(2026, 11, 23)], read_authorized(file, "breadth"))
        self.assertEqual(values, [None])
        self.assertEqual(metadata[0]["quality"], "low_membership_coverage")
        self.assertEqual(metadata[0]["valid_members"], 470)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.f = {
            "P": 110., "ma20": 100., "ma50": 100., "ma100": 100., "ma200": 100.,
            "dd252": .1, "vix": 20., "vix_peak20": 40., "vix_relief20": .2,
            "prior_low20": 105., "breadth50": .7, "breadth_delta20": .03,
            "breadth_proxy20": .02, "breadth_proxy_delta20": .01, "oas_delta20_bps": -15.,
            "credit_proxy20": .02, "mom63": .1, "rv20": .2,
        }

    def policy(self, number, **changes):
        return evaluate(BY_NUMBER[number], {**self.f, **changes}, .5, {"sma": 1., "rsi": 0.})

    def test_binary_boundary_exit_priority_and_missing(self):
        self.assertEqual(self.policy("S11", vix=25).target, .5)
        self.assertEqual(self.policy("S11", vix=30).target, .5)
        self.assertEqual(self.policy("S11", vix=30.01).target, 0.)
        self.assertEqual(self.policy("S12").target, 1.)
        self.assertEqual(self.policy("S12", vix_peak20=30).target, .5)
        self.assertEqual(self.policy("S12", P=104., vix=36.).target, 0.)
        self.assertEqual(self.policy("S13", P=90., breadth50=.60).target, .5)
        self.assertEqual(self.policy("S13", P=90., breadth50=.39).target, 0.)
        self.assertEqual(self.policy("S14", oas_delta20_bps=100).target, .5)
        self.assertEqual(self.policy("S14", oas_delta20_bps=101).target, 0.)
        self.assertIsNone(self.policy("S15", breadth50=float("nan")).target)
        self.assertIsNone(self.policy("S16", vix=None).target)

    def test_identical_votes_dynamic_mapping_and_core_endpoints(self):
        rows = [("P", 99.), ("vix", 30.), ("breadth50", .40), ("oas_delta20_bps", 1.), ("mom63", -.1)]
        f = self.f.copy()
        for score in range(5, -1, -1):
            binary = evaluate(BY_NUMBER["S15"], f, .5)
            dynamic = evaluate(BY_NUMBER["S16"], f, .5)
            self.assertEqual(binary.diagnostics["votes"], dynamic.diagnostics["votes"])
            self.assertEqual(dynamic.diagnostics["score"], score)
            self.assertAlmostEqual(dynamic.target, .25 + .15 * score)
            self.assertEqual(binary.target, 1 if score >= 4 else 0 if score <= 2 else .5)
            if score:
                key, value = rows[5-score]
                f[key] = value
        self.assertAlmostEqual(evaluate(BY_NUMBER["S18"], f).target, .775)
        self.assertEqual(evaluate(variant("S18", "zero", params={"alpha": 0}), f).target, .25)
        self.assertEqual(evaluate(variant("S18", "full", params={"alpha": 1}), {}).target, 1.)

    def test_switching_order_and_warm_experts(self):
        self.assertEqual(self.policy("S17").diagnostics["regime"], "recovery")
        self.assertEqual(self.policy("S17", P=99.).target, .25)
        self.assertEqual(self.policy("S17", dd252=.09).diagnostics["expert"], "sma")
        self.assertEqual(self.policy("S17", dd252=.09, vix=25.).diagnostics["expert"], "rsi")
        self.assertIsNone(evaluate(BY_NUMBER["S17"], {**self.f, "dd252": .09}, experts={}).target)
        names = ("sma", "rsi", "macd", "donchian", "bollinger")
        legacy = {name: [Rule(None, "", ())] * 3 for name in names}
        legacy["rsi"] = [Rule(1, "", ()), Rule(None, "", ()), Rule(0, "", ())]
        inputs = virtual_experts([{}] * 143, legacy)
        self.assertEqual([row["rsi"] for row in inputs[-3:]], [1, 1, 0])

    def test_dual_matrix_edges_and_volatility(self):
        for risk in range(3):
            for opportunity in range(3):
                self.assertEqual(dual_weight(opportunity, risk), MATRIX[risk][opportunity])
        self.assertEqual(dual_weight(3, 3), .75)
        self.assertEqual(self.policy("S19", rv20=0.).target, 1.)
        self.assertEqual(self.policy("S19", rv20=1.).target, .25)
        self.assertIsNone(self.policy("S19", rv20=-.01).target)
        proxy = REGISTRY["dual_score_allocation__proxy_proxy"]
        intent = evaluate(proxy, {**self.f, "credit_proxy20": -.04, "oas_delta20_bps": -10000})
        self.assertEqual(intent.diagnostics["risk_votes"]["credit_stress"], 1)
        self.assertEqual(intent.diagnostics["opportunity"], 3)
        self.assertEqual(intent.target, .90)


class PortfolioTests(unittest.TestCase):
    def test_overnight_gap_drift_and_fair_fee(self):
        bars = bars_from(date(2026, 11, 23), [100., 200., 300.])
        equity, fills, decisions = simulate(bars, [fixed(.5)] * 3, 0, 0)
        self.assertEqual([point["value"] for point in equity], [1., 1., 1.25])
        self.assertEqual(fills[0]["signal_date"], bars[0]["date"])
        self.assertEqual(fills[0]["date"], bars[1]["date"])
        self.assertAlmostEqual(fills[1]["pretrade_weight"], .6)
        self.assertAlmostEqual(fills[1]["posttrade_weight"], .5)
        self.assertEqual(decisions[-1]["order_status"], "NEXT_OPEN_CHECK")
        bars = bars_from(date(2026, 11, 23), [100., 100., 100.])
        ledger, trades, _ = simulate(bars, [fixed(1.), fixed(0.), fixed(0.)], 25)
        rate = .0025
        self.assertAlmostEqual(ledger[-1]["value"], (1-rate)/(1+rate))
        for trade in trades:
            self.assertAlmostEqual(trade["cost"], abs(trade["notional"]) * rate)
            self.assertAlmostEqual(trade["posttrade_nav"], trade["pretrade_nav"] - trade["cost"])
            self.assertGreaterEqual(trade["cash"], 0)

    def test_hold_on_missing_no_band_and_invalid_target(self):
        bars = bars_from(date(2026, 11, 23), [100., 100., 102., 103.])
        bars[1]["open"] = None
        empty = Intent(None, "fixture", status="DATA_MISSING")
        _, fills, decisions = simulate(bars, [fixed(.25), empty, fixed(.26), empty], 5)
        self.assertEqual(decisions[0]["deferred_dates"], [bars[1]["date"]])
        self.assertEqual(len(fills), 1)
        self.assertEqual(decisions[1]["action"], "MISSING")
        self.assertEqual(decisions[2]["order_status"], "SUPPRESSED")
        with self.assertRaises(ValueError):
            simulate(bars, [fixed(1.01)] * len(bars))
        self.assertTrue(scheduled(date(2026, 12, 24), "weekly"))
        self.assertFalse(scheduled(date(2026, 12, 23), "weekly"))

    def test_adjusted_units_apply_split_only_once(self):
        bars = bars_from(date(2026, 11, 23), [100., 100., 50.])
        bars[0]["adj_close"] = bars[1]["adj_close"] = 50.
        equity, fills, _ = simulate(bars, [fixed(1.)] * len(bars), 0)
        self.assertEqual([point["value"] for point in equity], [1., 1., 1.])
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["adjusted_open"], 50.)


class ResearchTests(unittest.TestCase):
    def test_event_rearm_strict_coverage_and_rolling_drawdown(self):
        frames = [{"dd252": value} for value in (0., .10, .15, .09, .20, .05, .10, .04, .10)]
        events = causal_events(frames, 0)
        self.assertEqual([i for i, threshold in events if threshold == .10], [1, 8])
        blank = Intent(None, "", status="DATA_MISSING")
        self.assertEqual(longest_support([[fixed(.5), blank, fixed(.5), fixed(.5)], [fixed(1.)] * 4]), (2, 4))
        first = next(window_returns([1., 1.2, .9], [1., 1.3, 1.1], ["a", "b", "c"], 2, 1))
        self.assertAlmostEqual(first["drawdown"], -.25)
        self.assertAlmostEqual(first["return"], -.10)
        values = [1. + i / 100 for i in range(80)]
        self.assertEqual(bootstrap_paired(values, values, 21, 7), [0., 0.])

    def test_exact_reproduction_and_blocked_native_ui_data(self):
        snapshot = load_snapshot()
        manifest = audit_legacy(snapshot)
        self.assertEqual(len(manifest["legacy_historical_events"]), 3)
        context = MarketContext(snapshot, public={"series": {}, "errors": {}}, breadth=[], oas=[])
        extension = Extension(PolicyLab(snapshot, context), snapshot)
        for symbol in SYMBOLS:
            direct = extension.details[symbol, "trend_breadth"]
            self.assertFalse(direct["available"])
            self.assertIsNone(direct["latest"]["target"])
            self.assertIsNone(direct["stats"])
            self.assertTrue(extension.details[symbol, "volatility_targeting"]["available"])


if __name__ == "__main__":
    unittest.main()
