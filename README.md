# 量化决策面板

QQQ、SOXX、SPY 的日线研究面板，含二十个主策略及显式代理版本。每组在收盘后判断一次。每日决策页显示信号、目标仓位和模拟成交，历史情景页显示事件后的收益、回撤和仓位，研究页显示冻结配置下的对照结果。

## 启动

在项目目录中运行，需 Python 3.9 或更高版本，无需安装 Python 或 JavaScript 第三方包。

```sh
python3 app.py
```

打开 [本地面板](http://127.0.0.1:8765)。内置价格快照和本地语境缓存可离线使用。点击 **更新行情** 会校验三只 ETF 的日线，并尝试刷新 VIX 和代理 ETF 数据。来源请求失败时沿用缓存，超出可用期限的信号标为缺失。

面板按美东交易日对齐数据。常规或提前收盘后预留 20 分钟再接纳当日数据，Yahoo Finance 的实际到达和修订时间由数据源决定。界面显示实际信号交易日和最近取数时间。刷新失败时可稍后重试。

如需从命令行刷新缓存或导出 CSV，可在面板启动前使用以下命令。面板内的下载链接直接导出其当前快照。

```sh
python3 app.py --refresh
python3 app.py --export-data
```

## 数据和模拟口径

- 随附三份日 K 线 CSV，期间为 **2015-01-02 至 2026-11-23**，各含 2,991 个交易日。缓存另含 2014 年的 150 个预热交易日。每日决策视图用最近一年计算绩效，算法用其之前 150 个交易日预热。数据来自 [Yahoo Finance 历史行情](https://help.yahoo.com/kb/SLN2311.html)。
- CSV 中的 OHLC 和成交量使用行情源字段，`adj_close` 是供应商复权收盘价。K 线及模拟成交表显示行情源价格。指标和净值计算按当日 `adj_close / close` 调整 OHLC，分红和拆股影响由供应商复权序列反映，模拟净值不另外计入分红现金。复权序列可随供应商修订。
- 回测首日从现金开始，首个信号可在次日开盘执行，现金收益为零。S01–S10 使用原有二元仓位账本。S11–S20 使用分数份额账本，默认下一开盘按成交前实际仓位判断是否达到 5 个百分点调仓差，允许必要的清仓和进场。
- 单边基础成本为 5 个基点。新账本按实际成交额收费并在目标仓位方程中扣费。每日决策页展示 0、5、10 和 25 个基点的扩展策略敏感度。历史情景页可选择原有或分数仓位口径的买入持有。
- 净收益和回撤从初始净值 1 计算。Sharpe 以日净收益和零无风险利率计算并按 252 个交易日年化。交易次数按模拟成交笔数计算，持仓时间按每个交易日收盘的模拟仓位计算。
- 美股休市和提前收盘按 NYSE 日历处理，另纳入 2018 年 12 月 5 日和 2025 年 1 月 9 日的特殊休市。临时新增的特殊休市需要同步日历。[NYSE 交易日历](https://www.nyse.com/trade/hours-calendars) · [2018 年休市公告](https://ir.theice.com/press/news-details/2018/New-York-Stock-Exchange-to-Honor-President-George-H-W-Bush/default.aspx) · [2025 年休市公告](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)

## 历史情景

[历史情景视图](http://127.0.0.1:8765/scenarios) 使用同一快照和模拟成交逻辑。

1. 选择最近一个 SPY 单日复权收益不高于 −3%、QQQ 与 SOXX 同跌的交易日作为基准。
2. 从 2015 年起筛选符合相同大跌条件、且截至基准日已积累完整 252 个后续交易日的节点。
3. 对三只 ETF 各取事件日的 1、5、20 日累计复权收益与距过去 20 个收盘价最高点的跌幅。用各特征在候选集合中的总体标准差缩放差异，按 12 项标准化差异的均方根距离排序，较小表示较相似。依序取三个节点，节点之间至少相隔 21 个交易日。
4. 每个事件日收盘从现金开始，下一交易日开盘执行买入持有或策略信号。历史情景回放随后 252 个真实交易日，页面同时显示当前基准日之后已发生交易日的模拟收益。单边成本为 5 个基点，回测沿用原有的规则和复权口径。

更新行情会延长当前回放，下一次符合条件的大跌会更新基准日和匹配结果。

## 运行多信号研究

```sh
python3 app.py --update-context
python3 app.py --validate-context
python3 app.py --experiment E00
python3 app.py --experiment all
```

`--experiment` 也接受单个 E01–E10。生成的批次保存在 `reports/runs/`，含配置、输入哈希、覆盖率、指标、事件和滚动收益、决策、成交、现金和份额账本。打开 [研究页](http://127.0.0.1:8765/research) 查看最近一次全套实验。[固定配置](configs/multisignal.json) · [研究报告](reports/multisignal_research_report.md) · [数据接入及覆盖](reports/data_coverage.md) · [实施记录](STATUS.md)

## 验证

```sh
python3 -m unittest discover -s tests
```
# quant-simulator
