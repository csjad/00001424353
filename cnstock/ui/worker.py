# -*- coding: utf-8 -*-
"""
通用后台线程包装。

网络请求（akshare/Tushare）可能耗时数秒，全部走子线程避免界面卡顿。
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class Worker(QThread):
    """在子线程执行任意 callable，结果通过信号回传。"""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # type: ignore[override]
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as exc:  # 子线程异常必须捕获，否则静默丢失
            self.error.emit(str(exc))
