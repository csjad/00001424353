# -*- coding: utf-8 -*-
"""
模拟交易视图。

包含：账户总览、模拟下单（买/卖、限价/市价）、持仓、当日委托、成交记录、撤单、账户重置。

与行情视图联动：选中某只股票后，自动填入下单代码并拉取现价。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from ...core.config import AppConfig, load_config
from ...core.constants import OrderSide, OrderStatus, OrderType
from ...data.manager import DataManager
from ...engine.broker import SimBroker
from ..theme import DOWN, FLAT, TEXT_2, UP
from ..worker import Worker


class TradeView(QWidget):
    """模拟交易主视图。"""

    def __init__(self, broker: SimBroker, dm: DataManager, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.broker = broker
        self.dm = dm
        self.cfg = cfg or load_config()
        self._price_worker: Worker | None = None
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_market)
        if self.cfg.ui.auto_refresh:
            self.timer.start(max(self.cfg.ui.refresh_interval, 3) * 1000)

        # 首次刷新
        self.sync_market()

    # ============================================================
    # UI 构建
    # ============================================================

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- 账户总览 ----
        self.summary = self._build_summary()
        root.addWidget(self.summary)

        # ---- 主区域 ----
        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：下单
        split.addWidget(self._build_order_panel())

        # 中：持仓
        pos_widget = QGroupBox("持仓")
        pv = QVBoxLayout(pos_widget)
        self.pos_table = self._make_table(
            ["代码", "名称", "持股", "可用", "成本价", "现价", "市值", "盈亏", "盈亏%"]
        )
        pv.addWidget(self.pos_table)
        split.addWidget(pos_widget)

        # 右：委托 / 成交
        tabs = QTabWidget()
        order_widget = QWidget()
        ov = QVBoxLayout(order_widget)
        self.order_table = self._make_table(["订单号", "代码", "方向", "数量", "委托价", "状态", "原因"])
        self.cancel_btn = QPushButton("撤单（选中待成交单）")
        self.cancel_btn.clicked.connect(self._on_cancel)
        ov.addWidget(self.order_table, 1)
        ov.addWidget(self.cancel_btn)

        trade_widget = QWidget()
        tv = QVBoxLayout(trade_widget)
        self.trade_table = self._make_table(["时间", "代码", "方向", "成交价", "数量", "金额", "费用"])
        tv.addWidget(self.trade_table, 1)

        tabs.addTab(order_widget, "当日委托")
        tabs.addTab(trade_widget, "成交记录")
        tabs.setMinimumWidth(360)
        split.addWidget(tabs)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 1)
        root.addWidget(split, 1)

    def _build_summary(self) -> QGroupBox:
        group = QGroupBox("账户总览")
        h = QHBoxLayout(group)
        self.sum_labels: dict[str, QLabel] = {}
        for key in ("总资产", "可用资金", "持仓市值", "总盈亏", "持仓比例"):
            box = QVBoxLayout()
            k = QLabel(key)
            k.setStyleSheet(f"color: {TEXT_2};")
            v = QLabel("--")
            v.setStyleSheet("font-size: 11pt; font-weight: bold;")
            box.addWidget(k)
            box.addWidget(v)
            self.sum_labels[key] = v
            h.addLayout(box)
        h.addStretch(1)
        self.reset_btn = QPushButton("重置账户")
        self.reset_btn.clicked.connect(self._on_reset)
        h.addWidget(self.reset_btn)
        return group

    def _build_order_panel(self) -> QGroupBox:
        group = QGroupBox("模拟下单")
        form = QFormLayout(group)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("股票代码，如 600519")
        self.name_label = QLabel("--")

        self.price_edit = QDoubleSpinBox()
        self.price_edit.setRange(0, 9999.0)
        self.price_edit.setDecimals(2)
        self.price_edit.setSuffix(" 元")
        self.order_type_combo = QComboBox()
        self.order_type_combo.addItems([OrderType.LIMIT.value, OrderType.MARKET.value])

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(0, 10_000_000)
        self.qty_spin.setSingleStep(100)

        self.balance_label = QLabel("--")

        form.addRow("代码", self.code_edit)
        form.addRow("名称", self.name_label)
        form.addRow("委托类型", self.order_type_combo)
        form.addRow("委托价", self.price_edit)
        form.addRow("数量(股)", self.qty_spin)
        form.addRow("可用资金", self.balance_label)

        # 快捷数量
        quick = QHBoxLayout()
        for pct in (25, 50, 100):
            btn = QPushButton(f"买{pct}%")
            btn.clicked.connect(lambda _c, p=pct: self._apply_quick_buy(p))
            quick.addWidget(btn)
        self.sell_all_btn = QPushButton("全仓卖出")
        self.sell_all_btn.setObjectName("SellBtn")
        self.sell_all_btn.clicked.connect(self._apply_sell_all)
        form.addRow("数量快捷", quick)
        form.addRow(self.sell_all_btn)

        self.buy_btn = QPushButton("买入")
        self.buy_btn.setObjectName("BuyBtn")
        self.sell_btn = QPushButton("卖出")
        self.sell_btn.setObjectName("SellBtn")
        self.buy_btn.clicked.connect(lambda: self._submit(OrderSide.BUY))
        self.sell_btn.clicked.connect(lambda: self._submit(OrderSide.SELL))
        row = QHBoxLayout()
        row.addWidget(self.buy_btn)
        row.addWidget(self.sell_btn)
        form.addRow(row)

        self.tip_label = QLabel("")
        self.tip_label.setStyleSheet(f"color: {TEXT_2};")
        form.addRow(self.tip_label)

        group.setFixedWidth(260)
        return group

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        return t

    # ============================================================
    # 联动
    # ============================================================

    def set_symbol(self, symbol: str) -> None:
        """从行情视图联动：填入代码并拉取现价。"""
        if not symbol:
            return
        self.code_edit.setText(symbol)
        self._price_worker = Worker(self.dm.realtime, [symbol])
        self._price_worker.finished.connect(self._on_price_loaded)
        self._price_worker.error.connect(lambda _e: None)
        self._price_worker.start()

    def _on_price_loaded(self, df: Any) -> None:
        if df is None or df.empty:
            return
        row = df.iloc[0]
        self.name_label.setText(str(row.get("name", "") or ""))
        price = float(row.get("price", 0.0) or 0.0)
        if price > 0:
            self.price_edit.setValue(price)

    # ============================================================
    # 下单
    # ============================================================

    def _submit(self, side: OrderSide) -> None:
        symbol = self.code_edit.text().strip()
        if not symbol:
            QMessageBox.warning(self, "缺少代码", "请输入股票代码")
            return
        qty = self.qty_spin.value()
        if qty <= 0:
            QMessageBox.warning(self, "数量无效", "委托数量必须大于 0")
            return

        order_type = (
            OrderType.LIMIT
            if self.order_type_combo.currentText() == OrderType.LIMIT.value
            else OrderType.MARKET
        )
        price = self.price_edit.value() if order_type == OrderType.LIMIT else 0.0

        order = self.broker.submit_order(
            symbol, side, qty, price=price, order_type=order_type
        )

        if order.status == OrderStatus.REJECTED:
            QMessageBox.warning(self, "委托被拒", order.message)
        elif order.status == OrderStatus.FILLED:
            self.tip_label.setText(f"已成交：{side.value} {qty} 股 @ {order.filled_amount / qty:.2f}" if qty else "")
        else:
            self.tip_label.setText(order.message)

        self.refresh()

    def _apply_quick_buy(self, pct: int) -> None:
        """按可用资金比例估算买入股数（向下取整到 100 股）。"""
        price = self.price_edit.value()
        if price <= 0:
            self.tip_label.setText("请先填入有效委托价")
            return
        acct = self.broker.account
        budget = acct.cash * (pct / 100.0)
        per = price * (1 + self.cfg.fee.commission_rate + self.cfg.fee.transfer_fee_rate)
        qty = int(budget / per / 100) * 100 if per > 0 else 0
        self.qty_spin.setValue(max(qty, 0))

    def _apply_sell_all(self) -> None:
        """填充当前代码的可卖股数到数量框。"""
        symbol = self.code_edit.text().strip()
        if not symbol:
            return
        pos = self.broker.account.get_position(symbol)
        self.qty_spin.setValue(pos.available_qty if pos else 0)

    def _on_cancel(self) -> None:
        row = self.order_table.currentRow()
        if row < 0:
            return
        order_id = self.order_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not order_id:
            return
        if self.broker.cancel_order(order_id):
            self.tip_label.setText("撤单成功")
        else:
            self.tip_label.setText("撤单失败（订单已终结）")
        self.refresh()

    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self, "确认重置",
            "将清空所有持仓、订单与成交记录，且不可恢复。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.broker.reset()
        # 持久化层同步清空
        if getattr(self.broker, "persister", None) is not None:
            try:
                self.broker.persister.reset_all()
            except Exception:
                pass
        self.refresh()

    # ============================================================
    # 刷新
    # ============================================================

    def sync_market(self) -> None:
        """刷新持仓最新价并重绘。"""
        try:
            self.broker.sync_market()
        except Exception as exc:
            self.tip_label.setText(f"行情刷新失败：{exc}"[:60])
        self.refresh()

    def refresh(self) -> None:
        acct = self.broker.account
        # 总览
        self._set_summary("总资产", f"{acct.total_value:,.2f}", FLAT)
        self._set_summary("可用资金", f"{acct.cash:,.2f}", FLAT)
        self._set_summary("持仓市值", f"{acct.market_value:,.2f}", FLAT)
        pnl = acct.total_pnl
        pnl_color = UP if pnl >= 0 else DOWN
        self._set_summary(
            "总盈亏",
            f"{pnl:+,.2f} ({acct.total_pnl_ratio * 100:+.2f}%)",
            pnl_color,
        )
        self._set_summary("持仓比例", f"{acct.position_ratio * 100:.1f}%", FLAT)

        self.balance_label.setText(f"{acct.cash:,.2f} 元")

        # 持仓
        self._fill_positions()
        # 委托
        self._fill_orders()
        # 成交
        self._fill_trades()

    def _set_summary(self, key: str, text: str, color: str) -> None:
        lbl = self.sum_labels.get(key)
        if lbl:
            lbl.setText(text)
            lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {color};")

    def _fill_positions(self) -> None:
        table = self.pos_table
        table.setRowCount(0)
        for pos in self.broker.positions:
            r = table.rowCount()
            table.insertRow(r)
            pnl = pos.unrealized_pnl
            pnl_color = UP if pnl >= 0 else DOWN
            ratio = pos.pnl_ratio * 100
            self._set_cell(table, r, 0, pos.symbol)
            self._set_cell(table, r, 1, pos.name)
            self._set_cell(table, r, 2, f"{pos.total_qty:,}")
            self._set_cell(table, r, 3, f"{pos.available_qty:,}")
            self._set_cell(table, r, 4, f"{pos.avg_cost:,.2f}")
            self._set_cell(table, r, 5, f"{pos.last_price:,.2f}")
            self._set_cell(table, r, 6, f"{pos.market_value:,.2f}")
            self._set_cell(table, r, 7, f"{pnl:+,.2f}", pnl_color)
            self._set_cell(table, r, 8, f"{ratio:+.2f}%", pnl_color)

    def _fill_orders(self) -> None:
        table = self.order_table
        table.setRowCount(0)
        for o in self.broker.all_orders()[:200]:
            r = table.rowCount()
            table.insertRow(r)
            self._set_cell(table, r, 0, o.order_id, user_data=o.order_id)
            self._set_cell(table, r, 1, o.symbol)
            self._set_cell(table, r, 2, o.side.value)
            self._set_cell(table, r, 3, f"{o.quantity:,}")
            price = f"{o.price:,.2f}" if o.price > 0 else "市价"
            self._set_cell(table, r, 4, price)
            self._set_cell(table, r, 5, o.status.value)
            self._set_cell(table, r, 6, o.message)

    def _fill_trades(self) -> None:
        table = self.trade_table
        table.setRowCount(0)
        for t in self.broker.trades()[:200]:
            r = table.rowCount()
            table.insertRow(r)
            self._set_cell(table, r, 0, t.traded_at)
            self._set_cell(table, r, 1, t.symbol)
            self._set_cell(table, r, 2, t.side.value, UP if t.side == OrderSide.BUY else DOWN)
            self._set_cell(table, r, 3, f"{t.price:,.2f}")
            self._set_cell(table, r, 4, f"{t.quantity:,}")
            self._set_cell(table, r, 5, f"{t.amount:,.2f}")
            self._set_cell(table, r, 6, f"{t.fee:,.2f}")

    # ============================================================
    # 表格辅助
    # ============================================================

    def _set_cell(
        self,
        table: QTableWidget,
        row: int,
        col: int,
        text: str,
        color: str | None = None,
        user_data: Any = None,
    ) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if color:
            item.setForeground(QColor(color))
        if user_data is not None:
            item.setData(Qt.ItemDataRole.UserRole, user_data)
        table.setItem(row, col, item)

    def set_auto_refresh(self, enabled: bool, interval_sec: int) -> None:
        if enabled:
            self.timer.start(max(interval_sec, 3) * 1000)
        else:
            self.timer.stop()
