"""Daily, long-or-cash strategy rules shared by replay and current decisions."""

from dataclasses import dataclass
from math import sqrt
from statistics import fmean, pstdev, stdev


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    group: str
    rule: str


STRATEGIES = (
    Strategy("sma", "双均线交叉", "趋势", "20 日均线高于 60 日均线时持有，否则空仓。"),
    Strategy("macd", "MACD", "趋势", "MACD(12, 26) 高于 9 日信号线时持有，否则空仓。"),
    Strategy("donchian", "唐奇安通道", "突破", "突破此前 55 日最高价时买入，跌破此前 20 日最低价时卖出。"),
    Strategy("momentum", "63 日动量", "动量", "过去 63 个交易日收益为正时持有，否则空仓。"),
    Strategy("rsi", "RSI(2) 回调", "反转", "100 日均线之上且 RSI < 10 时买入，RSI > 70 或跌破均线时卖出。"),
    Strategy("bollinger", "布林带回归", "反转", "跌破 20 日下轨时买入，回到中轨时卖出。下轨为均线减 2 倍标准差。"),
    Strategy("adx", "ADX 趋势过滤", "趋势", "ADX(14) > 25 且 +DI > −DI 时持有，否则空仓。"),
    Strategy("obv", "OBV 量能趋势", "成交量", "OBV 高于自身 20 日均线时持有，否则空仓。"),
    Strategy("volatility", "波动率过滤", "风险过滤", "价格高于 50 日均线，且 20 日波动率低于此前 120 日的第 75 百分位时持有。"),
    Strategy("ibs", "IBS 区间反转", "反转", "收盘位于当日高低区间底部 20% 时买入，进入顶部 20% 时卖出。"),
)


@dataclass(frozen=True)
class Rule:
    target: object  # None preserves the existing position.
    reason: str
    indicators: tuple


def moving_average(values, i, period):
    return fmean(values[i - period + 1 : i + 1])


def ema(values, period):
    alpha = 2 / (period + 1)
    smoothed = [values[0]]
    for value in values[1:]:
        smoothed.append(smoothed[-1] + alpha * (value - smoothed[-1]))
    return smoothed


def wilder(values, period):
    smoothed = [None] * min(len(values), period - 1)
    if len(values) >= period:
        smoothed.append(fmean(values[:period]))
        for value in values[period:]:
            smoothed.append(smoothed[-1] + (value - smoothed[-1]) / period)
    return smoothed


def rsi_series(close):
    changes = [current - last for last, current in zip(close, close[1:])]
    gains = wilder([max(change, 0) for change in changes], 2)
    losses = wilder([max(-change, 0) for change in changes], 2)
    result = [None, None]
    for gain, loss in zip(gains[1:], losses[1:]):
        total = gain + loss
        result.append(100 * gain / total if total else 50.0)
    return result


def directional_series(high, low, close):
    ranges, positive, negative = [], [], []
    for i in range(1, len(close)):
        rise = high[i] - high[i - 1]
        fall = low[i - 1] - low[i]
        ranges.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        positive.append(rise if rise > max(fall, 0) else 0.0)
        negative.append(fall if fall > max(rise, 0) else 0.0)
    period = 14
    atr = wilder(ranges, period)[period - 1 :]
    plus = [100 * move / span if span else 0 for move, span in zip(wilder(positive, period)[period - 1 :], atr)]
    minus = [100 * move / span if span else 0 for move, span in zip(wilder(negative, period)[period - 1 :], atr)]
    dx = [100 * abs(p - m) / (p + m) if p + m else 0 for p, m in zip(plus, minus)]
    padding = [None] * period
    return padding + wilder(dx, period), padding + plus, padding + minus


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    bottom = int(position)
    top = min(bottom + 1, len(ordered) - 1)
    return ordered[bottom] + (ordered[top] - ordered[bottom]) * (position - bottom)


def overlay_values(bars):
    close = [bar["adj_close"] for bar in bars]
    factors = [bar["adj_close"] / bar["close"] for bar in bars]
    high = [bar["high"] * factor for bar, factor in zip(bars, factors)]
    low = [bar["low"] * factor for bar, factor in zip(bars, factors)]
    volume = [bar["volume"] for bar in bars]
    macd = [fast - slow for fast, slow in zip(ema(close, 12), ema(close, 26))]
    obv = [0]
    for i in range(1, len(close)):
        direction = int(close[i] > close[i - 1]) - int(close[i] < close[i - 1])
        obv.append(obv[-1] + direction * volume[i])
    returns = [current / last - 1 for last, current in zip(close, close[1:])]
    volatility = [None] * 20 + [
        stdev(returns[i - 20 : i]) * sqrt(252) for i in range(20, len(close))
    ]
    adx, plus, minus = directional_series(high, low, close)
    return close, high, low, macd, ema(macd, 9), obv, volatility, rsi_series(close), adx, plus, minus


