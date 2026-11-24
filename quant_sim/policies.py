"""Frozen multi-input policies, explicit variants, and virtual expert state."""

from dataclasses import dataclass, field, replace
from typing import Optional

from .context import CONFIG, valid_number
from .strategies import decision_rules


@dataclass(frozen=True)
class Policy:
    id: str
    number: str
    name: str
    group: str
    rule: str
    inputs: tuple
    breadth: str = "true"
    credit: str = "true"
    variant: str = "native"
    params: dict = field(default_factory=dict)
    version: str = CONFIG["version"]
    initialization: str = "flat_with_warm_experts"

    def dependencies(self):
        return tuple(
            {"breadth50": "breadth_proxy20", "breadth_delta20": "breadth_proxy_delta20"}.get(key, key)
            if self.breadth == "proxy" and key.startswith("breadth")
            else "credit_proxy20" if self.credit == "proxy" and key == "oas_delta20_bps" else key
            for key in self.inputs
        )


VOTE_INPUTS = {
    "trend": ("P", "ma100"),
    "volatility": ("vix",),
    "breadth": ("breadth50",),
    "credit": ("oas_delta20_bps",),
    "momentum": ("mom63",),
}
VOTE_FIELDS = tuple(key for fields in VOTE_INPUTS.values() for key in fields)
DUAL_FIELDS = ("dd252", "vix_peak20", "vix_relief20", "breadth_delta20", "oas_delta20_bps", "vix", "P", "ma200")

DEFAULTS = (
    Policy("trend_fear_filter", "S11", "趋势与 VIX 过滤", "多信号", "100 日趋势结合 VIX 25/30 双阈值。", ("P", "ma100", "vix")),
    Policy("panic_recovery", "S12", "恐慌后修复", "多信号", "回撤至少 10% 且 VIX 从高点回落后进场。", ("P", "dd252", "vix_peak20", "vix_relief20", "vix", "prior_low20")),
    Policy("trend_breadth", "S13", "趋势与市场广度", "多信号", "50 日趋势由市场广度确认，离场也要求两项同时触发。", ("P", "ma50", "breadth50")),
    Policy("drawdown_credit", "S14", "回撤与信用风险", "多信号", "回撤结合高收益债利差信号。", ("dd252", "oas_delta20_bps")),
    Policy("multi_signal_voting", "S15", "五信号投票", "多信号", "五票中至少 4 票持有，至多 2 票空仓，3 票维持。", VOTE_FIELDS),
    Policy("score_dynamic_exposure", "S16", "五信号动态仓位", "动态仓位", "与 S15 共用五票，每票在 25% 基础仓位上增加 15 个百分点。", VOTE_FIELDS),
    Policy("regime_strategy_switching", "S17", "市场状态切换", "动态仓位", "依序检查修复、下跌、上涨和其他状态，并使用持续更新的专家。", ("P", "ma20", "ma100", "dd252", "vix", "vix_relief20")),
    Policy("core_tactical", "S18", "核心与战术仓位", "动态仓位", "70% 核心加 30% 的 S16 目标，默认目标下限为 77.5%。", VOTE_FIELDS),
    Policy("volatility_targeting", "S19", "波动率目标", "动态仓位", "15% 除以 20 日年化波动率，仓位限制在 25% 至 100%。", ("rv20",), variant="price_only"),
    Policy("dual_score_allocation", "S20", "机会与风险双评分", "动态仓位", "分别计算三个机会票与三个风险票，从 3×3 表查目标仓位。", DUAL_FIELDS),
)
BY_ID = {policy.id: policy for policy in DEFAULTS}
BY_NUMBER = {policy.number: policy for policy in DEFAULTS}


def variant(policy, suffix, **changes):
    base = BY_NUMBER[policy] if isinstance(policy, str) else policy
    return replace(base, id=f"{base.id}__{suffix}", variant=suffix, **changes)


PROXY_POLICIES = tuple(
    variant(policy, suffix, breadth=breadth, credit=credit)
    for policy in DEFAULTS
    for suffix, breadth, credit in (
        ("breadth_proxy", "proxy", "true"),
        ("credit_proxy", "true", "proxy"),
        ("proxy_proxy", "proxy", "proxy"),
    )
    if (breadth != "proxy" or any(key.startswith("breadth") for key in policy.inputs))
    and (credit != "proxy" or "oas_delta20_bps" in policy.inputs)
)
REGISTRY = {policy.id: policy for policy in (*DEFAULTS, *PROXY_POLICIES)}


