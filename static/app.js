import { DecisionChart } from "./charts.js";

const names = {
  QQQ: "纳斯达克 100",
  SOXX: "半导体",
  SPY: "标普 500",
};
const actionLabels = { BUY: "买入", SELL: "卖出", HOLD: "持有", CASH: "空仓", MISSING: "缺数据" };
const regimeLabels = { recovery: "修复", bear: "下跌", bull: "上涨", other: "其他" };
const orderLabels = { NEXT_OPEN_CHECK: "次日开盘检查", SUPPRESSED: "未达到调仓条件", DEFERRED: "开盘价缺失，递延", SUPERSEDED: "被新信号替代", DATA_MISSING: "无新订单" };
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const percent = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedPercent = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 });
const pacific = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/Los_Angeles", dateStyle: "medium", timeStyle: "short", hour12: false,
});

const $ = id => document.getElementById(id);
const state = { symbol: "QQQ", strategy: "sma", family: "legacy", mode: "price", range: "66", request: 0 };
const chart = new DecisionChart($("chart"), $("tooltip"), selectDate);

function text(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function color(value) {
  return value > 0 ? "positive" : value < 0 ? "negative" : "";
}

function actionBadge(action) {
  return `<span class="action" data-action="${text(action)}">${text(actionLabels[action])}</span>`;
}

async function request(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "无法读取数据。");
  return result;
}

function showAlert(message) {
  $("alert").textContent = message;
  $("alert").hidden = !message;
}

function renderSummary() {
  const { as_of, expected_session, symbols, source, fetched_at, warmup_sessions } = state.summary;
  const stale = as_of < expected_session;
  $("as-of").textContent = as_of;
  $("status").dataset.state = stale ? "stale" : "current";
  $("status").lastElementChild.textContent = stale ? "待更新" : "行情已就绪";
  $("status").title = stale ? `等待 ${expected_session} 行情` : `最近读取于 ${pacific.format(new Date(fetched_at))} 太平洋时间`;

  $("market").innerHTML = Object.entries(symbols).map(([symbol, entry]) => `
    <button class="market-card" type="button" data-symbol="${text(symbol)}" aria-pressed="${symbol === state.symbol}">
      <div class="ticker-copy"><strong>${text(symbol)}</strong><span>${text(names[symbol])}</span></div>
      <div class="ticker-quote"><strong>${usd.format(entry.last_close)}</strong><span class="${color(entry.daily_change)}">${signedPercent.format(entry.daily_change)}</span></div>
    </button>`).join("");

  const entry = symbols[state.symbol];
  $("strategy-count").textContent = `${entry.strategies.filter(row => !row.id.includes("__")).length} 个策略`;
  renderStrategyList(entry);
  $("market-period").textContent = `${entry.first} 至 ${as_of} · ${entry.sessions} 个交易日`;
  $("download").href = `/api/data/${state.symbol}.csv`;
  $("download").innerHTML = `<span aria-hidden="true">↓</span> 下载 ${text(state.symbol)} 全量日线`;
  $("footer").innerHTML = `
    <span>行情来源 ${text(source)} · 日线</span>
    <span>最近取数 ${text(pacific.format(new Date(fetched_at)))}（太平洋时间）</span>
    <span>原有策略预热 ${text(warmup_sessions)} 日 · 多信号策略预热 300 日 · 现金收益按零计</span>`;
  if (stale) showAlert(`${expected_session} 的日线待更新。当前显示 ${as_of} 的决策。`);
}

function renderStrategyList(entry) {
  const rows = entry.strategies.filter(row => state.family === "legacy" ? !row.is_extension
    : row.is_extension && (state.family === "proxy") === row.id.includes("__"));
  for (const button of $("policy-filters").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.dataset.family === state.family);
  }
  $("strategies").innerHTML = rows.map(strategy => `
    <button class="strategy-row" type="button" data-strategy="${text(strategy.id)}" aria-pressed="${strategy.id === state.strategy}">
      <span class="strategy-name">${strategy.number ? `${text(strategy.number)} ` : ""}${text(strategy.name)}
        ${state.family === "proxy" ? `<small>${text(strategy.variant_label)}</small>` : ""}</span>
      ${actionBadge(strategy.latest.action)}
      <span class="strategy-return ${color(strategy.stats?.return)}">${strategy.stats ? signedPercent.format(strategy.stats.return) : "—"}</span>
    </button>`).join("");
  return rows;
}

