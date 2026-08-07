# A股做T历史分析器 MVP

这是一个面向 **底仓做T研究** 的本地工具原型。它不预测“明天一定涨跌”，而是利用真实历史行情量化：

- 平均日振幅
- 成交额/流动性
- 日内可交易空间
- 均值回归特征
- 趋势稳定性
- 最大回撤与异常波动风险
- 5分钟级“有效T机会”（第一版 ZigZag 统计）
- 综合 `T Score 0~100`

## 设计原则

1. **数据源适配层与算法彻底分离**：任何公开接口失效，只替换 provider。
2. **多源降级**：MVP 默认 `东方财富直连 -> AKShare`；后续可增加腾讯/百度/交易所/合法商业行情源。
3. **先统计、后预测**：T Score 每个子分数都可解释、可回测。
4. **先自选池、后全市场**：避免第一版就用高频接口扫5000+股票，先验证指标价值。
5. **许可证与数据权利分开处理**：开源代码许可证不等于行情数据可商业转售。

## 开源调研结论（2026-08）

- `simonlin1212/a-stock-data`：Apache-2.0，当前仍活跃；最值得借鉴的是多数据源与降级设计。本项目没有直接复制其代码，采用同类“provider 可替换”架构思想。
- `AKShare`：MIT，生态成熟，作为备用适配器；其上游公开网站接口可能变更，因此不作为唯一数据源。
- `mootdx`：技术上适合A股行情，但存在维护/依赖与商业授权评估问题，本MVP暂不作为强依赖。

## 安装

推荐 Python 3.11/3.12：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

如果需要 AKShare 备用源：

```bash
pip install akshare
```

## 1. 离线验证

不联网也能验证程序逻辑：

```bash
python -m app.cli --provider demo
```

输出：

```text
output/t_scores.csv
```

## 2. 真实历史行情

优先直接行情接口，失败时可自动尝试 AKShare：

```bash
python -m app.cli --provider auto
```

或者只用东方财富直连：

```bash
python -m app.cli --provider eastmoney
```

编辑 `config/watchlist.txt` 可以修改自选池。

## 3. 启动界面

```bash
streamlit run app/dashboard.py
```

浏览器打开 Streamlit 给出的本地地址即可。

## 当前 T Score v0.1

权重：

- 25% 振幅能力
- 20% 流动性
- 20% 日内可交易空间（日线高低区间的初级代理）
- 15% 均值回归
- 10% 趋势稳定
- 10% 风险控制

**注意：这是待回测的研究公式，不是投资建议。** 后续必须用历史数据验证不同参数下的稳定性，不能凭主观印象固定权重。

## 下一迭代

1. 全A股股票列表与分层扫描
2. 日K本地 DuckDB 缓存和增量更新
3. 5分钟K缓存
4. 有效T空间 v2（局部高低点 + 手续费/滑点过滤）
5. 正T/倒T独立回测
6. 高低点时间分布图
7. 市场状态分类（趋势/震荡/单边）
8. T Score 参数回测与自动校准

## 风险说明

本工具只用于历史行情研究和策略验证。公开数据接口可能发生限流、字段变化或停止服务；真实交易还涉及手续费、印花税、滑点、涨跌停、停牌、T+1、不同板块涨跌幅限制等规则，后续回测必须显式建模。

## v0.2 全市场扫描（开发分支）

启用 DuckDB 本地缓存后，可以从全A股股票池筛选候选并逐步建立历史缓存：

```bash
python -m app.cli --provider auto --all --limit 100 --min-spot-amount 500000000
```

强制刷新股票池：

```bash
python -m app.cli --provider auto --all --limit 100 --refresh-universe
```

首次全市场历史灌库已经支持受控并发；仍建议先使用 `--limit 50~200` 验证上游接口稳定性。后续再次运行时，`CachedProvider` 只补本地缺失日期，不会重复下载完整历史区间。

## v0.2 并发灌库与健康检查

