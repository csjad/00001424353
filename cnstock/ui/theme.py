# -*- coding: utf-8 -*-
"""
界面主题：暗色金融风 + A 股配色（涨红跌绿）。

PyQt6 的 stylesheet 与调色板集中在此，便于全局统一风格。
"""
from __future__ import annotations

from typing import Final

#: 涨（红）
UP: Final[str] = "#F5222D"
#: 跌（绿）
DOWN: Final[str] = "#00B96B"
#: 平（灰）
FLAT: Final[str] = "#8C8C8C"

#: 背景
BG: Final[str] = "#0E1117"
#: 面板
PANEL: Final[str] = "#161B22"
#: 次级面板
PANEL_2: Final[str] = "#1C2129"
#: 边框
BORDER: Final[str] = "#30363D"
#: 主文本
TEXT: Final[str] = "#E6EDF3"
#: 次级文本
TEXT_2: Final[str] = "#8B949E"
#: 高亮（蓝）
ACCENT: Final[str] = "#2F81F7"
#: 警告（橙）
WARN: Final[str] = "#D29922"

#: 默认字体
FONT_FAMILY: Final[str] = "Microsoft YaHei UI, Microsoft YaHei, SimHei, sans-serif"
FONT_SIZE: Final[int] = 9


def get_colors() -> dict[str, str]:
    """返回统一颜色字典（供图表与表格复用）。"""
    return {
        "up": UP,
        "down": DOWN,
        "flat": FLAT,
        "bg": BG,
        "panel": PANEL,
        "panel2": PANEL_2,
        "border": BORDER,
        "text": TEXT,
        "text2": TEXT_2,
        "accent": ACCENT,
        "warn": WARN,
    }


#: 整站暗色 QSS
DARK_QSS: Final[str] = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE}pt;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

QFrame, QGroupBox {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QGroupBox {{
    margin-top: 6px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    color: {TEXT_2};
}}

QLabel {{
    background: transparent;
    color: {TEXT};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background-color: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    border: 1px solid {ACCENT};
    background-color: #232B36;
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: #FFFFFF;
}}
QPushButton:disabled {{
    color: {TEXT_2};
    background-color: {PANEL};
}}

QPushButton#BuyBtn {{
    background-color: {UP};
    color: #FFFFFF;
    border: none;
}}
QPushButton#BuyBtn:hover {{ background-color: #FF4D4F; }}

QPushButton#SellBtn {{
    background-color: {DOWN};
    color: #FFFFFF;
    border: none;
}}
QPushButton#SellBtn:hover {{ background-color: #36CFC9; }}

QListWidget, QTableWidget, QTreeWidget, QTextEdit, QPlainTextEdit {{
    background-color: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    gridline-color: {BORDER};
    color: {TEXT};
    selection-background-color: #243447;
}}

QHeaderView::section {{
    background-color: {PANEL};
    color: {TEXT_2};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px;
}}

QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; }}
QTabBar::tab {{
    background-color: {PANEL};
    color: {TEXT_2};
    padding: 6px 14px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background-color: {PANEL_2};
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

QScrollBar:vertical, QScrollArea {{
    background: transparent;
}}
QScrollBar:vertical {{
    background-color: {PANEL};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {TEXT_2}; }}

QStatusBar {{
    background-color: {PANEL};
    color: {TEXT_2};
    border-top: 1px solid {BORDER};
}}

QToolTip {{
    background-color: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
"""
