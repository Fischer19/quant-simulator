"""Adapters between the multisignal portfolio ledger and existing dashboard."""

from dataclasses import asdict

from .context import CONFIG, MarketContext
from .policies import BY_NUMBER, REGISTRY, Intent, fixed, policy_intents, virtual_experts
from .portfolio import metrics, simulate
from .strategies import STRATEGIES, decision_rules


LEGACY = {strategy.id: strategy for strategy in STRATEGIES}


def is_percent(name):
    return (name in ("dd252", "rv20", "mom63", "vix_relief20", "breadth50", "breadth_delta20")
            or name.startswith(("breadth_proxy", "credit_proxy")))


VARIANT_NAMES = {
    "native": "原始数据", "price_only": "仅价格", "proxy_proxy": "广度与信用代理",
    "breadth_proxy": "广度代理", "credit_proxy": "信用代理",
}
CORRECTED_BENCHMARK = "corrected_benchmark"


class PolicyLab:
    def __init__(self, snapshot, context=None):
        self.context = context if context is not None else MarketContext(snapshot)
        self.bars = self.context.bars
        self.date_index = {day.isoformat(): i for i, day in enumerate(self.context.days)}
        self.legacy_rules = {symbol: decision_rules(bars, 140) for symbol, bars in self.bars.items()}
        self.experts = {symbol: virtual_experts(bars, self.legacy_rules[symbol]) for symbol, bars in self.bars.items()}

    def intents(self, symbol, policy, first, stop, warm_state=False):
        start = max(CONFIG["warmup_sessions"], first) if not warm_state else CONFIG["warmup_sessions"]
        start = min(start, stop)
        frames = self.context.frames[symbol][start:stop]
        experts = self.experts[symbol][start:stop]
        intents = policy_intents(policy, frames, experts)
        if start >= first:
            return [Intent(None, "历史指标预热中。", {"missing": ["warmup"]}, "DATA_MISSING")] * (start - first) + intents
        return intents[first - start:]

    def run(self, symbol, intents, first, stop, cost=CONFIG["cost_bps"],
            band=CONFIG["default_band"], schedule="daily"):
        bars = self.bars[symbol][first:stop]
        equity, fills, decisions = simulate(bars, intents, cost, band, schedule)
        return {"stats": metrics(bars, equity, fills, decisions), "equity": equity,
                "fills": fills, "decisions": decisions, "bars": bars}

    def data_message(self, policy, symbol):
        missing = []
        for key in policy.dependencies():
            if not any(frame.get(key) is not None for frame in self.context.frames[symbol]):
                missing.append(key)
        if not missing:
            return ""
        return "缺少 " + "、".join(missing) + " 的历史数据。"

    def add_diagnostics(self, run, symbol, policy, first):
        external_fields = {
            "vix": ("vix",), "vix_peak20": ("vix",), "vix_relief20": ("vix",),
            "breadth50": ("breadth",), "breadth_delta20": ("breadth",),
            "breadth_proxy20": ("rsp", "spy"), "breadth_proxy_delta20": ("rsp", "spy"),
            "oas_pp": ("oas",), "oas_delta20_bps": ("oas",), "credit_proxy20": ("hyg", "ief"),
        }
        keys = policy.dependencies()
        streams = sorted({stream for key in keys for stream in external_fields.get(key, ())})
        for i, decision in enumerate(run["decisions"], first):
            frame = self.context.frames[symbol][i]
            decision.update({
                "policy_id": policy.id,
                "version": policy.version,
                "inputs": {key: frame[key] for key in keys},
                "quality": {stream: self.context.metadata[stream][i] for stream in streams},
                "indicators": tuple((key, frame[key], "percent" if is_percent(key) else "number") for key in keys),
            })
        return run

    def study_window(self, symbol, strategy, start_day, end_day):
        first, stop = self.date_index[start_day], self.date_index[end_day] + 1
        policy = REGISTRY.get(strategy)
        intents = [fixed(1.)] * (stop - first) if strategy == CORRECTED_BENCHMARK else self.intents(symbol, policy, first, stop)
        if all(intent.status != "OK" for intent in intents):
            return {"date": start_day, "end": end_day, "available": False,
                    "reason": self.data_message(policy, symbol) or "此窗口没有可用信号。"}
        run = self.run(symbol, intents, first, stop)
        statistics = run["stats"]
        return {
            "date": start_day, "end": end_day, "available": True,
            "path": [point["value"] - 1 for point in run["equity"]],
            "exposure_path": [point["position"] for point in run["equity"]],
            **{key: statistics[key] for key in ("return", "drawdown", "trades", "missing", "exposure", "turnover")},
        }


class Extension:
    def __init__(self, lab, snapshot):
        self.lab = lab
        self.summary = {}
        self.details = {}
        first = next(i for day, i in lab.date_index.items() if day >= snapshot["evaluation_start"])
        stop = len(lab.date_index)
        for symbol in snapshot["bars"]:
            baseline = lab.run(symbol, [fixed(1.)] * (stop - first), first, stop)
            entries = []
            for policy in REGISTRY.values():
                intents = lab.intents(symbol, policy, first, stop)
                usable = any(intent.status == "OK" for intent in intents)
                run = lab.add_diagnostics(lab.run(symbol, intents, first, stop), symbol, policy, first)
                sensitivity = {}
                if usable:
                    for cost in CONFIG["cost_stress_bps"]:
                        variation = run if cost == CONFIG["cost_bps"] else lab.run(symbol, intents, first, stop, cost)
                        sensitivity[cost] = variation["stats"]["return"]
                overview = {
                    **asdict(policy), "latest": run["decisions"][-1], "is_extension": True,
                    "variant_label": VARIANT_NAMES[policy.variant],
                    "available": usable, "blocked_reason": "" if usable else
                    lab.data_message(policy, symbol) or "此窗口没有可用信号。",
                    "stats": run["stats"] if usable else None, "sensitivity": sensitivity,
                    "baseline_stats": baseline["stats"],
                }
                entries.append(overview)
                self.details[symbol, policy.id] = {
                    **overview, "symbol": symbol, "bars": run["bars"], "equity": run["equity"] if usable else [],
                    "fills": run["fills"], "decisions": run["decisions"], "benchmark": baseline["equity"],
                }
            self.summary[symbol] = entries

    def detail(self, symbol, strategy):
        detail = self.details.get((symbol, strategy))
        if not detail or detail["number"] not in ("S15", "S16") or not detail["available"]:
            return detail
        variant = REGISTRY[strategy].variant
        ids = [BY_NUMBER[number].id + (f"__{variant}" if variant != "native" else "") for number in ("S15", "S16")]
        first, stop = self.lab.date_index[detail["bars"][0]["date"]], len(self.lab.date_index)
        intents = [self.lab.intents(symbol, REGISTRY[key], first, stop) for key in ids]
        if any(intent.status != "OK" for rows in intents for intent in rows):
            return detail
        runs = [self.lab.run(symbol, rows, first, stop, band=0) for rows in intents]
        return {**detail, "comparison": {
            number: {"values": [point["value"] for point in run["equity"]], "stats": run["stats"]}
            for number, run in zip(("S15", "S16"), runs)
        }}

    def scenario_choices(self):
        choices = [(CORRECTED_BENCHMARK, "买入持有（分数仓位成交口径）", False)]
        choices.extend(
            (policy.id, f"{policy.number} {policy.name}" + (
                f"（{VARIANT_NAMES[policy.variant]}）" if "__" in policy.id else ""
            ), all(bool(self.lab.data_message(policy, symbol)) for symbol in self.summary))
            for policy in REGISTRY.values()
        )
        return choices
