# -*- coding: utf-8 -*-
"""
应用入口。

启动顺序要点：
1. 先 ``import PyQt6.QtWidgets`` 再 ``import pyqtgraph``，否则 pyqtgraph 可能选错 Qt 后端；
2. 加载配置、初始化数据源调度器与账户持久化；
3. 恢复上次会话的订单 / 成交 / 持仓；
4. 拉起主窗口，退出时清理资源。
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

import pyqtgraph as pg  # noqa: E402 必须在 PyQt6.QtWidgets 之后

from .core.config import load_config
from .core.logging_setup import setup_logging
from .data.manager import DataManager
from .engine.broker import SimBroker
from .storage.db import SqliteStorage
from .ui.main_window import MainWindow
from .ui.theme import BG, DARK_QSS, FONT_FAMILY, FONT_SIZE, TEXT_2

logger = logging.getLogger(__name__)


def main() -> int:
    """主函数，返回进程退出码。"""
    setup_logging()
    logger.info("启动 A股模拟交易终端")

    # pyqtgraph 全局样式（暗色 + 抗锯齿）
    pg.setConfigOptions(background=BG, foreground=TEXT_2, antialias=True)

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))

    cfg = load_config()
    dm = DataManager(cfg)

    # ---- 账户持久化与恢复 ----
    storage = SqliteStorage()
    account = storage.load_account(cfg.account.initial_cash)
    broker = SimBroker(dm, cfg, persister=storage, account=account)
    broker.load_orders(storage.load_orders())
    broker.load_trades(storage.load_trades())
    for pos in storage.load_positions():
        broker.account.positions[pos.symbol] = pos

    win = MainWindow(dm, broker, cfg)
    win.show()

    def _cleanup() -> None:
        try:
            dm.shutdown()
        except Exception:
            logger.exception("数据源关闭失败")
        try:
            storage.close()
        except Exception:
            logger.exception("数据库关闭失败")

    app.aboutToQuit.connect(_cleanup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
