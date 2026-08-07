# v0.2 联网合并前验收

本文件定义 `dev/v0.2-market-scanner` 合入 `main` 之前必须在真实联网开发机执行的端到端检查。

## 为什么不能只看 pytest

离线单元测试能证明缓存、回测、校准和防泄漏控制流符合设计，但不能证明公开行情源今天仍可访问，也不能证明第三方数据源对沪深北和历史证券名单的真实覆盖完整。因此真实数据回归必须单独保存报告。

## 开源实现参考

- `simonlin1212/a-stock-data` 当前把指数/ETF当成独立行情类型处理，并强调行情源需要降级；本项目继续采用独立 Provider + fallback 思路，不直接复制其代码。
- AKShare 当前 `stock_zh_index_daily_em` 对 `sz/sh/csi` 使用明确市场标识，其中中证系列使用 `2.xxxxxx`；ETF `fund_etf_hist_em` 使用沪市 `1`、深市 `0` 的 market id。本项目据此将证券类型显式化，不再用六码本身猜“股票还是指数”。

## 宽基/ETF参考资产

当前内置：

- 指数：上证指数、沪深300、中证500、中证1000、深证成指、创业板指、科创50
- ETF：上证50ETF、沪/深两只沪深300ETF、中证500ETF、创业板ETF、科创50ETF

中证指数在 Eastmoney 不同接口中可能出现不同 namespace，因此 `csi300/csi500/csi1000` 会按声明顺序尝试多个 secid，并记录最终实际命中的 secid。

## 建议安装

```bash
pip install -r requirements.txt
pip install akshare
pip install "baostock>=0.9.3"
pip install duckdb
```

## 一键联网验收

```bash
python -m app.live_validate_cli \
  --provider auto \
  --benchmark-provider auto \
  --benchmark csi300 \
  --reference-etf csi300_etf_sh \
  --with-baostock \
  --json-out output/live_validation.json
```

成功退出码为 `0`；存在硬失败时退出码为 `2`。

## 硬检查

1. 当前A股股票池总数不低于最低门槛。
2. 默认必须同时存在 SH / SZ / BJ。
3. 用户给定代表股票 + 股票池中自动抽取的各市场样本都能获取近期日K。
4. 至少一个代表股票能获取5分钟K。
5. 显式宽基指数能获取日K，且资产类型为 `index`。
6. 显式ETF能获取日K，且资产类型为 `etf`。
7. 启用 BaoStock 后，历史/当前点时快照不能异常偏小。
8. Eastmoney/AKShare 当前股票池与 BaoStock 点时快照在沪深市场的代码重合率必须达到配置阈值（默认90%）。
9. DuckDB 使用临时数据库完成 K 线和证券快照写入/读取，不污染正式 `market.duckdb`。

## 北交所处理

北交所对当前全A股扫描是硬要求：市场行情股票池默认必须有 BJ。

BaoStock 的北交所历史证券主表覆盖仍需要独立来源交叉验证，因此 `point_in_time_overlap_bj` 当前只作为警告项；不能因为该项 PASS 就宣称北交所历史点时名单已经权威验证。

## 不能降低的合并门槛

- 不能用 `--allow-missing-bj` 跑过一次就视为正式通过；该参数只用于定位某个数据源是否缺北交所。
- 不能仅靠 AKShare 与 Eastmoney 互相证明股票池完整，因为 AKShare 的部分接口本身也可能使用 Eastmoney 上游。
- 必须保留生成的 JSON 报告，记录执行日期、实际命中 Provider 和失败/警告项。
- 真实多股票 OOS / regime 分层结果没有通过晋级门槛前，不生成正式 T Score v0.2 权重。
