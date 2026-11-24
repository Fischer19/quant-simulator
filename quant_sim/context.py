"""Point-in-time market context and local-only provider snapshots."""

import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from pathlib import Path
from statistics import fmean, stdev
from urllib.request import Request, urlopen

from .market import (
    DATA_DIR, NEW_YORK, SYMBOLS, fetch_chart, is_session, next_session,
    parse_chart, save_snapshot, session_end,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/multisignal.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
CACHE = DATA_DIR / "context"
PUBLIC_PATH = CACHE / "public.json"
VIX_URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
PROXIES = ("RSP", "HYG", "IEF")
PROVIDER_START = date(2013, 9, 1)


def valid_number(value):
    return type(value) in (int, float) and isfinite(value)


@dataclass(frozen=True)
class Observation:
    day: date
    value: float
    available: datetime
    source: str
    unit: str
    quality: str = "ok"
    valid_members: int = None
    members: int = None


def cutoff(day):
    return session_end(day) + timedelta(minutes=CONFIG["cutoff_minutes_after_close"])


def shifted_day(day, steps):
    for _ in range(steps):
        day = next_session(day)
    return day


def observation(day, value, source, unit, lag=1, available=None, quality="ok", valid_members=None, members=None):
    release = datetime.combine(shifted_day(day, lag), time.min, NEW_YORK) if available is None else available
    if release.tzinfo is None or release.utcoffset() is None:
        raise ValueError(f"{source} 的 available_at 必须含时区")
    if release.astimezone(NEW_YORK).date() < day:
        raise ValueError(f"{source} 的 available_at 早于观察日")
    if is_session(day) and release < session_end(day):
        raise ValueError(f"{source} 的 available_at 早于当日收盘")
    return Observation(day, value, release, source, unit, quality, valid_members, members)


def align(days, observations, extra_lag=0):
    """Backward as-of availability join, capped at three target sessions."""
    def eligible_at(obs):
        if not extra_lag:
            return obs.available
        day = obs.available.astimezone(NEW_YORK).date()
        return datetime.combine(shifted_day(day, extra_lag), time.min, NEW_YORK)

    events = sorted(((eligible_at(obs), obs) for obs in observations), key=lambda event: event[0])
    values, metadata = [], []
    cursor = 0
    current = None
    for day in days:
        while cursor < len(events) and events[cursor][0] <= cutoff(day):
            release, obs = events[cursor]
            if current is None or obs.day >= current[1].day:
                current = release, obs
            cursor += 1
        if current is None:
            values.append(None)
            metadata.append({"quality": "missing"})
            continue
        release, obs = current
        age = 0
        observed = obs.day
        while observed < day and age <= CONFIG["carry_sessions"]:
            observed = next_session(observed)
            age += 1
        quality = "stale" if age > CONFIG["carry_sessions"] else obs.quality
        values.append(obs.value if quality == "ok" else None)
        metadata.append({
            "observed_at": obs.day.isoformat(),
            "available_at": release.isoformat(),
            "stale_sessions": age,
            "source": obs.source,
            "unit": obs.unit,
            "quality": quality,
            **({"valid_members": obs.valid_members, "members": obs.members} if obs.members is not None else {}),
        })
    return values, metadata


def read_authorized(path, kind):
    if not path.exists():
        return []
    observations = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row["date"])
            value = float(row["value"])
            reported = row.get("available_at", "")
            release = datetime.fromisoformat(reported.replace("Z", "+00:00")) if reported else None
            lag = CONFIG["external_lag_sessions"]
            source = row.get("source") or ("PIT S&P 500 breadth" if kind == "breadth" else "Authorized HY OAS")
            quality = "ok"
            valid_members = members = None
            if kind == "breadth":
                members, valid_members = int(row["members"]), int(row["valid_members"])
                if not (0 <= value <= 1 and 0 <= valid_members <= members and members > 0 and release):
                    raise ValueError(f"无效的 PIT 市场广度 {day}")
                if valid_members / members < CONFIG["breadth_min_coverage"]:
                    quality = "low_membership_coverage"
            elif not isfinite(value) or value < 0:
                raise ValueError(f"无效的 HY OAS {day}")
            observations.append(observation(day, value, source, "fraction" if kind == "breadth" else "pp",
                                            lag, release, quality, valid_members, members))
    return observations


