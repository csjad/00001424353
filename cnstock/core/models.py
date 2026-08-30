# -*- coding: utf-8 -*-
"""
领域模型：订单、成交、持仓、账户快照、回测结果。

全部使用 ``dataclass`` 定义，并提供 ``to_dict`` / ``from_dict`` 以便 SQLite 持久化。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from .constants import OrderSide, OrderStatus, OrderType


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6]}"


# ============================================================
# 订单
# ============================================================


@dataclass
class Order:
    """一笔委托。"""

    symbol: str
    name: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    price: float = 0.0
    order_id: str = field(default_factory=lambda: _new_id("O"))
    created_at: str = field(default_factory=_now_str)
    filled_qty: int = 0
    filled_amount: float = 0.0        # 成交金额（不含费）
    fee: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    message: str = ""                 # 拒绝原因或备注

    @property
    def is_done(self) -> bool:
        """订单是否已终结（不会再产生成交）。"""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )

    @property
    def remaining_qty(self) -> int:
        return max(self.quantity - self.filled_qty, 0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["order_type"] = self.order_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Order":
        return cls(
            symbol=d["symbol"],
            name=d.get("name", ""),
            side=OrderSide(d["side"]),
            quantity=int(d["quantity"]),
            order_type=OrderType(d.get("order_type", OrderType.LIMIT.value)),
            price=float(d.get("price", 0.0)),
            order_id=d.get("order_id", _new_id("O")),
            created_at=d.get("created_at", _now_str()),
            filled_qty=int(d.get("filled_qty", 0)),
            filled_amount=float(d.get("filled_amount", 0.0)),
            fee=float(d.get("fee", 0.0)),
            status=OrderStatus(d.get("status", OrderStatus.PENDING.value)),
            message=d.get("message", ""),
        )


# ============================================================
# 成交
# ============================================================


@dataclass
class Trade:
    """一笔成交。"""

    symbol: str
    name: str
    side: OrderSide
    price: float
    quantity: int
    amount: float
    fee: float
    trade_id: str = field(default_factory=lambda: _new_id("T"))
    order_id: str = ""
    traded_at: str = field(default_factory=_now_str)

    @property
    def cash_delta(self) -> float:
        """对现金的影响：买入为负（金额+费），卖出为正（金额-费）。"""
        if self.side == OrderSide.BUY:
            return -(self.amount + self.fee)
        return self.amount - self.fee

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trade":
        return cls(
            symbol=d["symbol"],
            name=d.get("name", ""),
            side=OrderSide(d["side"]),
            price=float(d["price"]),
            quantity=int(d["quantity"]),
            amount=float(d["amount"]),
            fee=float(d.get("fee", 0.0)),
            trade_id=d.get("trade_id", _new_id("T")),
            order_id=d.get("order_id", ""),
            traded_at=d.get("traded_at", _now_str()),
        )


# ============================================================
# 持仓
# ============================================================


@dataclass
class Position:
    """
    单只股票持仓。

    ``locked_qty`` 表示 T+1 制度下当日买入被锁定的股份，不可卖出。
    """

    symbol: str
    name: str = ""
    total_qty: int = 0
    locked_qty: int = 0               # 当日买入、T+1 锁定
    avg_cost: float = 0.0             # 摊薄成本价
    last_price: float = 0.0
    realized_pnl: float = 0.0         # 已实现盈亏（累计，含费）
    updated_at: str = field(default_factory=_now_str)

    @property
    def available_qty(self) -> int:
        """可卖数量 = 总持仓 - T+1 锁定。"""
        return max(self.total_qty - self.locked_qty, 0)

    @property
    def market_value(self) -> float:
        return self.last_price * self.total_qty

    @property
    def cost_value(self) -> float:
        return self.avg_cost * self.total_qty

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.avg_cost) * self.total_qty

    @property
    def pnl_ratio(self) -> float:
        """浮动盈亏比例（小数形式，0.1 = +10%）。"""
        if self.avg_cost <= 0 or self.total_qty == 0:
            return 0.0
        return self.last_price / self.avg_cost - 1.0

    def reset_daily_lock(self) -> None:
        """每日开盘前调用：解除 T+1 锁定。"""
        self.locked_qty = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(
            symbol=d["symbol"],
            name=d.get("name", ""),
            total_qty=int(d.get("total_qty", 0)),
            locked_qty=int(d.get("locked_qty", 0)),
            avg_cost=float(d.get("avg_cost", 0.0)),
            last_price=float(d.get("last_price", 0.0)),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            updated_at=d.get("updated_at", _now_str()),
        )


# ============================================================
# 账户
# ============================================================


@dataclass
class Account:
    """模拟账户。"""

    initial_cash: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def total_value(self) -> float:
        return self.cash + self.market_value

    @property
    def total_pnl(self) -> float:
        return self.total_value - self.initial_cash

    @property
    def total_pnl_ratio(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return self.total_value / self.initial_cash - 1.0

    @property
    def position_ratio(self) -> float:
        """仓位比例（市值 / 总资产）。"""
        tv = self.total_value
        return self.market_value / tv if tv > 0 else 0.0

    def get_position(self, symbol: str) -> Position | None:
        p = self.positions.get(symbol)
        return p if p and p.total_qty > 0 else None


# ============================================================
# 回测结果
# ============================================================


@dataclass
class Metrics:
    """回测绩效指标。"""

    initial_cash: float = 0.0
    final_value: float = 0.0
    total_return: float = 0.0        # 总收益率
    annual_return: float = 0.0       # 年化收益率
    max_drawdown: float = 0.0        # 最大回撤（正数，0.2 = -20%）
    sharpe: float = 0.0              # 夏普比率
    sortino: float = 0.0
    win_rate: float = 0.0            # 胜率
    profit_factor: float = 0.0       # 盈亏比
    trade_count: int = 0
    avg_hold_days: float = 0.0
    benchmark_return: float = 0.0    # 基准（买入持有）收益
    alpha: float = 0.0

    def to_rows(self) -> list[tuple[str, str]]:
        """转成 ``(指标名, 显示值)`` 列表，供 UI 表格直接渲染。"""
        return [
            ("期初资金", f"{self.initial_cash:,.2f}"),
            ("期末资金", f"{self.final_value:,.2f}"),
            ("总收益率", f"{self.total_return * 100:+.2f}%"),
            ("年化收益率", f"{self.annual_return * 100:+.2f}%"),
            ("最大回撤", f"{self.max_drawdown * 100:.2f}%"),
            ("夏普比率", f"{self.sharpe:.3f}"),
            ("索提诺比率", f"{self.sortino:.3f}"),
            ("基准收益", f"{self.benchmark_return * 100:+.2f}%"),
            ("超额收益 α", f"{self.alpha * 100:+.2f}%"),
            ("交易次数", f"{self.trade_count}"),
            ("胜率", f"{self.win_rate * 100:.2f}%"),
            ("盈亏比", f"{self.profit_factor:.3f}"),
            ("平均持股天数", f"{self.avg_hold_days:.1f}"),
        ]


@dataclass
class BacktestResult:
    """一次完整回测的产物。"""

    strategy_name: str = ""
    symbol: str = ""
    metrics: Metrics = field(default_factory=Metrics)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)  # [(日期, 总资产)]
    trades: list[Trade] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
