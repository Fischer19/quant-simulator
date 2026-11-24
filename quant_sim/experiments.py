"""Reproducible offline experiments for the versioned strategy extension."""

import csv
import gzip
import hashlib
import json
import platform
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import exp, log
from pathlib import Path
from statistics import fmean, median

from .context import CACHE, CONFIG, CONFIG_PATH, ROOT, MarketContext
from .lab import LEGACY, PolicyLab
from .market import SNAPSHOT_PATH, SYMBOLS, save_snapshot
from .policies import BY_NUMBER, DEFAULTS, REGISTRY, Intent, Policy, VOTE_INPUTS, fixed, variant
from .strategies import percentile

EXPERIMENTS = tuple(f"E{i:02d}" for i in range(11))
LEGACY_MANIFEST = ROOT / "reports/E00-legacy-2026-11-23-v1.json"


@dataclass(frozen=True)
class Case:
    id: str
    experiment: str
    policy: Policy = None
    weight: float = None
    experts: tuple = ()
    legacy: str = None

    @property
    def data_variant(self):
        return self.policy.variant if self.policy else "price_only"


def numbered(number, proxy=False):
    item = BY_NUMBER[number]
    if not proxy:
        return item
    suffix = {"S13": "breadth_proxy", "S14": "credit_proxy"}.get(number, "proxy_proxy")
    return REGISTRY[f"{item.id}__{suffix}"]


def preregistered_cases():
    yield from (Case(f"fixed_{weight:g}", "E01", weight=weight) for weight in (0., .25, .50, .75, 1.))
    yield from (Case(f"legacy_{name}", "E01", legacy=name) for name in LEGACY)
    for policy in REGISTRY.values():
        experiment = {"S16": "E03", "S17": "E04", "S18": "E05", "S19": "E06", "S20": "E07"}.get(policy.number, "E02")
        yield Case(policy.id, experiment, policy)
    controls = {
        "S11": ("P", "ma100"), "S12": ("dd252",), "S13": ("P", "ma50"), "S14": ("dd252",),
    }
    for number, inputs in controls.items():
        policy = variant(number, "price_control", inputs=inputs)
        yield Case(policy.id, "E02", policy)
    policy = variant("S12", "price_stress_exit", inputs=("P", "dd252", "prior_low20"), params={"stress_exit": True})
    yield Case(policy.id, "E02", policy)
    policy = variant("S15", "price_control", params={"omit": ("volatility", "breadth", "credit")})
    yield Case(policy.id, "E02", policy)
    for key in ("sma", "rsi"):
        yield Case(f"warm_expert_{key}", "E04", experts=((key, 1.),))
    yield Case("warm_expert_mixture", "E04", experts=(("sma", .5), ("rsi", .5)))
    for suffix, params in (
        ("fixed_gate", {"fixed_gate": True}),
        ("macd_bollinger", {"bull": "macd", "other": "bollinger"}),
        ("donchian_bollinger", {"bull": "donchian", "other": "bollinger"}),
    ):
        policy = variant("S17", suffix, params=params)
        yield Case(policy.id, "E04", policy)

    for base in (numbered("S18"), numbered("S18", True)):
        for tactical in ("S15", "S16"):
            for fraction in CONFIG["core_fractions"]:
                if fraction == .70 and tactical == "S16":
                    continue  # This is the registered default.
                policy = variant(base, f"{'proxy' if base.breadth == 'proxy' else 'native'}_{tactical}_core_{fraction:g}",
                                 params={"alpha": fraction, "tactical": tactical})
                yield Case(policy.id, "E05", policy)
    for target in CONFIG["vol_targets"]:
        if target != .15:
            policy = variant("S19", f"vol_{target:g}", params={"vol_target": target})
            yield Case(policy.id, "E06", policy)

    for base in (numbered("S20"), numbered("S20", True)):
        label = "proxy" if base.breadth == "proxy" else "native"
        for mode in ("opportunity", "risk", "scalar", "realized_risk"):
            params, inputs = ({"mode": mode}, base.inputs) if mode != "realized_risk" else (
                {"realized_risk": True}, tuple("rv20" if key == "vix" else key for key in base.inputs))
            policy = variant(base, f"{label}_{mode}", params=params, inputs=inputs)
            yield Case(policy.id, "E07" if mode in ("scalar", "realized_risk") else "E08", policy)
        for threshold in (40, 60):
            if base.credit == "true":
                policy = variant(base, f"credit_{threshold}", params={"credit_stress": threshold})
                yield Case(policy.id, "E09", policy)
    for number in ("S15", "S16"):
        for base in (numbered(number), numbered(number, True)):
            label = "proxy" if base.breadth == "proxy" else "native"
            for omit in (*((key,) for key in VOTE_INPUTS), ("trend", "momentum")):
                policy = variant(base, f"{label}_no_{'_'.join(omit)}", params={"omit": omit})
                yield Case(policy.id, "E08", policy)
    for threshold in (20, 30):
        policy = variant("S11", f"vix_{threshold}", params={"vix_entry": threshold, "vix_exit": threshold + 5})
        yield Case(policy.id, "E09", policy)
    for period in (80, 120):
        policy = variant("S11", f"ma{period}", inputs=("P", f"ma{period}", "vix"), params={"ma": f"ma{period}"})
        yield Case(policy.id, "E09", policy)
    for key, values in (("relief", (.15, .25)), ("drawdown", (.08, .12))):
        for value in values:
            policy = variant("S12", f"{key}_{value:g}", params={key: value})
            yield Case(policy.id, "E09", policy)


