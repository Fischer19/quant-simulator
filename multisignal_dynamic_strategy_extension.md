# Multi-Signal & Dynamic Strategy Extension

**Incremental engineering specification v2.0 | Coding agent handoff**  
Audience: Codex / Claude Code | Status: proposed research, not validated trading rules  
Target: extend the existing ten-strategy backtesting repository; preserve its original functionality and results.

## 0. Coding agent instructions

Read this document, the repository README, existing project plans, `AGENTS.md` / `CLAUDE.md`, current strategy code, execution engine, data pipeline, tests, and prior reports before making changes. This is an incremental extension, not a greenfield rewrite. If an earlier broad Market Intelligence project plan exists, this document supersedes its strategy-extension scope and milestone order only; retain compatible research safeguards.

- Recover the existing ticker universe, actual 2020/2022/2025 event dates, execution conventions, parameters, and recorded results from the repo. Do not assume SPY, QQQ, or crash-bottom dates were used. Record unknowns.
- Preserve S01–S10 and their public interfaces wherever practical. Add adapters rather than rewriting working strategy code. Do not overwrite original experiment artifacts or unrelated user changes.
- First reproduce the existing experiments and freeze an immutable legacy result manifest. If a correctness defect is discovered, preserve the legacy reproduction, document the defect, add a regression test, and produce a separately labeled corrected run. Never silently change the baseline.
- Implement the phases in order. Make reasonable reversible engineering decisions autonomously; record assumptions and blockers rather than repeatedly asking for approval. Do not commit secrets, use brokerage credentials, submit orders, or deploy anything externally.
- Use real historical data for empirical results. Synthetic fixtures are for tests only, clearly marked; never substitute them into research findings. Missing external data must yield an explicit unavailable result or separately labeled proxy experiment.
- Keep dependencies and UI changes minimal; reuse the existing stack. No ML training, neural MoE, paid data purchase, leverage, shorting, or new multi-asset allocation engine in this iteration.
- Update an implementation checklist and `STATUS.md` (or the repo's equivalent) with commands, tests, files, experiment IDs, limitations and outstanding tasks. Run tests after each phase. Do not claim completion unless supported by actual artifacts.

## 1. Objective and research questions

The existing ten strategies are predominantly single-asset OHLCV-based, binary long/cash policies. Their previous 252-session tests following the 2020, 2022 and 2025 declines did not beat buy-and-hold in absolute return, according to the project owner. Reproduce rather than assume that finding.

The goal is to investigate whether adding market-context signals and dynamic equity exposure provides useful, robust out-of-sample investment decisions over a 1–2 year horizon. Do not assume outperformance. Distinguish:

1. **Signal value:** does external information improve a policy relative to a matched price-only version?
2. **Allocation value:** does fractional exposure improve results relative to binary exposure with the same signals?
3. **Switching value:** does a rule-based regime gate improve on fixed use of the same expert strategies?
4. **Recovery behavior:** can the policy participate in rebounds without taking excessive downside risk during prolonged bear markets?
5. **Absolute versus risk-adjusted performance:** do gains survive exposure-, turnover- and risk-matched benchmarks, realistic costs and chronological holdouts?

Primary deliverable: S11–S20 implemented and evaluated in the existing backtester, with reproducible reports and explicit limitations. Preserve the original 10 strategies as comparison candidates.

## 2. Strategy inventory

| ID | Name / stable slug | Family | Default exposure | Inputs |
|---|---|---|---|---|
| S11 | `trend_fear_filter` | Multi-input binary | 0 or 1 | Trend, VIX |
| S12 | `panic_recovery` | Multi-input binary | 0 or 1 | Drawdown, VIX, price |
| S13 | `trend_breadth` | Multi-input binary | 0 or 1 | Trend, breadth |
| S14 | `drawdown_credit` | Multi-input binary | 0 or 1 | Drawdown, HY OAS |
| S15 | `multi_signal_voting` | Multi-input binary | 0 or 1 | Trend, VIX, breadth, credit, momentum |
| S16 | `score_dynamic_exposure` | Dynamic sizing | 0.25–1 | Exactly the S15 signals |
| S17 | `regime_strategy_switching` | Hard-gated experts | 0, 0.25 or 1 | Drawdown, VIX, trend, legacy expert outputs |
| S18 | `core_tactical` | Dynamic sizing wrapper | alpha–1 | Core weight + underlying strategy |
| S19 | `volatility_targeting` | Dynamic sizing baseline | 0.25–1 | Realized volatility |
| S20 | `dual_score_allocation` | Two-dimensional dynamic sizing | 0.25–1 | Opportunity and risk signals |

All numbers are frozen starting hypotheses, not fitted parameters or live trade recommendations. Implement all ten new strategies, but mark data-blocked configurations unavailable. A full-support subset (including proxy variants) may be run separately; do not hide sample differences. S19 is intentionally price-only, to isolate the effect of dynamic sizing.

## 3. Data contract and feature definitions

### 3.1 Asset and context separation

`target_symbol` is the traded instrument from the existing project. `context_symbol` defaults to that same instrument for price-derived features. Use broad US equity context for external signals by default (e.g. SPY for the breadth universe, VIX for S&P 500 implied volatility). If the target is QQQ or another instrument, record the market-context mismatch; do not silently substitute a different ticker or volatility index. An alternative context may be an explicit sensitivity test. Include `target_symbol`, `context_symbol`, `breadth_universe`, `data_variant`, and `feature_version` in each run.

### 3.2 Providers and quality

| Data | Candidate source | Required handling |
|---|---|---|
| Target and context OHLCV, dividends, splits | Existing provider; otherwise yfinance or user-supplied CSV | Preserve provider metadata; validate split/dividend adjustment and executable open prices; no double counting dividends |
| VIX daily closes | Cboe historical data or FRED `VIXCLS` | Respect actual availability, timezone and market hours; use only observations published by decision cutoff |
| True market breadth | Point-in-time S&P 500 membership + constituent adjusted price history; licensed/local import if needed | Never use today's constituents to construct historical breadth; track coverage and valid denominator |
| Breadth proxy | RSP and SPY adjusted total-return prices | `RSP/SPY` relative strength; not true breadth, no invented pre-inception prices |
| US HY credit spread | FRED `BAMLH0A0HYM2` (ICE BofA US High Yield OAS), authorized historical files | Verify current availability, permitted use and actual date coverage; respect release lag/revisions; no fabricated historical extension |
| Credit proxy | HYG / IEF adjusted total-return prices | Separately named relative-performance proxy; exposed to duration/rates; not an OAS measurement |
| Cash return | Existing cash model; optionally point-in-time short Treasury rate | Same cash treatment for all policies and baselines, accrual across calendar days; explicitly label zero-cash-return variant |

Check actual data availability before implementing research runs. Provider download failures must not be silently converted to zeros. Credit series may have restricted historical coverage; if true OAS is unavailable, enable `credit_proxy` as a different experiment, not a substitution for true credit spread. Respect terms of use and caching/redistribution limits. Prefer local import hooks for proprietary histories. Do not scrape CNN Fear & Greed or introduce a paid dependency in this iteration.

Use immutable raw snapshots if permitted; otherwise store source identifiers, hashes, query times and coverage metadata. Cache incremental updates and allow offline reproduction. Maintain `observed_at`, `available_at`, `source`, `unit`, `quality_flag` (or equivalent) for external observations. Align to the traded asset's exchange calendar using backward as-of joins on availability time; no backward filling. Daily decisions are made at a documented cutoff after close and execute no earlier than the next session open. Use known publication timestamps, or a conservative one-session lag where unavailable; document residual uncertainty and test an additional lag. For true breadth, require at least 95% of point-in-time membership to have valid 50-session histories (configurable); otherwise mark missing. Limit carried-forward external values to 3 target sessions after their observation date by default; record staleness. Do not use incomplete current-day bars.

### 3.3 Features (all through the decision date t only)

Let `P_t` be the positive, consistently adjusted closing price of `context_symbol`. Use trading sessions of the context/target calendar. `MA_n(t)` is the arithmetic mean of the last n closes, including t. `R_n(t) = P_t/P_(t-n) - 1`. Use complete windows (`min_periods=n`, or n+1 prices for n returns).

| Feature | Definition |
|---|---|
| `ma20`, `ma50`, `ma100`, `ma200` | Moving averages as above |
| `mom63` | `R_63(t)` |
| `high252` | max of last 252 closes including t |
| `dd252` | `1 - P_t/high252(t)`; positive fraction (0.10 = 10% drawdown) |
| `prior_low20` | min of closes t-20 through t-1, excludes t |
| `vix` | Latest available daily VIX level, in index points |
| `vix_peak20` | max of 20 aligned VIX observations through t |
| `vix_relief20` | `1 - vix/vix_peak20`; positive fraction |
| `breadth50` | fraction of then-members with valid data whose close is above own MA50 |
| `breadth_delta20` | `breadth50(t) - breadth50(t-20)` |
| `breadth_proxy20` | `(RSP_t/SPY_t)/(RSP_(t-20)/SPY_(t-20)) - 1`, total-return adjusted prices |
| `breadth_proxy_delta20` | `breadth_proxy20(t) - breadth_proxy20(t-20)` |
| `oas_pp` | High yield OAS in percentage points (4.0 = 400 bps) |
| `oas_delta20_bps` | `100*(oas_pp(t)-oas_pp(t-20))`, bps (positive = widening) |
| `credit_proxy20` | 20-session relative total return of HYG versus IEF, same ratio-return formula as breadth proxy |
| `rv20` | sample standard deviation (`ddof=1`) of last 20 daily simple target returns × sqrt(252), decimal annualized |

The default drawdown reference is rolling 252-session high, not all-time high. The peak is not frozen on entry. Test a frozen entry-peak variant only as a separately specified sensitivity if useful. No use of future troughs, centered windows, full-sample quantiles or future membership. Fetch sufficient pre-start history (at least 300 sessions, more if other existing indicators require it). Warm-up does not contribute to performance. If any required input is missing after warm-up, apply the missing-data policy below; do not count NaN as a negative vote.

### 3.4 Proxy definitions (separate policy IDs / variants)

For S13: true breadth entry `> 0.60` and exit `< 0.40`; proxy entry `breadth_proxy20 > 0.01`, exit `< -0.01`. These thresholds are illustrative hypotheses, not calibrated equivalences.

For S15/S16: replace the true breadth vote with `breadth_proxy20 > 0`; replace credit vote with `credit_proxy20 >= 0`.

For S14: proxy entry `credit_proxy20 > 0`; proxy credit-stress exit `credit_proxy20 < -0.03`. No conversion to basis points. For S20: proxy breadth improvement is `breadth_proxy_delta20 > 0`; credit-risk vote is `credit_proxy20 < -0.03`. Allow true/true, proxy/true, true/proxy, proxy/proxy configurations as available. Always compare on common dates as well as each variant's own full coverage. Do not interpret proxy breadth and credit as independent of equities.

## 4. Deterministic strategy specifications

General conventions: weights are equity fractions of total NAV; the balance is cash. `prev` denotes the policy's own prior valid desired binary state (not a daily drifted realized weight). Binary states start flat at the beginning of each replay unless the explicitly documented legacy convention differs. Exit has priority if both entry/exit conditions happen to be true. New strategies do not change state on unavailable required inputs. Default missing-data behavior: after initial warm-up, hold existing shares/cash without new orders, emit `DATA_MISSING`, and count stale days; initial flat if no valid signal has ever been generated. Also report strict common-coverage results excluding unavailable configurations, not dropping isolated days from an otherwise continuous equity curve. Do not reset indicators at each evaluation date. See section 6 for event/fold initialization. Equality boundaries follow the exact operators shown.

### S11 — Trend + Fear Filter

```python
enter = (P > ma100) and (vix < 25)
exit = (P < ma100) or (vix > 30)
w = 0.0 if exit else (1.0 if enter else prev)
```

The separate VIX entry/exit thresholds are intentional hysteresis. Record reasons. Control: price-only MA100 entry/exit with identical state/execution conventions.

### S12 — Panic & Recovery

```python
enter = (dd252 >= 0.10) and (vix_peak20 > 30) and (vix_relief20 >= 0.20)
exit = (dd252 <= 0.05) or ((vix > 35) and (P < prior_low20))
w = 0.0 if exit else (1.0 if enter else prev)
```

Do not identify the final trough in real time. Recent VIX peak includes t; prior price low excludes t. Default intentionally has no MA20 entry condition (S17 does). Control: drawdown-only entry at 10%, exit at 5%, plus separately report price-only stress exit `P < prior_low20` if needed for an ablation.

### S13 — Trend + Market Breadth

```python
enter = (P > ma50) and (breadth50 > 0.60)
exit = (P < ma50) and (breadth50 < 0.40)
w = 0.0 if exit else (1.0 if enter else prev)
```

Use the same AND exit for the proxy variant with its own thresholds. An OR exit is a distinct sensitivity, not the default. Control: MA50-only entry/exit. This tests whether broader market confirmation improves trading decisions; it does not assume breadth causes returns.

### S14 — Drawdown + Credit Risk

```python
enter = (dd252 >= 0.10) and (oas_delta20_bps < 0)
exit = (dd252 <= 0.03) or (oas_delta20_bps > 100)
w = 0.0 if exit else (1.0 if enter else prev)
```

The credit proxy variant uses the explicit predicates in section 3.4. Control: drawdown-only entry/exit (10%/3%). Do not use a value reported after the decision cutoff.

### S15 — Multi-Signal Voting (binary)

Implement a shared pure score function used verbatim by S15 and S16:

```python
votes = {
    'trend': int(P > ma100),
    'volatility': int(vix < 25),
    'breadth': int(breadth50 > 0.50),
    'credit': int(oas_delta20_bps <= 0),
    'momentum': int(mom63 > 0),
}
score = sum(votes.values())  # all five inputs must be valid
w = 1.0 if score >= 4 else (0.0 if score <= 2 else prev)
```

Do not silently renormalize a missing vote. Log every vote. Price-only comparator uses trend and momentum only, with entry normalized score >= 0.8, exit <= 0.4. Use normalized score `score / active_vote_count` for predeclared leave-one-feature/group-out research variants only; never use that as an implicit runtime missing-data fallback. Trend and momentum overlap; include a joint price-trend ablation.

### S16 — Score-based Dynamic Exposure

```python
score = shared_vote_function(features)  # identical to S15
w = 0.25 + 0.15 * score
```

| Score | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Target weight | 25% | 40% | 55% | 70% | 85% | 100% |

Normalized ablation mapping: `0.25 + 0.75 * (score / active_vote_count)`. S15/S16 must consume identical feature rows and data variants; no special feature cleaning or optimized thresholds for one of them. S16 is not designed as a crash opportunity score; a low trend score can reduce rebound participation. Measure rather than assume this effect.

### S17 — Regime-based Strategy Switching (hard-gated MoE)

Use rules, not learned regime classification. Calculate all expert signals continuously, even when not selected. Ordered gate:

```python
recovery = (dd252 >= 0.10) and (vix_relief20 >= 0.20) and (P > ma20)
bear = dd252 >= 0.10  # only reached if recovery is false
bull = (dd252 < 0.10) and (P > ma100) and (vix < 25)

if recovery:
    regime, w = 'recovery', 1.0
elif bear:
    regime, w = 'bear', 0.25
elif bull:
    regime, w = 'bull', legacy_ma_20_60_signal
else:
    regime, w = 'other', legacy_rsi2_signal
```

Default experts: existing 20/60 MA strategy and existing RSI(2) pullback strategy, unchanged. Explicit alternative: MACD or Donchian for bull, Bollinger mean reversion for other, each registered before runs; do not select best expert post hoc. Require the chosen expert output to be valid, and keep all expert states warm. `other` is a residual bucket, not proof of a sideways market. `bear` is a rule label, not a verified economic regime. Every valid row belongs to exactly one regime. Default has no minimum dwell time. Report state transitions and time spent per regime.

Control A: each individual expert used continuously. Control B: same four-state gate, but bull and other weights fixed to 1.0 and 0.50 respectively, recovery 1.0, bear 0.25. Control C: fixed expert mixture `0.5*MA_signal + 0.5*RSI_signal` (virtual experts, independent of realized trades). Test whether switching adds value beyond expert choice and basic exposure management.

### S18 — Core + Tactical Allocation

Wrapper, not a standalone forecast. Default tactical policy S16, default core fraction 0.70.

```python
w = alpha + (1.0-alpha) * tactical_weight
```

Use `alpha` in {0, 0.50, 0.70, 0.90, 1.0} as a prespecified sensitivity. Also test the same core fractions with S15; optional S17 as a labeled secondary variant. Alpha=0 exactly reproduces the underlying strategy and alpha=1 is full-equity buy-and-hold (subject to the shared initial-entry convention). With S16, tactical minimum is 25%, so default total minimum is 77.5%; do not mislabel it as 70%. This is a target-weight floor applied to total portfolio NAV, not a separate untouched sleeve or buy-and-forget lot. No duplicated trades or costs from simulated sub-portfolios. When underlying data are unavailable, hold current shares rather than recomputing a new target; alpha=1 has no dependency on tactical data.

### S19 — Volatility Targeting

```python
w = clip(0.15 / max(rv20, 1e-6), 0.25, 1.0)
```

Require valid `rv20 >= 0`; true zero volatility after QC maps to maximum exposure, not division by zero. Target 15% annualized; test 10% and 20% as sensitivities. This is inverse-volatility sizing, not inverse-variance sizing. It is a risk-control baseline, not a return forecast. Realized portfolio volatility need not equal target due to bounds, gaps and changing market volatility. Compare with fixed 25–100% weights and fixed risk-matched policies.

### S20 — Dual-Score Opportunity × Risk Allocation

Implement two independent, interpretable three-vote scores. Do not subtract the scores into a single scalar. Each of the following is a candidate heuristic, not a validated relationship:

```python
opportunity_votes = {
    'drawdown': int(dd252 >= 0.10),
    'panic_relief': int((vix_peak20 > 30) and (vix_relief20 >= 0.20)),
    'breadth_improvement': int(breadth_delta20 > 0),
}
risk_votes = {
    'credit_stress': int(oas_delta20_bps > 50),
    'high_vix': int(vix > 30),
    'weak_trend': int(P < ma200),
}
O = sum(opportunity_votes.values())
R = sum(risk_votes.values())
# Scores 0=low, 1=medium, 2 or 3=high.
row = min(R, 2)
col = min(O, 2)
w = [[0.75, 0.90, 1.00],
     [0.50, 0.75, 0.90],
     [0.25, 0.50, 0.75]][row][col]
```

| Risk \\ Opportunity | Low (0) | Medium (1) | High (2–3) |
|---|---:|---:|---:|
| Low (0) | 75% | 90% | 100% |
| Medium (1) | 50% | 75% | 90% |
| High (2–3) | 25% | 50% | 75% |

Note: proxy credit threshold follows section 3.4, not 50 bps. Exclude realized volatility as a separate risk vote in the default to limit overlap with VIX. Optional predeclared sensitivity replaces the VIX risk vote with `rv20 > 0.25`; do not add more weights. The design deliberately allows high opportunity and high risk to coexist. In all cases weights stay within [0,1]. Test opportunity-only (row fixed at medium), risk-only (column fixed at medium), and the full matrix. Also compare with S16 on the same dates; the score sets are not identical, so do not attribute differences to the matrix alone. A useful additional structural control maps the same six votes to a single scalar `clip(0.625 + 0.125*(O-R), 0.25, 1.0)`, which is deliberately simple and not tuned.

## 5. Integration into the existing backtester

Do not force a new framework if the repository already supports these concepts. The following contract is conceptual; adapt names to existing conventions and maintain backwards compatibility.

```python
@dataclass(frozen=True)
class PolicyDecision:
    target_weight: float | None  # None = no new order / hold existing shares
    reason: str
    regime: str | None = None
    diagnostics: dict = field(default_factory=dict)

class Policy(Protocol):
    def reset(self) -> None: ...
    def on_close(self, features, previous_state) -> PolicyDecision: ...
```

- Add a strategy registry with stable IDs, version, feature dependencies, params, family, data variant and initialization policy. Legacy binary outputs adapt to target weights 0 or 1; preserve legacy signal timing and state in the reproduction path.
- Separate desired target weight, actual pretrade weight, posttrade weight and holdings. Target weights are fractions, not percentages. Reject out-of-range, nonfinite or invalid values. No leverage in this iteration.
- Decision at close t, executed at next eligible open t+1; all policies and fixed benchmarks use the same fill convention and cutoff. No same-close fill unless reproducing a separately labeled legacy run. Do not apply the newly decided weight to the overnight return before execution.
- Use an explicit holdings/NAV ledger. At open, first mark existing shares and cash, then compute the new target from pretrade NAV and transact, accounting for fees and slippage without negative cash. Handle dividends and splits consistently or use coherent adjusted-price units with documented limitations. No mixing raw opens with inconsistent adjusted closes. Record pre-/post-trade NAV, shares, fees, cash, execution price and desired/actual weights. Fractional shares permitted for research. With missing executable price, defer and log rather than fabricate a fill.
- Binary 0/1 strategies trade only when necessary. Dynamic strategies: default daily signal evaluation, daily execution only if absolute difference between desired and pretrade weight is at least 5 percentage points, or a full-exit/entry boundary is reached. Also run no-band daily and weekly (last exchange session of week, next open) variants, applied consistently to matched comparators. For all experiments isolating allocation versus binary decisions, include a common no-band schedule. Weekly policies update underlying indicators and expert state daily, but transact only on scheduled days. Do not backfill a holiday Friday.
- Fixed fractional benchmarks use the same schedule/band and cash convention; full buy-and-hold makes one entry and holds. Cash accrual is identical for all policies. Stress per-side all-in proportional trading costs of 0, 5 (base), 10 and 25 bps on actual traded notional, including entry. If costs and slippage are separate, avoid double-counting. State whether taxes are excluded (default yes); turnover is still reported.
- Do not force liquidation at the end for some strategies but not others. Mark all at close consistently; optional liquidation with costs is a distinct run.
- For each decision log date, availability cutoff, policy ID, version, target, prior target, inputs, data quality, entry/exit predicates or votes, regime, selected expert, reason and whether an order was suppressed or filled. No fake performance or future-looking labels in live decision fields.

## 6. Experiment protocol

### 6.1 Evaluation windows and chronology

A. **Legacy reproduction:** exactly reproduce the three original 252-session event windows for all legacy strategies and buy-and-hold using the historical configuration, and then evaluate new strategies on the identical dates where valid. If old implementation contains look-ahead or accounting errors, label these results descriptive/legacy; create corrected comparison tables for all strategies with identical corrected timing. Never use old results to select an allegedly out-of-sample model.

B. **Causal drawdown event study:** independently detect first crossing of 10%, 15%, 20% drawdown from the trailing 252-session high using information at close. Entry/initial decision is that close, earliest fill next open. Track one event per drawdown episode per threshold; re-arm only once drawdown is below 5% (or another frozen reset rule). Keep thresholds separate, flag overlapping events/horizons, and do not treat each crossing as an independent crisis. Evaluate 21/63/126/252/504 sessions wherever complete; mark right-censored windows (especially recent events). A hindsight trough study may be included explicitly as descriptive only, not as evidence of deployable timing. Include all available episodes, including prolonged drawdowns, not only the three chosen years.

C. **Continuous historical simulation:** longest defensible common sample for the traded instrument and features, including both bull and bear regimes. True and proxy configurations may have different coverage. Report both pairwise common support and each variant's native sample; use a shared intersection for leaderboards. Never fabricate pre-inception ETF data or fill multi-year OAS gaps. Test SPY/QQQ transfer only if data permit, in addition to the original ticker, and report differing universes separately.

D. **Rolling horizon analysis:** calculate 252- and 504-session compounded returns from continuous out-of-sample equity curves. Report overlapping windows and also nonoverlapping windows or episode-level summaries. Calculate maximum drawdown within each rolling window with its initial NAV as the starting reference. No assumption that rolling windows are independent.

Initialization: for a new event investment, start identical cash/NAV at the event decision date, with fully warmed indicators and expert state, enter no earlier than the next open. Default top-level binary policy state is flat at the event decision (evaluate its rule); report the alternative of replayed prior policy state as a sensitivity. For continuous simulations, initialize at the common valid start; replay prior features/expert states as specified but do not count warm-up P&L. All fixed benchmarks enter at the same earliest fill. For rolling windows on a continuous curve, inherit actual holdings; do not reset them. Explicitly document any differing legacy convention.

### 6.2 Freeze and validation

- Before new comparative runs, save versioned experiment configs, parameter grids, primary metrics and the candidate list. Record that 2020/2022/2025 outcomes have already influenced research design; these are development/diagnostic events, not pristine holdouts.
- Use chronological development/validation/test blocks, chosen from actual common data coverage before reviewing performance. For long samples, a 60/20/20 chronological split is an acceptable initial convention; if fewer than two complete 504-session outcomes fit in a block, report insufficiency instead of pretending precision. Use expanding walk-forward for any parameter selection; fixed hand-designed strategies need no fitting but still require temporal separation of research decisions and evaluation. Only train/tune on past data; any forward-return label whose endpoint crosses a split must be purged from earlier training/selection data. No purging of valid backward-looking indicator history merely because it precedes a split. Report the exact dates and rules.
- For a continuous test period, replay prior states using frozen params without scoring the past; use a common initial cash and next-open entry at the test boundary, or carry a common predeclared portfolio simulation across folds and explicitly report that protocol. Do not switch initialization by strategy.
- A historical date being withheld computationally does not guarantee the designer has never seen its market outcome. Label all retrospective tests honestly; the truly prospective period begins after strategy and configuration freeze. Never tune on the locked test results and report the same test as independent.
- Use block bootstrap of aligned paired daily returns (e.g. 21/63-session blocks as sensitivity) or event-level resampling when enough independent events exist. Report sample sizes and dependence caveats. If intervals are wide or only a handful of episodes exist, mark inconclusive. Report exploratory sensitivity results in full; no cherry-picking or p-value fishing. A research result of no improvement is successful software delivery.

### 6.3 Prespecified experiment matrix

| ID | Experiment | Required comparison / hypothesis |
|---|---|---|
| E00 | Legacy replication | S01–S10 vs original B&H and saved results |
| E01 | Corrected fixed baselines | B&H 100%; cash; fixed 25/50/75/100% equity; existing legacy strategies |
| E02 | New binary policies | S11–S15 vs B&H, legacy strategies, and matched price-only controls in section 4 |
| E03 | Same-signal sizing | S15 vs S16, identical votes/dates/fees; fixed mean-exposure control |
| E04 | Regime switching | S17 vs two continuous experts, fixed expert mixture, fixed regime weights |
| E05 | Core/tactical | S18 grid with S15 and S16; vs underlying strategies and B&H |
| E06 | Volatility targeting | S19 vs fixed allocation and risk-matched baseline |
| E07 | Dual-score allocation | S20 vs S16, opportunity-only, risk-only, same-vote scalar and fixed allocation |
| E08 | Feature ablations | S15/S16 remove one vote at a time and remove trend+momentum jointly; S20 single-axis controls; true/proxy comparisons |
| E09 | Stress / robustness | Costs, lag, rebalance frequency, thresholds, crisis/continuous windows |
| E10 | Final OOS summary | Frozen candidate set across chronological test and rolling 1/2-year windows; transfer test if available |

E03 and E07 are primary research comparisons. Do not rank strategies by a single in-sample CAGR. First execute all default configs; use small one-at-a-time sensitivity sets, not a Cartesian grid search. Examples: VIX entry 20/25/30 with exit thresholds maintaining a gap; VIX relief 15/20/25%; drawdown trigger 8/10/12%; moving average 80/100/120 for MA100 rules; credit stress 40/50/60 bps for S20; volatility target 10/15/20%; rebalance band 0/5/10 percentage points; extra external-data lag 1 session. Register exact sensitivity variants before running; do not promote the historical best variant as a validated strategy.

### 6.4 Benchmark fairness and return attribution

- Always include full buy-and-hold, fixed 25/50/75/100%, and cash. Include price-only counterparts of the multivariate policies.
- Add a deployable fixed-exposure benchmark whose weight is estimated only on the development period (then frozen for validation/test). A constant weight equal to realized test-period mean exposure is allowed only as an ex-post diagnostic, prominently labeled non-investable. Same schedule/cost model. Analogously estimate fixed risk-matched exposure using development-period realized risk and report the actual test risk mismatch.
- Optional random-timing benchmark: circularly shift or block-permute binary signal sequences preserving approximate exposure and holding durations, use 1,000+ fixed-seed replicates, same initial/fill/cash/cost conventions, and report null percentiles. This is a retrospective diagnostic, not a tradeable baseline; avoid presenting it as proof of statistical independence.
- Report time-weighted market exposure, actual exposure distribution, turnover, and net cost drag. Separate overnight versus intraday returns if fills are next-open. For a frictionless aligned interval with predetermined exposure w, equity excess over full equity is `(w-1)*(r_equity-r_cash)`; include cash and costs in actual ledger-based attribution. Do not treat the sum of daily arithmetic excess returns as terminal compound wealth difference.
- Report missed positive sessions, capture of best 10 equity-return sessions, avoided worst 10 sessions, and upswing participation during event windows. Do not select those dates before decisions. Use actual held exposure for each return interval; clarify overnight/intraday treatment. Record what drove any relative return deficit: low equity exposure, unfavorable timing, trade costs or combinations.

## 7. Metrics and research decision gates

Required metrics, with explicit definitions and units:

| Group | Metrics |
|---|---|
| Return | Terminal NAV, total return, geometric annualized return when horizon supports it, net excess vs each baseline |
| Risk | Annualized volatility, maximum drawdown (negative convention or explicit magnitude), Sharpe using same cash series, Sortino, Calmar for suitable horizons, daily 95% historical expected shortfall |
| 1–2 year objective | Median and 10th/90th percentiles of rolling 252/504-session returns, fraction beating B&H and fixed benchmark, fraction below 0 and -10%, worst rolling return, rolling maximum drawdown |
| Behavior | Average actual exposure, time in each exposure band, turnover (sum absolute traded notional / pretrade NAV), number of trades, trading cost, upside/downside capture, missing-data count |
| Regime / signals | Regime frequency, transitions, conditional performance, vote frequencies, score distribution, feature coverage and ablation deltas |

Use trading-session annualization 252 for daily returns and report conventions. Risk-free/cash rate and missing handling consistent. Define upside/downside capture as mean strategy return divided by mean asset return over positive/negative asset days respectively (arithmetic diagnostic), or use existing documented definition for all strategies. Undefined ratios must be null, not zero. Include both dollar/percent wealth differences and percentage-point return differences, not ambiguous 'alpha'.

Research conclusions classify each candidate as:
- `promising`: evidence of useful net improvement relative to predeclared fixed/risk-matched controls across validation periods, with reasonable rebound participation and no obvious fragility;
- `risk_control_only`: lower downside at the expense of absolute return, with no compelling same-exposure return advantage;
- `no_incremental_value`: no observed robust improvement over simpler matched controls;
- `inconclusive`: insufficient coverage, independent events, confidence, or unstable results.

Before examining validation results, register a quantitative selection rule or use this default illustrative rule: positive OOS net geometric growth difference vs development-matched fixed allocation; lower magnitude of worst rolling 252-session drawdown than full B&H; median rolling 252-session return no more than 2 percentage points below full B&H; and no sign reversal in excess growth at 10 bps cost and reasonable nearby parameters. Also explicitly report 504-session evidence and uncertainty; a pass is not proof of future return. For an absolute-return objective, separately report whether a strategy beats full B&H and how often. Do not redefine success after looking at test data. No guaranteed profits; the constrained long/cash design may reduce losses or miss rallies and cannot increase exposure above 100%.

## 8. Engineering milestones and acceptance

| Phase | Scope | Completion criteria |
|---|---|---|
| M0: Audit and reproduction | Inspect repo, document legacy conventions, freeze baseline, add regression fixtures | Existing S01–S10 remain callable; actual reproduction report or explicit blocker; tests pass or failures documented |
| M1: Feature extension | Provider adapters, data validation, PIT alignment, coverage report, true/proxy features | Feature tests, no future-data leakage, explicit coverage table, cached or imported real-data snapshot where permitted |
| M2: Multi-input strategies | S11–S15 and matched price-only controls | Exact rule/state tests; shared voting function; event and continuous results where available |
| M3: Fractional exposure | Extend existing engine as needed; S16/S18/S19, fixed baselines, trading ledger | Accounting tests; binary compatibility; same-vote comparisons; cost/rebalance stress |
| M4: Regime and dual-score | S17/S20, expert controls, axis/feature ablations | Gate precedence, persistent experts, exact 3×3 matrix tests, diagnostic logs |
| M5: Evaluation and UI | All E00–E10, chronological/rolling validation, reports, integrate existing dashboard | Reproducible config/CLI, results manifests, tables/plots, honest research conclusion, no new live trading |

M0 through M4 can be implemented even if some data or performance gates fail; unavailable strategies remain explicitly labeled. No ML or additional optimization phase unless separately requested and motivated by evidence. Do not make market outperformance a software acceptance condition.

### Suggested interfaces / commands (adapt to existing CLI)

```bash
# illustrative commands, expose equivalent working commands and document them
python -m backtest audit-legacy
python -m backtest fetch-data --config configs/multisignal.yaml
python -m backtest validate-data --config configs/multisignal.yaml
python -m backtest run --config configs/multisignal.yaml --experiment E03
python -m backtest run --config configs/multisignal.yaml --experiment all
python -m backtest report --run-id <actual-run-id>
pytest -q
```

Create a version-controlled config with target symbol, context symbol, data variants, date boundaries, cutoff, warm-up, rebalance schedule/band, base costs, cash model, params and seeds. Separate experimental defaults from user overrides. Keep existing CLI commands working. No network required for unit tests; real-data integration tests may be explicitly skipped with reason. Never hardcode illustrative tickers/dates in place of the recovered legacy configuration.

## 9. Required tests

- **Legacy regression:** exact existing signal and result reproduction on a frozen fixture where compatible; report known differences for corrected engine.
- **Feature windows:** off-by-one checks for moving averages, 63-day return, rolling 252 high, prior low excluding t, VIX peak/relief, 20-session deltas, OAS percentage-point-to-bps conversion, volatility annualization and adjusted-price ratios.
- **Point-in-time:** append or mutate future observations (including later data releases and constituent membership) and verify past features, decisions and fills remain unchanged; late OAS cannot influence an earlier cutoff. Verify holidays, timezone, stale and missing observations.
- **Rules and state:** exact threshold equality, entry/exit priority, hysteresis, missing inputs, no NaN-as-false, all five S15 votes, S16 six target weights, score parity between S15/S16, four exhaustive ordered S17 branches, inactive experts updating, S18 alpha endpoints and 77.5% floor, S19 zero/invalid volatility and clipping, all nine S20 matrix cells and proxy unit handling.
- **Execution:** next-open fill including overnight gaps, no same-day benefit, fixed weights drift before rebalance, no duplicate trades, cash and NAV conservation, proportional fee cost, no negative cash/short/over-100% target, dividends/splits handled exactly once, final valuation and missing fill policy. Use tiny deterministic hand-calculated examples including an overnight gap, a constant-price round trip with costs and a zero-cost full-equity buy-and-hold path.
- **Validation and metrics:** overlapping labels purged across training boundaries, correct event re-arm, incomplete windows flagged, identical benchmark start/fill/end, rolling metrics matched to direct calculations, drawdown sign and null ratios, identical run config/seed producing identical results given same data.
- **UI / integration:** strategy registry shows all 20, disabled data-blocked strategies explain why, results match saved run artifacts, no synthetic figures mislabeled as real and no look-ahead diagnostics shown as current inputs.

## 10. Deliverables and reporting

Use existing folders/components when possible; suggested new artifacts:

```text
configs/multisignal.yaml
configs/experiments/                 # frozen, versioned manifests
reports/legacy_reproduction.md
reports/data_coverage.md
reports/multisignal_research_report.md
reports/<run_id>/config.yaml
reports/<run_id>/metrics.csv
reports/<run_id>/rolling_returns.parquet
reports/<run_id>/event_returns.csv
reports/<run_id>/decisions.parquet
reports/<run_id>/trades.parquet
reports/<run_id>/equity_curves.parquet
reports/<run_id>/run_manifest.json
STATUS.md
```

Run manifest includes data hashes/source coverage, git commit or code snapshot identifier, strategy versions, configs, runtime dependencies, dates, seeds and skipped runs/reasons. Ensure outputs do not redistribute restricted raw data. Keep old result files intact. Output CSV/Parquet equivalent to existing storage if needed.

Minimum report/UI extension (reuse existing dashboard; CLI and static plots sufficient if no UI exists):

1. Strategy leaderboard with data variant, dates, total return, net excess, max drawdown, average exposure, turnover and rolling 1/2-year metrics; sortable but not claiming a historical winner is future-best.
2. Original crash comparison with aligned normalized equity curves, drawdown curves and exposure history; separate legacy and corrected/event-triggered runs.
3. S15/S16 overlay: same score, binary versus fractional exposure, wealth difference, turnover and exposure diagnostics.
4. S17 regime timeline with active expert and target/actual exposure; distinguish observed gate label from future market outcome.
5. S20 opportunity/risk score heatmap and exposure matrix, historical frequency and conditional return diagnostics with sample counts; show both opportunity and risk separately.
6. Ablation and robustness tables, missing/coverage report, cost stress and chronological split results; every chart links to its actual config/run ID. No invented results.

Final report must answer: Which external input added measurable incremental value, if any? Did dynamic sizing improve outcomes relative to identical binary signals and matched fixed exposure? Did switching add value beyond fixed experts? Did the dual-score matrix preserve recovery participation? Which results are driven by beta, risk reduction or costs? Which findings are fragile or inconclusive? Would another iteration be justified, or should simpler baselines remain preferred? State all limitations, especially event sample dependence and lack of true prospective evidence. This is a research tool, not a brokerage or automatic trading system.

## 11. Ready-to-use coding agent prompt

```text
Read MULTISIGNAL_STRATEGY_EXTENSION.md in full. It is the incremental
specification for my existing ten-strategy quantitative backtesting project.
Inspect the repository and earlier plans first; do not rebuild the project.

Implement M0–M5 sequentially, preserving the original strategies, interfaces
and prior results. Start by reproducing the 2020/2022/2025 experiments and
freezing the legacy artifacts. Document any accounting or look-ahead issues
and run a separate corrected comparison rather than silently modifying history.

Add the external market features and implement S11–S20 exactly as defined,
including shared S15/S16 votes, fractional target weights, continuously updated
legacy experts, proxy variants and the two-axis S20 allocation matrix.
Prioritize causal data alignment, accurate next-open execution, reproducibility
and matched baselines. Use actual historical data where available; do not
invent data, fills, results, or provider coverage. Run the tests and experiments,
save outputs and integrate them into the existing UI if present.

Maintain a progress log and document assumptions, coverage limitations and
blockers. Make reasonable reversible engineering decisions independently.
Do not add ML, leverage, automated trading, or optimize for a historical winner.
Report negative or inconclusive results as clearly as positive results.

At completion, give me the changed files, reproducible commands, test results,
experiment summary, and evidence-based recommendation for the next research
iteration. The goal is to isolate the incremental value of new information
and dynamic allocation, not to manufacture a backtest that beats buy-and-hold.
```
