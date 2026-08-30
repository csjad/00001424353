# -*- coding: utf-8 -*-
"""
回测策略基类与上下文。

**防未来函数约定（重要）**：

策略在 ``on_bar`` 中只能使用 ``ctx.data.iloc[: ctx.i + 1]``（含当前 bar）的数据。
产生的买卖信号不会立即成交，而是由引擎在**下一根 bar 的开盘价**执行，
这与实盘「收盘后决策、次日开盘下单」一致。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Signal:
    """一条待执行信号。"""

    side: str                 # "buy" | "sell"
    quantity: int = 0         # 指定股数（0 表示按 percent 计算）
    percent: float = 0.0      # 目标资金/持仓比例（0~1）
    reason: str = ""


@dataclass
class BacktestContext:
    """
    策略上下文。

    策略通过它读取行情、查询账户、发出买卖信号。
    """

    data: pd.DataFrame                    # 完整数据（含预计算指标列）
    i: int = 0                            # 当前 bar 索引
    cash: float = 0.0                     # 可用资金（上一日终值）
    qty: int = 0                          # 当前持仓股数
    avg_cost: float = 0.0
    last_price: float = 0.0
    signals: list[Signal] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    # ---------------- 行情访问 ----------------

    @property
    def row(self) -> pd.Series:
        """当前 bar（含指标列）。"""
        return self.data.iloc[self.i]

    @property
    def history(self) -> pd.DataFrame:
        """截至当前 bar 的历史数据（含当前）。策略只应使用它。"""
        return self.data.iloc[: self.i + 1]

    @property
    def date(self) -> str:
        return str(self.data.iloc[self.i]["date"])

    @property
    def close(self) -> float:
        return float(self.data.iloc[self.i]["close"])

    def indicator(self, name: str, offset: int = 0) -> float:
        """
        读取当前 bar 的指标值。

        :param offset: ``-1`` 表示上一根 bar
        """
        idx = self.i + offset
        if idx < 0 or idx >= len(self.data):
            return float("nan")
        try:
            return float(self.data.iloc[idx][name])
        except (KeyError, TypeError, ValueError):
            return float("nan")

    # ---------------- 账户 ----------------

    @property
    def market_value(self) -> float:
        return self.last_price * self.qty

    @property
    def total_value(self) -> float:
        return self.cash + self.market_value

    @property
    def position_ratio(self) -> float:
        tv = self.total_value
        return self.market_value / tv if tv > 0 else 0.0

    # ---------------- 下单（产生信号，次日开盘执行） ----------------

    def buy(self, quantity: int = 0, percent: float = 0.0, reason: str = "") -> None:
        """
        买入信号。

        :param quantity: 指定股数（优先）
        :param percent: 或按可用资金比例买入（0~1），引擎会向下取整到 100 股
        """
        self.signals.append(Signal("buy", int(quantity), float(percent), reason))

    def sell(self, quantity: int = 0, percent: float = 0.0, reason: str = "") -> None:
        """
        卖出信号。

        :param percent: 1.0 表示清仓
        """
        self.signals.append(Signal("sell", int(quantity), float(percent), reason))

    def log(self, message: str) -> None:
        self.logs.append(f"{self.date}  {message}")


class Strategy(ABC):
    """策略基类。"""

    #: 策略显示名
    name: str = "未命名策略"

    #: 策略参数（可在 UI 中调整）
    params: dict[str, Any] = {}

    #: 参数说明，用于 UI 自动生成表单：{参数名: (默认值, 最小值, 最大值, 说明)}
    param_spec: dict[str, tuple] = {}

    def __init__(self, **kwargs: Any) -> None:
        merged = dict(self.params)
        merged.update(kwargs)
        self.params = merged

    # ---------------- 可选重写 ----------------

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        预计算指标（向量化，在回测循环开始前调用一次）。

        重写此方法并在 df 上追加指标列，然后在 ``on_bar`` 中用
        ``ctx.indicator("ma5")`` 读取，性能远好于逐 bar 计算。
        """
        return df

    def warmup(self) -> int:
        """
        需要的预热 bar 数（如 60 日均线需要至少 60 根 bar）。

        引擎会跳过前 ``warmup`` 根 bar 不产生交易。
        """
        return 0

    # ---------------- 必须实现 ----------------

    @abstractmethod
    def on_bar(self, ctx: BacktestContext) -> None:
        """每根 bar 调用一次，策略在此发出买卖信号。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.name} {self.params}>"
