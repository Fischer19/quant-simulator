import { DecisionChart } from "./charts.js";

const names = {
  QQQ: "纳斯达克 100",
  SOXX: "半导体",
  SPY: "标普 500",
};
const actionLabels = { BUY: "买入", SELL: "卖出", HOLD: "持有", CASH: "空仓" };
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const percent = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedPercent = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 });
const pacific = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/Los_Angeles", dateStyle: "medium", timeStyle: "short", hour12: false,
});

const $ = id => document.getElementById(id);
const state = { symbol: "QQQ", strategy: "sma", mode: "price", range: "66", request: 0 };
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
  $("strategy-count").textContent = `${entry.strategies.length} 个策略`;
  $("strategies").innerHTML = entry.strategies.map(strategy => `
    <button class="strategy-row" type="button" data-strategy="${text(strategy.id)}" aria-pressed="${strategy.id === state.strategy}">
      <span class="strategy-name">${text(strategy.name)}</span>
      ${actionBadge(strategy.latest.action)}
      <span class="strategy-return ${color(strategy.stats.return)}">${signedPercent.format(strategy.stats.return)}</span>
    </button>`).join("");
  $("market-period").textContent = `${entry.first} 至 ${as_of} · ${entry.sessions} 个交易日`;
  $("download").href = `/api/data/${state.symbol}.csv`;
  $("download").innerHTML = `<span aria-hidden="true">↓</span> 下载 ${text(state.symbol)} 全量日线`;
  $("footer").innerHTML = `
    <span>行情来源 ${text(source)} · 日线</span>
    <span>最近取数 ${text(pacific.format(new Date(fetched_at)))}（太平洋时间）</span>
    <span>${text(warmup_sessions)} 个预热交易日 · 净值包含复权影响 · 现金收益按零计</span>`;
  if (stale) showAlert(`${expected_session} 的日线待更新。当前显示 ${as_of} 的决策。`);
}

function renderDetail() {
  const data = state.detail;
  const stats = data.stats;
  const benchmark = state.summary.symbols[data.symbol].benchmark;
  $("workspace").hidden = false;
  $("symbol-label").textContent = data.symbol;
  $("strategy-group").textContent = data.group;
  $("strategy-title").textContent = data.name;
  $("strategy-rule").textContent = data.rule;
  $("latest-action").textContent = actionLabels[data.latest.action];
  $("latest-action").dataset.action = data.latest.action;
  const metrics = [
    ["策略净收益", signedPercent.format(stats.return), color(stats.return)],
    ["买入持有", signedPercent.format(benchmark.return), color(benchmark.return)],
    ["最大回撤", percent.format(stats.drawdown), color(stats.drawdown)],
    ["Sharpe", stats.sharpe === null ? "—" : stats.sharpe.toFixed(2), ""],
    ["持仓时间", percent.format(stats.exposure), ""],
  ];
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
      </tr>`).join("");
    $("trades").innerHTML = `<table aria-label="模拟成交"><thead><tr>
      <th scope="col">信号日</th><th scope="col">成交日</th><th scope="col">动作</th><th scope="col">开盘价</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } else {
    $("trades").innerHTML = '<p class="empty">这段回测期间没有模拟成交。</p>';
  }
  $("sensitivity").innerHTML = `<span>单边成本敏感度 · 净收益</span>` +
    Object.entries(data.sensitivity).map(([bps, result]) =>
      `<span>${text(bps)} bp${Number(bps) === state.summary.cost_bps ? "（默认）" : ""}
      <b class="${color(result)}">${signedPercent.format(result)}</b></span>`).join("");
}

function indicatorText(value, unit) {
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
  const holdings = `持仓 ${decision.position * 100}% → 目标 ${decision.target * 100}%`;
  $("decision").innerHTML = `
    <div class="decision-state">
      <time datetime="${text(decision.date)}">${text(decision.date)}</time>
      ${actionBadge(decision.action)}<span>${text(holdings)}</span>${execution}
    </div>
    <p class="decision-reason">${text(decision.reason)}</p>
    <div class="indicators">${decision.indicators.map(([name, value, unit]) =>
      `<span>${text(name)} <b>${text(indicatorText(value, unit))}</b></span>`).join("")}</div>`;
  renderChart();
}

function renderChart() {
  for (const toggle of $("chart-modes").querySelectorAll("button")) {
    toggle.setAttribute("aria-pressed", toggle.dataset.mode === state.mode);
  }
  for (const toggle of $("chart-ranges").querySelectorAll("button")) {
    toggle.setAttribute("aria-pressed", toggle.dataset.range === state.range);
  }
  $("legend").innerHTML = state.mode === "price"
    ? '<span><i class="buy"></i>买入信号</span><span><i class="sell"></i>卖出信号</span><span><i class="fill"></i>模拟成交</span>'
    : '<span><i></i>策略</span><span><i style="--marker:#8493ad"></i>买入持有</span>';
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
