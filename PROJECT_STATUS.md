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
- pytest：5/5 通过
- Demo 全市场模式可生成排行榜
- Python compileall 通过

## 当前环境限制

构建环境无法解析公网 DNS，且无法从 PyPI 安装 DuckDB，因此本轮不能在当前容器执行真实东财接口和真实 DuckDB 驱动回归。DuckDB 模块采用延迟导入；缓存控制逻辑已通过内存 Store 替身测试。用户联网开发机安装 requirements 后应优先执行真实数据回归。

## v0.2 下一阶段

- [ ] 真实东财股票池接口回归并校验沪深北数量
- [ ] DuckDB 真机回归与 schema migration
- [ ] 批量/并发日K首次灌库，避免5000只串行请求
- [ ] 数据源健康检查与失败重试
- [ ] 5分钟数据按需缓存
- [ ] 正T/倒T回测模型（手续费、印花税、滑点、T+1）
- [ ] 高低点时间分布
- [ ] T Score 参数回测
