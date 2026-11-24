import { gridScale, strokeSeries } from "./charts.js";

const $ = id => document.getElementById(id);
const colors = ["#315fd4", "#0a8073", "#ae561b"];
const actualColor = "#25395f";
const formatPercent = new Intl.NumberFormat("en-US", {
  style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const axisPercent = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 });
const state = { symbol: "QQQ", strategy: "benchmark", horizon: 252, mode: "value", version: 0 };

function text(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function tone(value) {
  return value > 0 ? "positive" : value < 0 ? "negative" : "";
}

async function request(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "无法读取情景模拟。");
  return result;
}

function showAlert(message) {
  $("alert").textContent = message;
  $("alert").hidden = !message;
}

class ScenarioChart {
  constructor(canvas, tooltip) {
    this.canvas = canvas;
    this.tooltip = tooltip;
    this.ctx = canvas.getContext("2d");
    this.cursor = null;
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener("pointermove", event => this.track(event));
    canvas.addEventListener("click", event => this.track(event));
    canvas.addEventListener("pointerleave", () => {
      this.cursor = null;
      this.draw();
    });
    canvas.addEventListener("keydown", event => {
      if (!this.series || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let index = this.cursor ?? 0;
      if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
      if (event.key === "ArrowRight") index = Math.min(this.days, index + 1);
      if (event.key === "Home") index = 0;
      if (event.key === "End") index = this.days;
      this.cursor = index;
      this.draw();
    });
  }

  update(result, days, mode) {
    this.days = days;
    const valuesFor = scenario => {
      if (mode === "exposure") return scenario.exposure_path;
      if (mode === "drawdown") {
        let peak = 1;
        return scenario.path.map(value => {
          peak = Math.max(peak, 1 + value);
          return (1 + value) / peak - 1;
        });
      }
      return scenario.path;
    };
    this.series = result.scenarios.map((scenario, index) => scenario.path ? ({
      label: scenario.date, values: valuesFor(scenario), color: colors[index],
    }) : null).filter(Boolean);
    if (result.actual?.path) {
      this.series.push({ label: "当前回放", values: valuesFor(result.actual), color: actualColor, actual: true });
    }
    this.cursor = null;
    this.draw();
  }

  track(event) {
    if (!this.layout) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const { left, width, top, bottom } = this.layout;
    this.cursor = x < left || x > left + width || y < top || y > bottom
      ? null : Math.round((x - left) / width * this.days);
    this.draw();
  }

  draw() {
    if (!this.series?.length) return;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    if (!width || !height) return;
    const dpr = window.devicePixelRatio || 1;
    const canvasWidth = Math.round(width * dpr);
    const canvasHeight = Math.round(height * dpr);
    if (this.canvas.width !== canvasWidth) this.canvas.width = canvasWidth;
    if (this.canvas.height !== canvasHeight) this.canvas.height = canvasHeight;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const left = 52;
    const top = 18;
    const bottom = height - 29;
    const plotWidth = width - left - 23;
    this.layout = { left, width: plotWidth, top, bottom };
    const series = this.series.map(line => ({ ...line, values: line.values.slice(0, this.days + 1) }));
    const values = series.flatMap(line => line.values);
    const low = Math.min(0, ...values);
    const high = Math.max(0, ...values);
    const padding = Math.max(.01, (high - low) * .045);
    const scale = gridScale(low - padding, high + padding);
    const x = index => left + index / this.days * plotWidth;
    const y = value => bottom - (value - scale.low) / (scale.high - scale.low) * (bottom - top);

    ctx.font = "11px -apple-system, BlinkMacSystemFont, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let value = scale.low; value <= scale.high + scale.step / 1000; value += scale.step) {
      const atZero = Math.abs(value) < 1e-8;
      const row = y(value);
      ctx.fillStyle = "#586680";
      ctx.fillText(axisPercent.format(atZero ? 0 : value), left - 8, row);
      ctx.fillStyle = atZero ? "#bbc8e0" : "#e8edf5";
      ctx.fillRect(left, Math.round(row) + .5, plotWidth, 1);
    }
    ctx.textAlign = "center";
    ctx.fillStyle = "#586680";
    for (let i = 0; i <= 4; i++) {
      const day = Math.round(this.days * i / 4);
      ctx.fillText(day ? `T+${day}` : "T+0", x(day), height - 11);
    }

    for (const line of series) {
      strokeSeries(ctx, line.values, x, y, line.color, line.actual ? [4, 3] : []);
      const end = line.values.length - 1;
      this.drawPoint(x(end), y(line.values[end]), line.color, line.actual ? 4 : 2.5);
    }

    if (this.cursor === null) {
      this.tooltip.hidden = true;
      return;
    }

    const vertical = x(this.cursor);
    ctx.strokeStyle = "#a4b5d3";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(vertical, top);
    ctx.lineTo(vertical, bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    const entries = series.filter(line => this.cursor < line.values.length);
    for (const line of entries) this.drawPoint(vertical, y(line.values[this.cursor]), line.color, 3);

    this.tooltip.innerHTML = `<strong>T+${this.cursor}</strong>` + entries.map(line =>
      `<span>${text(line.label)} <b>${formatPercent.format(line.values[this.cursor])}</b></span>`).join("");
    this.tooltip.hidden = false;
    const tooltipWidth = this.tooltip.offsetWidth;
    const offset = vertical + tooltipWidth + 10 <= width ? vertical + 10 : vertical - tooltipWidth - 10;
    this.tooltip.style.left = `${Math.max(0, offset)}px`;
    this.tooltip.style.top = "14px";
  }

  drawPoint(x, y, color, radius) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
  }
}