def fetch_vix(due):
    request = Request(VIX_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=15) as response:
        raw = response.read()
    values = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        day = datetime.strptime(row["DATE"], "%m/%d/%Y").date()
        if PROVIDER_START <= day <= due and is_session(day):
            value = float(row["CLOSE"])
            if not isfinite(value) or value <= 0 or day.isoformat() in values:
                raise ValueError(f"无效的 VIX 收盘值 {day}")
            values[day.isoformat()] = value
    if not values:
        raise ValueError("Cboe 未返回所需覆盖范围")
    return {"source": VIX_URL, "unit": "index points", "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "values": values}


def fetch_prices(symbol, due):
    response = fetch_chart(symbol, due, PROVIDER_START)
    rows = parse_chart(symbol, response, due)
    if not rows:
        raise ValueError(f"{symbol} 未返回历史价格")
    data = {"source": f"Yahoo Finance chart/{symbol}", "unit": "adjusted USD",
            "raw_sha256": hashlib.sha256(json.dumps(response, separators=(",", ":")).encode()).hexdigest()}
    if symbol in SYMBOLS:
        data["prefix"] = [row for day, row in sorted(rows.items()) if day < date(2015, 1, 1)]
    else:
        data["values"] = {day.isoformat(): row["adj_close"] for day, row in rows.items()}
    return data


def load_public(path=PUBLIC_PATH):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"series": {}, "errors": {}}


