# -*- coding: utf-8 -*-
"""
回测绩效指标计算。

所有收益率均以**小数**表示（0.1532 = +15.32%），回撤以正数表示（0.2 = -20%）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: A 股年化交易日数
TRADING_DAYS_PER_YEAR: int = 252


@dataclass
class TradePair:
    """一次完整的开平仓配对（FIFO）。"""

    open_date: str = ""
    open_price: float = 0.0
    close_date: str = ""
    close_price: float = 0.0
    quantity: int = 0
    pnl: float = 0.0           # 已扣除双边费用
    hold_days: float = 0.0


def pair_trades(trades: list) -> list[TradePair]:
    """
    将成交记录按 FIFO 配对成开平仓。

    :param trades: 按时间**升序**排列的成交列表（需含 side/price/quantity/amount/fee/traded_at）
    """
    from datetime import datetime

    def _days(a: str, b: str) -> float:
        try:
            da = datetime.strptime(str(a)[:10], "%Y-%m-%d")
            db = datetime.strptime(str(b)[:10], "%Y-%m-%d")
            return max((db - da).days, 0)
        except Exception:
            return 0.0

    queue: list[tuple[str, float, int]] = []       # (date, price, qty)
    pairs: list[TradePair] = []

    for t in trades:
        side = str(getattr(t, "side", "")).lower()
        if "buy" in side or "买入" in str(getattr(t, "side", "")):
            queue.append((str(t.traded_at)[:10], float(t.price), int(t.quantity)))
            continue

        remain = int(t.quantity)
        while remain > 0 and queue:
            od, op, oq = queue[0]
            matched = min(remain, oq)
            fee_ratio = float(t.fee) / float(t.amount) if t.amount else 0.0
            gross = (float(t.price) - op) * matched
            fee = float(t.price) * matched * fee_ratio
            pairs.append(TradePair(
                open_date=od,
                open_price=op,
                close_date=str(t.traded_at)[:10],
                close_price=float(t.price),
                quantity=matched,
                pnl=gross - fee,
                hold_days=_days(od, str(t.traded_at)),
            ))
            remain -= matched
            if matched >= oq:
                queue.pop(0)
            else:
                queue[0] = (od, op, oq - matched)

    # 未平仓部分不计入胜率统计
    return pairs


def max_drawdown(values: list[float]) -> float:
    """最大回撤（正数）。"""
    if not values:
        return 0.0
    arr = np.asarray(values, dtype=float)
    peak = np.maximum.accumulate(arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (peak - arr) / peak, 0.0)
    return float(np.max(dd)) if dd.size else 0.0


def sharpe_ratio(daily_returns: np.ndarray, rf_annual: float = 0.0) -> float:
    """夏普比率（年化）。"""
    if daily_returns.size < 2:
        return 0.0
    std = float(np.std(daily_returns, ddof=1))
    if std <= 1e-12:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS_PER_YEAR
    excess = daily_returns - rf_daily
    return float(np.mean(excess) / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def sortino_ratio(daily_returns: np.ndarray, rf_annual: float = 0.0) -> float:
    """索提诺比率（只惩罚下行波动）。"""
    if daily_returns.size < 2:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS_PER_YEAR
    excess = daily_returns - rf_daily
    downside = excess[excess < 0]
    if downside.size < 2:
        return 0.0
    dd_std = float(np.std(downside, ddof=1))
    if dd_std <= 1e-12:
        return 0.0
    return float(np.mean(excess) / dd_std * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_metrics(
    equity_curve: list[tuple[str, float]],
    trades: list,
    initial_cash: float,
    benchmark_return: float = 0.0,
    rf_annual: float = 0.0,
) -> "Metrics":
    """
    由资金曲线与成交记录计算全套绩效指标。

    :param equity_curve: ``[(日期, 总资产), ...]`` 按时间升序
    :param trades: 成交记录（升序）
    :param initial_cash: 期初资金
    :param benchmark_return: 基准收益率（买入持有）
    :param rf_annual: 年化无风险利率
    """
    from ..core.models import Metrics

    if not equity_curve:
        return Metrics(initial_cash=initial_cash, final_value=initial_cash)

    values = [float(v) for _, v in equity_curve]
    final_value = values[-1]

    total_return = final_value / initial_cash - 1.0 if initial_cash > 0 else 0.0

    n_days = max(len(values), 1)
    years = n_days / TRADING_DAYS_PER_YEAR
    if years > 0 and initial_cash > 0 and final_value > 0:
        annual_return = (final_value / initial_cash) ** (1.0 / years) - 1.0
    else:
        annual_return = 0.0

    arr = np.asarray(values, dtype=float)
    daily_returns = np.diff(arr) / arr[:-1] if arr.size > 1 else np.array([])
    daily_returns = daily_returns[np.isfinite(daily_returns)]

    pairs = pair_trades(trades)
    win = sum(1 for p in pairs if p.pnl > 0)
    gross_profit = sum(p.pnl for p in pairs if p.pnl > 0)
    gross_loss = abs(sum(p.pnl for p in pairs if p.pnl < 0))

    return Metrics(
        initial_cash=initial_cash,
        final_value=final_value,
        total_return=total_return,
        annual_return=annual_return,
        max_drawdown=max_drawdown(values),
        sharpe=sharpe_ratio(daily_returns, rf_annual),
        sortino=sortino_ratio(daily_returns, rf_annual),
        win_rate=win / len(pairs) if pairs else 0.0,
        profit_factor=gross_profit / gross_loss if gross_loss > 1e-9 else (
            float("inf") if gross_profit > 0 else 0.0
        ),
        trade_count=len(pairs),
        avg_hold_days=float(np.mean([p.hold_days for p in pairs])) if pairs else 0.0,
        benchmark_return=benchmark_return,
        alpha=total_return - benchmark_return,
    )