function renderDetail() {
  const data = state.detail;
  const stats = data.stats;
  const benchmark = data.baseline_stats || state.summary.symbols[data.symbol].benchmark;
  $("workspace").hidden = false;
  $("symbol-label").textContent = data.symbol;
  $("strategy-group").textContent = data.is_extension ? `${data.group} · ${data.variant_label}` : data.group;
  $("strategy-title").textContent = data.number ? `${data.number} ${data.name}` : data.name;
  $("strategy-rule").textContent = data.rule;
  $("policy-status").hidden = !data.is_extension;
  $("policy-status").textContent = data.blocked_reason || (data.is_extension ? `价格语境 ${data.symbol} · 广义市场外部信号${data.symbol !== "SPY" ? "（与交易标的不同）" : ""}` : "");
  $("latest-action").textContent = actionLabels[data.latest.action];
  $("latest-action").dataset.action = data.latest.action;
  $("chart-modes").querySelector('[data-mode="allocation"]').hidden = !data.is_extension || !stats;
  $("chart-modes").querySelector('[data-mode="comparison"]').hidden = !data.comparison;
  if ((!stats && state.mode !== "price") || (!data.is_extension && state.mode === "allocation") || (!data.comparison && state.mode === "comparison")) state.mode = "price";
  for (const mode of ["equity", "drawdown"]) $("chart-modes").querySelector(`[data-mode="${mode}"]`).disabled = !stats;
  const metrics = stats ? [
    ["策略净收益", signedPercent.format(stats.return), color(stats.return)],
    ["买入持有", signedPercent.format(benchmark.return), color(benchmark.return)],
    ["最大回撤", percent.format(stats.drawdown), color(stats.drawdown)],
    ["Sharpe", stats.sharpe === null ? "—" : stats.sharpe.toFixed(2), ""],
    [data.is_extension ? "平均实际仓位" : "持仓时间", percent.format(stats.exposure), ""],
  ] : [];
  $("metrics").innerHTML = metrics.map(([label, value, tone]) =>
    `<div class="metric"><span>${text(label)}</span><strong class="${tone}">${text(value)}</strong></div>`).join("");
  renderTrades(data);
  selectDate(data.bars.length - 1);
}

function renderTrades(data) {
  $("trade-count").textContent = `${data.fills.length} 笔`;
  if (data.fills.length) {
    const rows = [...data.fills].reverse().map(fill => `
      <tr>
        <td>${text(fill.signal_date)}</td>
        <td>${text(fill.date)}</td>
        <td>${actionBadge(fill.action)}</td>
        <td>${usd.format(fill.open)}</td>
        ${data.is_extension ? `<td>${percent.format(fill.pretrade_weight)} → ${percent.format(fill.posttrade_weight)}</td><td>${(100 * fill.cost / fill.pretrade_nav).toFixed(3)}%</td>` : ""}
      </tr>`).join("");
    $("trades").innerHTML = `<table aria-label="模拟成交"><thead><tr>
      <th scope="col">信号日</th><th scope="col">成交日</th><th scope="col">动作</th><th scope="col">开盘价</th>
      ${data.is_extension ? '<th scope="col">成交前后仓位</th><th scope="col">成本 / 成交前净值</th>' : ""}
      </tr></thead><tbody>${rows}</tbody></table>`;
  } else {
    $("trades").innerHTML = '<p class="empty">这段回测期间没有模拟成交。</p>';
  }
  $("sensitivity").hidden = !data.stats;
  $("sensitivity").innerHTML = `<span>单边成本敏感度 · 净收益</span>` +
    Object.entries(data.sensitivity).map(([bps, result]) =>
      `<span>${text(bps)} bp${Number(bps) === state.summary.cost_bps ? "（默认）" : ""}
      <b class="${color(result)}">${signedPercent.format(result)}</b></span>`).join("");
}

function indicatorText(value, unit) {
  if (value === null) return "缺失";
  if (unit === "percent") return percent.format(value);
  if (unit === "price") return usd.format(value);
  if (unit === "volume") return compact.format(value);
  return value.toFixed(2);
}

