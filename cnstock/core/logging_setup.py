# -*- coding: utf-8 -*-
"""
日志配置。

日志同时输出到控制台与用户目录下的 ``app.log``，便于排查数据源/撮合问题。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .config import user_data_dir


def setup_logging(level: int = logging.INFO) -> Path:
    """
    初始化全局日志。

    :return: 日志文件路径
    """
    log_file = user_data_dir() / "app.log"
    try:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(),
            ],
            force=True,
        )
    except Exception:
        logging.basicConfig(level=level, force=True)
    return log_file
