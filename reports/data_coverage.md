# 多信号数据覆盖

批次基准行情截至 2026-11-23。交易标的为 QQQ、SOXX、SPY。价格指标用各标的自身复权价格，外部 VIX 和市场广度语境是广义美国股票市场。对 QQQ 和 SOXX，语境与交易标的存在成分差异。

| 来源 | 对齐后首日 | 对齐后末日 | 有效／总交易日 |
|---|---|---|---:|
| Cboe VIX | 2013-09-04 | 2026-11-23 | 3,326／3,327 |
| Yahoo Finance RSP、SPY、HYG、IEF | 2013-09-04 | 2026-11-23 | 各 3,326／3,327 |
| RSP／SPY、HYG／IEF 配对比值 | 2013-09-04 | 2026-11-23 | 各 3,326／3,327 |
| 点时 S&P 500 成分广度 | — | — | 0／3,327 |
| 真实 HY OAS | — | — | 0／3,327 |

表中日期为目标交易日的决策对齐日期。20 日变化类指标另需前置完整窗口。决策截止为美东收盘后 20 分钟，提前收盘同理。VIX 与代理 ETF 日线没有逐历史日的发布时间，保守延后一交易日，另测试再延后一交易日。超过观察日三个目标交易日的值标为过期。到达截止前的授权观察按最新已知版本对齐，不向前回填。Yahoo 复权序列可能有历史修订，重现使用冻结输入哈希与本地缓存。

RSP／SPY 是等权和市值加权指数 ETF 的相对表现。HYG／IEF 是信用及利率久期共同驱动的相对表现。两者代理版本各有独立 ID。FRED 的 ICE 系列自 2026 年 4 月起只列最近三年，且有转发许可限制，本次完整历史研究使用信用代理。[Cboe VIX 历史数据](https://www.cboe.com/tradable_products/vix/vix_historical_data) · [Yahoo 复权定义](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html) · [FRED 的 ICE HY OAS 说明](https://fred.stlouisfed.org/series/BAMLH0A0HYM2)

## 授权数据接入

在本地 `data/context/` 中放置有权使用的 CSV，再重启面板或重新运行实验。原始文件和逐日实验输出遵循本地忽略规则。

- `breadth.csv` 接收 `date,value,available_at,valid_members,members,source`。输入需由当时的 S&P 500 成分名单计算。`value` 是有 50 日复权收盘历史的有效成员中高于各自 MA50 的比例。完整成员数和有效分母均保留。有效覆盖小于 95% 的数据标为缺失。
- `oas.csv` 接收 `date,value,available_at,source`。单位为百分点，例如 3.76 表示 376 个基点。提供带时区的实际发布时间可区分数据版本。缺少时间戳时按一交易日滞后处理，修订可用性的剩余误差需在分析中说明。