def prepare(lab, symbol, case, first, stop, warm=False):
    if case.policy:
        return lab.intents(symbol, case.policy, first, stop, warm)
    if case.weight is not None:
        return [fixed(case.weight)] * (stop - first)
    if case.experts:
        return [fixed(sum(experts.get(key, 0.) * weight for key, weight in case.experts))
                for experts in lab.experts[symbol][first:stop]]
    rules = lab.legacy_rules[symbol][case.legacy][first - 140 : stop - 140]
    previous = 0.
    result = []
    for rule in rules:
        if rule.target is not None:
            previous = rule.target
        result.append(Intent(previous, rule.reason))
    return result


def longest_support(intent_lists, offset=0):
    best = (0, 0)
    start = 0
    for i, daily in enumerate(zip(*intent_lists)):
        if any(intent.status != "OK" for intent in daily):
            start = i + 1
        elif i + 1 - start > best[1] - best[0]:
            best = (start, i + 1)
    return (best[0] + offset, best[1] + offset)


def causal_events(frames, first):
    armed = {threshold: True for threshold in CONFIG["event_thresholds"]}
    previous = None
    events = []
    for index, features in enumerate(frames):
        dd = features["dd252"]
        if dd is None:
            continue
        for threshold, ready in armed.items():
            if dd < CONFIG["event_rearm"]:
                armed[threshold] = True
            elif ready and dd >= threshold and (previous is None or previous < threshold):
                armed[threshold] = False
                if index >= first and previous is not None:
                    events.append((index, threshold))
        previous = dd
    return events


def temporal_splits(first, stop):
    n = stop - first
    dev, validation, _ = CONFIG["fold_fractions"]
    a = first + int(n * dev)
    b = first + int(n * (dev + validation))
    return {"development": (first, a), "validation": (a, b), "test": (b, stop)}


def window_returns(values, benchmark, dates, horizon, step):
    for end in range(horizon, len(values), step):
        start = end - horizon
        window = values[start : end + 1]
        peak, drawdown = window[0], 0.
        for value in window[1:]:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1)
        yield {
            "horizon": horizon, "start": dates[start], "end": dates[end],
            "return": values[end] / values[start] - 1, "drawdown": drawdown,
            "benchmark": benchmark[end] / benchmark[start] - 1,
        }


