from __future__ import annotations

import pandas as pd


def _status_amplitude(value: float) -> str:
    if value < 1.5:
        return "空间偏小"
    if value < 2.5:
        return "一般"
    if value <= 5.5:
        return "高分区间"
    if value <= 8.0:
        return "空间较大但开始降分"
    return "高波动风险"


def _status_space(value: float) -> str:
    if value < 1.5:
        return "空间偏小"
    if value < 3.0:
        return "可用"
    if value < 5.0:
        return "较好"
    return "达到该维度满分区间"


def _status_reversal(value: float) -> str:
    if 48.4 <= value <= 66.6:
        return "约80分以上区间"
    if 42.0 <= value <= 73.0:
        return "中等均值回归特征"
    return "偏离高分中心较多"


def build_component_detail(score, features: dict[str, float]) -> pd.DataFrame:
    """Six T-score dimensions with the actual raw inputs used by scoring."""
    amount_yi = float(features["median_amount"]) / 1e8
    trend_distance = float(features["trend_distance"])
    drawdown = float(features["max_drawdown"])
    extreme_rate = float(features["extreme_day_rate"])
    volatility = float(features["return_volatility"])

    rows = [
        {
            "维度": "振幅空间",
            "原始指标": "历史平均振幅",
            "原始值": f"{float(features['avg_amplitude']):.2f}%",
            "评分参考": "2.5%–5.5%：65→100分；5.5%后逐步降分；>8%进入高波动惩罚",
            "当前状态": _status_amplitude(float(features["avg_amplitude"])),
            "分数": float(score.amplitude_score),
        },
        {
            "维度": "流动性",
            "原始指标": "历史中位成交额",
            "原始值": f"{amount_yi:.2f}亿元",
            "评分参考": "按对数函数连续评分；约5亿≈49分，50亿≈84分，约139亿达到100分",
            "当前状态": "成交额越高分数越高，100分后封顶",
            "分数": float(score.liquidity_score),
        },
        {
            "维度": "日内空间",
            "原始指标": "历史平均日内高低空间",
            "原始值": f"{float(features['avg_intraday_space']):.2f}%",
            "评分参考": "线性评分：1%=20分，2.5%=50分，4%=80分，≥5%=100分",
            "当前状态": _status_space(float(features["avg_intraday_space"])),
            "分数": float(score.tradable_space_score),
        },
        {
            "维度": "均值回归",
            "原始指标": "相邻交易日涨跌方向反转率",
            "原始值": f"{float(features['reversal_rate']):.2f}%",
            "评分参考": "57.5%为100分中心；约48.4%–66.6%对应80分以上",
            "当前状态": _status_reversal(float(features["reversal_rate"])),
            "分数": float(score.mean_reversion_score),
        },
        {
            "维度": "趋势稳定",
            "原始指标": "20日均线偏离 + 最大回撤",
            "原始值": f"均线偏离 {trend_distance:.2f}% ｜ 最大回撤 {drawdown:.2f}%",
            "评分参考": "均线偏离≤1.5%不扣分；最大回撤绝对值≤8%不扣分；两部分权重60%/40%",
            "当前状态": "偏离越小、回撤越浅越稳定",
            "分数": float(score.trend_score),
        },
        {
            "维度": "风险控制",
            "原始指标": "极端振幅日占比 + 年化波动率 + 最大回撤",
            "原始值": f"极端日 {extreme_rate:.2f}% ｜ 波动率 {volatility:.2f}% ｜ 回撤 {drawdown:.2f}%",
            "评分参考": "极端振幅日直接扣分；年化波动率>45%开始扣分；最大回撤绝对值>15%开始扣分",
            "当前状态": "三项风险暴露越低越好",
            "分数": float(score.risk_score),
        },
    ]
    return pd.DataFrame(rows)


def build_raw_metric_detail(features: dict[str, float]) -> pd.DataFrame:
    """One row per raw feature, including units and scoring context."""
    amount_yi = float(features["median_amount"]) / 1e8
    rows = [
        ("历史平均振幅", float(features["avg_amplitude"]), "%", "振幅空间", "2.5%–5.5%为当前评分高分段"),
        ("历史中位成交额", amount_yi, "亿元", "流动性", "对数连续评分，无硬阈值"),
        ("历史平均日内空间", float(features["avg_intraday_space"]), "%", "日内空间", "≥5%该维度封顶100分"),
        ("相邻日方向反转率", float(features["reversal_rate"]), "%", "均值回归", "57.5%为评分中心"),
        ("20日均线平均偏离", float(features["trend_distance"]), "%", "趋势稳定", "≤1.5%该子项不扣分"),
        ("最大回撤", float(features["max_drawdown"]), "%", "趋势稳定/风险", "绝对值≤8%趋势子项不扣分；>15%风险项开始扣分"),
        ("极端振幅日占比", float(features["extreme_day_rate"]), "%", "风险控制", "振幅>10%的交易日占比，越低越好"),
        ("年化收益波动率", float(features["return_volatility"]), "%", "风险控制", "≤45%风险项不因该指标扣分"),
        ("ATR(14)/收盘价", float(features["atr_pct"]), "%", "辅助观察", "当前T Score未直接使用，保留用于解释真实波动"),
        ("最新收盘价", float(features["latest_close"]), "元", "基础信息", "前复权日线的最新收盘价"),
        ("有效样本数", float(features["observations"]), "交易日", "数据质量", "由T Score回看窗口决定"),
    ]
    return pd.DataFrame(rows, columns=["原始指标", "原始值", "单位", "参与维度", "参考/说明"])