def update_public(due, path=PUBLIC_PATH):
    """Refresh each provider in isolation, retaining the last verified vintage on failure."""
    previous = load_public(path)
    series, errors = dict(previous["series"]), {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = {"VIX": pool.submit(fetch_vix, due)}
        jobs.update({symbol: pool.submit(fetch_prices, symbol, due) for symbol in (*SYMBOLS, *PROXIES)})
        for name, job in jobs.items():
            try:
                fresh = job.result()
                if name == "VIX" and name in series:
                    fresh["values"] = {**series[name]["values"], **fresh["values"]}
                fresh["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                series[name] = fresh
            except (OSError, ValueError, KeyError) as exc:
                errors[name] = str(exc)
    result = {"series": series, "errors": errors}
    save_snapshot(result, path)
    return result


class MarketContext:
    def __init__(self, snapshot, public=None, breadth=None, oas=None, extra_lag=0):
        self.snapshot = snapshot
        self.public = load_public() if public is None else public
        self.errors = dict(self.public.get("errors", {}))
        self.inputs = {}
        for key, override in (("breadth", breadth), ("oas", oas)):
            try:
                self.inputs[key] = read_authorized(CACHE / f"{key}.csv", key) if override is None else override
            except (OSError, ValueError, KeyError) as exc:
                self.inputs[key] = []
                self.errors[key] = str(exc)
        self.series = self.public["series"]
        self.bars = {symbol: self.with_prefix(symbol) for symbol in SYMBOLS}
        self.days = [date.fromisoformat(bar["date"]) for bar in self.bars["SPY"]]
        lag = CONFIG["external_lag_sessions"]
        streams = {
            "vix": self.observations("VIX", lag),
            "rsp": self.observations("RSP", lag),
            "hyg": self.observations("HYG", lag),
            "ief": self.observations("IEF", lag),
            **self.inputs,
            "spy": [observation(day, bar["adj_close"], "Yahoo Finance SPY", "adjusted USD", lag)
                    for day, bar in zip(self.days, self.bars["SPY"])],
        }
        aligned = {key: align(self.days, stream, extra_lag) for key, stream in streams.items()}
        self.values = {key: pair[0] for key, pair in aligned.items()}
        self.metadata = {key: pair[1] for key, pair in aligned.items()}
        self.values["breadth_ratio"] = self.ratio("rsp", "spy")
        self.values["credit_ratio"] = self.ratio("hyg", "ief")
        self.frames = {symbol: self.features(symbol) for symbol in SYMBOLS}

    def observations(self, name, lag):
        series = self.series.get(name, {})
        return [observation(date.fromisoformat(day), value, series["source"], series["unit"], lag)
                for day, value in series.get("values", {}).items()]

    def with_prefix(self, symbol):
        bars = self.snapshot["bars"][symbol]
        # Require the same dated prefix for every target. Rebase the provider's
        # adjusted close to the existing snapshot using its first overlapping day.
        first = bars[0]["date"]
        prefixes = {key: {row["date"]: row for row in self.series.get(key, {}).get("prefix", [])}
                    for key in SYMBOLS}
        common = sorted(set.intersection(*(set(rows) for rows in prefixes.values())))
        common = [day for day in common if day < first]
        overlap = prefixes[symbol].get(first)
        if not overlap or not common or not all(first in rows for rows in prefixes.values()):
            return bars
        if any(next_session(date.fromisoformat(day)).isoformat() != following
               for day, following in zip(common, (*common[1:], first))):
            return bars
        factor = bars[0]["adj_close"] / overlap["adj_close"]
        return [{**prefixes[symbol][day], "adj_close": prefixes[symbol][day]["adj_close"] * factor}
                for day in common] + bars

    def ratio(self, left, right):
        return [
            x / y if x is not None and y is not None
            and self.metadata[left][i]["observed_at"] == self.metadata[right][i]["observed_at"] else None
            for i, (x, y) in enumerate(zip(self.values[left], self.values[right]))
        ]

    def features(self, symbol):
        prices = [bar["adj_close"] for bar in self.bars[symbol]]
        returns = [None] + [right / left - 1 for left, right in zip(prices, prices[1:])]
        external = self.values

        def change(series, i, periods, ratio=False):
            if i < periods or series[i] is None or series[i - periods] is None:
                return None
            return series[i] / series[i - periods] - 1 if ratio else series[i] - series[i - periods]

        breadth_proxy = [change(external["breadth_ratio"], i, 20, True) for i in range(len(prices))]
        frames = []
        for i, price in enumerate(prices):
            vix = external["vix"][i]
            vix_window = external["vix"][max(i - 19, 0) : i + 1]
            peak = max(vix_window) if len(vix_window) == 20 and None not in vix_window else None
            high = max(prices[i - 251 : i + 1]) if i >= 251 else None
            prior_oas = change(external["oas"], i, 20)
            frames.append({
                "P": price,
                **{f"ma{n}": fmean(prices[i - n + 1 : i + 1]) if i >= n - 1 else None for n in (20, 50, 80, 100, 120, 200)},
                "mom63": change(prices, i, 63, True), "high252": high,
                "dd252": 1 - price / high if high else None,
                "prior_low20": min(prices[i - 20 : i]) if i >= 20 else None,
                "rv20": stdev(returns[i - 19 : i + 1]) * 252 ** .5 if i >= 20 else None,
                "vix": vix, "vix_peak20": peak, "vix_relief20": 1 - vix / peak if peak else None,
                "breadth50": external["breadth"][i],
                "breadth_delta20": change(external["breadth"], i, 20),
                "breadth_proxy20": breadth_proxy[i], "breadth_proxy_delta20": change(breadth_proxy, i, 20),
                "oas_pp": external["oas"][i], "oas_delta20_bps": 100 * prior_oas if prior_oas is not None else None,
                "credit_proxy20": change(external["credit_ratio"], i, 20, True),
            })
        return frames

    def coverage(self):
        result = {}
        for name, values in self.values.items():
            dates = [day.isoformat() for day, value in zip(self.days, values) if value is not None]
            result[name] = {"first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                            "count": len(dates), "sessions": len(values)}
        return result
