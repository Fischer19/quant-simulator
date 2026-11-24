"""Cash and fractional-share next-open simulator for the extension."""

from datetime import date
from math import ceil, isclose, prod, sqrt
from statistics import fmean, stdev

from .context import CONFIG, cutoff, valid_number
from .market import next_session

EPSILON = 1e-12


def rebalance_required(actual, target, band):
    difference = abs(target - actual)
    if difference <= EPSILON:
        return False
    return target in (0, 1) or actual <= EPSILON or difference + EPSILON >= band


def adjusted_open(bar):
    raw, close, adjusted = bar.get("open"), bar.get("close"), bar.get("adj_close")
    if all(valid_number(value) and value > 0 for value in (raw, close, adjusted)):
        return raw * adjusted / close
    return None


def apply_trade(cash, shares, price, target, rate):
    equity = shares * price
    nav = equity + cash
    delta = target * nav - equity
    denominator = 1 + target * rate if delta >= 0 else 1 - target * rate
    notional = delta / denominator
    fee = abs(notional) * rate
    cash -= notional + fee
    shares += notional / price
    if abs(cash) <= EPSILON * nav:
        cash = 0.0
    if abs(shares * price) <= EPSILON * nav:
        shares = 0.0
    if cash < 0 or shares < 0 or not isclose(cash + shares * price, nav - fee, rel_tol=1e-11):
        raise ArithmeticError("cash, holdings and fees do not reconcile")
    return cash, shares, notional, fee


def scheduled(day, schedule):
    if schedule == "daily":
        return True
    if schedule == "weekly":
        return day.isocalendar()[:2] != next_session(day).isocalendar()[:2]
    raise ValueError("schedule must be daily or weekly")


def simulate(bars, intents, cost_bps=5, band=0.05, schedule="daily"):
    if len(bars) != len(intents) or not bars:
        raise ValueError("bars and decisions must have the same nonzero length")
    if not valid_number(cost_bps) or not 0 <= cost_bps < 10_000 or not 0 <= band <= 1:
        raise ValueError("invalid transaction cost or band")
    cash, shares, peak = 1.0, 0.0, 1.0
    pending, desired = None, None
    equity, fills, decisions = [], [], []
    rate = cost_bps / 10_000
    for bar, intent in zip(bars, intents):
        day = date.fromisoformat(bar["date"])
        price = adjusted_open(bar)
        before = equity[-1]["value"] if equity else 1.0
        opening_nav = cash + shares * price if price is not None else None
        pretrade_weight = shares * price / opening_nav if opening_nav is not None else None
        opening_exposure = equity[-1]["position"] if equity else 0.0
        net_open = opening_nav
        if pending:
            target, signal = pending
            if price is None:
                signal["order_status"] = "DEFERRED"
                signal.setdefault("deferred_dates", []).append(bar["date"])
            else:
                if rebalance_required(pretrade_weight, target, band):
                    cash, shares, notional, fee = apply_trade(cash, shares, price, target, rate)
                    net_open = cash + shares * price
                    fill = {
                        "signal_date": signal["date"], "date": bar["date"],
                        "action": "BUY" if notional > 0 else "SELL", "open": bar["open"],
                        "adjusted_open": price, "target": target, "pretrade_weight": pretrade_weight,
                        "posttrade_weight": shares * price / net_open, "pretrade_nav": opening_nav,
                        "posttrade_nav": net_open, "shares": shares, "cash": cash,
                        "notional": notional, "cost": fee, "turnover": abs(notional) / opening_nav,
                    }
                    fills.append(fill)
                    signal["order_status"] = "FILLED"
                    signal["execution"] = {"date": bar["date"], "pending": False}
                else:
                    signal["order_status"] = "SUPPRESSED"
                    signal["checked_open"] = bar["date"]
                    signal["execution"] = None
                pending = None

        close_equity = shares * bar["adj_close"]
        nav = cash + close_equity
        current = close_equity / nav
        peak = max(peak, nav)
        equity.append({
            "date": bar["date"], "value": nav, "drawdown": nav / peak - 1,
            "position": current, "shares": shares, "cash": cash, "open_nav": opening_nav,
            "overnight_exposure": opening_exposure,
            "intraday_exposure": shares * price / net_open if net_open is not None else None,
            "overnight": opening_nav / before - 1 if opening_nav is not None else None,
            "intraday": nav / net_open - 1 if net_open is not None else None,
        })
        target = intent.target
        action = "HOLD" if current > EPSILON else "CASH"
        order_status = intent.status if target is None else "OFF_SCHEDULE"
        decision = {
            "date": bar["date"], "cutoff": cutoff(day).isoformat(), "action": action, "position": current,
            "target": target, "previous_target": desired, "reason": intent.reason,
            "diagnostics": intent.diagnostics, "data_status": intent.status, "order_status": order_status,
            "execution": None,
        }
        if target is not None:
            if not valid_number(target) or not 0 <= target <= 1:
                raise ValueError(f"invalid target exposure on {day}: {target}")
            desired = target
            if scheduled(day, schedule):
                if pending:
                    pending[1]["order_status"] = "SUPERSEDED"
                    pending[1]["execution"] = None
                pending = (target, decision)
                decision["order_status"] = "NEXT_OPEN_CHECK"
                if rebalance_required(current, target, band):
                    decision["action"] = "BUY" if target > current else "SELL"
                    decision["execution"] = {"date": next_session(day).isoformat(), "pending": True}
        else:
            decision["action"] = "MISSING"
        decisions.append(decision)
    return equity, fills, decisions