const chart = new ScenarioChart($("scenario-chart"), $("scenario-tooltip"));

function renderMeta() {
  const meta = state.meta;
  const stale = meta.as_of < meta.expected_session;
  $("status").dataset.state = stale ? "stale" : "current";
  $("status").lastElementChild.textContent = stale ? "待更新" : "行情已就绪";
  $("reference-date").textContent = meta.reference?.date || "—";
  $("scenario-content").hidden = !meta.reference;
  $("footer").textContent = meta.data_start
    ? `Yahoo Finance 日线 · ${meta.data_start} 至 ${meta.as_of}` : "";
  if (!meta.reference) {
    showAlert("当前快照中没有符合条件的基准大跌日。");
    return;
  }
  if (stale) showAlert(`${meta.expected_session} 日线待更新，当前使用 ${meta.as_of} 快照。`);
  if (meta.matches.length < 3 && !stale) showAlert("当前快照中符合条件的独立历史节点少于三个。");
  $("symbols").innerHTML = meta.symbols.map(symbol => `
    <button class="scenario-symbol" type="button" data-symbol="${text(symbol)}" aria-pressed="${symbol === state.symbol}">
      <strong>${text(symbol)}</strong><span class="negative">${formatPercent.format(meta.reference.profile[symbol][0])}</span>
    </button>`).join("");
  $("strategy").innerHTML = meta.strategies.map(([id, label, blocked]) =>
    `<option value="${text(id)}" ${blocked ? "disabled" : ""}>${text(label)}${blocked ? "（缺数据）" : ""}</option>`).join("");
  $("strategy").value = state.strategy;
  $("match-caption").textContent = `从 ${meta.candidate_count} 个已有完整后续一年的大跌日中，按标准化距离排序。`;
  $("method").textContent = `大跌定义为 SPY 单日跌幅至少 ${axisPercent.format(Math.abs(meta.spy_drop))}，且 QQQ 和 SOXX 同跌。对三只 ETF 各比较 1、5、20 日收益和距 20 日收盘高点的跌幅，共 12 项。各项按候选日的标准差缩放，以标准化均方根距离排序，入选日期至少相隔 ${meta.spacing} 个交易日。`;
  $("assumptions").textContent = `每条路径从事件日收盘的现金组合开始，按下一交易日开盘价执行，计入单边 ${meta.cost_bps} 个基点的成本。彩线回放各历史事件后的真实行情，虚线随当前大跌后实际发生的交易日延长。`;
  renderProfiles();
}