@dataclass(frozen=True)
class Intent:
    target: Optional[float]
    reason: str
    diagnostics: dict = field(default_factory=dict)
    status: str = "OK"


def fixed(weight):
    return Intent(weight, "固定仓位。")


def binary(enter, exit, previous, reason):
    return Intent(0.0 if exit else 1.0 if enter else previous, reason, {"entry": enter, "exit": exit})


def missing(inputs):
    return Intent(None, "缺少所需数据，维持现有现金及份额。", {"missing": inputs}, "DATA_MISSING")


def votes(features, policy):
    """One shared scoring routine, used without feature substitutions at runtime."""
    active = tuple(key for key in VOTE_INPUTS if key not in policy.params.get("omit", ()))
    required = [item for key in active for item in VOTE_INPUTS[key]]
    requirements = replace(policy, inputs=tuple(required)).dependencies()
    absent = [key for key in requirements if not valid_number(features.get(key))]
    if absent:
        return None, absent
    f = features
    criteria = {
        "trend": lambda: f["P"] > f["ma100"],
        "volatility": lambda: f["vix"] < policy.params.get("vix_entry", 25),
        "breadth": lambda: f["breadth_proxy20"] > 0 if policy.breadth == "proxy" else f["breadth50"] > .50,
        "credit": lambda: f["credit_proxy20"] >= 0 if policy.credit == "proxy" else f["oas_delta20_bps"] <= 0,
        "momentum": lambda: f["mom63"] > 0,
    }
    return {key: int(criteria[key]()) for key in active}, []


MATRIX = ((.75, .90, 1.00), (.50, .75, .90), (.25, .50, .75))


def dual_weight(opportunity, risk, mode="matrix"):
    if mode == "scalar":
        return min(1.0, max(.25, .625 + .125 * (opportunity - risk)))
    row = 1 if mode == "opportunity" else min(risk, 2)
    col = 1 if mode == "risk" else min(opportunity, 2)
    return MATRIX[row][col]


