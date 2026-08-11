# 点时（Point-in-Time）历史股票池

横截面 T Score 校准不能拿“今天仍上市的股票”直接回看过去，否则会把已经退市的失败样本排除，并把未来才上市的股票错误放进历史样本，形成幸存者偏差和未来信息泄漏。

## 开源方案选择

当前开发分支把 BaoStock 作为**可选**证券主表源，而不是行情主源或硬依赖：

- PyPI 最新版 `baostock 0.9.3`（2026-07-10），BSD License；
- `query_all_stock(day=YYYY-MM-DD)` 可直接获得指定历史交易日的证券代码、交易状态和名称；
- `query_stock_basic()` 提供 `ipoDate / outDate / type / status` 生命周期字段；
- `query_trade_dates()` 用来把周末/节假日请求解析到最近一个真实交易日。

安装：

```bash
pip install -e '.[baostock]'
```

或：

```bash
pip install baostock>=0.9.3
```

## 架构

证券身份与行情价格分离：

```text
MarketDataProvider
  ├─ Eastmoney
  └─ AKShare

SecurityMasterProvider
  └─ BaoStock (optional)
```

这样未来可以增加交易所证券主表、商业数据源或自建历史快照，而不影响 K 线和评分逻辑。

## 两级点时过滤

### Lifecycle fallback

使用 IPO / 退市日期剔除尚未上市、退市后的股票和非股票证券。主表未知证券默认剔除。这是低成本 fallback，但不能表达每一天的停牌或所有历史状态变化。

### Exact historical snapshot（正式横截面优先）

对每个历史评分日期调用 `query_all_stock(day)`，得到当天实际证券名单与 `tradeStatus`。做T研究默认只保留 `tradeStatus=1` 的可交易股票。

```bash
python -m app.cross_sectional_cli \
  --provider auto \
  --security-master baostock-snapshot \
  --codes 300059,601899,601138,000063,300750,300308
```

为了避免数百个历史日期重复请求，`CachedSecurityMasterProvider` 会把每个交易日快照写入独立的 `DuckDBSecuritySnapshotStore`；第二次校准只补缺失日期。

单独查看某个历史日期：

```bash
python -m app.universe_cli --as-of 2020-01-02 --exchange SH
```

## 北交所覆盖说明

BaoStock 的历史 `query_all_stock` 可能返回 `bj.*` 证券，但其基本资料接口文档主要以沪深代码为例，北交所生命周期元数据覆盖不应在未真机验证前视为与沪深完全一致。

当前实现对北交所历史快照采用保守代码前缀识别（43/83/87/88/92），明确排除 899xxx 指数命名空间；这是 best-effort。正式全A股 v0.2 校准前仍需要在联网开发机核对北交所实际覆盖、历史退市/转板样本与代码规则。

## 合并硬门槛

PR #2 仍保持 Draft。点时框架和离线适配器测试通过不等于数据源真实覆盖已经验证。合并前必须至少完成 BaoStock 真机历史快照回归、沪深北历史日期抽查、退市/未来 IPO/停牌案例检查、DuckDB 快照持久化，以及另一独立历史证券主表的交叉抽样比对。
