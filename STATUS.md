# 多信号与动态策略扩展

| 阶段 | 状态 | 实现和可核对产物 |
|---|---|---|
| M0 | 已复现 | [E00 冻结清单](reports/E00-legacy-2026-11-23-v1.json) 和 [旧版费用口径](reports/legacy_reproduction.md) |
| M1 | 公开数据已接通 | `quant_sim/context.py` 和 [覆盖报告](reports/data_coverage.md)，真实广度与 OAS 已有授权文件接口 |
| M2 | 已实现 | `quant_sim/policies.py` 的 S11–S15、相匹配的价格对照和缺失状态 |
| M3 | 已实现 | `quant_sim/portfolio.py` 的分数份额、次开盘账本、仓位偏移、实际成交费用和 S16、S18、S19 |
| M4 | 已实现 | S17 连续虚拟专家、门控对照和 S20 双评分、代理及消融 |
| M5 | 已运行 | [研究报告](reports/multisignal_research_report.md)、[批次摘要](reports/multisignal_summary.json) 和面板三个视图 |

## 命令

```sh
python3 app.py --experiment E00
python3 app.py --update-context
python3 app.py --validate-context
python3 app.py --experiment all
python3 -m unittest discover -s tests
python3 app.py
```

冻结的起始配置为 [multisignal.json](configs/multisignal.json)。E01–E10 各自也可用 `--experiment` 单独运行。批次清单记录实际运行 ID、源代码和输入哈希、数据缺失跳过项及输出清单。大型 CSV 账本和供应商缓存留在本地，可在相同输入上重建。

本轮全套批次为 `all-2026-11-23-9172f3b6f84c`。共输出 834 行指标、10,440 行事件窗口、13,350 行滚动区间和 222 行配对对照。22 项单元与集成测试通过，包含 E00 逐路径哈希复现。

策略功能含数据缺失时维持份额、晚到数据按截止时间处理、下一开盘重新计算实际调仓差、周频大节假日对齐和价缺递延。在当前快照中，真实广度或真实 OAS 的组合需要补充合法的数据文件，纯真值与代理的比较待该数据到位。独立的前瞻评估待冻结后新行情推进。
