# -*- coding: utf-8 -*-
"""
模拟撮合引擎（Paper Broker）。

严格遵循 A 股规则：
- **T+1**：当日买入股份被锁定，次日才可卖
- **涨跌停**：涨停无法买入、跌停无法卖出（模拟真实排队失败）
- **最小单位**：买入必须为 100 股整数倍
- **费用**：佣金（含最低 5 元）+ 印花税（卖出单边）+ 过户费（双边），买入费用摊入持仓成本
- **资金校验**：买入金额 + 费用不得超过可用资金

设计上留了 ``persister`` 钩子，传入持久化对象即可落库；不传则纯内存运行。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Iterable, Protocol

from ..core.config import AppConfig, FeeConfig, load_config
from ..core.constants import (
    LOT_SIZE,
    OrderSide,
    OrderStatus,
    OrderType,
    buy_fee,
    price_limit,
    round_price,
    sell_fee,
)
from ..core.models import Account, Order, Position, Trade

logger = logging.getLogger(__name__)


class Persister(Protocol):
    """持久化钩子协议（duck typing，可不实现）。"""

    def save_order(self, order: Order) -> None: ...
    def save_trade(self, trade: Trade) -> None:
        ...
    def save_position(self, position: Position) -> None:
        ...
    def save_account(self, account: Account) -> None:
        ...
    def delete_position(self, symbol: str) -> None: ...


class RejectReason:
    """常见拒单原因（集中定义，便于 UI 统一提示）。"""

    BAD_QUANTITY = "委托数量必须大于 0"
    LOT_SIZE = f"买入数量必须为 {LOT_SIZE} 股的整数倍"
    NO_QUOTE = "未获取到行情，无法撮合"
    SUSPENDED = "该股票当前停牌或无成交"
    LIMIT_UP = "涨停无法买入"
    LIMIT_DOWN = "跌停无法卖出"
    T1_LOCKED = "可卖数量不足（T+1 制度，当日买入不可卖出）"
    INSUFFICIENT_CASH = "可用资金不足"
    NO_POSITION = "无持仓可卖"
    PRICE_INVALID = "委托价格无效"


class SimBroker:
    """模拟撮合 + 账户管理。"""

    def __init__(
        self,
        data_mgr: Any = None,
        cfg: AppConfig | None = None,
        persister: Persister | None = None,
        account: Account | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.data_mgr = data_mgr
        self.persister = persister
        self.account = account or Account(
            initial_cash=self.cfg.account.initial_cash,
            cash=self.cfg.account.initial_cash,
        )
        self._lock = threading.RLock()
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._last_settle_date: str = datetime.now().strftime("%Y-%m-%d")

    # ============================================================
    # 行情刷新
    # ============================================================

    def refresh_prices(self, quotes: dict[str, dict] | Iterable[dict] | Any) -> None:
        """
        用最新行情更新持仓市值，并尝试撮合挂单。

        :param quotes: 支持三种形式
            1. ``{symbol: {price:..., open:..., high:..., low:..., prev_close:..., name:...}}``
            2. 实时快照 DataFrame（含 symbol/price/open/high/low/prev_close/name 列）
            3. dict 列表
        """
        normalized = self._normalize_quotes(quotes)
        if not normalized:
            return

        with self._lock:
            for symbol, q in normalized.items():
                pos = self.account.positions.get(symbol)
                if pos and pos.total_qty > 0:
                    pos.last_price = round_price(q.get("price", 0.0) or pos.last_price)
                    pos.name = q.get("name") or pos.name
                    pos.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 撮合挂单
            for order in list(self._orders.values()):
                if order.status in (OrderStatus.PENDING, OrderStatus.PARTIAL):
                    q = normalized.get(order.symbol)
                    if q:
                        self._try_match(order, q)

            self._persist_positions()

    def sync_market(self, symbols: Iterable[str] | None = None) -> None:
        """通过 DataManager 拉取实时行情并刷新（需要构造时传入 data_mgr）。"""
        if self.data_mgr is None:
            return
        syms = list(symbols) if symbols else [
            s for s, p in self.account.positions.items() if p.total_qty > 0
        ]
        if not syms:
            return
        try:
            df = self.data_mgr.realtime(syms)
        except Exception as exc:
            logger.warning("刷新行情失败：%s", exc)
            return
        if df is not None and not df.empty:
            self.refresh_prices(df)

    # ============================================================
    # 下单
    # ============================================================

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float = 0.0,
        order_type: OrderType = OrderType.LIMIT,
        quote: dict | None = None,
    ) -> Order:
        """
        提交委托并立即尝试撮合。

        :param symbol: 6 位代码
        :param side: 买/卖
        :param quantity: 委托数量（股）
        :param price: 委托价格；市价单可传 0
        :param order_type: 限价 / 市价
        :param quote: 可选，直接给定行情以跳过网络请求
        :return: 处理后的 ``Order``（含最终状态与拒单原因）
        """
        symbol = str(symbol).strip()[-6:]
        side = OrderSide(side)
        order_type = OrderType(order_type)

        order = Order(
            symbol=symbol,
            name="",
            side=side,
            quantity=int(quantity),
            order_type=order_type,
            price=round_price(price) if price else 0.0,
        )

        with self._lock:
            self._orders[order.order_id] = order

            # ---- 1. 数量校验 ----
            if order.quantity <= 0:
                return self._reject(order, RejectReason.BAD_QUANTITY)

            if side == OrderSide.BUY and order.quantity % LOT_SIZE != 0:
                return self._reject(order, RejectReason.LOT_SIZE)

            # ---- 2. 获取行情 ----
            q = quote
            if q is None:
                q = self._fetch_quote(symbol)
            if not q:
                return self._reject(order, RejectReason.NO_QUOTE)

            order.name = q.get("name", "") or symbol

            cur = float(q.get("price", 0.0) or 0.0)
            prev_close = float(q.get("prev_close", 0.0) or 0.0)
            if cur <= 0:
                return self._reject(order, RejectReason.SUSPENDED)

            # ---- 3. 涨跌停校验 ----
            up_limit, down_limit = price_limit(symbol, order.name, prev_close)
            if up_limit > 0 and cur >= up_limit and side == OrderSide.BUY:
                return self._reject(order, RejectReason.LIMIT_UP)
            if down_limit > 0 and cur <= down_limit and side == OrderSide.SELL:
                return self._reject(order, RejectReason.LIMIT_DOWN)

            # ---- 4. T+1 / 持仓校验 ----
            if side == OrderSide.SELL:
                pos = self.account.get_position(symbol)
                if pos is None:
                    return self._reject(order, RejectReason.NO_POSITION)
                if self.cfg.account.enforce_t1 and order.quantity > pos.available_qty:
                    return self._reject(
                        order, f"{RejectReason.T1_LOCKED}（可卖 {pos.available_qty} 股）"
                    )

            # ---- 5. 价格判定 ----
            if order_type == OrderType.LIMIT:
                if order.price <= 0:
                    return self._reject(order, RejectReason.PRICE_INVALID)
                if side == OrderSide.BUY and order.price < cur:
                    order.status = OrderStatus.PENDING
                    order.message = f"限价低于现价 {cur:.2f}，已挂单等待"
                    self._persist_order(order)
                    return order
                if side == OrderSide.SELL and order.price > cur:
                    order.status = OrderStatus.PENDING
                    order.message = f"限价高于现价 {cur:.2f}，已挂单等待"
                    self._persist_order(order)
                    return order

            # ---- 6. 撮合成交 ----
            return self._execute(order, cur, q)

    def cancel_order(self, order_id: str) -> bool:
        """撤单（仅未成交或部分成交的委托可撤）。"""
        with self._lock:
            order = self._orders.get(order_id)
            if order is None or order.is_done:
                return False
            order.status = OrderStatus.CANCELLED
            order.message = "用户撤单"
            self._persist_order(order)
            return True

    def pending_orders(self) -> list[Order]:
        return [o for o in self._orders.values()
                if o.status in (OrderStatus.PENDING, OrderStatus.PARTIAL)]

    def all_orders(self) -> list[Order]:
        return sorted(self._orders.values(), key=lambda o: o.created_at, reverse=True)

    def trades(self) -> list[Trade]:
        return sorted(self._trades, key=lambda t: t.traded_at, reverse=True)

    # ============================================================
    # 撮合核心
    # ============================================================

    def _try_match(self, order: Order, q: dict) -> None:
        """挂单在行情刷新时尝试成交。"""
        cur = float(q.get("price", 0.0) or 0.0)
        if cur <= 0:
            return

        if order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.BUY and order.price < cur:
                return
            if order.side == OrderSide.SELL and order.price > cur:
                return

        # 重新校验可卖数量与资金（T+1 解锁后可能变化）
        if order.side == OrderSide.SELL:
            pos = self.account.get_position(order.symbol)
            if pos is None or order.quantity > pos.available_qty:
                return

        self._execute(order, cur, q)

    def _execute(self, order: Order, price: float, q: dict) -> Order:
        """执行成交：扣款/入账、更新持仓、记录成交。"""
        fee_cfg: FeeConfig = self.cfg.fee
        price = round_price(price)
        amount = price * order.quantity

        if order.side == OrderSide.BUY:
            fee = buy_fee(
                amount,
                commission_rate=fee_cfg.commission_rate,
                commission_min=fee_cfg.commission_min,
                transfer_fee_rate=fee_cfg.transfer_fee_rate,
            )["total"]
            total_cost = amount + fee
            if total_cost > self.account.cash:
                return self._reject(
                    order,
                    f"{RejectReason.INSUFFICIENT_CASH}"
                    f"（需 {total_cost:,.2f}，可用 {self.account.cash:,.2f}）",
                )

            self.account.cash -= total_cost
            self._apply_buy(order, price, amount, fee)

        else:
            fee = sell_fee(
                amount,
                commission_rate=fee_cfg.commission_rate,
                commission_min=fee_cfg.commission_min,
                stamp_duty_rate=fee_cfg.stamp_duty_rate,
                transfer_fee_rate=fee_cfg.transfer_fee_rate,
            )["total"]
            self.account.cash += amount - fee
            self._apply_sell(order, price, amount, fee)

        order.filled_qty = order.quantity
        order.filled_amount = amount
        order.fee = fee
        order.status = OrderStatus.FILLED
        order.message = "已成交"

        trade = Trade(
            symbol=order.symbol,
            name=order.name or order.symbol,
            side=order.side,
            price=price,
            quantity=order.quantity,
            amount=amount,
            fee=fee,
            order_id=order.order_id,
        )
        self._trades.append(trade)

        self._persist_order(order)
        if self.persister is not None:
            try:
                self.persister.save_trade(trade)
                self.persister.save_account(self.account)
            except Exception:
                logger.exception("成交持久化失败")
        self._persist_positions()
        return order

    def _apply_buy(self, order: Order, price: float, amount: float, fee: float) -> None:
        """买入：增加持仓，费用摊入成本，新增股份 T+1 锁定。"""
        pos = self.account.positions.get(order.symbol)
        if pos is None or pos.total_qty <= 0:
            pos = Position(symbol=order.symbol, name=order.name)
            self.account.positions[order.symbol] = pos

        old_qty = pos.total_qty
        old_cost_value = pos.avg_cost * old_qty
        new_qty = old_qty + order.quantity

        pos.avg_cost = (old_cost_value + amount + fee) / new_qty if new_qty > 0 else 0.0
        pos.total_qty = new_qty
        if self.cfg.account.enforce_t1:
            pos.locked_qty += order.quantity
        pos.last_price = price
        pos.name = order.name or pos.name
        pos.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _apply_sell(self, order: Order, price: float, amount: float, fee: float) -> None:
        """卖出：减少持仓（只卖非锁定部分），结算已实现盈亏。"""
        pos = self.account.positions[order.symbol]
        realized = (price - pos.avg_cost) * order.quantity - fee

        pos.total_qty -= order.quantity
        pos.realized_pnl += realized
        pos.last_price = price
        pos.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if pos.total_qty <= 0:
            pos.total_qty = 0
            pos.locked_qty = 0
            pos.avg_cost = 0.0
            if self.persister is not None:
                try:
                    self.persister.delete_position(order.symbol)
                except Exception:
                    logger.exception("删除持仓记录失败")

    # ============================================================
    # 日终结算
    # ============================================================

    def daily_settlement(self, force: bool = False) -> bool:
        """
        日终结算：解除 T+1 锁定。

        跨交易日自动触发；``force=True`` 可强制解锁（调试用）。
        返回是否实际执行了结算。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if not force and today == self._last_settle_date:
                return False

            for pos in self.account.positions.values():
                pos.reset_daily_lock()
            self._last_settle_date = today
            self._persist_positions()
            if self.persister is not None:
                try:
                    self.persister.save_account(self.account)
                except Exception:
                    logger.exception("账户持久化失败")
            logger.info("日终结算完成，T+1 锁定已解除")
            return True

    # ============================================================
    # 账户维护
    # ============================================================

    def reset(self, initial_cash: float | None = None) -> None:
        """重置账户（清空持仓、订单、成交记录）。"""
        with self._lock:
            cash = initial_cash if initial_cash is not None else self.cfg.account.initial_cash
            self.account = Account(initial_cash=cash, cash=cash, positions={})
            self._orders.clear()
            self._trades.clear()
            if self.persister is not None:
                try:
                    self.persister.save_account(self.account)
                except Exception:
                    logger.exception("账户重置持久化失败")

    def load_account(self, account: Account) -> None:
        """从持久化层恢复账户。"""
        with self._lock:
            self.account = account

    def load_orders(self, orders: Iterable[Order]) -> None:
        for o in orders:
            self._orders[o.order_id] = o

    def load_trades(self, trades: Iterable[Trade]) -> None:
        self._trades.extend(trades)

    @property
    def positions(self) -> list[Position]:
        return [p for p in self.account.positions.values() if p.total_qty > 0]

    # ============================================================
    # 内部工具
    # ============================================================

    def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        order.message = reason
        logger.info("拒单 %s %s %s：%s", order.symbol, order.side.value, order.quantity, reason)
        self._persist_order(order)
        return order

    def _fetch_quote(self, symbol: str) -> dict | None:
        """从 DataManager 获取单只股票行情；失败返回 None。"""
        if self.data_mgr is None:
            return None
        try:
            df = self.data_mgr.realtime([symbol])
        except Exception as exc:
            logger.warning("获取 %s 行情失败：%s", symbol, exc)
            return None
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "symbol": str(row.get("symbol", symbol)),
            "name": str(row.get("name", "") or ""),
            "price": float(row.get("price", 0.0) or 0.0),
            "open": float(row.get("open", 0.0) or 0.0),
            "high": float(row.get("high", 0.0) or 0.0),
            "low": float(row.get("low", 0.0) or 0.0),
            "prev_close": float(row.get("prev_close", 0.0) or 0.0),
        }

    @staticmethod
    def _normalize_quotes(quotes: Any) -> dict[str, dict]:
        """把 DataFrame / dict / list 统一成 ``{symbol: quote_dict}``。"""
        if not quotes:
            return {}

        # 已经是 {symbol: {...}}
        if isinstance(quotes, dict):
            first = next(iter(quotes.values()), None)
            if isinstance(first, dict):
                return {str(k).strip()[-6:]: v for k, v in quotes.items()}

        # DataFrame 或 list[dict]
        rows: list[dict] = []
        if hasattr(quotes, "to_dict"):
            rows = quotes.to_dict("records")
        elif isinstance(quotes, (list, tuple)):
            rows = [r if isinstance(r, dict) else {} for r in quotes]

        out: dict[str, dict] = {}
        for r in rows:
            sym = str(r.get("symbol", "")).strip()[-6:]
            if sym:
                out[sym] = r
        return out

    def _persist_order(self, order: Order) -> None:
        if self.persister is not None:
            try:
                self.persister.save_order(order)
            except Exception:
                logger.exception("订单持久化失败")

    def _persist_positions(self) -> None:
        if self.persister is None:
            return
        for pos in self.account.positions.values():
            try:
                self.persister.save_position(pos)
            except Exception:
                logger.exception("持仓持久化失败")
