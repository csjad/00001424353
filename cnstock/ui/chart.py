# -*- coding: utf-8 -*-
"""
K 线与成交量图表（基于 pyqtgraph）。

- ``CandlestickItem``：自定义蜡烛绘制（涨红跌绿）
- ``DateAxisItem``：底部坐标轴显示交易日期
- ``PriceChart``：组合 K 线（上）+ 成交量（下），共享 X 轴联动缩放

注意：使用 pyqtgraph 前必须先 ``import PyQt6.QtWidgets``，由 ``main.py`` 保证。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

from .theme import BG, BORDER, DOWN, TEXT_2, UP

#: 均线配色
_MA_COLORS = {5: "#FFD666", 10: "#B37FEB", 20: "#2F81F7", 30: "#36CFC9", 60: "#FF9C6E"}


class DateAxisItem(pg.AxisItem):
    """底部日期坐标轴。"""

    def __init__(self, dates: list[str] | None = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dates: list[str] = list(dates or [])

    def set_dates(self, dates: list[str]) -> None:
        self.dates = list(dates)

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        out = []
        for v in values:
            idx = int(round(v))
            out.append(self.dates[idx] if 0 <= idx < len(self.dates) else "")
        return out


class CandlestickItem(pg.GraphicsObject):
    """蜡烛图绘制项。数据格式 ``[x, open, close, low, high]``。"""

    def __init__(self, df: pd.DataFrame) -> None:
        super().__init__()
        self._data = self._prepare(df)
        self._picture = QtGui.QPicture()
        self._generate()
        self._rect = QtCore.QRectF(self._picture.boundingRect())

    @staticmethod
    def _prepare(df: pd.DataFrame) -> np.ndarray:
        n = len(df)
        arr = np.empty((n, 5), dtype=float)
        for i, (_, r) in enumerate(df.iterrows()):
            try:
                arr[i] = [i, float(r["open"]), float(r["close"]),
                          float(r["low"]), float(r["high"])]
            except (ValueError, TypeError):
                # 单行缺失（NaN / 非数值）不应拖垮整张图，降级为 0 仅影响该根蜡烛
                arr[i] = [i, 0.0, 0.0, 0.0, 0.0]
        return arr

    def _generate(self) -> None:
        p = QtGui.QPainter(self._picture)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        width = 0.6
        up_pen = pg.mkPen(UP, width=1)
        down_pen = pg.mkPen(DOWN, width=1)
        up_brush = pg.mkBrush(UP)
        down_brush = pg.mkBrush(DOWN)
        for x, o, c, low, high in self._data:
            rising = o <= c
            pen = up_pen if rising else down_pen
            brush = up_brush if rising else down_brush
            p.setPen(pen)
            p.setBrush(brush)
            p.drawLine(QtCore.QPointF(x, low), QtCore.QPointF(x, high))
            top = o if rising else c
            bottom = c if rising else o
            p.drawRect(QtCore.QRectF(x - width / 2.0, min(top, bottom), width, abs(bottom - top)))
        p.end()

    def paint(self, painter, *args) -> None:  # noqa: D401
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:  # type: ignore[override]
        return self._rect


class PriceChart(pg.GraphicsLayoutWidget):
    """K 线 + 成交量组合图表。"""

    def __init__(self) -> None:
        super().__init__()
        self.setBackground(BG)
        self.setMinimumHeight(320)

        self.date_axis = DateAxisItem(orientation="bottom")
        self.plt = self.addPlot(row=0, col=0, axisItems={"bottom": self.date_axis})
        self.vol_plt = self.addPlot(row=1, col=0)
        self.vol_plt.hideAxis("bottom")
        self.plt.setXLink(self.vol_plt)

        for ax in (self.plt, self.vol_plt):
            ax.showGrid(True, True, 0.12)
            ax.getAxis("left").setPen(pg.mkPen(BORDER))
            ax.getAxis("left").setTextPen(pg.mkPen(TEXT_2))
            ax.getAxis("left").setStyle(tickTextOffset=4)
        self.date_axis.setPen(pg.mkPen(BORDER))
        self.date_axis.setTextPen(pg.mkPen(TEXT_2))
        self.plt.setLabel("right", "价格")
        self.vol_plt.setLabel("right", "成交量")

        # 视图框：去掉默认右键菜单里的无关项
        self.plt.setMenuEnabled(False)
        self.vol_plt.setMenuEnabled(False)
        self.plt.hideButtons()
        self.vol_plt.hideButtons()

    def set_data(self, df: pd.DataFrame | None, ma_list: tuple[int, ...] = (5, 10, 20)) -> None:
        """
        渲染 K 线。

        :param df: 统一 schema 的日线数据
        :param ma_list: 要叠加的均线周期
        """
        self.plt.clear()
        self.vol_plt.clear()

        if df is None or df.empty:
            self.date_axis.set_dates([])
            return

        self.date_axis.set_dates(df["date"].tolist())
        x = np.arange(len(df))

        # ---- K 线 ----
        self.plt.addItem(CandlestickItem(df))

        # ---- 均线 ----
        for p in ma_list:
            if p <= 0 or p > len(df):
                continue
            ma = df["close"].rolling(int(p), min_periods=int(p)).mean().values
            self.plt.plot(
                x, ma,
                pen=pg.mkPen(_MA_COLORS.get(p, "#FFFFFF"), width=1.2),
                name=f"MA{p}",
            )

        # ---- 成交量 ----
        vol = df["volume"].fillna(0).to_numpy(dtype=float)
        rising = df["close"].to_numpy(dtype=float) >= df["open"].to_numpy(dtype=float)
        brushes = np.where(rising, pg.mkBrush(UP), pg.mkBrush(DOWN))
        bars = pg.BarGraphItem(x=x, height=vol, width=0.8, brushes=list(brushes))
        self.vol_plt.addItem(bars)

        self.plt.autoRange()
        self.vol_plt.autoRange()
        self.vol_plt.setYRange(0, float(vol.max()) * 1.1 if vol.max() > 0 else 1.0)