def evaluate(policy, features, previous=0.0, experts=None):
    f, p, number = features, policy.params, policy.number
    experts = experts or {}

    if number == "S18":
        alpha = p.get("alpha", .70)
        if not valid_number(alpha) or not 0 <= alpha <= 1:
            raise ValueError("核心仓位必须在 [0, 1]")
        if alpha == 1:
            return fixed(1.0)
        base = BY_NUMBER[p.get("tactical", "S16")]
        tactical = evaluate(replace(base, breadth=policy.breadth, credit=policy.credit), f, previous, experts)
        if tactical.target is None:
            return tactical
        return Intent(alpha + (1 - alpha) * tactical.target, "核心仓位加战术目标。",
                      {**tactical.diagnostics, "alpha": alpha, "tactical_target": tactical.target})

    if number in ("S15", "S16"):
        ballots, absent = votes(f, policy)
        if absent:
            return missing(absent)
        score = sum(ballots.values())
        normalized = score / len(ballots)
        info = {"votes": ballots, "score": score, "normalized_score": normalized}
        if number == "S15":
            rule = binary(normalized >= .8, normalized <= .4, previous, f"{score} / {len(ballots)} 票。")
            return replace(rule, diagnostics={**rule.diagnostics, **info})
        target = round(.25 + .75 * normalized, 12)
        return Intent(target, f"{score} / {len(ballots)} 票，目标仓位 {target:.0%}。", info)

    deps = policy.dependencies()
    absent = [key for key in deps if not valid_number(f.get(key))]
    if absent:
        return missing(absent)

    if number == "S11":
        ma = f[p.get("ma", "ma100")]
        return binary(f["P"] > ma and ("vix" not in deps or f["vix"] < p.get("vix_entry", 25)),
                      f["P"] < ma or ("vix" in deps and f["vix"] > p.get("vix_exit", 30)),
                      previous, "按趋势和设定的恐慌阈值执行双阈值规则。")
    if number == "S12":
        enter = f["dd252"] >= p.get("drawdown", .10)
        exit = f["dd252"] <= .05
        if "vix" in deps:
            enter = enter and f["vix_peak20"] > 30 and f["vix_relief20"] >= p.get("relief", .20)
            exit = exit or (f["vix"] > 35 and f["P"] < f["prior_low20"])
        elif p.get("stress_exit"):
            exit = exit or f["P"] < f["prior_low20"]
        return binary(enter, exit, previous, "按回撤与设定的恐慌修复规则进出。")
    if number == "S13":
        key = "breadth_proxy20" if policy.breadth == "proxy" else "breadth50"
        upper, lower = (.01, -.01) if policy.breadth == "proxy" else (.60, .40)
        entry = f["P"] > f["ma50"] and (key not in deps or f[key] > upper)
        exit = f["P"] < f["ma50"] and (key not in deps or f[key] < lower)
        return binary(entry, exit, previous, "50 日趋势与所选广度输入共同确认。")
    if number == "S14":
        entry, exit = f["dd252"] >= .10, f["dd252"] <= .03
        if "credit_proxy20" in deps:
            entry, exit = entry and f["credit_proxy20"] > 0, exit or f["credit_proxy20"] < -.03
        elif "oas_delta20_bps" in deps:
            entry, exit = entry and f["oas_delta20_bps"] < 0, exit or f["oas_delta20_bps"] > 100
        return binary(entry, exit, previous, "按回撤和所选信用条件进出。")
    if number == "S17":
        trend = f["P"]
        if f["dd252"] >= .10 and f["vix_relief20"] >= .20 and trend > f["ma20"]:
            regime, weight, expert = "recovery", 1.0, None
        elif f["dd252"] >= .10:
            regime, weight, expert = "bear", .25, None
        elif trend > f["ma100"] and f["vix"] < 25:
            regime, weight, expert = "bull", 1.0, p.get("bull", "sma")
        else:
            regime, weight, expert = "other", .50, p.get("other", "rsi")
        if expert and not p.get("fixed_gate"):
            weight = experts.get(expert)
            if weight is None:
                return missing([expert])
        return Intent(weight, f"当前规则状态 {regime}。", {"regime": regime, "expert": expert if not p.get("fixed_gate") else None})
    if number == "S19":
        if f["rv20"] < 0:
            return missing(["rv20"])
        return Intent(min(1., max(.25, p.get("vol_target", .15) / max(f["rv20"], 1e-6))), "按已实现波动率调整目标仓位。")
    if number == "S20":
        opportunity = {
            "drawdown": int(f["dd252"] >= .10),
            "panic_relief": int(f["vix_peak20"] > 30 and f["vix_relief20"] >= .20),
            "breadth_improvement": int(f["breadth_proxy_delta20" if policy.breadth == "proxy" else "breadth_delta20"] > 0),
        }
        risk = {
            "credit_stress": int(f["credit_proxy20"] < -.03 if policy.credit == "proxy" else f["oas_delta20_bps"] > p.get("credit_stress", 50)),
            "volatility": int(f["rv20"] > .25 if p.get("realized_risk") else f["vix"] > 30),
            "weak_trend": int(f["P"] < f["ma200"]),
        }
        o, r = sum(opportunity.values()), sum(risk.values())
        return Intent(dual_weight(o, r, p.get("mode", "matrix")), f"机会 {o} / 3，风险 {r} / 3。",
                      {"opportunity": o, "risk": r, "opportunity_votes": opportunity, "risk_votes": risk})
    raise ValueError(f"unknown policy {number}")


def virtual_experts(bars, rules=None):
    start = 140
    rules = decision_rules(bars, start) if rules is None else rules
    selected = ("sma", "rsi", "macd", "donchian", "bollinger")
    state = {key: 0.0 for key in selected}
    experts = [{} for _ in range(start)]
    for index in range(len(bars) - start):
        for key in selected:
            desired = rules[key][index].target
            if desired is not None:
                state[key] = desired
        experts.append(dict(state))
    return experts


def policy_intents(policy, frames, experts, state=0.0):
    result = []
    for features, active in zip(frames, experts):
        decision = evaluate(policy, features, state, active)
        result.append(decision)
        if decision.target is not None:
            state = decision.diagnostics.get("tactical_target", decision.target)
    return result