def summarize_rolling(rows):
    if not rows:
        return {"sample_size": 0}
    returns = [row["return"] for row in rows]
    return {
        "sample_size": len(returns), "median": median(returns), "p10": percentile(returns, .10),
        "p90": percentile(returns, .90), "worst": min(returns),
        "worst_drawdown": min(row["drawdown"] for row in rows),
        "below_zero": fmean(value < 0 for value in returns), "below_minus_10": fmean(value < -.10 for value in returns),
        "beat_benchmark": fmean(row["return"] > row["benchmark"] for row in rows),
    }


def bootstrap_paired(left, right, block, seed):
    if len(left) != len(right) or len(left) <= block:
        return None
    logs = [log(b / a) - log(d / c) for (a, b), (c, d) in zip(zip(left, left[1:]), zip(right, right[1:]))]
    if len(logs) < 2 * block:
        return None
    prefix = [0.]
    for value in logs:
        prefix.append(prefix[-1] + value)
    rng = random.Random(seed)
    samples = []
    for _ in range(CONFIG["bootstrap_replicates"]):
        remainder, total = len(logs), 0.
        while remainder:
            length = min(remainder, block)
            start = rng.randrange(len(logs) - length + 1)
            total += prefix[start + length] - prefix[start]
            remainder -= length
        samples.append(exp(total * 252 / len(logs)) - 1)
    return [percentile(samples, .025), percentile(samples, .975)]


def json_write(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def csv_write(path, rows, gzip_output=False):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    names = list(dict.fromkeys(key for row in rows for key in row))
    open_file = gzip.open if gzip_output else open
    with open_file(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                          if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()} for row in rows)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def audit_legacy(snapshot):
    import subprocess

    from .backtest import Research
    from .scenarios import CHOICES, ScenarioStudy

    manifest = json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))
    if fingerprint(SNAPSHOT_PATH) != manifest["data_sha256"]:
        raw = subprocess.check_output(["git", "show", f'{manifest["code"]}:data/daily.json'], cwd=ROOT)
        if hashlib.sha256(raw).hexdigest() != manifest["data_sha256"]:
            raise ValueError("frozen legacy data hash mismatch")
        snapshot = json.loads(raw)
    study, research = ScenarioStudy(snapshot), Research(snapshot)
    for symbol in SYMBOLS:
        actual = {row["id"]: [row["latest"][key] for key in ("date", "action", "target")]
                  for row in research.symbols[symbol]["strategies"]}
        if actual != manifest["daily"][symbol]:
            raise ValueError(f"legacy daily signal regression for {symbol}")
        for strategy, _ in CHOICES:
            results = []
            for window in study.result(symbol, strategy)["scenarios"]:
                path = window.pop("path")
                results.append({**window, "path_sha256": hashlib.sha256(
                    json.dumps(path, separators=(",", ":")).encode()).hexdigest()})
            if results != manifest["results"][symbol][strategy]:
                raise ValueError(f"legacy event regression for {symbol} {strategy}")
    return manifest


def row_for(symbol, case, window, run, details=None):
    return {
        "symbol": symbol, "target_symbol": symbol, "context_symbol": symbol,
        "breadth_universe": CONFIG["breadth_universe"], "feature_version": CONFIG["feature_version"],
        "experiment": case.experiment, "policy": case.id, "variant": case.data_variant,
        "window": window, "start": run["bars"][0]["date"], "end": run["bars"][-1]["date"],
        "status": "ok", **run["stats"], **(details or {}),
    }


def value_curve(run):
    return [point["value"] for point in run["equity"]]


def versus(run, baseline):
    stats, benchmark = run["stats"], baseline["stats"]
    total = stats["nav"] - benchmark["nav"]
    return {"nav_difference": total, "excess_return_pp": 100 * total, "relative_wealth": stats["nav"] / benchmark["nav"] - 1}


