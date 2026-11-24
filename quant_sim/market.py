"""U.S. equity sessions and the daily Yahoo Finance snapshot."""

import csv
import io
import json
import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SYMBOLS = ("QQQ", "SOXX", "SPY")
NEW_YORK = ZoneInfo("America/New_York")
SOURCE = "Yahoo Finance"
WARMUP = 150
SETTLE_MINUTES = 20
HISTORY_START = date(2015, 1, 1)
PROVIDER_START = date(2014, 5, 1)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SNAPSHOT_PATH = DATA_DIR / "daily.json"
BAR_FIELDS = ("date", "open", "high", "low", "close", "adj_close", "volume")
CHART_FIELDS = ("open", "high", "low", "close", "volume")


class DataError(ValueError):
    pass


def one_year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def weekday_in_month(year, month, weekday, occurrence):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def observed(day):
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def easter_sunday(year):
    # Gregorian computus.
    century, year_in_century = divmod(year, 100)
    metonic = year % 19
    leap_centuries, century_remainder = divmod(century, 4)
    correction = (century + 8) // 25
    solar = (century - correction + 1) // 3
    epact = (19 * metonic + century - leap_centuries - solar + 15) % 30
    leap_years, year_remainder = divmod(year_in_century, 4)
    weekday = (32 + 2 * century_remainder + 2 * leap_years - epact - year_remainder) % 7
    offset = (metonic + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * offset + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=16)
def holidays(year):
    january_first = date(year, 1, 1)
    end_of_may = date(year, 5, 31)
    june_closure = {observed(date(year, 6, 19))} if year >= 2022 else set()
    special_closures = {date(2018, 12, 5), date(2025, 1, 9)}
    return {
        january_first + timedelta(days=int(january_first.weekday() == 6)),
        weekday_in_month(year, 1, 0, 3),
        weekday_in_month(year, 2, 0, 3),
        easter_sunday(year) - timedelta(days=2),
        end_of_may - timedelta(days=end_of_may.weekday()),
        observed(date(year, 7, 4)),
        weekday_in_month(year, 9, 0, 1),
        weekday_in_month(year, 11, 3, 4),
        observed(date(year, 12, 25)),
    } | june_closure | {day for day in special_closures if day.year == year}


def is_session(day):
    return day.weekday() < 5 and day not in holidays(day.year)


def session_end(day):
    early = (
        (day.month, day.day) in {(7, 3), (12, 24)}
        or day == weekday_in_month(day.year, 11, 3, 4) + timedelta(days=1)
    )
    return datetime.combine(day, time(13 if early else 16), tzinfo=NEW_YORK)


def latest_due_session(now):
    local = now.astimezone(NEW_YORK)
    day = local.date()
    while not is_session(day) or local < session_end(day) + timedelta(minutes=SETTLE_MINUTES):
        day -= timedelta(days=1)
    return day


def next_session(day):
    day += timedelta(days=1)
    while not is_session(day):
        day += timedelta(days=1)
    return day


def evaluation_start(last):
    return one_year_before(last) + timedelta(days=1)


def fetch_chart(symbol, due):
    start = datetime.combine(PROVIDER_START, time.min, tzinfo=NEW_YORK)
    end = datetime.combine(due + timedelta(days=1), time.min, tzinfo=NEW_YORK)
    params = urlencode({
        "period1": int(start.timestamp()), "period2": int(end.timestamp()),
        "interval": "1d", "includeAdjustedClose": "true",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=12) as response:
            return json.load(response)
    except (OSError, ValueError) as exc:
        raise DataError(f"{symbol} 行情请求失败（{exc}）") from exc


def parse_chart(symbol, payload, due):
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if meta["symbol"] != symbol or meta["dataGranularity"] != "1d":
            raise DataError(f"{symbol} 行情返回了不同的标的或周期")
        last_trade = datetime.fromtimestamp(meta["regularMarketTime"], NEW_YORK)
        if last_trade < session_end(due) - timedelta(minutes=1):
            raise DataError(f"{symbol} 的 {due} 收盘行情还未到达")
        timestamps = result["timestamp"]
        quotes = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
        rows = {}
        for i, timestamp in enumerate(timestamps):
            day = datetime.fromtimestamp(timestamp, NEW_YORK).date()
            if day > due or not is_session(day):
                continue
            values = {field: quotes[field][i] for field in CHART_FIELDS}
            values["adj_close"] = adjusted[i]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values.values()):
                raise DataError(f"{symbol} 在 {day} 的日线数据不完整")
            if (
                min(values["open"], values["close"], values["low"], values["adj_close"]) <= 0
                or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"])
                or values["volume"] <= 0
            ):
                raise DataError(f"{symbol} 在 {day} 的日线数据无效")
            if day in rows:
                raise DataError(f"{symbol} 在 {day} 返回了重复日线")
            rows[day] = {"date": day.isoformat(), **values}
    except (KeyError, IndexError, TypeError, OverflowError) as exc:
        raise DataError(f"{symbol} 的行情格式无法识别") from exc
    return rows


def build_snapshot(charts, now):
    due = latest_due_session(now)
    parsed = {symbol: parse_chart(symbol, charts[symbol], due) for symbol in SYMBOLS}
    missing_today = [symbol for symbol in SYMBOLS if due not in parsed[symbol]]
    if missing_today:
        raise DataError(f"{due} 的日线还未到齐（{', '.join(missing_today)}）")

    first = HISTORY_START
    for _ in range(WARMUP):
        first -= timedelta(days=1)
        while not is_session(first):
            first -= timedelta(days=1)
    expected = []
    day = first
    while day <= due:
        if is_session(day):
            expected.append(day)
        day += timedelta(days=1)

    bars = {}
    for symbol, rows in parsed.items():
        missing = [day for day in expected if day not in rows]
        if missing:
            raise DataError(f"{symbol} 缺少 {missing[0]} 等 {len(missing)} 个交易日")
        bars[symbol] = [rows[day] for day in expected]
    return {
        "source": SOURCE,
        "fetched_at": now.isoformat(timespec="seconds"),
        "as_of": due.isoformat(),
        "evaluation_start": evaluation_start(due).isoformat(),
        "history_start": HISTORY_START.isoformat(),
        "warmup_sessions": WARMUP,
        "bars": bars,
    }


def download_snapshot():
    # Freeze the eligibility cutoff before making any requests.
    now = datetime.now(NEW_YORK)
    due = latest_due_session(now)
    with ThreadPoolExecutor(max_workers=len(SYMBOLS)) as executor:
        requests = {symbol: executor.submit(fetch_chart, symbol, due) for symbol in SYMBOLS}
        charts = {symbol: request.result() for symbol, request in requests.items()}
    return build_snapshot(charts, now)


def save_snapshot(snapshot, path=SNAPSHOT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(snapshot, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def load_snapshot(path=SNAPSHOT_PATH):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def csv_text(snapshot, symbol):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=BAR_FIELDS)
    writer.writeheader()
    start = snapshot.get("history_start", snapshot["evaluation_start"])
    writer.writerows(bar for bar in snapshot["bars"][symbol] if bar["date"] >= start)
    return stream.getvalue()
