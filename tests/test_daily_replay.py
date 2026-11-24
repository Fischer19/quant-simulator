import csv
import io
import unittest
from datetime import date, datetime, timedelta

from quant_sim.backtest import Research, decisions_for, replay
from quant_sim.market import (
    NEW_YORK, SYMBOLS, DataError, build_snapshot, csv_text, latest_due_session,
    load_snapshot, next_session, session_end,
)
from quant_sim.strategies import Rule, decision_rules


def fake_bar(day, open_price, close):
    return {"date": day, "open": open_price, "close": close, "adj_close": close}


def quote_payload(symbol, bars, last_trade):
    return {"chart": {"result": [{
        "meta": {"symbol": symbol, "dataGranularity": "1d", "regularMarketTime": last_trade.timestamp()},
        "timestamp": [
            datetime.fromisoformat(bar["date"]).replace(hour=9, minute=30, tzinfo=NEW_YORK).timestamp()
            for bar in bars
        ],
        "indicators": {
            "quote": [{
                field: [bar[field] for bar in bars] for field in ("open", "high", "low", "close", "volume")
            }],
            "adjclose": [{"adjclose": [bar["adj_close"] for bar in bars]}],
        },
    }]}}


class DailyReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_snapshot()
        cls.research = Research(cls.snapshot)

    def test_signal_uses_next_open_and_last_signal_is_pending(self):
        bars = [
            fake_bar("2026-11-19", 9, 10),
            fake_bar("2026-11-20", 20, 30),
            fake_bar("2026-11-23", 40, 50),
        ]
        decisions = decisions_for(bars, [Rule(target, "", ()) for target in (1, 0, 1)])
        self.assertEqual([d["action"] for d in decisions], ["BUY", "SELL", "BUY"])
        equity, fills = replay(bars, [d["target"] for d in decisions], 5)
        self.assertEqual([point["position"] for point in equity], [0, 1, 0])
        self.assertEqual([(fill["signal_date"], fill["date"], fill["open"]) for fill in fills], [
            ("2026-11-19", "2026-11-20", 20),
            ("2026-11-20", "2026-11-23", 40),
        ])
        self.assertAlmostEqual(equity[-1]["value"], 2 * (1 - .0005) ** 2)
        self.assertEqual(decisions[-1]["execution"], {"date": "2026-11-24", "pending": True})

    def test_holiday_and_early_close_gate(self):
        thanksgiving = date(2026, 11, 26)
        friday = thanksgiving + timedelta(days=1)
        self.assertEqual(next_session(date(2026, 11, 25)), friday)
        self.assertEqual(session_end(friday).hour, 13)
        self.assertEqual(latest_due_session(datetime(2026, 11, 27, 13, 19, tzinfo=NEW_YORK)), date(2026, 11, 25))
        self.assertEqual(latest_due_session(datetime(2026, 11, 27, 13, 20, tzinfo=NEW_YORK)), friday)

    def test_missing_or_still_intraday_quote_blocks_refresh(self):
        source = self.snapshot
        due = date.fromisoformat(source["as_of"])
        close = session_end(due)
        now = close + timedelta(minutes=30)
        charts = {symbol: quote_payload(symbol, source["bars"][symbol], close) for symbol in SYMBOLS}
        self.assertEqual(build_snapshot(charts, now)["bars"], source["bars"])

        charts["SPY"] = quote_payload("SPY", source["bars"]["SPY"][:-1], close)
        with self.assertRaisesRegex(DataError, "SPY"):
            build_snapshot(charts, now)
        charts["SPY"] = quote_payload("SPY", source["bars"]["SPY"], close - timedelta(minutes=10))
        with self.assertRaisesRegex(DataError, "收盘行情"):
            build_snapshot(charts, now)

    def test_all_strategies_ignore_future_bars(self):
        for symbol in SYMBOLS:
            bars = self.snapshot["bars"][symbol]
            warmup = self.snapshot["warmup_sessions"]
            prefix_size = len(bars) - 15
            full = decision_rules(bars, warmup)
            prefix = decision_rules(bars[:prefix_size], warmup)
            for strategy in full:
                with self.subTest(symbol=symbol, strategy=strategy):
                    self.assertEqual(full[strategy][:prefix_size - warmup], prefix[strategy])

    def test_all_ledgers_line_up_with_the_same_exported_sessions(self):
        self.assertEqual(len(self.research.details), 30)
        for symbol in SYMBOLS:
            rows = list(csv.DictReader(io.StringIO(csv_text(self.snapshot, symbol))))
            exported = [row["date"] for row in rows]
            self.assertEqual(exported, sorted(set(exported)))
            self.assertLessEqual(exported[0], self.snapshot["evaluation_start"])
            days = [day for day in exported if day >= self.snapshot["evaluation_start"]]
            self.assertEqual(days[-1], self.snapshot["as_of"])
            for strategy in self.research.symbols[symbol]["strategies"]:
                with self.subTest(symbol=symbol, strategy=strategy["id"]):
                    detail = self.research.details[symbol, strategy["id"]]
                    self.assertEqual(days, [decision["date"] for decision in detail["decisions"]])
                    fill_days = [fill["date"] for fill in detail["fills"]]
                    self.assertEqual(fill_days, sorted(set(fill_days)))
                    for fill in detail["fills"]:
                        self.assertEqual(next_session(date.fromisoformat(fill["signal_date"])).isoformat(), fill["date"])
                    self.assertGreaterEqual(detail["sensitivity"][0], detail["sensitivity"][5])
                    self.assertGreaterEqual(detail["sensitivity"][5], detail["sensitivity"][10])


if __name__ == "__main__":
    unittest.main()
