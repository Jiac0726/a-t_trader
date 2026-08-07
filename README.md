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
