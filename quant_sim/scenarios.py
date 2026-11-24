"""Match historical selloffs and replay their subsequent trading sessions."""

from datetime import date
from math import sqrt
from statistics import fmean, pstdev

from .backtest import DEFAULT_COST, decisions_for, replay, stats, with_drawdown
from .market import SYMBOLS
from .strategies import STRATEGIES, decision_rules

HORIZON = 252
MIN_SPACING = 21
SPY_DROP = -0.03
MATCH_COUNT = 3
PERIODS = (1, 5, 20)
BENCHMARK = "benchmark"
CHOICES = ((BENCHMARK, "买入持有"), *((strategy.id, strategy.name) for strategy in STRATEGIES))


def profile_at(closes, index):
    current = closes[index]
    returns = tuple(current / closes[index - period] - 1 for period in PERIODS)
    drawdown = current / max(closes[index - 19 : index + 1]) - 1
    return (*returns, drawdown)


def flatten_profiles(profiles):
    return tuple(value for symbol in SYMBOLS for value in profiles[symbol])


def is_selloff(profiles):
    return profiles["SPY"][0] <= SPY_DROP and all(profiles[symbol][0] < 0 for symbol in SYMBOLS)


def nearest_matches(vectors, eligible, reference):
    """Scale by the historical distribution, then enforce spacing between matches."""
    if not eligible:
        return []
    scales = tuple(pstdev(values) for values in zip(*(vectors[index] for index in eligible)))
    anchor = vectors[reference]

    def distance(index):
        return sqrt(fmean(
            ((value - target) / scale) ** 2
            for value, target, scale in zip(vectors[index], anchor, scales) if scale
        )) if any(scales) else 0.0

    ranked = sorted((distance(index), index) for index in eligible)
    selected = []
    for score, index in ranked:
        if all(abs(index - other) >= MIN_SPACING for other, _ in selected):
            selected.append((index, score))
            if len(selected) == MATCH_COUNT:
                break
    return selected


class ScenarioStudy:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.bars = snapshot["bars"]
        self.days = [bar["date"] for bar in self.bars["SPY"]]
        self.warmup = snapshot["warmup_sessions"]
        self.history_start = snapshot.get("history_start", snapshot["evaluation_start"])
        closes = {symbol: [bar["adj_close"] for bar in self.bars[symbol]] for symbol in SYMBOLS}

        start = next(i for i, day in enumerate(self.days) if day >= self.history_start)
        self.first = max(start, self.warmup)
        self.profiles = {
            index: {symbol: profile_at(closes[symbol], index) for symbol in SYMBOLS}
            for index in range(self.first, len(self.days))
        }
        selloffs = [index for index, profiles in self.profiles.items() if is_selloff(profiles)]
        self.reference = selloffs[-1] if selloffs else None
        self.eligible = [] if self.reference is None else [
            index for index in selloffs if index + HORIZON <= self.reference
        ]
        vectors = {index: flatten_profiles(self.profiles[index]) for index in self.eligible}
        if self.reference is not None:
            vectors[self.reference] = flatten_profiles(self.profiles[self.reference])
        self.matches = nearest_matches(vectors, self.eligible, self.reference)

    def summary(self):
        return {
            "as_of": self.snapshot["as_of"],
            "data_start": self.days[self.first] if self.profiles else None,
            "horizon": HORIZON,
            "spacing": MIN_SPACING,
            "spy_drop": SPY_DROP,
            "candidate_count": len(self.eligible),
            "cost_bps": DEFAULT_COST,
            "strategies": CHOICES,
            "symbols": SYMBOLS,
            "reference": self.describe(self.reference) if self.reference is not None else None,
            "matches": [
                {**self.describe(index), "distance": score, "end": self.days[index + HORIZON]}
                for index, score in self.matches
            ],
        }

    def describe(self, index):
        return {"date": self.days[index], "profile": self.profiles[index]}

    def run_window(self, symbol, strategy, start, end, diagnostics=False):
        bars = self.bars[symbol][start : end + 1]
        if strategy == BENCHMARK:
            targets = [1] * len(bars)
        else:
            context = self.bars[symbol][start - self.warmup : end + 1]
            rules = decision_rules(context, self.warmup)[strategy]
            targets = [decision["target"] for decision in decisions_for(bars, rules)]
        equity, fills = replay(bars, targets, DEFAULT_COST)
        with_drawdown(equity)
        statistics = stats(equity, fills)
        result = {
            "date": self.days[start],
            "end": self.days[end],
            "path": [point["value"] - 1 for point in equity],
            "return": statistics["return"],
            "drawdown": statistics["drawdown"],
            "trades": statistics["trades"],
        }
        if diagnostics:
            result["exposure_path"] = [point["position"] for point in equity]
        return result

    def result(self, symbol, strategy, diagnostics=False):
        if self.reference is None:
            return {"actual": None, "scenarios": []}
        end = min(self.reference + HORIZON, len(self.days) - 1)
        actual = self.run_window(symbol, strategy, self.reference, end, diagnostics)
        return {
            "actual": actual,
            "scenarios": [
                self.run_window(symbol, strategy, index, index + HORIZON, diagnostics)
                for index, _ in self.matches
            ],
        }
