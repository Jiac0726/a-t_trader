# v0.2 联网合并前验收

本地单元测试不能替代真实上游接口回归。`app.live_validate_cli` 用于联网开发机上的合并前检查，并输出机器可读 JSON。

## 推荐命令

```bash
pip install -r requirements.txt
pip install akshare baostock
python -m app.live_validate_cli \
  --provider auto \
  --benchmark-provider auto \
  --benchmark csi300 \
  --with-baostock \
  --db market.duckdb
```

默认检查：

1. 当前 A 股股票池数量与 SH/SZ 市场覆盖；异常小结果直接 FAIL。
2. 代表性股票最近日 K 是否可解析。
3. 代表性股票 5 分钟 K 是否可解析。
4. 明确证券类型的宽基指数历史行情，例如 `csi300`。
5. 可选 BaoStock 点时历史证券快照。
6. DuckDB 实际写入/读取 round-trip。

输出默认写入 `output/live_validation.json`。存在硬 FAIL 时进程退出码为 2，可直接接 CI/发布脚本。

## 宽基指数为什么独立建模

六位代码不足以唯一表达证券类型。例如 `000300` 作为沪深300指数在东方财富 K 线接口使用 `1.000300`；如果套用股票市场前缀猜测，会把它错误映射为深市普通证券。因此市场状态研究使用 `BenchmarkSpec` 显式注册表，不再依赖代码猜类型。

当前注册：

- `sse` 上证指数
- `csi300` 沪深300
- `csi500` 中证500
- `csi1000` 中证1000
- `szse` 深证成指
- `chinext` 创业板指
- `star50` 科创50

## 合并门槛

单次 PASS 仍不足以生成正式 T Score v0.2 权重。至少还需：

- 不同日期重复执行真实接口回归；
- 沪深北股票数量与独立来源交叉检查；
- BaoStock 历史快照抽样核对退市、停牌、未来 IPO 与北交所样本；
- DuckDB 旧库升级/新库创建均验证；
- 真实多股票横截面 OOS + 市场状态分层通过晋级门槛。
