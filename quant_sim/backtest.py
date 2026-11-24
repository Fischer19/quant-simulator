"""Replay a daily signal at the following session's open."""

from dataclasses import asdict
from datetime import date
from math import sqrt
from statistics import fmean, stdev

from .market import SYMBOLS, next_session
from .strategies import STRATEGIES, decision_rules

COSTS_BPS = (0, 5, 10)
DEFAULT_COST = 5


def action_for(position, target):
    if position == target:
        return "HOLD" if position else "CASH"
    return "BUY" if target else "SELL"


def replay(bars, targets, cost_bps):
    cost = cost_bps / 10_000
    position, cash, shares, previous_target = 0, 1.0, 0.0, 0
    fills, equity = [], []
    for i, (bar, target) in enumerate(zip(bars, targets)):
        if previous_target != position:
            adjusted_open = bar["open"] * bar["adj_close"] / bar["close"]
            equity_before = cash + shares * adjusted_open
            if previous_target:
                shares = cash * (1 - cost) / adjusted_open
                cash = 0.0
            else:
                cash = shares * adjusted_open * (1 - cost)
                shares = 0.0
            fills.append({
                "signal_date": bars[i - 1]["date"],
                "date": bar["date"],
                "action": action_for(position, previous_target),
                "open": bar["open"],
                "adjusted_open": adjusted_open,
                "cost": equity_before * cost,
            })
            position = previous_target
        value = cash + shares * bar["adj_close"]
        equity.append({"date": bar["date"], "value": value, "position": position})
        previous_target = target
    return equity, fills


def with_drawdown(equity):
    peak = 1.0
    for point in equity:
        peak = max(peak, point["value"])
        point["drawdown"] = point["value"] / peak - 1
    return equity


def stats(equity, fills):
    daily = [
        current["value"] / last["value"] - 1
        for last, current in zip(equity, equity[1:])
    ]
    dispersion = stdev(daily) if len(daily) >= 2 else 0
    return {
        "return": equity[-1]["value"] - 1,
        "drawdown": min(point["drawdown"] for point in equity),
        "sharpe": fmean(daily) / dispersion * sqrt(252) if dispersion else None,
        "trades": len(fills),
        "exposure": fmean(point["position"] for point in equity),
    }


def decisions_for(bars, rules):
    position = 0
    decisions = []
    for i, (bar, rule) in enumerate(zip(bars, rules)):
        target = position if rule.target is None else rule.target
        action = action_for(position, target)
        execution = None
        if action in ("BUY", "SELL"):
            if i + 1 < len(bars):
                execution = {"date": bars[i + 1]["date"], "pending": False}
            else:
                execution = {
                    "date": next_session(date.fromisoformat(bar["date"])).isoformat(),
                    "pending": True,
                }
        decisions.append({
            "date": bar["date"],
            "action": action,
            "position": position,
            "target": target,
            "reason": rule.reason,
            "indicators": rule.indicators,
            "execution": execution,
        })
        position = target
    return decisions


class Research:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.details = {}
        self.symbols = {}
        start = snapshot["evaluation_start"]
        for symbol in SYMBOLS:
            all_bars = snapshot["bars"][symbol]
            first = next(i for i, bar in enumerate(all_bars) if bar["date"] >= start)
            warmup = min(first, snapshot["warmup_sessions"])
            all_bars = all_bars[first - warmup :]
            first = warmup
            bars = all_bars[first:]
            rules = decision_rules(all_bars, first)
            baseline, baseline_fills = replay(bars, [1] * len(bars), DEFAULT_COST)
            with_drawdown(baseline)
            self.symbols[symbol] = {
                "last_close": bars[-1]["close"],
                "daily_change": bars[-1]["adj_close"] / bars[-2]["adj_close"] - 1,
                "first": bars[0]["date"],
                "sessions": len(bars),
                "benchmark": stats(baseline, baseline_fills),
                "strategies": [],
            }
            for strategy in STRATEGIES:
                decisions = decisions_for(bars, rules[strategy.id])
                targets = [decision["target"] for decision in decisions]
                equity, fills = replay(bars, targets, DEFAULT_COST)
                with_drawdown(equity)
                statistics = stats(equity, fills)
                sensitivity = {DEFAULT_COST: statistics["return"]}
                for cost in COSTS_BPS:
                    if cost != DEFAULT_COST:
                        scenario, _ = replay(bars, targets, cost)
                        sensitivity[cost] = scenario[-1]["value"] - 1
                overview = {
                    **asdict(strategy),
                    "latest": decisions[-1],
                    "stats": statistics,
                    "sensitivity": sensitivity,
                }
                self.symbols[symbol]["strategies"].append(overview)
                self.details[symbol, strategy.id] = {
                    **overview,
                    "symbol": symbol,
                    "bars": bars,
                    "decisions": decisions,
                    "fills": fills,
                    "equity": equity,
                    "benchmark": baseline,
                }

    def summary(self):
        return {
            "source": self.snapshot["source"],
            "fetched_at": self.snapshot["fetched_at"],
            "as_of": self.snapshot["as_of"],
            "evaluation_start": self.snapshot["evaluation_start"],
            "history_start": self.snapshot.get("history_start"),
            "warmup_sessions": self.snapshot["warmup_sessions"],
            "cost_bps": DEFAULT_COST,
            "symbols": self.symbols,
        }
