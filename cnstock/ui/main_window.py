# -*- coding: utf-8 -*-
"""
主窗口。

左侧导航（行情 / 交易 / 回测 / 设置）+ 中央堆叠视图，顶部状态栏展示账户与数据源状态。
三个业务视图通过信号联动：在行情中心选中标的，自动同步到交易与回测的下单 / 标的框。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig, load_config, save_config
from ..data.manager import DataManager
from ..engine.broker import SimBroker
from .theme import ACCENT, TEXT_2
from .widgets.backtest_view import BacktestView
from .widgets.market_view import MarketView
from .widgets.trade_view import TradeView

_NAV = ["行情中心", "模拟交易", "量化回测", "设置"]


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, dm: DataManager, broker: SimBroker, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.dm = dm
        self.broker = broker
        self.cfg = cfg or load_config()

        self.setWindowTitle("A股模拟交易终端  v0.1.0")
        self.resize(1366, 820)
        self._init_ui()

        # 视图联动：行情选中标的 -> 交易 / 回测同步
        self.market.symbol_selected.connect(self.trade.set_symbol)
        self.market.symbol_selected.connect(self.backtest.set_symbol)

        # 启动日终结算（跨交易日解锁 T+1）
        self.broker.daily_settlement()

        # 状态栏定时刷新
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(5000)
        self._update_status()

    # ============================================================
    # UI
    # ============================================================

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航
        self.nav = QListWidget()
        self.nav.addItems(_NAV)
        self.nav.setFixedWidth(120)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._switch)
        self.nav.setSpacing(2)
        root.addWidget(self.nav)

        # 中央堆叠
        self.stack = QStackedWidget()
        self.market = MarketView(self.dm, self.cfg)
        self.trade = TradeView(self.broker, self.dm, self.cfg)
        self.backtest = BacktestView(self.dm, self.cfg)
        self.settings = self._build_settings()
        self.stack.addWidget(self.market)
        self.stack.addWidget(self.trade)
        self.stack.addWidget(self.backtest)
        self.stack.addWidget(self.settings)
        root.addWidget(self.stack, 1)

    def _switch(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        if idx == 1:  # 切到交易页时刷新市值
            self.trade.sync_market()

    # ============================================================
    # 状态栏
    # ============================================================

    def _update_status(self) -> None:
        acct = self.broker.account
        token_ok = bool(self.cfg.data.tushare_token)
        providers = self.dm.provider_status()
        src = " / ".join(
            f"{n}{'✓' if ok else '✗'}" for n, ok in providers.items()
        )
        self.statusBar().showMessage(
            f"数据源 [{src}]  |  总资产 {acct.total_value:,.2f}  |  "
            f"可用 {acct.cash:,.2f}  |  持仓市值 {acct.market_value:,.2f}  |  "
            f"总盈亏 {acct.total_pnl:+,.2f}"
        )

    # ============================================================
    # 设置页
    # ============================================================

    def _build_settings(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 16, 16, 16)

        group = QGroupBox("交易设置")
        form = QFormLayout(group)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Tushare Pro token（可选，留空则仅用 akshare）")
        self.token_edit.setText(self.cfg.data.tushare_token)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Tushare Token", self.token_edit)

        self.primary_combo = QComboBox()
        self.primary_combo.addItems(["akshare", "tushare"])
        self.primary_combo.setCurrentText(self.cfg.data.primary)
        self.fallback_combo = QComboBox()
        self.fallback_combo.addItems(["tushare", "akshare", ""])
        self.fallback_combo.setCurrentText(self.cfg.data.fallback or "")
        form.addRow("主数据源", self.primary_combo)
        form.addRow("备数据源", self.fallback_combo)

        self.comm_edit = QDoubleSpinBox()
        self.comm_edit.setRange(0, 30)
        self.comm_edit.setSingleStep(0.1)
        self.comm_edit.setValue(self.cfg.fee.commission_rate * 10000)
        self.comm_edit.setSuffix(" ‱(万分之)")
        form.addRow("佣金费率", self.comm_edit)

        self.stamp_edit = QDoubleSpinBox()
        self.stamp_edit.setRange(0, 5)
        self.stamp_edit.setSingleStep(0.01)
        self.stamp_edit.setValue(self.cfg.fee.stamp_duty_rate * 1000)
        self.stamp_edit.setSuffix(" ‰(千分之)")
        form.addRow("印花税率", self.stamp_edit)

        self.transfer_edit = QDoubleSpinBox()
        self.transfer_edit.setRange(0, 5)
        self.transfer_edit.setSingleStep(0.01)
        self.transfer_edit.setValue(self.cfg.fee.transfer_fee_rate * 10000)
        self.transfer_edit.setSuffix(" ‱(万分之)")
        form.addRow("过户费率", self.transfer_edit)

        self.refresh_chk = QCheckBox("自动刷新行情")
        self.refresh_chk.setChecked(self.cfg.ui.auto_refresh)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(3, 120)
        self.refresh_spin.setValue(self.cfg.ui.refresh_interval)
        self.refresh_spin.setSuffix(" 秒")
        form.addRow(self.refresh_chk, self.refresh_spin)

        v.addWidget(group)

        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save_settings)
        save_btn.setFixedWidth(140)
        v.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        v.addStretch(1)
        return page

    def _save_settings(self) -> None:
        c = self.cfg
        c.data.tushare_token = self.token_edit.text().strip()
        c.data.primary = self.primary_combo.currentText()
        c.data.fallback = self.fallback_combo.currentText() or ""
        c.fee.commission_rate = self.comm_edit.value() / 10000.0
        c.fee.stamp_duty_rate = self.stamp_edit.value() / 1000.0
        c.fee.transfer_fee_rate = self.transfer_edit.value() / 10000.0
        c.ui.auto_refresh = self.refresh_chk.isChecked()
        c.ui.refresh_interval = self.refresh_spin.value()

        save_config(c)
        self.dm.reload_config(c)
        self.trade.set_auto_refresh(c.ui.auto_refresh, c.ui.refresh_interval)
        QMessageBox.information(self, "已保存", "设置已保存。费率与数据源即时生效。")
        self._update_status()
