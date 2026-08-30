# -*- coding: utf-8 -*-
"""
命令行快速回测示例（无需 GUI）。

演示项目在「无界面」环境下的可用性，也方便写 README 与 CI 验证。

用法：
    python scripts/cli_demo.py 600519 --strategy 双均线交叉 --years 3
    python scripts/cli_demo.py 300750 --strategy MACD 背离 --cash 500000
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

# 让脚本能 import 到项目根下的 cnstock 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cnstock.backtest.engine import BacktestEngine  # noqa: E402
from cnstock.backtest.strategies import get_strategy, list_strategies  # noqa: E402
from cnstock.core.config import load_config  # noqa: E402
from cnstock.data.manager import DataManager  # noqa: E402


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="A股模拟交易终端 - 命令行回测")
    parser.add_argument("symbol", help="股票代码，如 600519")
    parser.add_argument("--strategy", default="双均线交叉", choices=list_strategies())
    parser.add_argument("--years", type=int, default=3, help="回测年限")
    parser.add_argument("--cash", type=float, default=1_000_000, help="初始资金")
    args = parser.parse_args()

    cfg = load_config()
    dm = DataManager(cfg)
    start = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y%m%d")

    try:
        df = dm.daily(args.symbol, start=start, adjust="qfq", period="daily")
    except Exception as exc:  # noqa: BLE001
        print(f"获取行情失败：{exc}")
        return 1

    engine = BacktestEngine(cfg)
    result = engine.run(
        get_strategy(args.strategy), df, symbol=args.symbol, initial_cash=args.cash
    )

    m = result.metrics
    print("=" * 50)
    print(f"标的 {args.symbol}  |  策略 {args.strategy}  |  区间 {start} ~ 今")
    print("=" * 50)
    for label, val in m.to_rows():
        print(f"{label:<12}: {val}")
    print("=" * 50)
    print(f"交易次数 {m.trade_count}，共成交 {len(result.trades)} 笔")
    print(f"资金曲线采样点 {len(result.equity_curve)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
