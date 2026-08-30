# -*- coding: utf-8 -*-
"""
量化回测视图。

功能：选择标的、时间区间、初始资金、策略（及其可调参数），一键回测，
展示绩效指标、资金曲线与成交明细。回测在子线程执行，避免界面卡顿。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from ...core.config import AppConfig, load_config
from ...data.manager import DataManager
from ...backtest.engine import BacktestEngine
from ...backtest.strategies import STRATEGY_REGISTRY, get_strategy
from ..chart import DateAxisItem
from ..theme import BG, BORDER, DOWN, TEXT_2, UP
from ..worker import Worker


class BacktestView(QWidget):
    """量化回测主视图。"""

    def __init__(self, dm: DataManager, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.dm = dm
        self.cfg = cfg or load_config()
        self.engine = BacktestEngine(self.cfg)
        self._worker: Worker | None = None
        self._result = None
        self._param_inputs: dict[str, Any] = {}
        self._init_ui()

    # ============================================================
    # UI 构建
    # ============================================================

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- 顶部参数 ----
        top = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("标的代码，如 600519")
        self.code_edit.setFixedWidth(110)

        self.start_edit = QLabel(self._default_start())
        self.end_edit = QLabel(datetime.now().strftime("%Y%m%d"))

        self.cash_spin = QDoubleSpinBox()
        self.cash_spin.setRange(10_000, 100_000_000)
        self.cash_spin.setSingleStep(100_000)
        self.cash_spin.setValue(self.cfg.account.initial_cash)

        self.run_btn = QPushButton("运行回测")
        self.run_btn.clicked.connect(self._run)

        self.status_label = QLabel("选择标的和策略后点击「运行回测」")
        self.status_label.setStyleSheet(f"color: {TEXT_2};")

        top.addWidget(QLabel("标的"))
        top.addWidget(self.code_edit)
        top.addWidget(QLabel("起始"))
        top.addWidget(self.start_edit)
        top.addWidget(QLabel("结束"))
        top.addWidget(self.end_edit)
        top.addWidget(QLabel("初始资金"))
        top.addWidget(self.cash_spin)
        top.addWidget(self.run_btn)
        top.addStretch(1)
        top.addWidget(self.status_label)
        root.addLayout(top)

        # ---- 主区域 ----
        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：设置 + 参数
        left = QWidget()
        lv = QVBoxLayout(left)
        setting = QGroupBox("策略与参数")
        sf = QFormLayout(setting)
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(list(STRATEGY_REGISTRY.keys()))
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        sf.addRow("策略", self.strategy_combo)
        self.param_form = QFormLayout()
        self.param_box = QGroupBox("参数")
        self.param_box.setLayout(self.param_form)
        sf.addRow(self.param_box)
        lv.addWidget(setting)
        lv.addStretch(1)
        split.addWidget(left)

        # 右：结果
        right = QSplitter(Qt.Orientation.Vertical)
        result_widget = QWidget()
        rv = QVBoxLayout(result_widget)

        self.metrics_table = QTableWidget(0, 2)
        self.metrics_table.setHorizontalHeaderLabels(["指标", "数值"])
        self.metrics_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.metrics_table.setMaximumHeight(280)
        rv.addWidget(QLabel("绩效指标"))
        rv.addWidget(self.metrics_table)

        self.equity_plot = pg.PlotWidget(axisItems={"bottom": self._eq_axis()})
        self.equity_plot.setBackground(BG)
        self.equity_plot.showGrid(True, True, 0.12)
        self.equity_plot.setMinimumHeight(240)
        rv.addWidget(QLabel("资金曲线"))
        rv.addWidget(self.equity_plot, 1)
        right.addWidget(result_widget)

        self.trade_table = QTableWidget(0, 7)
        self.trade_table.setHorizontalHeaderLabels(
            ["日期", "代码", "方向", "成交价", "数量", "金额", "费用"]
        )
        self.trade_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right.addWidget(self.trade_table)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # 初始化参数表单
        self._on_strategy_changed(self.strategy_combo.currentText())

    def _eq_axis(self) -> DateAxisItem:
        self._eq = DateAxisItem(orientation="bottom")
        return self._eq

    @staticmethod
    def _default_start() -> str:
        return (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")

    # ============================================================
    # 策略参数动态表单
    # ============================================================

    def _on_strategy_changed(self, name: str) -> None:
        # 清空旧参数控件
        while self.param_form.rowCount() > 0:
            self.param_form.removeRow(0)
        self._param_inputs.clear()

        cls = STRATEGY_REGISTRY.get(name)
        if cls is None:
            return
        spec: dict = cls.param_spec
        for pname, spec_val in spec.items():
            default, mn, mx, label = spec_val
            if isinstance(default, float):
                step = (mx - mn) / 100.0 if mx > mn else 0.1
                spin = QDoubleSpinBox()
                spin.setRange(float(mn), float(mx))
                spin.setSingleStep(max(round(step, 4), 0.0001))
                spin.setDecimals(4 if step < 1 else 2)
                spin.setValue(float(default))
            else:
                spin = QSpinBox()
                spin.setRange(int(mn), int(mx))
                spin.setSingleStep(max(int((mx - mn) / 20) or 1, 1))
                spin.setValue(int(default))
            self.param_form.addRow(label, spin)
            self._param_inputs[pname] = spin

    def _collect_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for pname, spin in self._param_inputs.items():
            params[pname] = spin.value()
        # 与策略默认参数 merge，避免遗漏未暴露参数
        cls = STRATEGY_REGISTRY[self.strategy_combo.currentText()]
        return {**cls.params, **params}

    # ============================================================
    # 联动
    # ============================================================

    def set_symbol(self, symbol: str) -> None:
        if symbol:
            self.code_edit.setText(symbol)

    # ============================================================
    # 运行
    # ============================================================

    def _run(self) -> None:
        symbol = self.code_edit.text().strip()
        if not symbol:
            QMessageBox.warning(self, "缺少标的", "请输入股票代码")
            return
        strategy_name = self.strategy_combo.currentText()
        params = self._collect_params()
        start = self.start_edit.text().strip()
        end = self.end_edit.text().strip()
        cash = self.cash_spin.value()

        self.status_label.setText(f"回测中：{symbol} {strategy_name} ...")
        self.run_btn.setEnabled(False)
        self._worker = Worker(
            self._run_backtest, symbol, strategy_name, params, start, end, cash
        )
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_backtest(
        self,
        symbol: str,
        strategy_name: str,
        params: dict,
        start: str,
        end: str,
        cash: float,
    ) -> Any:
        df = self.dm.daily(
            symbol, start=start, end=end, adjust="qfq", period="daily"
        )
        name = ""
        try:
            res = self.dm.search(symbol, limit=1)
            name = res[0][1] if res else ""
        except Exception:
            pass
        strat = get_strategy(strategy_name, params)
        return self.engine.run(strat, df, symbol=symbol, name=name, initial_cash=cash)

    def _on_result(self, result: Any) -> None:
        self._result = result
        self.run_btn.setEnabled(True)
        m = result.metrics

        # 指标表
        self.metrics_table.setRowCount(0)
        for label, val in m.to_rows():
            r = self.metrics_table.rowCount()
            self.metrics_table.insertRow(r)
            self.metrics_table.setItem(r, 0, QTableWidgetItem(label))
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight)
            if "收益率" in label or "盈亏" in label or "超额" in label or "基准" in label:
                item.setForeground(QColor(UP if m.total_return >= 0 else DOWN))
            self.metrics_table.setItem(r, 1, item)

        # 资金曲线
        self._plot_equity(result)

        # 成交表
        self._fill_trades(result.trades)

        self.status_label.setText(
            f"完成：总收益 {m.total_return * 100:+.2f}% | 基准 {m.benchmark_return * 100:+.2f}% | "
            f"年化 {m.annual_return * 100:+.2f}% | 回撤 {m.max_drawdown * 100:.2f}% | "
            f"夏普 {m.sharpe:.2f}"
        )

    def _on_error(self, msg: str) -> None:
        """回测失败：界面内提示 + 状态栏，**不弹模态框**（离线时会频繁触发）。"""
        self.run_btn.setEnabled(True)
        self.status_label.setText(f"回测失败：{msg[:80]}")
        bar = getattr(self.window(), "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(f"回测失败：{msg[:120]}", 8000)
            except Exception:  # pragma: no cover
                pass

    # ============================================================
    # 渲染
    # ============================================================

    def _plot_equity(self, result: Any) -> None:
        self.equity_plot.clear()
        curve = result.equity_curve
        if not curve:
            return
        dates = [d for d, _ in curve]
        values = [v for _, v in curve]
        self._eq.set_dates(dates)
        x = np.arange(len(values))

        initial = result.metrics.initial_cash
        baseline = pg.PlotCurveItem(
            [0, len(values) - 1], [initial, initial],
            pen=pg.mkPen(BORDER, width=1, style=Qt.PenStyle.DashLine),
        )
        self.equity_plot.addItem(baseline)

        color = UP if values[-1] >= initial else DOWN
        self.equity_plot.plot(
            x, values, pen=pg.mkPen(color, width=1.6), name="策略净值"
        )
        self.equity_plot.autoRange()
        self.equity_plot.getAxis("left").setPen(pg.mkPen(BORDER))
        self.equity_plot.getAxis("bottom").setPen(pg.mkPen(BORDER))

    def _fill_trades(self, trades: list) -> None:
        table = self.trade_table
        table.setRowCount(0)
        for t in trades:
            r = table.rowCount()
            table.insertRow(r)
            for c, val in enumerate([
                t.traded_at,
                t.symbol,
                t.side.value,
                f"{t.price:,.2f}",
                f"{t.quantity:,}",
                f"{t.amount:,.2f}",
                f"{t.fee:,.2f}",
            ]):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight)
                if c == 2:
                    item.setForeground(QColor(UP if t.side.value == "买入" else DOWN))
                table.setItem(r, c, item)
