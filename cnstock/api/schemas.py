# -*- coding: utf-8 -*-
"""REST API 请求 / 响应模型（Pydantic）。

仅描述「线上契约」，不含任何 GUI 逻辑。所有 money 字段为 float（元），
side / order_type 同时接受中文值（买入/卖出、限价/市价）与英文别名
（buy/sell、limit/market），由 service 层归一化。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 回测数据
# ============================================================


class Bar(BaseModel):
    """一根日线。open/high/low 可省略（引擎用 close 兜底）。"""

    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None


class BacktestRequest(BaseModel):
    strategy: str = Field(..., description="策略名称，见 /api/strategies")
    params: dict[str, Any] | None = Field(None, description="策略参数覆盖（可选）")
    symbol: str = Field("", description="标的代码，仅用于标注")
    name: str = Field("", description="标的名称，仅用于标注")
    initial_cash: float | None = Field(None, description="期初资金（可选，默认取配置）")
    bars: list[Bar] = Field(..., description="升序日线，至少含 date + close")


class BacktestAllRequest(BaseModel):
    params: dict[str, Any] | None = None
    symbol: str = ""
    name: str = ""
    initial_cash: float | None = None
    bars: list[Bar]


# ============================================================
# 下单
# ============================================================


class OrderRequest(BaseModel):
    symbol: str
    side: str = Field(..., description="买入/卖出 或 buy/sell")
    quantity: int
    price: float = 0.0
    order_type: str = Field("LIMIT", description="限价/市价 或 limit/market")
    quote: dict[str, Any] | None = Field(
        None,
        description="撮合行情 {price, prev_close, name?}。"
        "离线环境下必须提供，否则以「未获取到行情」拒单",
    )


# ============================================================
# 绩效计算
# ============================================================


class MetricsRequest(BaseModel):
    equity_curve: list[list] = Field(..., description="[[日期, 总资产], ...] 升序")
    trades: list[dict] = Field(
        default_factory=list,
        description="成交字典列表，字段见 Trade.to_dict（symbol/side/price/quantity/amount/fee/traded_at）",
    )
    initial_cash: float
    benchmark_return: float = 0.0
