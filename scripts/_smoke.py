# -*- coding: utf-8 -*-
"""核心逻辑冒烟测试（合成数据，不依赖 GUI / 网络）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from cnstock.core.constants import OrderSide, OrderStatus
from cnstock.engine.broker import SimBroker
from cnstock.backtest.engine import BacktestEngine
from cnstock.backtest.strategies import STRATEGY_REGISTRY, get_strategy
from cnstock.core.models import Trade


def test_broker() -> None:
    print("[1] 撮合引擎 ...")
    b = SimBroker()
    q = {"price": 100.0, "open": 99, "high": 101, "low": 98, "prev_close": 99, "name": "测试"}

    # 买入 100 股
    o1 = b.submit_order("600519", OrderSide.BUY, 100, price=100.0, quote=q)
    assert o1.status == OrderStatus.FILLED and o1.filled_qty == 100, o1
    print(f"    买入成交：现金={b.account.cash:,.2f} 持仓={b.positions[0].total_qty}")

    # T+1 当日卖出应拒单
    o2 = b.submit_order("600519", OrderSide.SELL, 100, price=101.0,
                        quote={"price": 101, "prev_close": 99})
    assert o2.status == OrderStatus.REJECTED, o2.status
    print(f"    T+1 拒单：{o2.message}")

    # 解锁后可卖
    b.daily_settlement(force=True)
    o3 = b.submit_order("600519", OrderSide.SELL, 100, price=101.0,
                        quote={"price": 101, "prev_close": 99})
    assert o3.status == OrderStatus.FILLED, o3.status
    print(f"    次日卖出成交：盈亏={b.account.total_pnl:+,.2f}")

    # 非整百拒单
    b2 = SimBroker()
    o4 = b2.submit_order("600519", OrderSide.BUY, 150, price=10, quote=q)
    assert o4.status == OrderStatus.REJECTED, o4.status
    print(f"    非整百拒单：{o4.message}")

    # 涨停拒买
    b3 = SimBroker()
    up_q = {"price": 11.0, "open": 10, "high": 11.0, "low": 10, "prev_close": 10.0}
    # 涨停价 = 11.0，price 达到涨停
    o5 = b3.submit_order("600519", OrderSide.BUY, 100, price=11.0, quote=up_q)
    assert o5.status == OrderStatus.REJECTED, o5.status
    print(f"    涨停拒买：{o5.message}")

    # 资金不足拒单
    b4 = SimBroker()
    b4.reset(1000)
    o6 = b4.submit_order("600519", OrderSide.BUY, 10000, price=100, quote=q)
    assert o6.status == OrderStatus.REJECTED, o6.status
    print(f"    资金不足拒单：{o6.message}")
    print("    Broker OK\n")


def _make_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    price = 100 + np.cumsum(rng.standard_normal(n))
    return pd.DataFrame({
        "date": pd.date_range("2021-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": price + rng.standard_normal(n) * 0.5,
        "high": price + np.abs(rng.standard_normal(n)),
        "low": price - np.abs(rng.standard_normal(n)),
        "close": price,
        "volume": rng.integers(1000, 10000, n),
        "amount": price * rng.integers(1000, 10000, n) * 100,
        "pct_chg": rng.standard_normal(n),
        "turnover": rng.random(n) * 3,
    })


def test_backtest() -> None:
    print("[2] 回测引擎（6 策略） ...")
    df = _make_df(400)
    engine = BacktestEngine()
    for name in STRATEGY_REGISTRY:
        strat = get_strategy(name)
        res = engine.run(strat, df, symbol="600519", name="测试")
        m = res.metrics
        assert len(res.equity_curve) == 400, name
        print(f"    {name:<12} 总收益 {m.total_return*100:+.2f}% | 基准 {m.benchmark_return*100:+.2f}% | "
              f"交易 {m.trade_count} | 夏普 {m.sharpe:.2f} | 回撤 {m.max_drawdown*100:.2f}%")
    print("    Backtest OK\n")


def test_metrics() -> None:
    print("[3] 绩效指标 ...")
    from cnstock.backtest.metrics import compute_metrics
    eq = [(f"2021-01-{i:02d}", 1_000_000 + i * 1000) for i in range(1, 20)]
    trades = [
        Trade(symbol="600519", name="测试", side=OrderSide.BUY, price=100, quantity=100,
              amount=10000, fee=5, traded_at="2021-01-01 09:35:00"),
        Trade(symbol="600519", name="测试", side=OrderSide.SELL, price=110, quantity=100,
              amount=11000, fee=5.5, traded_at="2021-01-05 09:35:00"),
    ]
    m = compute_metrics(eq, trades, 1_000_000, benchmark_return=0.03)
    assert m.win_rate == 1.0, m.win_rate
    assert m.trade_count == 1, m.trade_count
    print(f"    胜率 {m.win_rate:.0%} 盈亏比 {m.profit_factor:.2f} 总收益 {m.total_return*100:.2f}%")
    print("    Metrics OK\n")


if __name__ == "__main__":
    test_broker()
    test_backtest()
    test_metrics()
    print("=== 全部冒烟测试通过 ===")