全市场候选首次建立历史缓存时，可使用受控并发。网络请求并发执行，但 DuckDB 写入保持串行，避免多写者冲突：

```bash
python -m app.cli --provider auto --all --limit 200 --hydrate-workers 4 --hydrate-rps 4
```

参数说明：

- `--hydrate-workers`：并发取数线程数，默认 4，内部上限 16。
- `--hydrate-rps`：所有线程合计请求速率，默认每秒 4 次。
- `--retries`：临时失败重试次数，默认 2 次，采用指数退避。
- `--no-prehydrate`：关闭并发预灌库，退回逐只按需读取。

检查当前数据源是否能正常返回股票池与日K：

```bash
python -m app.cli --provider auto --health
```

生产数据源的股票池还会做完整性保护：若返回数量异常偏小（当前阈值 3000），会视为上游分页/接口异常并触发备用源，而不是把残缺股票池当成完整市场继续运行。

## 缓存覆盖区间

日K缓存现在同时记录“实际K线行范围”和“已经向数据源检查过的日历范围”。例如周五请求到周日，数据表只有周五K线，但覆盖元数据会记到周日，因此周六、周日不会在下一次运行时被反复当作缺失数据请求。

## v0.2 做T回测实验室

第一版同时输出两种结果：

1. `hindsight_envelope`：按每个交易日5分钟收盘价寻找有时间顺序约束的最佳单次正T/倒T组合。它使用了全天未来信息，只用于回答“这一天理论上有没有足够空间覆盖交易成本”，**不能作为交易策略**。
2. `rolling_z_mean_reversion`：滚动均值/标准差计算Z分数，只使用当前及以前K线；出现信号后延后一根5分钟K执行，每天最多一轮，作为可回测的因果基线。

命令行示例：

```bash
python -m app.backtest_cli 300059 --provider auto --days 60 --mode positive
python -m app.backtest_cli 300059 --provider auto --days 60 --mode reverse
```

成本模型默认参数只是研究起点，全部可调整：券商佣金0.03%、每边最低佣金5元、卖出印花税0.05%、单边滑点2bp、其他单边费率0。卖出印花税默认值依据自2023年8月28日起实施且目前仍有效的证券交易印花税减半政策；沪深A股交易经手费标准为成交金额0.00341%双向，但券商佣金口径可能已经包含部分规费，因此本工具不默认额外叠加，避免重复计费。

T+1通过“单次T股数不能超过开盘前可卖底仓”建模：正T当天买入的新股不能卖，所以随后卖出的是原有可卖底仓；倒T先卖原有底仓，再当日买回。两种方式收盘总持股数恢复一致。

## 历史日内高低点时间分布

单股深度分析现在会从5分钟K线按交易日提取当日最高价/最低价首次出现的时间，并汇总为30分钟时间桶：

- 每个时间桶出现最高点的比例
- 每个时间桶出现最低点的比例
- 历史最高点/最低点中位时间
- 上午出现高/低点比例
- 14:00以后尾盘出现高/低点比例

该统计描述的是历史“股性”，不表示未来某个时间点必然形成高点或低点。若同一天最高/最低价在多个5分钟K线上并列，当前版本取最早出现的K线。

## T Score Walk-forward 校准与训练折优化

T Score 校准遵循时间顺序，不允许把未来测试期数据用于权重选择。固定 v0.1 可先做 OOS 诊断：

```bash
python -m app.calibration_cli 300059 --provider auto --days 500 --horizon 5
```

启用训练折内权重搜索与多基准对照：

```bash
python -m app.calibration_cli 300059 --provider auto --days 500 --horizon 5 \
  --optimize --candidates 256 --random-trials 200 --max-weight 0.55
```

当前优化流程：

