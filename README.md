# A股做T分析工具 MVP

一个面向 A 股历史行情的本地分析工具，用真实历史数据衡量股票是否具备稳定的日内做T空间。

## 当前能力

- 多行情 Provider 抽象与自动降级
- 东方财富历史日K/分钟K适配器
- AKShare 备用 Provider
- 离线 Demo Provider
- OHLCV 数据校验
- 60日历史特征计算
- T Score v0.1
- 5分钟 ZigZag 做T机会统计
- 自选池批量扫描
- Streamlit 本地界面
- DuckDB 存储骨架
- Windows 一键安装与启动脚本

## 运行

```bash
pip install -r requirements.txt
streamlit run app/dashboard.py
```

Windows 也可直接运行：

```text
install_windows.bat
run_dashboard.bat
```

## 测试

```bash
python -m pytest -q
```

当前 MVP 重点是历史特征分析和可解释评分，不提供收益承诺或确定性交易信号。