def decision_rules(bars, start):
    close, high, low, macd, signal, obv, vol, rsi, adx, plus, minus = overlay_values(bars)
    result = {strategy.id: [] for strategy in STRATEGIES}
    for i in range(start, len(bars)):
        price = close[i]
        sma20 = moving_average(close, i, 20)
        sma60 = moving_average(close, i, 60)
        sma100 = moving_average(close, i, 100)
        sma50 = moving_average(close, i, 50)
        top55 = max(high[i - 55 : i])
        bottom20 = min(low[i - 20 : i])
        momentum = price / close[i - 63] - 1
        bb_lower = sma20 - 2 * pstdev(close[i - 19 : i + 1])
        obv_average = moving_average(obv, i, 20)
        vol_limit = percentile(vol[i - 120 : i], 0.75)
        span = high[i] - low[i]
        ibs = min(1, max(0, (price - low[i]) / span)) if span else 0.5

        entry = {
            "sma": Rule(
                int(sma20 > sma60),
                "20 日均线高于 60 日均线。" if sma20 > sma60 else "20 日均线低于或等于 60 日均线。",
                (("SMA 20", sma20, "price"), ("SMA 60", sma60, "price")),
            ),
            "macd": Rule(
                int(macd[i] > signal[i]),
                "MACD 高于信号线。" if macd[i] > signal[i] else "MACD 低于或等于信号线。",
                (("MACD", macd[i], "number"), ("信号线", signal[i], "number")),
            ),
            "donchian": Rule(
                1 if price > top55 else 0 if price < bottom20 else None,
                "收盘突破此前 55 日最高价。" if price > top55 else
                "收盘跌破此前 20 日最低价。" if price < bottom20 else "收盘仍在通道内。",
                (("复权收盘", price, "price"), ("此前 55 日高点", top55, "price"), ("此前 20 日低点", bottom20, "price")),
            ),
            "momentum": Rule(
                int(momentum > 0),
                "63 日收益为正。" if momentum > 0 else "63 日收益低于或等于零。",
                (("63 日收益", momentum, "percent"),),
            ),
            "rsi": Rule(
                0 if price < sma100 or rsi[i] > 70 else 1 if price > sma100 and rsi[i] < 10 else None,
                "收盘跌破 100 日均线或 RSI 超过 70。" if price < sma100 or rsi[i] > 70 else
                "收盘高于 100 日均线且 RSI 低于 10。" if price > sma100 and rsi[i] < 10 else
                "RSI 与均线组合未达到调仓阈值。",
                (("RSI(2)", rsi[i], "number"), ("复权收盘", price, "price"), ("SMA 100", sma100, "price")),
            ),
            "bollinger": Rule(
                1 if price < bb_lower else 0 if price >= sma20 else None,
                "收盘跌破布林带下轨。" if price < bb_lower else
                "收盘达到或超过布林带中轨。" if price >= sma20 else "收盘位于布林带下轨和中轨之间。",
                (("复权收盘", price, "price"), ("下轨", bb_lower, "price"), ("中轨", sma20, "price")),
            ),
            "adx": Rule(
                int(adx[i] > 25 and plus[i] > minus[i]),
                "ADX 超过 25 且 +DI 高于 −DI。" if adx[i] > 25 and plus[i] > minus[i] else
                "ADX 或趋势方向未达到持仓条件。",
                (("ADX(14)", adx[i], "number"), ("+DI", plus[i], "number"), ("−DI", minus[i], "number")),
            ),
            "obv": Rule(
                int(obv[i] > obv_average),
                "OBV 高于自身 20 日均线。" if obv[i] > obv_average else "OBV 低于或等于自身 20 日均线。",
                (("OBV", obv[i], "volume"), ("OBV 20 日均线", obv_average, "volume")),
            ),
            "volatility": Rule(
                int(price > sma50 and vol[i] < vol_limit),
                "收盘在均线上方且波动率低于阈值。" if price > sma50 and vol[i] < vol_limit else
                "价格或波动率未达到持仓条件。",
                (("20 日年化波动率", vol[i], "percent"), ("历史 75 分位", vol_limit, "percent"),
                 ("复权收盘", price, "price"), ("SMA 50", sma50, "price")),
            ),
            "ibs": Rule(
                1 if ibs <= 0.2 else 0 if ibs >= 0.8 else None,
                "收盘位于当日区间底部 20%。" if ibs <= 0.2 else
                "收盘位于当日区间顶部 20%。" if ibs >= 0.8 else "收盘位于当日区间中段。",
                (("IBS", ibs, "percent"), ("复权日高", high[i], "price"), ("复权日低", low[i], "price")),
            ),
        }
        for strategy_id, rule in entry.items():
            result[strategy_id].append(rule)
    return result
