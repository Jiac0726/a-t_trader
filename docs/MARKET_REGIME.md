# 市场状态分层（Regime Stratification）

做T适合度很可能不是跨市场环境恒定的：同一套评分在震荡市、单边趋势、高波动阶段可能表现不同。因此 v0.2 不把“全样本平均 IC”作为唯一依据，而要分市场状态检查。

## 为什么第一版不用 HMM

开源调研里，HMM 是常见 regime 方法，但 `hmmlearn` 当前处于 limited-maintenance，并引入 scikit-learn/C 扩展。更关键的是，HMM 的整段 Viterbi / smoothed 历史状态如果直接用于回测，容易混入未来观测。

第一版选择可审计的 causal trailing 规则，不增加新依赖：

- 趋势性：Kaufman 风格 Efficiency Ratio，`|当前价-N日前价| / N日绝对路径长度`；
- 方向：N 日滚动收益；
- 波动：滚动对数收益年化实现波动率；
- 高波动：实现波动率既要处于自身 trailing 历史高分位，也要超过绝对年化波动率下限。

所有指标在日期 D 只使用 D 及以前的数据。

## 四类状态

优先级：

1. `high_vol`：历史波动分位高，且绝对年化波动率也超过下限；
2. `trend_up`：效率比高、N日收益超过最低幅度且为正；
3. `trend_down`：效率比高、N日收益超过最低幅度且为负；
4. `range`：其余已完成预热的日期。

高波动同时设置“相对阈值 + 绝对阈值”，避免长期极低波动序列只因微小波动抬升就被误判成风险状态。

## CLI

单独查看任意价格序列的状态：

```bash
python -m app.regime_cli 300059 --provider demo --days 300 --no-cache
```

横截面研究可以额外提供一个基准/代理代码：

```bash
python -m app.cross_sectional_cli \
  --provider demo \
  --regime-code 300059 \
  --codes 300059,601899,601138,000063,300750,300308
```

工具会输出每种状态的覆盖天数，以及固定 v0.1、振幅、流动性、日内空间在该状态中的逐日横截面 Rank IC 和 Top-Bottom 未来净T机会差。

## 当前边界

`--regime-code` 是通用价格序列入口；正式“市场状态”应使用经过验证的宽基指数/ETF，而不是任意单股。当前行情 Provider 对指数代码的市场识别还需要真实数据回归，因此 PR #2 合并前不会用 Demo 或单股代理结果生成正式 regime-conditioned 权重。

后续若透明规则在真实 OOS 中不足，再评估 HMM/聚类；任何拟合型 regime 模型都必须在每个训练折内部拟合，并用 filtered/online 状态评估未来折，不能使用全样本平滑状态。
