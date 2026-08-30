# -*- coding: utf-8 -*-
"""
回测引擎。

执行流程（逐 bar）：

    for i in range(len(data)):
        1. 解锁 T+1（当日买入的股份从本日起可卖）
        2. 用**当日开盘价**执行上一根 bar 产生的信号（含滑点）
        3. 用**当日收盘价**更新持仓市值，记录资金曲线
        4. 调用 strategy.on_bar(ctx)，收集新的买卖信号

收盘决策、次日开盘成交，杜绝未来函数。
"""
from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

from ..core.config import AppConfig, load_config
from ..core.constants import (
    LOT_SIZE,
    OrderSide,
    buy_fee,
    round_price,
    sell_fee,
)
from ..core.models import BacktestResult, Metrics, Trade
from .metrics import compute_metrics
from .strategy import BacktestContext, Signal, Strategy

logger = logging.getLogger(__name__)


class BacktestError(Exception):
    """回测执行失败。"""


class BacktestEngine:
    """单标的回测引擎。"""

    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()

    # ============================================================
    # 主入口
    # ============================================================

    def run(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        symbol: str = "",
        name: str = "",
        initial_cash: float | None = None,
    ) -> BacktestResult:
        """
        执行回测。

        :param strategy: 策略实例
        :param data: 统一 schema 的日线数据（``date`` 升序）
        :param symbol: 标的代码
        :param name: 标的名称
        :param initial_cash: 期初资金，None 则取配置值
        :raise BacktestError: 数据不足或执行失败
        """
        if data is None or data.empty:
            raise BacktestError("回测数据为空")

        df = data.reset_index(drop=True).copy()
        if "close" not in df.columns or "date" not in df.columns:
            raise BacktestError("回测数据缺少 date / close 列")

        cfg = self.cfg
        cash = float(
            initial_cash if initial_cash is not None else cfg.account.initial_cash
        )
        initial = cash

        # ---------- 预计算指标 ----------
        try:
            df = strategy.prepare(df)
        except Exception as exc:
            raise BacktestError(f"策略 {strategy.name} 指标预计算失败：{exc}") from exc

        warmup = max(int(strategy.warmup()), 0)
        if len(df) <= warmup + 1:
            raise BacktestError(
                f"数据量不足：共 {len(df)} 根 K 线，策略 {strategy.name} 至少需要 {warmup + 2} 根"
            )

        # ---------- 状态 ----------
        qty = 0
        locked_qty = 0
        avg_cost = 0.0
        pending: list[Signal] = []
        trades: list[Trade] = []
        equity: list[tuple[str, float]] = []
        logs: list[str] = []

        fee_cfg = cfg.fee
        enforce_t1 = cfg.account.enforce_t1

        # ---------- 逐 bar 循环 ----------
        for i in range(len(df)):
            bar = df.iloc[i]
            date = str(bar["date"])
            open_price = float(bar.get("open", bar["close"]) or bar["close"])
            close_price = float(bar["close"])

            # 1) T+1 解锁
            locked_qty = 0

            # 2) 执行上一根 bar 的信号（开盘价成交）
            if pending:
                for sig in pending:
                    if sig.side == "buy":
                        filled = self._exec_buy(
                            sig, date, open_price, cash,
                            fee_cfg.commission_rate, fee_cfg.commission_min,
                            fee_cfg.transfer_fee_rate, fee_cfg.slippage,
                        )
                        if filled is None:
                            continue
                        px, fqty, fee = filled
                        cash -= px * fqty + fee
                        avg_cost = ((avg_cost * qty) + px * fqty + fee) / (qty + fqty)
                        qty += fqty
                        if enforce_t1:
                            locked_qty += fqty
                        trades.append(self._mk_trade(
                            symbol, name or symbol, OrderSide.BUY, px, fqty, fee, date
                        ))
                        logs.append(f"{date} 买入 {fqty} 股 @{px:.2f}（{sig.reason}）")
                    else:
                        available = qty - locked_qty if enforce_t1 else qty
                        filled = self._exec_sell(
                            sig, date, open_price, available,
                            fee_cfg.commission_rate, fee_cfg.commission_min,
                            fee_cfg.stamp_duty_rate, fee_cfg.transfer_fee_rate,
                            fee_cfg.slippage,
                        )
                        if filled is None:
                            continue
                        px, fqty, fee = filled
                        cash += px * fqty - fee
                        qty -= fqty
                        if qty <= 0:
                            qty = 0
                            locked_qty = 0
                            avg_cost = 0.0
                        trades.append(self._mk_trade(
                            symbol, name or symbol, OrderSide.SELL, px, fqty, fee, date
                        ))
                        logs.append(f"{date} 卖出 {fqty} 股 @{px:.2f}（{sig.reason}）")
                pending = []

            # 3) 记录资金曲线（收盘市值）
            equity.append((date, cash + close_price * qty))

            # 4) 调用策略产生信号
            if i >= warmup:
                ctx = BacktestContext(
                    data=df,
                    i=i,
                    cash=cash,
                    qty=qty,
                    avg_cost=avg_cost,
                    last_price=close_price,
                )
                try:
                    strategy.on_bar(ctx)
                except Exception as exc:
                    raise BacktestError(
                        f"策略 {strategy.name} 在 {date} 执行出错：{exc}"
                    ) from exc
                pending = ctx.signals
                logs.extend(ctx.logs)

        # ---------- 基准：买入持有 ----------
        try:
            first_close = float(df.iloc[warmup]["close"])
            last_close = float(df.iloc[-1]["close"])
            benchmark = last_close / first_close - 1.0 if first_close > 0 else 0.0
        except Exception:
            benchmark = 0.0

        metrics = compute_metrics(
            equity_curve=equity,
            trades=trades,
            initial_cash=initial,
            benchmark_return=benchmark,
        )

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            metrics=metrics,
            equity_curve=equity,
            trades=trades,
            logs=logs,
        )

    # ============================================================
    # 撮合：买入 / 卖出
    # ============================================================

    @staticmethod
    def _exec_buy(
        sig: Signal,
        date: str,
        open_price: float,
        cash: float,
        commission_rate: float,
        commission_min: float,
        transfer_fee_rate: float,
        slippage: float,
    ) -> tuple[float, int, float] | None:
        """按开盘价（含滑点）买入。返回 ``(成交价, 数量, 费用)``，无法成交返回 None。"""
        price = round_price(open_price * (1.0 + slippage))
        if price <= 0:
            return None

        if sig.quantity > 0:
            qty = (int(sig.quantity) // LOT_SIZE) * LOT_SIZE
        elif sig.percent > 0:
            # 预估每股成本后向下取整到整手
            per_share = price * (1.0 + commission_rate + transfer_fee_rate)
            if per_share <= 0:
                return None
            qty = int((cash * min(sig.percent, 1.0)) / per_share)
            qty = (qty // LOT_SIZE) * LOT_SIZE
        else:
            return None

        if qty <= 0:
            return None

        # 资金不足则逐步减手
        amount = price * qty
        fee = buy_fee(amount, commission_rate, commission_min, transfer_fee_rate)["total"]
        while qty > 0 and (amount + fee) > cash:
            qty -= LOT_SIZE
            amount = price * qty
            fee = buy_fee(amount, commission_rate, commission_min, transfer_fee_rate)["total"]

        if qty <= 0:
            return None
        return price, qty, fee

    @staticmethod
    def _exec_sell(
        sig: Signal,
        date: str,
        open_price: float,
        available: int,
        commission_rate: float,
        commission_min: float,
        stamp_duty_rate: float,
        transfer_fee_rate: float,
        slippage: float,
    ) -> tuple[float, int, float] | None:
        """按开盘价（含滑点）卖出。返回 ``(成交价, 数量, 费用)``。"""
        if available <= 0:
            return None

        price = round_price(open_price * (1.0 - slippage))
        if price <= 0:
            return None

        if sig.quantity > 0:
            qty = min(int(sig.quantity), available)
            if qty < available:
                qty = (qty // LOT_SIZE) * LOT_SIZE
        elif sig.percent > 0:
            if sig.percent >= 0.999:               # 清仓，允许卖出零股
                qty = available
            else:
                qty = int(available * min(sig.percent, 1.0))
                qty = (qty // LOT_SIZE) * LOT_SIZE
        else:
            return None

        qty = min(max(qty, 0), available)
        if qty <= 0:
            return None

        amount = price * qty
        fee = sell_fee(
            amount, commission_rate, commission_min,
            stamp_duty_rate, transfer_fee_rate,
        )["total"]
        return price, qty, fee

    # ============================================================
    # 工具
    # ============================================================

    @staticmethod
    def _mk_trade(
        symbol: str, name: str, side: OrderSide,
        price: float, qty: int, fee: float, date: str,
    ) -> Trade:
        return Trade(
            symbol=symbol,
            name=name,
            side=side,
            price=price,
            quantity=qty,
            amount=price * qty,
            fee=fee,
            traded_at=f"{date} 09:35:00",
        )

    def run_many(
        self,
        strategies: Iterable[Strategy],
        data: pd.DataFrame,
        symbol: str = "",
        name: str = "",
        initial_cash: float | None = None,
    ) -> list[BacktestResult]:
        """批量回测多个策略，单个失败不影响其他。"""
        results: list[BacktestResult] = []
        for st in strategies:
            try:
                results.append(self.run(st, data, symbol, name, initial_cash))
            except BacktestError as exc:
                logger.warning("策略 %s 回测失败：%s", st.name, exc)
        return results
