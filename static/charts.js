const colors = {
  buy: "#08765e",
  sell: "#ad3d4b",
  up: "#27947b",
  down: "#cb6270",
  strategy: "#315fd4",
  benchmark: "#8493ad",
  grid: "#e8edf5",
  axes: "#61708a",
  cursor: "#a4b5d3",
};

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 });

function triangle(ctx, x, y, direction) {
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - 4, y + 6 * direction);
  ctx.lineTo(x + 4, y + 6 * direction);
  ctx.closePath();
  ctx.fillStyle = direction > 0 ? colors.buy : colors.sell;
  ctx.fill();
}

export function strokeSeries(ctx, values, x, y, color, dash = []) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.7;
  ctx.setLineDash(dash);
  ctx.beginPath();
  let connected = false;
  values.forEach((value, i) => {
    if (!Number.isFinite(value)) {
      connected = false;
      return;
    }
    if (connected) ctx.lineTo(x(i), y(value));
    else ctx.moveTo(x(i), y(value));
    connected = true;
  });
  ctx.stroke();
  ctx.setLineDash([]);
}

export function gridScale(min, max) {
  const target = (max - min) / 4;
  const power = 10 ** Math.floor(Math.log10(target));
  const step = [1, 2, 2.5, 5, 10].map(factor => factor * power).find(candidate => candidate >= target);
  const low = Math.floor(min / step + 1e-9) * step;
  const high = Math.ceil(max / step - 1e-9) * step;
  return { low, high, step };
}