def regime_summary(run):
    groups = {}
    transitions = 0
    previous = None
    scores = {}
    ballot_totals = {}
    for i, decision in enumerate(run["decisions"]):
        info = decision["diagnostics"]
        regime = info.get("regime")
        key = f'{info["opportunity"]}/{info["risk"]}' if "opportunity" in info else info.get("score")
        daily = run["equity"][i + 1]["value"] / run["equity"][i]["value"] - 1 if i + 1 < len(run["equity"]) else None
        if key is not None:
            scores.setdefault(str(key), []).append(daily)
        for group in ("votes", "opportunity_votes", "risk_votes"):
            for ballot, vote in info.get(group, {}).items():
                won, seen = ballot_totals.get(f"{group}.{ballot}", (0, 0))
                ballot_totals[f"{group}.{ballot}"] = won + vote, seen + 1
        if regime is not None:
            groups.setdefault(regime, []).append(daily)
            if previous is not None and previous != regime:
                transitions += 1
        previous = regime

    def summarize(groups):
        return {key: {"sessions": len(values), "mean_next_day_return": fmean(valid) if valid else None}
                for key, values in groups.items() for valid in [[value for value in values if value is not None]]}
    return {"regimes": summarize(groups), "transitions": transitions, "scores": summarize(scores),
            "vote_frequencies": {key: {"ones": won, "sessions": seen} for key, (won, seen) in ballot_totals.items()}}


def interval_stats(run, n):
    equity = run["equity"][:n + 1]
    dates = {point["date"] for point in equity}
    return {"return": equity[-1]["value"] - 1,
            "drawdown": min(point["drawdown"] for point in equity),
            "exposure": fmean(point["position"] for point in equity),
            "missing": sum(point["data_status"] != "OK" for point in run["decisions"][:n + 1]),
            "trades": sum(fill["date"] in dates for fill in run["fills"])}


def export_logs(folder, name, run):
    base = folder / name
    for item in ("equity", "fills", "decisions"):
        if run[item]:
            csv_write(base.with_suffix(f".{item}.csv.gz"), run[item], True)


def ensure_support(intents):
    return any(intent.status == "OK" for intent in intents)


def selected_default(case):
    return bool(case.policy and case.policy.id in REGISTRY and case.policy.variant in
                {"native", "price_only", "proxy_proxy", "credit_proxy", "breadth_proxy"})


def classify(test, fixed, rolling, benchmark, nearby_excess, uncertainty):
    if (rolling["sample_size"] < 2 or uncertainty is None or nearby_excess is None
            or uncertainty[0] <= 0 <= uncertainty[1]):
        return "inconclusive"
    excess = test["cagr"] - fixed["cagr"] if test["cagr"] is not None and fixed["cagr"] is not None else 0
    if (excess > 0 and rolling["worst_drawdown"] > benchmark["worst_drawdown"]
            and rolling["median"] >= benchmark["median"] - .02 and nearby_excess > 0):
        return "promising"
    if rolling["worst_drawdown"] > benchmark["worst_drawdown"] and test["return"] <= fixed["return"]:
        return "risk_control_only"
    return "no_incremental_value"