function selectDate(index) {
  state.selected = index;
  const decision = state.detail.decisions[index];
  const latest = index === state.detail.decisions.length - 1;
  $("inspect-title").textContent = latest ? "当日决策" : "历史决策";
  $("show-latest").hidden = latest;
  const plan = decision.execution;
  const execution = plan
    ? `<span>· ${plan.pending ? "待模拟成交" : "模拟成交"} ${text(plan.date)} 开盘</span>` : "";
  const holdings = `实际 ${percent.format(decision.position)} → 目标 ${decision.target === null ? "维持份额" : percent.format(decision.target)}`;
  const order = orderLabels[decision.order_status];
  $("decision").innerHTML = `
    <div class="decision-state">
      <time datetime="${text(decision.date)}">${text(decision.date)}</time>
      ${actionBadge(decision.action)}<span>${text(holdings)}</span>${execution || (order ? `<span>· ${text(order)}</span>` : "")}
    </div>
    <p class="decision-reason">${text(decision.reason)}</p>
    <div class="indicators">${decision.indicators.map(([name, value, unit]) =>
      `<span>${text(name)} <b>${text(indicatorText(value, unit))}</b></span>`).join("")}</div>`;
  renderDiagnostics(state.detail, decision);
  renderChart();
}

function renderDiagnostics(data, decision) {
  const container = $("policy-diagnostics");
  const diag = decision.diagnostics || {};
  const items = [];
  if (diag.regime) items.push(`规则状态 ${regimeLabels[diag.regime]} · 专家 ${diag.expert || "固定仓位"}`);
  const signals = {trend: "趋势", volatility: "波动率", breadth: "广度", credit: "信用", momentum: "动量",
    drawdown: "回撤", panic_relief: "恐慌缓解", breadth_improvement: "广度改善", credit_stress: "信用压力", weak_trend: "趋势偏弱"};
  for (const [label, votes] of [["五信号", diag.votes], ["机会", diag.opportunity_votes], ["风险", diag.risk_votes]]) {
    if (votes) items.push(`${label} · ${Object.entries(votes).map(([key, value]) => `${signals[key]} ${value}`).join(" · ")}`);
  }
  if (data.comparison) {
    const {S15, S16} = data.comparison;
    items.push(`同分对照 · S16 相对 S15 的终点净值差 ${(S16.stats.nav - S15.stats.nav).toFixed(3)} · 总换手 ${S16.stats.turnover.toFixed(1)}× / ${S15.stats.turnover.toFixed(1)}× · 平均仓位 ${percent.format(S16.stats.exposure)} / ${percent.format(S15.stats.exposure)}`);
  }
  const releases = Object.entries(decision.quality || {}).map(([key, value]) =>
    `<span>${text(key)} · ${value.observed_at ? text(value.observed_at) : "无观察值"} · ${text(value.quality)}${value.stale_sessions === undefined ? "" : ` · ${value.stale_sessions} 个交易日`}</span>`).join("");
  let matrix = "";
  if (data.number === "S20" && data.available) {
    const targets = [[.75, .90, 1], [.50, .75, .90], [.25, .50, .75]];
    const buckets = targets.map(row => row.map(() => ({count: 0, outcomes: []})));
    data.decisions.forEach((row, i) => {
      if (row.diagnostics.risk === undefined) return;
      const bucket = buckets[Math.min(2, row.diagnostics.risk)][Math.min(2, row.diagnostics.opportunity)];
      bucket.count += 1;
      if (i + 1 < data.equity.length) bucket.outcomes.push(data.equity[i + 1].value / data.equity[i].value - 1);
    });
    matrix = `<h3>机会 × 风险 · 历史分布</h3><table class="score-matrix"><thead><tr><th>风险／机会</th><th>低</th><th>中</th><th>高</th></tr></thead><tbody>` +
      targets.map((row, r) => `<tr><th>${["低", "中", "高"][r]}</th>${row.map((weight, o) => {
        const cell = buckets[r][o];
        const mean = cell.outcomes.length ? cell.outcomes.reduce((a, b) => a + b, 0) / cell.outcomes.length : null;
        const active = Math.min(2, diag.risk) === r && Math.min(2, diag.opportunity) === o;
        return `<td class="${active ? "active" : ""}" style="--frequency:${Math.min(.34, .04 + cell.count / data.decisions.length)}"><b>${percent.format(weight)}</b><small>${cell.count} 日 · ${mean === null ? "—" : signedPercent.format(mean)}</small></td>`;
      }).join("")}</tr>`).join("") + `</tbody></table><p>单元格显示目标仓位、历史样本数及次日平均组合收益。</p>`;
  }
  container.hidden = !items.length && !matrix && !releases;
  container.innerHTML = items.map(item => `<p>${text(item)}</p>`).join("") +
    (releases ? `<details><summary>数据观察日与质量</summary><div class="indicators">${releases}</div></details>` : "") + matrix;
}