export class DecisionChart {
  constructor(canvas, tooltip, onSelect) {
    this.canvas = canvas;
    this.tooltip = tooltip;
    this.ctx = canvas.getContext("2d");
    this.onSelect = onSelect;
    this.selected = -1;
    this.hover = null;
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener("pointermove", event => {
      this.hover = this.indexAt(event);
      this.draw();
    });
    canvas.addEventListener("pointerleave", () => {
      this.hover = null;
      this.draw();
    });
    canvas.addEventListener("click", event => {
      const index = this.indexAt(event);
      if (index !== null) this.onSelect(index);
    });
    canvas.addEventListener("keydown", event => {
      if (!this.detail || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const end = this.detail.bars.length - 1;
      let index = this.selected;
      if (event.key === "Home") index = this.from;
      if (event.key === "End") index = end;
      if (event.key === "ArrowLeft") index = Math.max(this.from, index - 1);
      if (event.key === "ArrowRight") index = Math.min(end, index + 1);
      this.hover = null;
      this.onSelect(index);
    });
  }

  update(detail, mode, range, selected) {
    if (detail !== this.detail || mode !== this.mode || range !== this.range) this.hover = null;
    this.detail = detail;
    this.mode = mode;
    this.range = range;
    this.selected = selected;
    this.from = range === "all" ? 0 : Math.max(0, detail.bars.length - Number(range));
    this.draw();
  }

  indexAt(event) {
    if (!this.layout) return null;
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const { left, top, bottom, plotWidth, count } = this.layout;
    if (x < left || x > left + plotWidth || y < top || y > bottom) return null;
    return this.from + Math.min(count - 1, Math.floor((x - left) / plotWidth * count));
  }

  draw() {
    if (!this.detail) return;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    if (!width || !height) return;
    const dpr = window.devicePixelRatio || 1;
    const pixelWidth = Math.round(width * dpr);
    const pixelHeight = Math.round(height * dpr);
    if (this.canvas.width !== pixelWidth) this.canvas.width = pixelWidth;
    if (this.canvas.height !== pixelHeight) this.canvas.height = pixelHeight;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.font = "11px -apple-system, BlinkMacSystemFont, sans-serif";
    const left = width < 440 ? 46 : 52;
    const top = 16;
    const bottom = height - 27;
    const plotWidth = width - left - 9;
    const bars = this.detail.bars.slice(this.from);
    const count = bars.length;
    this.layout = { left, top, bottom, plotWidth, count };
    const step = plotWidth / count;
    const x = i => left + (i + 0.5) * step;

    const prices = this.mode === "price";
    const allocation = this.mode === "allocation";
    const { values, baseline } = this.comparisonSeries(this.from);
    const series = [...values, ...baseline].filter(Number.isFinite);
    const minimum = prices ? Math.min(...bars.map(bar => bar.low)) : allocation ? 0 : Math.min(...series);
    const maximum = prices ? Math.max(...bars.map(bar => bar.high)) : allocation ? 1 : Math.max(...series);
    const padding = Math.max((maximum - minimum) * .1, prices ? minimum * .003 : .005);
    const scale = allocation ? {low: 0, high: 1, step: .25} : gridScale(minimum - padding, maximum + padding);
    const y = value => bottom - (value - scale.low) / (scale.high - scale.low) * (bottom - top);

    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 1;
    for (let value = scale.low; value <= scale.high + scale.step / 1000; value += scale.step) {
      const row = y(value);
      ctx.fillStyle = colors.axes;
      const label = prices ? "$" + Number(value.toFixed(1))
        : ["equity", "comparison"].includes(this.mode) ? value.toFixed(2) : percent.format(Math.abs(value) < 1e-9 ? 0 : value);
      ctx.fillText(label, left - 7, row);
      ctx.fillStyle = colors.grid;
      ctx.fillRect(left, Math.round(row) + .5, plotWidth, 1);
    }

    ctx.textAlign = "center";
    ctx.fillStyle = colors.axes;
    const ticks = Math.min(count - 1, width < 440 ? 3 : 7);
    for (let j = 0; j <= ticks; j++) {
      const i = ticks ? Math.round((count - 1) * j / ticks) : 0;
      const label = bars[i].date.slice(5).replace("-", "/");
      const position = Math.max(left + 13, Math.min(width - 20, x(i)));
      ctx.fillText(label, position, height - 10);
    }

    if (prices) this.drawCandles(bars, x, y, step);
    else {
      if (allocation && this.detail.number === "S17") {
        const tints = {recovery: "#a5e6cf", bear: "#f5bdc3", bull: "#bdd3fd", other: "#e2d8f8"};
        this.detail.decisions.slice(this.from).forEach((row, i) => {
          const tint = tints[row.diagnostics.regime];
          if (tint) {
            ctx.fillStyle = tint;
            ctx.fillRect(x(i) - step / 2, top, step + .5, 7);
          }
        });
      }
      strokeSeries(ctx, baseline, x, y, colors.benchmark, [4, 4]);
      strokeSeries(ctx, values, x, y, colors.strategy);
    }

    const historical = this.selected < this.detail.bars.length - 1;
    if (historical && this.selected >= this.from) this.drawCursor(x(this.selected - this.from), top, bottom, colors.strategy);
    if (this.hover !== null) {
      this.drawCursor(x(this.hover - this.from), top, bottom, colors.cursor);
      this.showTooltip(this.hover, x(this.hover - this.from), width);
    } else {
      this.tooltip.hidden = true;
    }
  }

  drawCandles(bars, x, y, step) {
    const ctx = this.ctx;
    const fills = new Map(this.detail.fills.map(fill => [fill.date, fill]));
    const body = Math.max(1, Math.min(10, step * .64));
    bars.forEach((bar, i) => {
      const cx = x(i);
      const color = bar.close >= bar.open ? colors.up : colors.down;
      ctx.fillStyle = color;
      ctx.fillRect(cx - .5, y(bar.high), 1, Math.max(1, y(bar.low) - y(bar.high)));
      const open = y(bar.open);
      const close = y(bar.close);
      ctx.fillRect(cx - body / 2, Math.min(open, close), body, Math.max(1, Math.abs(open - close)));

      const { action } = this.detail.decisions[i + this.from];
      if (action === "BUY") triangle(ctx, cx, y(bar.low) + 3, 1);
      if (action === "SELL") triangle(ctx, cx, y(bar.high) - 3, -1);
      const fill = fills.get(bar.date);
      if (fill) {
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = fill.action === "BUY" ? colors.buy : colors.sell;
        ctx.fillStyle = "#fff";
        ctx.beginPath();
        ctx.arc(cx, open, 3, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
      }
    });
  }

  drawCursor(x, top, bottom, color) {
    const ctx = this.ctx;
    ctx.strokeStyle = color;
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  showTooltip(index, x, width) {
    const bar = this.detail.bars[index];
    const {values, baseline} = this.comparisonSeries(index);
    const format = value => value === null ? "缺失" : ["equity", "comparison"].includes(this.mode) ? value.toFixed(3) : percent.format(value);
    const labels = this.mode === "comparison" ? ["S16", "S15"] : this.mode === "allocation" ? ["实际", "目标"] : ["策略", "买入持有"];
    const body = this.mode === "price"
      ? `开 ${usd.format(bar.open)} · 高 ${usd.format(bar.high)}<br>低 ${usd.format(bar.low)} · 收 ${usd.format(bar.close)}`
      : `${labels[0]} ${format(values[0])} · ${labels[1]} ${format(baseline[0])}`;
    this.tooltip.innerHTML = `<strong>${bar.date}</strong><span>${body}</span>`;
    this.tooltip.hidden = false;
    const tooltipWidth = this.tooltip.offsetWidth;
    const offset = x + 11 + tooltipWidth <= width ? x + 11 : x - tooltipWidth - 11;
    this.tooltip.style.left = `${Math.max(0, offset)}px`;
    this.tooltip.style.top = "13px";
  }

  comparisonSeries(start) {
    if (this.mode === "comparison") {
      return { values: this.detail.comparison.S16.values.slice(start), baseline: this.detail.comparison.S15.values.slice(start) };
    }
    const field = this.mode === "allocation" ? "position" : this.mode === "drawdown" ? "drawdown" : "value";
    return {
      values: this.detail.equity.slice(start).map(point => point[field]),
      baseline: this.mode === "allocation" ? this.detail.decisions.slice(start).map(row => row.target)
        : this.detail.benchmark.slice(start).map(point => point[field]),
    };
  }
}