1. 外层 expanding walk-forward 划分训练/未来测试窗口，中间保留 `gap=horizon`。
2. 每个外层训练窗口内部再次按时间顺序做验证，候选权重只根据训练窗口内部结果选择。
3. 六项权重非负、总和为1，并默认限制任一单项不超过55%。
4. 未来测试折同时比较：优化权重、固定 v0.1、振幅、流动性、日内空间和随机零假设。
5. 每个测试折默认生成200次随机零假设，输出优化结果相对随机分布的百分位和经验 p 值。
6. 只有同时通过“胜过最强简单基准、正 Rank IC 折比例、随机零假设显著性”三道晋级门槛，优化权重才有资格替换 v0.1。

工具还输出各权重跨外层折的均值/标准差/极值，用来识别“每个窗口都换一套完全不同权重”的不稳定过拟合。模拟或单股票结果不构成真实市场有效性证据；正式替换权重必须依赖真实历史数据、多股票/多市场状态的 OOS 结果。

## 性能修复：历史最佳T空间 O(n)

历史 `hindsight_envelope` 已从逐日枚举全部5分钟买卖组合的 O(n²) 实现改为等价 O(n) 实现。算法分别维护“此前最低买入总成本”或“此前最高卖出净回款”，仍精确保留最低佣金、卖出印花税、其他费率与滑点。随机价格序列回归测试会将线性算法与穷举算法逐笔对比净收益，确保性能优化没有改变结果。

## 多股票横截面 OOS 校准

单只股票时间序列验证只能回答“这只票自己的历史分数是否领先自己的未来机会”。最终全市场做T筛选更关键的问题是：**同一个交易日的候选股票里，高 T Score 的股票是否真的拥有更大的未来净T机会**。

横截面校准按“日期 × 股票”构建面板，并采用成熟因子研究中常见的逐日 Spearman Rank IC、分位组未来机会和 Top-Bottom 差值口径。命令行示例：

```bash
python -m app.cross_sectional_cli \
  --provider demo \
  --codes 300059,601899,601138,000063,300750,300308 \
  --days 500 --horizon 5 --min-assets 5 \
  --candidates 128 --random-trials 100
```

外层训练/测试仍按日期严格 walk-forward，并保留 `gap=horizon`；每个训练窗口内部独立选择权重，未来测试日期只用于最终 OOS 评价。输出包括：

- 逐日横截面 Rank IC
- Rank IC 正值比例、标准差和 ICIR
- 按日分位组未来净T机会
- Top-Bottom 分位差
- 优化权重 vs v0.1 / 振幅 / 流动性 / 日内空间
- 随机零假设百分位与经验 p 值
- 横截面专属晋级门槛

横截面优化内核会预计算每个日期的特征矩阵和标签排名，再使用 NumPy 评估候选权重，避免候选数增加时重复执行昂贵的 pandas groupby/rank。

**重要限制：真实历史横截面研究不能只拿“今天仍上市的股票”回看过去。** 这会产生幸存者偏差。正式全市场校准需要点时（point-in-time）股票池，至少正确处理历史上市、退市、停牌和可交易状态；在完成这一层之前，横截面结果只能作为研究诊断，不生成正式 T Score v0.2 权重。

## v0.2 联网合并前验收

宽基市场状态现在使用显式参考资产，不再把 `000300` 之类六码按普通股票处理。可单独验证指数或ETF：

```bash
python -m app.benchmark_cli csi300 --provider auto --days 500
python -m app.benchmark_cli csi300_etf_sh --provider auto --days 120
python -m app.regime_cli --benchmark csi300 --provider auto --days 500
```

完整联网合并门槛：

```bash
python -m app.live_validate_cli --provider auto --benchmark-provider auto \
  --benchmark csi300 --reference-etf csi300_etf_sh --with-baostock
```

该命令检查沪深北当前股票池、各市场代表日K、5分钟K、宽基指数、ETF、BaoStock点时证券快照、沪深跨源成员重合率，以及临时 DuckDB K线/证券快照读写。详细说明见 `docs/LIVE_VALIDATION.md`。当前执行环境若无法联网或缺少 DuckDB/BaoStock，报告应明确失败，而不是把未执行检查标成通过。