def run_experiments(snapshot, experiment="all"):
    if experiment == "E00":
        return LEGACY_MANIFEST if audit_legacy(snapshot) else None
    context = MarketContext(snapshot)
    lab = PolicyLab(snapshot, context)
    cases = tuple(preregistered_cases())
    config = {
        **CONFIG, "suite": experiment, "cases": [asdict(case) for case in cases],
        "development_events": "E00 windows known during design",
        "research_conclusion": "retrospective only",
        "default_execution": "next eligible open, daily, 5 percentage point band",
        "initialization": "continuous test replays policy state and starts cash",
        "selection_gate": "positive test CAGR vs development mean fixed; lower worst rolling 252 DD vs B&H; median rolling 252 return within 2pp of B&H; positive at 10bp; uncertainty check",
    }
    sources = {key: {field: value for field, value in series.items() if field not in ("prefix", "values")}
               for key, series in context.series.items()}
    run_inputs = {
        "target": fingerprint(SNAPSHOT_PATH), "public": fingerprint(CACHE / "public.json"),
        "breadth": fingerprint(CACHE / "breadth.csv"), "oas": fingerprint(CACHE / "oas.csv"),
        "frozen_legacy": fingerprint(LEGACY_MANIFEST), "settings": fingerprint(CONFIG_PATH),
        "code": {path.name: fingerprint(path) for path in [ROOT / "app.py", *sorted((ROOT / "quant_sim").glob("*.py"))]},
    }
    run_key = hashlib.sha256(json.dumps([config, run_inputs], sort_keys=True).encode()).hexdigest()[:12]
    run_id = f'{experiment}-{snapshot["as_of"]}-{run_key}'
    folder = ROOT / "reports/runs" / run_id
    if (folder / "run_manifest.json").exists():
        return folder
    if experiment == "all":
        audit_legacy(snapshot)
    folder.mkdir(exist_ok=True, parents=True)
    json_write(folder / "config.json", config)
    json_write(folder / "data_coverage.json", {"aligned": context.coverage(), "sources": sources, "errors": context.errors})
    metric_rows, event_rows, rolling_rows, primary_rows, comparisons, skipped = [], [], [], [], [], []
    scheduled_cases = [case for case in cases if experiment in ("all", "E10") or case.experiment in (experiment, "E01")]
    extra_lab = PolicyLab(snapshot, MarketContext(snapshot, public=context.public,
                          breadth=context.inputs["breadth"], oas=context.inputs["oas"], extra_lag=1)) if experiment in ("all", "E09") else None

    for symbol in SYMBOLS:
        days = [bar["date"] for bar in lab.bars[symbol]]
        first = max(CONFIG["warmup_sessions"], next(i for i, day in enumerate(days) if day >= CONFIG["start"]))
        stop = len(days)
        intents = {case.id: prepare(lab, symbol, case, first, stop) for case in scheduled_cases}
        baseline_case = next(case for case in cases if case.id == "fixed_1")
        baseline = lab.run(symbol, intents["fixed_1"], first, stop)
        runnable = [case for case in scheduled_cases if ensure_support(intents[case.id])]
        defaults = [case for case in runnable if selected_default(case)]
        # Include full-coverage policies in the shared leaderboard. A late licensed
        # history still gets its own native and pairwise interval without truncating
        # the main comparison for the public-data cohort.
        common_cases = [case for case in defaults if all(intent.status == "OK" for intent in intents[case.id])]
        common = longest_support([intents["fixed_1"], *(intents[case.id] for case in common_cases)], first)
        splits = temporal_splits(*common)
        base_splits = {name: lab.run(symbol, [fixed(1.)] * (b - a), a, b)
                       for name, (a, b) in splits.items() if b > a}
        base_test = value_curve(base_splits["test"])
        base_rolling = summarize_rolling(list(window_returns(base_test, base_test, days[splits["test"][0]:stop], 252, 1)))

        for case in scheduled_cases:
            orders = intents[case.id]
            if case not in runnable:
                message = lab.data_message(case.policy, symbol) or "no valid decision"
                skipped.append({"symbol": symbol, "experiment": case.experiment, "policy": case.id, "reason": message})
                metric_rows.append({"symbol": symbol, "experiment": case.experiment, "policy": case.id,
                                    "variant": case.data_variant, "window": "native", "status": "unavailable", "reason": message})
                continue
            window = lab.run(symbol, orders, first, stop)
            baseline_row = row_for(symbol, case, "continuous", window, versus(window, baseline))
            metric_rows.append(baseline_row)
            a, b = longest_support([orders], first)
            if (a, b) != (first, stop):
                native = lab.run(symbol, prepare(lab, symbol, case, a, b), a, b)
                native_bh = lab.run(symbol, [fixed(1.)] * (b - a), a, b)
                metric_rows.append(row_for(symbol, case, "strict_native_pairwise_bh", native, versus(native, native_bh)))
            if not selected_default(case):
                continue
            window = lab.add_diagnostics(window, symbol, case.policy, first)
            export_logs(folder, f"{symbol}-{case.id}", window)
            details = regime_summary(window)
            baseline_row["diagnostics"] = details
            if case not in common_cases:
                continue
            folds = {}
            for name, (a, b) in splits.items():
                if b - a <= 1:
                    continue
                orders = prepare(lab, symbol, case, a, b, warm=name != "development")
                fold = lab.run(symbol, orders, a, b)
                folds[name] = fold
                metric_rows.append(row_for(symbol, case, name, fold, versus(fold, base_splits[name])))
            if "test" not in folds or "development" not in folds:
                continue
            test = folds["test"]
            a, b = splits["test"]
            dev = folds["development"]["stats"]
            bh_dev = base_splits["development"]["stats"]
            tests = {}
            for kind, weight in (("mean_exposure", dev["exposure"]),
                                 ("risk", min(1., dev["volatility"] / bh_dev["volatility"]))):
                control = Case(f"{case.id}__dev_matched_{kind}", "E06" if kind == "risk" else "E03", weight=weight)
                run = lab.run(symbol, [fixed(weight)] * (b - a), a, b)
                tests[kind] = run
                metric_rows.append(row_for(symbol, control, "test", run, {"fixed_weight": weight, **versus(run, base_splits["test"])}))
                comparisons.append({"symbol": symbol, "policy": case.id, "comparison": control.id, **versus(test, run),
                                    "actual_volatility_mismatch": test["stats"]["volatility"] - run["stats"]["volatility"]})
            daily = value_curve(test)
            matched_values = value_curve(tests["mean_exposure"])
            horizon_summaries = {}
            for horizon in (252, 504):
                for style, step in (("overlapping", 1), ("nonoverlapping", horizon)):
                    rows = list(window_returns(daily, base_test, days[a:b], horizon, step))
                    for row in rows:
                        first_value = matched_values[lab.date_index[row["start"]] - a]
                        last_value = matched_values[lab.date_index[row["end"]] - a]
                        row.update({"symbol": symbol, "policy": case.id, "variant": case.data_variant, "style": style,
                                    "fixed_benchmark": last_value / first_value - 1})
                    rolling_rows.extend(rows)
                    summary = summarize_rolling(rows)
                    summary["beat_fixed"] = fmean(row["return"] > row["fixed_benchmark"] for row in rows) if rows else None
                    horizon_summaries[f"{horizon}_{style}"] = summary
            confidence = {str(block): bootstrap_paired(daily, matched_values, block, CONFIG["seed"])
                          for block in CONFIG["bootstrap_blocks"]}
            stress = lab.run(symbol, prepare(lab, symbol, case, a, b, True), a, b, 10)
            fixed_stress = lab.run(symbol, [fixed(dev["exposure"])] * (b - a), a, b, 10)
            nearby = (stress["stats"]["cagr"] - fixed_stress["stats"]["cagr"]
                      if stress["stats"]["cagr"] is not None else None)
            conclusion = classify(test["stats"], tests["mean_exposure"]["stats"], horizon_summaries["252_overlapping"],
                                  base_rolling, nearby, confidence["63"])
            primary_rows.append({
                "symbol": symbol, "policy": case.id, "label": case.policy.name, "number": case.policy.number,
                "variant": case.data_variant, "start": days[a], "end": days[b - 1],
                "splits": {name: [days[x], days[y - 1]] for name, (x, y) in splits.items()},
                "metrics": test["stats"], **versus(test, base_splits["test"]),
                "matched_fixed_excess_pp": versus(test, tests["mean_exposure"])["excess_return_pp"],
                "rolling": horizon_summaries, "intervals": confidence, "classification": conclusion,
                "504_note": "fewer than two nonoverlapping observations" if horizon_summaries["504_nonoverlapping"]["sample_size"] < 2 else "",
            })

        if experiment in ("all", "E02", "E03", "E04", "E07", "E08", "E10"):
            ids = {case.id: case for case in cases}
            pairs = []
            for number in ("S11", "S12", "S13", "S14", "S15"):
                base = numbered(number)
                ids_to_compare = [base.id] + ([numbered(number, True).id] if number in ("S13", "S14", "S15") else [])
                pairs.extend(("E02", key, f"{base.id}__price_control") for key in ids_to_compare)
            for proxy in (False, True):
                s15, s16, s20 = (numbered(number, proxy) for number in ("S15", "S16", "S20"))
                pairs.extend((group, left.id, right.id) for group, left, right in (
                    ("E03", s16, s15), ("E07", s20, s16)))
                for base in (s15, s16, s20):
                    if proxy:
                        pairs.append(("E08", base.id, BY_NUMBER[base.number].id))
                    for case in cases:
                        if case.policy and case.policy.number == base.number and case.experiment in ("E07", "E08"):
                            if case.policy.breadth == base.breadth and case.policy.credit == base.credit and case.id != base.id:
                                pairs.append((case.experiment, base.id, case.id))
            for control in ("warm_expert_sma", "warm_expert_rsi", "warm_expert_mixture",
                            f'{numbered("S17").id}__fixed_gate'):
                pairs.append(("E04", numbered("S17").id, control))
            for group, left, right in dict.fromkeys(pairs):
                if experiment not in ("all", "E10", group):
                    continue
                candidates = [intents[key] if key in intents else prepare(lab, symbol, ids[key], first, stop)
                              for key in (left, right)]
                start, end = longest_support(candidates, first)
                if end - start < 2:
                    skipped.append({"symbol": symbol, "experiment": group, "policy": left,
                                    "comparison": right, "reason": "no continuous common feature coverage"})
                    continue
                for schedule, band in (("daily", 0.), ("daily", .05)):
                    runs = [lab.run(symbol, prepare(lab, symbol, ids[key], start, end), start, end,
                                    band=band, schedule=schedule) for key in (left, right)]
                    comparisons.append({
                        "symbol": symbol, "experiment": group, "policy": left, "comparison": right,
                        "start": days[start], "end": days[end - 1], "schedule": schedule, "band": band,
                        **versus(*runs), "left_exposure": runs[0]["stats"]["exposure"],
                        "right_exposure": runs[1]["stats"]["exposure"],
                        "left_turnover": runs[0]["stats"]["turnover"], "right_turnover": runs[1]["stats"]["turnover"],
                        "left_return": runs[0]["stats"]["return"], "right_return": runs[1]["stats"]["return"],
                        "left_drawdown": runs[0]["stats"]["drawdown"], "right_drawdown": runs[1]["stats"]["drawdown"],
                    })

        if experiment in ("all", "E09", "E10"):
            for case in [baseline_case, *(case for case in runnable if case.weight is not None and case.id != baseline_case.id), *defaults]:
                orders = intents[case.id]
                costs = [(value, .05, "daily") for value in CONFIG["cost_stress_bps"] if value != CONFIG["cost_bps"]]
                schedules = [(5, band, "daily") for band in (0., .10)] + [(5, band, "weekly") for band in (0., .05)]
                for cost, band, schedule in (*costs, *schedules):
                    stress = lab.run(symbol, orders, first, stop, cost, band, schedule)
                    bmark = lab.run(symbol, intents["fixed_1"], first, stop, cost, band, schedule)
                    metric_rows.append(row_for(symbol, case, "stress", stress, {
                        "cost_bps": cost, "band": band, "schedule": schedule, **versus(stress, bmark)}))
                if extra_lab and case.policy:
                    lagged = prepare(extra_lab, symbol, case, first, stop)
                    run = extra_lab.run(symbol, lagged, first, stop)
                    metric_rows.append(row_for(symbol, case, "stress_lag_plus_1", run, versus(run, baseline)))

            events = causal_events(context.frames[symbol], first)
            legacy = json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))
            event_configs = [(index, "causal", threshold) for index, threshold in events]
            event_configs.extend((lab.date_index[day], "legacy_event_corrected", None)
                                 for day in legacy["legacy_historical_events"])
            event_cases = [case for case in runnable if selected_default(case) or case.experiment == "E01"]
            for index, kind, threshold in event_configs:
                horizons = CONFIG["event_horizons"] if kind == "causal" else [252]
                end = min(stop, index + max(horizons) + 1)
                bh = lab.run(symbol, [fixed(1.)] * (end - index), index, end)
                for case in event_cases:
                    initializations = (False, True) if selected_default(case) and kind == "legacy_event_corrected" else (False,)
                    for warm in initializations:
                        orders = prepare(lab, symbol, case, index, end, warm)
                        if not ensure_support(orders):
                            continue
                        run = lab.run(symbol, orders, index, end)
                        for horizon in horizons:
                            n = min(horizon, end - index - 1)
                            diagnostics = interval_stats(run, n)
                            benchmark = interval_stats(bh, n)
                            event_rows.append({
                                "symbol": symbol, "policy": case.id, "variant": case.data_variant, "type": kind,
                                "threshold": threshold, "start": days[index], "end": days[index + n],
                                "horizon": horizon, "observed_sessions": n, "right_censored": n < horizon,
                                "overlap": any(other != (index, threshold) and abs(index - other[0]) < horizon for other in events),
                                "initialization": "warm_policy" if warm else "flat_policy",
                                **diagnostics, "benchmark_return": benchmark["return"],
                                "excess_return_pp": 100 * (diagnostics["return"] - benchmark["return"]),
                            })
                if kind == "legacy_event_corrected":
                    for case in event_cases:
                        if not selected_default(case):
                            continue
                        orders = prepare(lab, symbol, case, index, end)
                        if ensure_support(orders):
                            report = lab.add_diagnostics(lab.run(symbol, orders, index, end), symbol, case.policy, index)
                            export_logs(folder, f"{symbol}-{days[index]}-{case.id}", report)

    csv_write(folder / "metrics.csv", metric_rows)
    csv_write(folder / "event_returns.csv", event_rows)
    csv_write(folder / "rolling_returns.csv.gz", rolling_rows, True)
    csv_write(folder / "comparisons.csv", comparisons)
    json_write(folder / "leaderboard.json", primary_rows)
    summary = {"run_id": run_id, "suite": experiment, "as_of": snapshot["as_of"], "coverage": context.coverage(),
               "data_errors": context.errors, "leaderboard": primary_rows,
               "comparisons": [row for row in comparisons if row.get("experiment") in ("E03", "E04", "E07")],
               "counts": {"metrics": len(metric_rows), "events": len(event_rows), "rolling": len(rolling_rows),
                          "comparisons": len(comparisons), "skipped": len(skipped)}}
    manifest = {
        "id": run_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "inputs": run_inputs,
        "runtime": platform.python_version(), "data_as_of": snapshot["as_of"], "sources": sources,
        "counts": summary["counts"], "skipped": skipped,
        "experiment_ids": EXPERIMENTS if experiment == "all" else [experiment],
        "outputs": sorted(p.name for p in folder.iterdir()),
    }
    if experiment == "all":
        save_snapshot(summary, ROOT / "reports/multisignal_summary.json")
    save_snapshot(manifest, folder / "run_manifest.json")
    return folder