function renderChart() {
  for (const toggle of $("chart-modes").querySelectorAll("button")) {
    toggle.setAttribute("aria-pressed", toggle.dataset.mode === state.mode);
  }
  for (const toggle of $("chart-ranges").querySelectorAll("button")) {
    toggle.setAttribute("aria-pressed", toggle.dataset.range === state.range);
  }
  const legends = {
    price: '<span><i class="buy"></i>买入信号</span><span><i class="sell"></i>卖出信号</span><span><i class="fill"></i>模拟成交</span>',
    allocation: '<span><i></i>实际收盘仓位</span><span><i style="--marker:#8493ad"></i>目标仓位</span>',
    comparison: '<span><i></i>S16 分数仓位</span><span><i style="--marker:#8493ad"></i>S15 二元仓位</span><span>同信号 · 每日 0% 调仓阈值</span>',
  };
  $("legend").innerHTML = legends[state.mode] || '<span><i></i>策略</span><span><i style="--marker:#8493ad"></i>买入持有</span>';
  if (state.mode === "allocation" && state.detail.number === "S17") {
    $("legend").insertAdjacentHTML("beforeend", '<span><i style="--marker:#a5e6cf"></i>修复</span><span><i style="--marker:#f5bdc3"></i>下跌</span><span><i style="--marker:#bdd3fd"></i>上涨</span><span><i style="--marker:#e2d8f8"></i>其他</span>');
  }
  chart.update(state.detail, state.mode, state.range, state.selected);
}

async function loadDetail() {
  const generation = ++state.request;
  const column = document.querySelector(".detail-column");
  column.setAttribute("aria-busy", "true");
  try {
    const detail = await request(`/api/detail/${state.symbol}/${state.strategy}`);
    if (generation !== state.request) return;
    state.detail = detail;
    renderDetail();
  } catch (error) {
    if (generation === state.request) showAlert(`无法读取策略详情。 ${error.message}`);
  } finally {
    if (generation === state.request) column.removeAttribute("aria-busy");
  }
}

$("market").addEventListener("click", event => {
  const button = event.target.closest("[data-symbol]");
  if (!button || state.symbol === button.dataset.symbol) return;
  state.symbol = button.dataset.symbol;
  renderSummary();
  $("market").querySelector(`[data-symbol="${state.symbol}"]`).focus({ preventScroll: true });
  loadDetail();
});
$("policy-filters").addEventListener("click", event => {
  const button = event.target.closest("[data-family]");
  if (!button || button.dataset.family === state.family) return;
  state.family = button.dataset.family;
  const entry = state.summary.symbols[state.symbol];
  const candidates = renderStrategyList(entry);
  const first = candidates.find(row => row.available !== false) || candidates[0];
  if (first) {
    state.strategy = first.id;
    renderStrategyList(entry);
    loadDetail();
  }
});
$("strategies").addEventListener("click", event => {
  const button = event.target.closest("[data-strategy]");
  if (!button || state.strategy === button.dataset.strategy) return;
  state.strategy = button.dataset.strategy;
  for (const option of $("strategies").querySelectorAll("button")) {
    option.setAttribute("aria-pressed", option === button);
  }
  loadDetail();
});
$("chart-modes").addEventListener("click", event => {
  const button = event.target.closest("[data-mode]");
  if (button) {
    state.mode = button.dataset.mode;
    renderChart();
  }
});
$("chart-ranges").addEventListener("click", event => {
  const button = event.target.closest("[data-range]");
  if (!button) return;
  state.range = button.dataset.range;
  if (state.range !== "all" && state.selected < state.detail.bars.length - Number(state.range)) {
    selectDate(state.detail.bars.length - 1);
  } else {
    renderChart();
  }
});
$("show-latest").addEventListener("click", () => {
  selectDate(state.detail.decisions.length - 1);
  $("chart").focus({ preventScroll: true });
});
$("refresh").addEventListener("click", async () => {
  const button = $("refresh");
  button.disabled = true;
  const label = button.innerHTML;
  button.textContent = "正在更新…";
  showAlert("");
  try {
    state.summary = await request("/api/refresh", { method: "POST" });
    renderSummary();
    await loadDetail();
  } catch (error) {
    showAlert(`行情更新失败，面板继续显示已验证的日线。 ${error.message}`);
  } finally {
    button.innerHTML = label;
    button.disabled = false;
  }
});

try {
  state.summary = await request("/api/summary");
  renderSummary();
  await loadDetail();
} catch (error) {
  $("status").lastElementChild.textContent = "读取失败";
  showAlert(error.message);
}
