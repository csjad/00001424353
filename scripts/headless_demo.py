# -*- coding: utf-8 -*-
"""无头（headless）运行证明：纯逻辑层在完全没有 Qt 的情况下能跑通。

这是「接口层剥离」的动态验证：把 engine / backtest / data / core / storage
当成可独立运行的服务端内核来对待。只要本脚本能退出 0，就说明这套逻辑
不依赖任何 GUI，将来套一层 FastAPI/Flask 即可直接变成服务端，UI 层只是
它的一个消费者。

做法：在导入任何业务模块之前，先往 sys.meta_path 装一个阻断器，
凡是要 import PyQt6.* 或 pyqtgraph.* 一律抛 ImportError。
在这种"没有 Qt 的世界"里跑完撮合 + 6 策略回测 + 绩效指标。

用法：  python scripts/headless_demo.py
退出码：0 = 纯逻辑层零 GUI 依赖；1 = 有模块偷偷引了 Qt
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------- 第 0 步：装阻断器（必须在任何业务 import 之前） ----------

BLOCKED_ROOTS = ("PyQt6", "pyqtgraph")


class _QtBlocker:
    """元路径查找器：阻断 GUI 依赖，模拟服务端没有装 Qt 的环境。"""

    def find_spec(self, fullname: str, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED_ROOTS:
            raise ImportError(
                f"[headless] 纯逻辑层禁止导入 GUI 依赖：{fullname}。"
                f"服务端部署环境不安装 PyQt6/pyqtgraph，请在 "
                f"core/data/engine/backtest/storage 中移除该导入。"
            )
        return None


sys.meta_path.insert(0, _QtBlocker())

# ---------- 第 1 步：现在才允许导入业务模块 ----------
import tempfile  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cnstock.backtest.engine import BacktestEngine  # noqa: E402
from cnstock.backtest.metrics import compute_metrics  # noqa: E402
from cnstock.backtest.strategies import STRATEGY_REGISTRY, get_strategy  # noqa: E402
from cnstock.core.constants import OrderSide, OrderStatus  # noqa: E402
from cnstock.core.models import Account  # noqa: E402
from cnstock.engine.broker import SimBroker  # noqa: E402
from cnstock.storage.db import SqliteStorage  # noqa: E402


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


def demo_broker() -> None:
    """撮合引擎：真实 A 股规则全链路（无 Qt）。"""
    print("[1] 撮合引擎（无 Qt） ...")
    b = SimBroker()
    q = {"price": 100.0, "open": 99, "high": 101, "low": 98, "prev_close": 99, "name": "测试"}

    o1 = b.submit_order("600519", OrderSide.BUY, 100, price=100.0, quote=q)
    assert o1.status == OrderStatus.FILLED and o1.filled_qty == 100, o1
    print(f"    买入成交：现金={b.account.cash:,.2f} 持仓={b.positions[0].total_qty}")

    o2 = b.submit_order("600519", OrderSide.SELL, 100, price=101.0,
                        quote={"price": 101, "prev_close": 99})
    assert o2.status == OrderStatus.REJECTED, o2.status
    print(f"    T+1 拒单：{o2.message}")

    b.daily_settlement(force=True)
    o3 = b.submit_order("600519", OrderSide.SELL, 100, price=101.0,
                        quote={"price": 101, "prev_close": 99})
    assert o3.status == OrderStatus.FILLED, o3.status
    print(f"    次日卖出成交：盈亏={b.account.total_pnl:+,.2f}")

    b2 = SimBroker()
    o4 = b2.submit_order("600519", OrderSide.BUY, 150, price=10, quote=q)
    assert o4.status == OrderStatus.REJECTED, o4.status
    print(f"    非整百拒单：{o4.message}")

    b3 = SimBroker()
    up_q = {"price": 11.0, "open": 10, "high": 11.0, "low": 10, "prev_close": 10.0}
    o5 = b3.submit_order("600519", OrderSide.BUY, 100, price=11.0, quote=up_q)
    assert o5.status == OrderStatus.REJECTED, o5.status
    print(f"    涨停拒买：{o5.message}")

    b4 = SimBroker()
    o6 = b4.submit_order("600519", OrderSide.BUY, 10000, price=100, quote=q)
    assert o6.status == OrderStatus.REJECTED, o6.status
    print(f"    资金不足拒单：{o6.message}")
    print("    Broker OK\n")


def demo_backtest() -> None:
    """回测引擎：6 策略全跑（无 Qt）。"""
    print("[2] 回测引擎 6 策略（无 Qt） ...")
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


def demo_storage() -> None:
    """持久化：SQLite 往返（无 Qt）。"""
    print("[3] 持久化（无 Qt） ...")
    tmp = Path(tempfile.mkdtemp(prefix="cnstock_headless_"))
    db = SqliteStorage(tmp / "headless.db")
    acc = Account(initial_cash=1_000_000.0, cash=1_000_000.0)
    db.save_account(acc)
    got = db.load_account()
    assert got is not None and abs(got.cash - 1_000_000.0) < 1e-6, got
    print(f"    账户写入→读回：cash={got.cash:,.2f} initial_cash={got.initial_cash:,.2f}")
    print("    Storage OK\n")


def demo_metrics() -> None:
    """绩效指标（无 Qt）。"""
    print("[4] 绩效指标（无 Qt） ...")
    eq = [(f"2021-01-{i:02d}", 1_000_000 + i * 1000) for i in range(1, 20)]
    m = compute_metrics(eq, [], 1_000_000.0)
    print(f"    总收益 {m.total_return*100:+.2f}% | 夏普 {m.sharpe:.2f} | 回撤 {m.max_drawdown*100:.2f}%")
    print("    Metrics OK\n")


def main() -> int:
    demo_broker()
    demo_backtest()
    demo_storage()
    demo_metrics()

    leaked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED_ROOTS)
    if leaked:
        print(f"=== 失败：检测到 Qt 模块被加载：{leaked[:5]} ===")
        return 1

    print("=== 无头证明通过：纯逻辑层零 GUI 依赖，可直接套 FastAPI 变服务端 ===")
    print("    （sys.modules 中 PyQt6/pyqtgraph 条目数：0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