def metrics(bars, equity, fills, decisions):
    n = len(equity) - 1
    returns = [right["value"] / left["value"] - 1 for left, right in zip(equity, equity[1:])]
    asset = [right["adj_close"] / left["adj_close"] - 1 for left, right in zip(bars, bars[1:])]
    nav = equity[-1]["value"]
    annual = nav ** (252 / n) - 1 if n >= 252 else None
    dispersion = stdev(returns) if len(returns) > 1 else None
    average = fmean(returns) if returns else None
    downside = sqrt(fmean(min(0., value) ** 2 for value in returns)) if returns else None
    dd = min(point["drawdown"] for point in equity)
    tail = sorted(returns)[:ceil(.05 * n)]
    best = sorted(range(n), key=lambda index: asset[index], reverse=True)[:10]
    worst = sorted(range(n), key=lambda index: asset[index])[:10]

    def capture(indices):
        denominator = sum(asset[i] for i in indices)
        return sum(returns[i] for i in indices) / denominator if indices and denominator else None

    exposures = [point["position"] for point in equity]
    overnight = [point["overnight"] for point in equity[1:] if point["overnight"] is not None]
    intraday = [point["intraday"] for point in equity[1:] if point["intraday"] is not None]
    cost_multiplier = prod(fill["posttrade_nav"] / fill["pretrade_nav"] for fill in fills)
    groups = {"0": 0, "(0,25%)": 0, "[25%,50%)": 0, "[50%,75%)": 0, "[75%,100%)": 0, "100%": 0}
    for weight in exposures:
        key = ("0" if weight < EPSILON else "100%" if weight > 1 - EPSILON
               else "(0,25%)" if weight < .25 else "[25%,50%)" if weight < .50
               else "[50%,75%)" if weight < .75 else "[75%,100%)")
        groups[key] += 1
    return {
        "nav": nav, "return": nav - 1, "cagr": annual, "drawdown": dd, "trades": len(fills),
        "volatility": dispersion * sqrt(252) if dispersion is not None else None,
        "sharpe": average / dispersion * sqrt(252) if dispersion else None,
        "sortino": average / downside * sqrt(252) if downside else None,
        "calmar": annual / -dd if dd and annual is not None else None,
        "expected_shortfall_95": fmean(tail) if tail else None,
        "exposure": fmean(exposures), "exposure_bands": groups,
        "turnover": sum(fill["turnover"] for fill in fills), "fees": sum(fill["cost"] for fill in fills),
        "upside_capture": capture([i for i, value in enumerate(asset) if value > 0]),
        "downside_capture": capture([i for i, value in enumerate(asset) if value < 0]),
        "best10_capture": capture(best), "worst10_capture": capture(worst),
        "overnight_compounded": prod(1 + value for value in overnight) - 1 if overnight else None,
        "intraday_compounded": prod(1 + value for value in intraday) - 1 if intraday else None,
        "trade_cost_multiplier": cost_multiplier, "priced_open_sessions": len(overnight),
        "missed_up_days": sum(value > 0 and equity[i]["position"] <= EPSILON
                              and (equity[i + 1]["intraday_exposure"] or 0) <= EPSILON for i, value in enumerate(asset)),
        "missing": sum(decision["data_status"] != "OK" for decision in decisions),
    }
