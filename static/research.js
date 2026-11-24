const $ = id => document.getElementById(id);
const percent = new Intl.NumberFormat("en-US", {style: "percent", maximumFractionDigits: 2, signDisplay: "exceptZero"});
const labels = {
  native: "原始数据", price_only: "仅价格", breadth_proxy: "广度代理", credit_proxy: "信用代理",
  proxy_proxy: "双代理", promising: "达到研究门槛", risk_control_only: "仅风险控制",
  no_incremental_value: "未见稳健增益", inconclusive: "证据不足",
};

const text = value => String(value).replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const format = value => value === null || value === undefined ? "—" : percent.format(value);
const pp = value => value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)} pp`;
const tone = value => value > 0 ? "positive" : value < 0 ? "negative" : "";
let report;

function table(headers, rows) {
  return `<table><thead><tr>${headers.map(name => `<th scope="col">${text(name)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(([name, ...cells]) => `<tr><th scope="row">${text(name)}</th>${cells.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderLeaderboard() {
  const symbol = $("report-symbol").value;
  const rows = report.leaderboard.filter(row => row.symbol === symbol);
  const key = $("report-sort").value;
  if (key === "policy") rows.sort((a,b) => a.number.localeCompare(b.number));
  else if (key === "fixed") rows.sort((a,b) => b.matched_fixed_excess_pp - a.matched_fixed_excess_pp);
  else rows.sort((a,b) => b.metrics[key] - a.metrics[key]);
  const splits = rows[0]?.splits;
  $("test-range").textContent = splits
    ? `开发 ${splits.development.join(" 至 ")} · 验证 ${splits.validation.join(" 至 ")} · 测试 ${splits.test.join(" 至 ")}`
    : "此标的暂时没有完整的测试区间。";
  $("leaderboard").innerHTML = rows.length ? table(
    ["策略", "数据版本", "净收益", "超额／买入持有", "超额／开发期匹配", "最大回撤", "平均仓位", "总换手", "滚动 1 年中位数", "滚动 2 年中位数", "结论"],
    rows.map(row => [`${row.number} ${row.label}`, text(labels[row.variant] || row.variant),
      `<b class="${tone(row.metrics.return)}">${format(row.metrics.return)}</b>`, pp(row.excess_return_pp),
      pp(row.matched_fixed_excess_pp), format(row.metrics.drawdown), format(row.metrics.exposure),
      `${row.metrics.turnover.toFixed(1)}×`, format(row.rolling["252_overlapping"].median),
      format(row.rolling["504_overlapping"].median), text(labels[row.classification])])
  ) : "";
  const comparisons = report.comparisons.filter(row => row.symbol === symbol && row.band === 0);
  $("research-comparisons").innerHTML = table(
    ["实验", "策略", "对照", "累计净收益差", "策略／对照仓位", "策略／对照换手"],
    comparisons.map(row => [row.experiment, text(row.policy), text(row.comparison), pp(row.excess_return_pp),
      `${format(row.left_exposure)} / ${format(row.right_exposure)}`,
      `${row.left_turnover.toFixed(1)}× / ${row.right_turnover.toFixed(1)}×`])
  );
}

try {
  const response = await fetch("/api/research");
  report = await response.json();
  if (!response.ok) throw new Error(report.error);
  $("report-date").textContent = report.as_of;
  $("run-id").textContent = `运行批次 ${report.run_id}`;
  const symbols = [...new Set(report.leaderboard.map(row => row.symbol))];
  $("report-symbol").innerHTML = symbols.map(symbol => `<option>${text(symbol)}</option>`).join("");
  const names = {vix: "VIX", rsp: "RSP", hyg: "HYG", ief: "IEF", spy: "SPY", breadth: "真实市场广度", oas: "真实 HY OAS",
    breadth_ratio: "RSP／SPY 代理", credit_ratio: "HYG／IEF 代理"};
  $("research-coverage").innerHTML = table(["数据源", "首个可用日", "最后可用日", "可用交易日"],
    Object.entries(report.coverage).map(([key, row]) => [names[key] || key, text(row.first || "—"),
      text(row.last || "—"), row.count ? `${row.count} / ${row.sessions}` : "缺数据"]));
  $("report-content").hidden = false;
  $("report-symbol").addEventListener("change", renderLeaderboard);
  $("report-sort").addEventListener("change", renderLeaderboard);
  renderLeaderboard();
} catch (error) {
  $("research-alert").textContent = error.message;
  $("research-alert").hidden = false;
}