function renderProfiles() {
  const { reference, matches } = state.meta;
  const rows = [reference, ...matches].map(row => `
    <tr>
      <th scope="row">${text(row.date)}${row === reference ? "（基准）" : ""}</th>
      ${row.profile[state.symbol].map(value => `<td class="${tone(value)}">${formatPercent.format(value)}</td>`).join("")}
      <td>${row.distance !== undefined ? row.distance.toFixed(2) : "—"}</td>
    </tr>`).join("");
  $("profiles").innerHTML = `<table aria-label="${text(state.symbol)} 大跌特征对比">
    <thead><tr><th scope="col">交易日</th><th scope="col">1 日</th><th scope="col">5 日</th>
    <th scope="col">20 日</th><th scope="col">距 20 日高点</th><th scope="col">匹配距离</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderChart() {
  $("return-title").textContent = ({value: "累计模拟收益", drawdown: "模拟回撤", exposure: "实际收盘仓位"})[state.mode];
  $("scenario-chart").parentElement.hidden = !state.result.actual?.path && !state.result.scenarios.some(caseRow => caseRow.path);
  for (const button of $("horizons").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", Number(button.dataset.days) === state.horizon);
  }
  for (const button of $("scenario-modes").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.dataset.mode === state.mode);
  }
  chart.update(state.result, state.horizon, state.mode);
}

function renderResult() {
  const { actual, scenarios } = state.result;
  $("actual").innerHTML = actual?.path ? `
    <strong>当前模拟回放</strong>
    <b class="${tone(actual.return)}">${formatPercent.format(actual.return)}</b>
    <span>第 ${actual.path.length - 1} / ${state.meta.horizon} 个交易日</span>
    <span>截至 ${text(actual.end)}</span>${actual.missing ? `<span>缺数据 ${actual.missing} 日</span>` : ""}` : text(actual?.reason || "");
  $("legend").innerHTML = scenarios.map((scenario, index) => scenario.path ?
    `<span><i style="--marker:${colors[index]}"></i>${text(scenario.date)}</span>` : "").join("") +
    (actual?.path ? '<span><i class="actual-line"></i>当前回放</span>' : "");
  $("cases").innerHTML = scenarios.map((scenario, index) => `
    <article class="scenario-case" style="--case-color:${colors[index]}">
      <header><time datetime="${text(scenario.date)}">${text(scenario.date)}</time>
        <span>距离 ${state.meta.matches[index].distance.toFixed(2)}</span></header>
      ${scenario.path ? `<div class="case-result"><span>252 日收益</span>
        <strong class="${tone(scenario.return)}">${formatPercent.format(scenario.return)}</strong></div>
      <div class="case-foot"><span>最大回撤 ${formatPercent.format(scenario.drawdown)}</span><span>${scenario.trades} 笔成交</span></div>
      ${scenario.missing ? `<div class="case-foot">缺数据 ${scenario.missing} 日</div>` : ""}` : `<div class="case-foot">${text(scenario.reason)}</div>`}
    </article>`).join("");
  renderChart();
}

async function loadResult() {
  const version = ++state.version;
  const panel = document.querySelector(".scenario-panel");
  panel.setAttribute("aria-busy", "true");
  try {
    const result = await request(`/api/scenarios/${state.symbol}/${state.strategy}`);
    if (version !== state.version) return;
    state.result = result;
    renderResult();
  } catch (error) {
    if (version === state.version) showAlert(`读取模拟结果失败。 ${error.message}`);
  } finally {
    if (version === state.version) panel.removeAttribute("aria-busy");
  }
}

async function load() {
  state.meta = await request("/api/scenarios");
  renderMeta();
  if (state.meta.reference) await loadResult();
}

$("symbols").addEventListener("click", event => {
  const button = event.target.closest("[data-symbol]");
  if (!button || button.dataset.symbol === state.symbol) return;
  state.symbol = button.dataset.symbol;
  for (const option of $("symbols").querySelectorAll("button")) {
    option.setAttribute("aria-pressed", option === button);
  }
  renderProfiles();
  loadResult();
});

$("strategy").addEventListener("change", event => {
  state.strategy = event.target.value;
  loadResult();
});

$("horizons").addEventListener("click", event => {
  const button = event.target.closest("[data-days]");
  if (!button || !state.result) return;
  state.horizon = Number(button.dataset.days);
  renderChart();
});
$("scenario-modes").addEventListener("click", event => {
  const button = event.target.closest("[data-mode]");
  if (!button || !state.result) return;
  state.mode = button.dataset.mode;
  renderChart();
});

$("refresh").addEventListener("click", async () => {
  const button = $("refresh");
  button.disabled = true;
  const label = button.innerHTML;
  button.textContent = "正在更新…";
  showAlert("");
  try {
    await request("/api/refresh", { method: "POST" });
    await load();
  } catch (error) {
    showAlert(`行情更新失败，当前显示上一次验证通过的快照。 ${error.message}`);
  } finally {
    button.innerHTML = label;
    button.disabled = false;
  }
});

try {
  await load();
} catch (error) {
  $("status").lastElementChild.textContent = "读取失败";
  showAlert(error.message);
}
