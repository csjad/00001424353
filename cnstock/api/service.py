# -*- coding: utf-8 -*-
"""交易服务：把纯逻辑层（SimBroker + SqliteStorage + BacktestEngine + 指标）封装成
有状态单例，供 FastAPI 路由调用。

本模块是「无头内核」的消费者之一（另一个是桌面 UI）。它只 import：
- core / engine / backtest / storage / strategies  —— 全部零 Qt
- fastapi 只在 app 层引入，本模块也不需要
因此后端边界守卫（_verify.py [8]/[9]）能在阻断 PyQt 后正常 import 本包。

线程安全：SimBroker 内部持有 RLock，所有订单/结算操作都在锁内；本服务的薄封装
直接调用 broker 方法即可。FastAPI 同步路由跑在线程池里，多请求并发安全。
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ..backtest.engine import BacktestEngine
from ..backtest.metrics import compute_metrics
from ..backtest.strategies import STRATEGY_REGISTRY, get_strategy, list_strategies
from ..core.config import load_config
from ..core.constants import OrderSide, OrderType, price_limit
from ..core.models import Trade
from ..engine.broker import SimBroker
from ..storage.db import SqliteStorage


# ============================================================
# 输入归一化
# ============================================================

_SIDE_ALIASES = {
    "买入": "买入", "卖出": "卖出",
    "buy": "买入", "b": "买入", "buy_in": "买入",
    "sell": "卖出", "s": "卖出", "sell_out": "卖出",
}
_TYPE_ALIASES = {
    "限价": "限价", "市价": "市价",
    "limit": "限价", "l": "限价",
    "market": "市价", "m": "市价",
}


def parse_side(raw: str) -> OrderSide:
    """接受中文值或英文别名，归一化为 OrderSide。"""
    if isinstance(raw, OrderSide):
        return raw
    s = str(raw).strip()
    if s in ("买入", "卖出"):
        return OrderSide(s)
    v = _SIDE_ALIASES.get(s.lower())
    if v is None:
        raise ValueError(f"未知买卖方向：{raw!r}（应为 买入/卖出 或 buy/sell）")
    return OrderSide(v)


def parse_order_type(raw: str) -> OrderType:
    """接受中文值或英文别名，归一化为 OrderType。"""
    if isinstance(raw, OrderType):
        return raw
    s = str(raw).strip()
    if s in ("限价", "市价"):
        return OrderType(s)
    v = _TYPE_ALIASES.get(s.lower())
    if v is None:
        raise ValueError(f"未知订单类型：{raw!r}（应为 限价/市价 或 limit/market）")
    return OrderType(v)


# ============================================================
# 序列化
# ============================================================


def _metrics_to_dict(m) -> dict[str, Any]:
    """Metrics dataclass -> JSON 安全的 dict（inf/nan -> None）。"""
    d = asdict(m)
    for k, v in d.items():
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            d[k] = None
    return d


def _bars_to_frame(bars: list[Any]) -> pd.DataFrame:
    rows = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars]
    return pd.DataFrame(rows)


# ============================================================
# 交易服务单例
# ============================================================


class TradingService:
    """有状态交易会话：撮合 + 持久化 + 回测 + 指标。"""

    def __init__(self, db_file: Optional[str | Path] = None) -> None:
        self.cfg = load_config()
        self.storage = SqliteStorage(db_file)
        account = self.storage.load_account(self.cfg.account.initial_cash)
        self.broker = SimBroker(self.cfg, persister=self.storage, account=account)

        # 恢复持久化层里的订单 / 成交 / 持仓
        for o in self.storage.load_orders():
            self.broker._orders[o.order_id] = o
        for t in self.storage.load_trades():
            self.broker._trades.append(t)
        for p in self.storage.load_positions():
            self.broker.account.positions[p.symbol] = p

    # ---------- 账户 / 持仓 ----------

    def account_dict(self) -> dict[str, Any]:
        acc = self.broker.account
        return {
            "initial_cash": acc.initial_cash,
            "cash": acc.cash,
            "market_value": acc.market_value,
            "total_value": acc.total_value,
            "total_pnl": acc.total_pnl,
            "total_pnl_ratio": acc.total_pnl_ratio,
            "position_ratio": acc.position_ratio,
        }

    def positions_dict(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.broker.positions]

    # ---------- 下单 ----------

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float = 0.0,
        order_type: OrderType = OrderType.LIMIT,
        quote: Optional[dict] = None,
    ) -> dict[str, Any]:
        o = self.broker.submit_order(
            symbol, side, quantity, price, order_type, quote=quote
        )
        return o.to_dict()

    def cancel(self, order_id: str) -> bool:
        return self.broker.cancel_order(order_id)

    def settlement(self, force: bool = False) -> bool:
        return self.broker.daily_settlement(force=force)

    def reset(self, initial_cash: Optional[float] = None) -> dict[str, Any]:
        self.broker.reset(initial_cash)
        return self.account_dict()

    def list_orders(self) -> list[dict[str, Any]]:
        return [o.to_dict() for o in self.broker.all_orders()]

    def list_trades(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.broker.trades()]

    # ---------- 回测 ----------

    def _run_one(self, strategy_name: str, params, symbol: str, name: str,
                 initial_cash, bars) -> dict[str, Any]:
        strat = get_strategy(strategy_name, params)
        df = _bars_to_frame(bars)
        res = BacktestEngine(self.cfg).run(
            strat, df, symbol=symbol, name=name, initial_cash=initial_cash
        )
        return {
            "strategy_name": res.strategy_name,
            "symbol": res.symbol,
            "metrics": _metrics_to_dict(res.metrics),
            "equity_curve": res.equity_curve,
            "trades": [t.to_dict() for t in res.trades],
            "logs": res.logs[-50:],
        }

    def run_backtest(self, req: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run_one(
                req["strategy"], req.get("params"), req.get("symbol", ""),
                req.get("name", ""), req.get("initial_cash"), req["bars"],
            )
        except KeyError as exc:
            raise ValueError(f"未知策略：{req.get('strategy')}") from exc

    def run_backtest_all(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        df = _bars_to_frame(req["bars"])
        engine = BacktestEngine(self.cfg)
        out: list[dict[str, Any]] = []
        for name in list_strategies():
            try:
                strat = get_strategy(name, req.get("params"))
                res = engine.run(
                    strat, df, symbol=req.get("symbol", ""),
                    name=req.get("name", ""), initial_cash=req.get("initial_cash"),
                )
                out.append({
                    "strategy_name": res.strategy_name,
                    "symbol": res.symbol,
                    "metrics": _metrics_to_dict(res.metrics),
                    "equity_curve": res.equity_curve,
                    "trades": [t.to_dict() for t in res.trades],
                    "logs": res.logs[-50:],
                })
            except Exception as exc:  # 单策略失败不影响其他
                out.append({"strategy_name": name, "error": str(exc)})
        return out

    # ---------- 策略目录 / 工具 ----------

    def strategies(self) -> list[dict[str, Any]]:
        return [
            {"name": n, "params": cls.params, "param_spec": cls.param_spec}
            for n, cls in STRATEGY_REGISTRY.items()
        ]

    def price_limit(self, symbol: str, name: str, prev_close: float) -> dict[str, float]:
        up, down = price_limit(symbol, name, prev_close)
        return {"up": up, "down": down}

    def compute_metrics(self, req: dict[str, Any]) -> dict[str, Any]:
        trades = [Trade.from_dict(t) for t in req.get("trades", [])]
        return _metrics_to_dict(
            compute_metrics(
                req["equity_curve"], trades, req["initial_cash"],
                benchmark_return=req.get("benchmark_return", 0.0),
            )
        )


# ============================================================
# 进程级单例
# ============================================================

_service: Optional[TradingService] = None
_service_lock = threading.Lock()


def get_service() -> TradingService:
    """懒加载单例；状态库路径由 ``CNSTOCK_API_DB`` 控制，默认当前目录 server_state.db。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                db = os.environ.get("CNSTOCK_API_DB")
                _service = TradingService(db)
    return _service
