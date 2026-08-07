# 项目状态

## v0.1 已完成并合入 main

- [x] Provider 抽象层
- [x] 东方财富历史日K/分钟K直连接口适配器
- [x] AKShare 日K备用适配器
- [x] 自动降级 ProviderChain
- [x] 离线 DemoProvider
- [x] OHLCV 数据质量检查
- [x] 60日历史特征
- [x] T Score v0.1
- [x] 5分钟 ZigZag T机会统计
- [x] 自选池批量扫描 CLI
- [x] Streamlit 界面
- [x] Windows 一键安装/启动脚本
- [x] pytest 自动测试

## v0.2 已完成第一阶段

- [x] 全A股股票池 Provider 接口
- [x] 东方财富全A股股票池直连实现
- [x] AKShare `stock_zh_a_spot_em` 备用股票池
- [x] ProviderChain 股票池自动降级
- [x] DuckDB 股票池缓存
- [x] DuckDB K线按日期覆盖写入
- [x] 历史K线缓存边界查询
- [x] CachedProvider 缺失日期增量补取
- [x] scanner 可写入评分缓存
- [x] `--all` 全市场模式
- [x] `--limit` 首次缓存限量模式
- [x] ST / 当前成交额预筛
- [x] Streamlit 全市场扫描页
- [x] 缓存复用自动测试

## 当前验证

- 离线流水线完整运行
- pytest：17/17 通过
- Demo 全市场模式可生成排行榜
- Python compileall 通过

## 当前环境限制

构建环境无法解析公网 DNS，且无法从 PyPI 安装 DuckDB，因此本轮不能在当前容器执行真实东财接口和真实 DuckDB 驱动回归。DuckDB 模块采用延迟导入；缓存控制逻辑已通过内存 Store 替身测试。用户联网开发机安装 requirements 后应优先执行真实数据回归。

## v0.2 第二阶段已完成

- [x] 有上限的并发日K首次灌库（网络并发、数据库串行写入）
- [x] 聚合请求速率限制
- [x] 指数退避重试包装器
- [x] 数据源健康检查
- [x] 股票池异常小结果保护（防分页/接口退化被误当完整市场）
- [x] 缓存覆盖区间元数据，正确处理周末/节假日“无K线但已检查”场景

## v0.2 第三阶段已完成

- [x] 5分钟数据按需缓存（复用通用 CachedProvider）
- [x] 正T/倒T回测模型 v0.1（佣金、最低佣金、卖出印花税、额外费率、滑点、T+1底仓约束）
- [x] 历史最佳单次T空间（后视机会天花板，明确非策略）
- [x] 因果滚动Z分数均值回归基线（信号后延后一根5分钟K执行）
- [x] 独立回测 CLI + Streamlit T回测实验室

## v0.2 下一阶段

- [ ] 真实东财股票池接口回归并校验沪深北数量
- [ ] DuckDB 真机回归与 schema migration
- [x] 5分钟日内最高/最低点时间分布（30分钟桶 + 中位时间/上午/尾盘占比）
- [ ] T Score 参数回测
