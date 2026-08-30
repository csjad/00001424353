# -*- coding: utf-8 -*-
"""
行情看盘视图。

功能：代码/名称搜索、自选股、多周期（日/周/月/分钟）、前/后复权、K 线 + 均线、
实时盘口（最新价/涨跌幅/开高低/量额/换手）。

所有网络请求走子线程（``Worker``），避免阻塞界面。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.config import AppConfig, load_config, save_config
from ...data.manager import DataManager
from ..chart import PriceChart
from ..theme import DOWN, FLAT, TEXT_2, UP
from ..worker import Worker

_PERIOD_MAP = {
    "日线": "daily",
    "5分钟": "5",
    "15分钟": "15",
    "30分钟": "30",
    "60分钟": "60",
    "周线": "weekly",
    "月线": "monthly",
}
_ADJUST_MAP = {"前复权": "qfq", "后复权": "hfq", "不复权": ""}


class MarketView(QWidget):
    """行情主视图。"""

    #: 选中某只股票时发出（供交易/回测联动）
    symbol_selected = pyqtSignal(str)

    def __init__(self, dm: DataManager, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.dm = dm
        self.cfg = cfg or load_config()
        self.current_symbol = ""
        self.current_df = None
        self._worker: Worker | None = None
        self._rt_worker: Worker | None = None
        self._init_ui()
        self._load_watchlist()

    # ============================================================
    # UI 构建
    # ============================================================

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- 顶部工具栏 ----
        top = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("输入代码或名称搜索，如 600519 / 茅台")
        self.search_box.returnPressed.connect(self._on_search)
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)

        self.period_combo = QComboBox()
        self.period_combo.addItems(list(_PERIOD_MAP.keys()))
        self.period_combo.setCurrentText("日线")
        self.period_combo.currentTextChanged.connect(self._on_period_changed)

        self.adjust_combo = QComboBox()
        self.adjust_combo.addItems(list(_ADJUST_MAP.keys()))
        self.adjust_combo.setCurrentText("前复权")
        self.adjust_combo.currentTextChanged.connect(
            lambda _: self._load(self.current_symbol) if self.current_symbol else None
        )

        self.refresh_btn = QPushButton("刷新行情")
        self.refresh_btn.clicked.connect(
            lambda: self._load(self.current_symbol) if self.current_symbol else None
        )

        self.title_label = QLabel("行情中心")
        self.title_label.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {TEXT_2};")

        top.addWidget(self.search_box, 3)
        top.addWidget(self.search_btn)
        top.addWidget(self.period_combo)
        top.addWidget(self.adjust_combo)
        top.addWidget(self.refresh_btn)
        top.addStretch(1)
        top.addWidget(self.title_label)
        root.addLayout(top)

        # ---- 主体 ----
        main = QHBoxLayout()
        main.setSpacing(8)

        # 左：自选股
        left = QVBoxLayout()
        left.addWidget(QLabel("自选股（双击查看）"))
        self.watch_list = QListWidget()
        self.watch_list.itemDoubleClicked.connect(self._on_watch_selected)
        self.add_watch_btn = QPushButton("加入自选")
        self.add_watch_btn.clicked.connect(self._add_watch)
        self.remove_watch_btn = QPushButton("移除")
        self.remove_watch_btn.clicked.connect(self._remove_watch)
        left.addWidget(self.watch_list, 1)
        left.addWidget(self.add_watch_btn)
        left.addWidget(self.remove_watch_btn)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(150)

        # 中：图表
        self.chart = PriceChart()

        # 右：盘口
        self.quote_panel = self._build_quote_panel()

        main.addWidget(left_widget)
        main.addWidget(self.chart, 1)
        main.addWidget(self.quote_panel, 0)
        root.addLayout(main, 1)

    def _build_quote_panel(self) -> QGroupBox:
        group = QGroupBox("盘口")
        v = QVBoxLayout(group)

        self.price_label = QLabel("--")
        self.price_label.setStyleSheet("font-size: 20pt; font-weight: bold;")
        self.change_label = QLabel("--")
        self.change_label.setStyleSheet(f"color: {FLAT};")

        v.addWidget(self.price_label)
        v.addWidget(self.change_label)
        v.addSpacing(6)

        self.quote_grid: dict[str, QLabel] = {}
        for key in ("今开", "最高", "最低", "昨收", "成交量", "成交额", "换手率"):
            row = QHBoxLayout()
            k = QLabel(key)
            k.setStyleSheet(f"color: {TEXT_2};")
            val = QLabel("--")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k)
            row.addWidget(val)
            self.quote_grid[key] = val
            v.addLayout(row)

        v.addStretch(1)
        group.setFixedWidth(210)
        return group

    # ============================================================
    # 数据加载
    # ============================================================

    def select_symbol(self, symbol: str) -> None:
        """供外部（交易/回测视图）联动切换标的。"""
        self._load(symbol)

    def _period_value(self) -> str:
        return _PERIOD_MAP.get(self.period_combo.currentText(), "daily")

    def _adjust_value(self) -> str:
        return _ADJUST_MAP.get(self.adjust_combo.currentText(), "qfq")

    def _load(self, symbol: str) -> None:
        if not symbol:
            return
        self.current_symbol = symbol
        self.title_label.setText(f"加载 {symbol} ...")
        period = self._period_value()
        adjust = self._adjust_value()

        self._worker = Worker(self._fetch, symbol, period, adjust)
        self._worker.finished.connect(lambda df: self._on_data(symbol, df))
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _fetch(self, symbol: str, period: str, adjust: str) -> Any:
        if period in ("5", "15", "30", "60"):
            return self.dm.minute(symbol, period=period, adjust=adjust)
        start = (datetime.now() - timedelta(days=1100)).strftime("%Y%m%d")
        return self.dm.daily(symbol, start=start, adjust=adjust, period=period)

    def _on_data(self, symbol: str, df: Any) -> None:
        if df is None or df.empty:
            self.title_label.setText(f"{symbol} 无数据")
            return
        self.current_df = df
        ma = (5, 10, 20, 60) if self._period_value() == "daily" else (5, 10, 20)
        self.chart.set_data(df, ma_list=ma)
        self._update_quote_from_df(df)
        self.symbol_selected.emit(symbol)
        self._refresh_quote(symbol)

    def _on_error(self, msg: str) -> None:
        self.title_label.setText(f"加载失败：{msg[:60]}")
        QMessageBox.warning(self, "行情加载失败", msg)

    def _refresh_quote(self, symbol: str) -> None:
        """用实时行情刷新盘口与名称（失败时静默）。"""
        self._rt_worker = Worker(self.dm.realtime, [symbol])
        self._rt_worker.finished.connect(self._apply_quote)
        self._rt_worker.error.connect(lambda _e: None)
        self._rt_worker.start()

    def _apply_quote(self, df: Any) -> None:
        if df is None or df.empty:
            return
        row = df.iloc[0]
        name = str(row.get("name", "") or "")
        price = float(row.get("price", 0.0) or 0.0)
        prev = float(row.get("prev_close", 0.0) or 0.0)
        pct = float(row.get("pct_chg", 0.0) or 0.0)
        chg = price - prev
        self._set_quote(
            name, price, prev, chg, pct,
            open=float(row.get("open", 0.0) or 0.0),
            high=float(row.get("high", 0.0) or 0.0),
            low=float(row.get("low", 0.0) or 0.0),
            volume=float(row.get("volume", 0.0) or 0.0),
            amount=float(row.get("amount", 0.0) or 0.0),
            turnover=row.get("turnover"),
        )

    def _update_quote_from_df(self, df: Any) -> None:
        if df is None or df.empty:
            return
        last = df.iloc[-1]
        price = float(last["close"])
        prev = float(df.iloc[-2]["close"]) if len(df) > 1 else price
        chg = price - prev
        pct = chg / prev * 100 if prev else 0.0
        self._set_quote(
            str(last.get("name", self.current_symbol) or self.current_symbol),
            price, prev, chg, pct,
            open=float(last.get("open", 0.0) or 0.0),
            high=float(last.get("high", 0.0) or 0.0),
            low=float(last.get("low", 0.0) or 0.0),
            volume=float(last.get("volume", 0.0) or 0.0),
            amount=float(last.get("amount", 0.0) or 0.0),
            turnover=last.get("turnover"),
        )

    # ============================================================
    # 盘口渲染
    # ============================================================

    def _set_quote(self, name: str, price: float, prev: float, chg: float,
                   pct: float, **kw: float) -> None:
        color = UP if chg >= 0 else DOWN
        sign = "+" if chg >= 0 else ""
        self.price_label.setText(f"{price:,.2f}")
        self.price_label.setStyleSheet(f"font-size:20pt;font-weight:bold;color:{color};")
        self.change_label.setText(f"{sign}{chg:,.2f}  {sign}{pct:.2f}%")
        self.change_label.setStyleSheet(f"color:{color};")
        self.title_label.setText(f"{name}  ({self.current_symbol})")
        self.title_label.setStyleSheet(f"color:{TEXT_2};font-size:12pt;font-weight:bold;")

        mapping = {
            "今开": float(kw.get("open", 0.0)),
            "最高": float(kw.get("high", 0.0)),
            "最低": float(kw.get("low", 0.0)),
            "昨收": prev,
            "成交量": self._fmt_vol(float(kw.get("volume", 0.0))),
            "成交额": self._fmt_amount(float(kw.get("amount", 0.0))),
            "换手率": f"{float(kw.get('turnover') or 0.0):.2f}%",
        }
        for k, v in mapping.items():
            self.quote_grid[k].setText(str(v))

    @staticmethod
    def _fmt_vol(v: float) -> str:
        if v >= 1e8:
            return f"{v / 1e8:.2f}亿手"
        if v >= 1e4:
            return f"{v / 1e4:.1f}万手"
        return f"{v:.0f}手"

    @staticmethod
    def _fmt_amount(v: float) -> str:
        if v >= 1e8:
            return f"{v / 1e8:.2f}亿"
        if v >= 1e4:
            return f"{v / 1e4:.1f}万"
        return f"{v:.0f}"

    # ============================================================
    # 搜索与自选股
    # ============================================================

    def _on_search(self) -> None:
        kw = self.search_box.text().strip()
        if not kw:
            return
        try:
            results = self.dm.search(kw, limit=40)
        except Exception as exc:
            QMessageBox.warning(self, "搜索失败", str(exc))
            return
        if not results:
            QMessageBox.information(self, "无结果", f"未找到「{kw}」")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("搜索结果")
        dlg.setMinimumWidth(260)
        lv = QListWidget()
        for sym, nm in results:
            item = QListWidgetItem(f"{sym}  {nm}")
            item.setData(Qt.ItemDataRole.UserRole, sym)
            lv.addItem(item)
        lv.itemDoubleClicked.connect(
            lambda it: (dlg.accept(), self._load(it.data(Qt.ItemDataRole.UserRole)))
        )
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("双击选择标的"))
        lay.addWidget(lv)
        dlg.setLayout(lay)
        dlg.exec()

    def _name_of(self, symbol: str) -> str:
        try:
            res = self.dm.search(symbol, limit=1)
            return res[0][1] if res else symbol
        except Exception:
            return symbol

    def _load_watchlist(self) -> None:
        # 构造期不联网（名称在加载行情后由实时接口填充到盘口标题）
        self.watch_list.clear()
        for sym in self.cfg.watchlist:
            item = QListWidgetItem(sym)
            item.setData(Qt.ItemDataRole.UserRole, sym)
            self.watch_list.addItem(item)

    def _on_watch_selected(self, item: QListWidgetItem) -> None:
        sym = item.data(Qt.ItemDataRole.UserRole)
        if sym:
            self._load(sym)

    def _add_watch(self) -> None:
        if not self.current_symbol:
            return
        if self.current_symbol in self.cfg.watchlist:
            return
        self.cfg.watchlist.append(self.current_symbol)
        save_config(self.cfg)
        self._load_watchlist()

    def _remove_watch(self) -> None:
        item = self.watch_list.currentItem()
        if item is None:
            return
        sym = item.data(Qt.ItemDataRole.UserRole)
        if sym and sym in self.cfg.watchlist:
            self.cfg.watchlist.remove(sym)
            save_config(self.cfg)
            self._load_watchlist()

    def _on_period_changed(self, _text: str) -> None:
        if self.current_symbol:
            self._load(self.current_symbol)
