# Streamlit Community Cloud 部署

当前 `dev/v0.2-market-scanner` 已按 Streamlit Community Cloud 结构准备：

- 入口：`app/dashboard.py`
- Python：3.12
- Python 依赖：根目录 `requirements.txt`
- Streamlit 配置：`.streamlit/config.toml`
- 默认可不配置任何密钥运行
- 可选 `TUSHARE_TOKEN` 作为授权型备用历史源

## 最快部署

1. 打开 `https://share.streamlit.io/`
2. 使用 GitHub 登录并授权公开仓库访问
3. 点击 **Create app**
4. 选择 **Yup, I have an app**
5. 填写：

```text
Repository: Jiac0726/a-t_trader
Branch: dev/v0.2-market-scanner
Main file path: app/dashboard.py
Python version: 3.12
```

6. 点击 **Deploy**

也可以在部署页使用 **Paste GitHub URL**，粘贴：

```text
https://github.com/Jiac0726/a-t_trader/blob/dev/v0.2-market-scanner/app/dashboard.py
```

## 可选：Tushare Token

没有 Token 也能部署。

如果需要 Tushare 作为授权型备用源，在 Streamlit Community Cloud 的 **Advanced settings → Secrets** 中加入根级 secret：

```toml
TUSHARE_TOKEN = "你的token"
```

不要把 Token 写进 GitHub 仓库。

## 第一次上线后的验证顺序

### 1. 先验证界面

数据源选择：

```text
离线演示
```

进入：

```text
做T候选榜 → 自选池 → 生成自选池候选榜
```

这一条完全不依赖公网行情，应该能立即看到：

- 排名
- T Score
- 适合度
- 风险
- 原因解释

### 2. 再验证真实沪深历史

数据源切换为：

```text
自动降级
```

先扫描少量自选股，例如：

```text
300059
601899
601138
000063
```

自动链优先使用 BaoStock 获取沪深历史，再降级到 Eastmoney / AKShare / Tencent；配置 Tushare Token 时还会加入 Tushare。

### 3. 最后测试全市场候选

首次不要直接扫 5000 多只，建议：

```text
本轮最多扫描：50~100
```

确认数据链正常后再提高数量。

## 云端 DuckDB 说明

`market.duckdb` 在 Community Cloud 上适合作为运行缓存，不应视为永久数据库。

Community Cloud 不保证运行时生成的本地文件永久保留；应用重启、重建或平台回收后，本地缓存可能丢失。

因此：

- 体验 / Demo：继续使用 DuckDB 即可
- 长期正式运行：后续改为持久数据库或对象存储

## 当前已验证边界

真实 GitHub Runner 已验证：

- 当前A股股票池约 5538 只，SH/SZ/BJ 均存在
- 沪深代表股日K通过
- 5分钟 raw K线通过
- 沪深300指数通过
- 沪深300ETF通过
- BaoStock点时沪深证券快照通过
- DuckDB读写通过

仍保留 WARN：

- 北交所免费云源长历史不稳定
- BaoStock点时快照不覆盖 BJ
- 北交所 2025-10-09 新旧代码迁移连续历史尚未完成生产验证

这些 WARN 不影响先体验沪深候选榜、自选池、单股分析和正T/倒T回测。
