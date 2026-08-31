# -*- coding: utf-8 -*-
"""``python -m cnstock.api`` 入口：启动 uvicorn 服务。"""
from __future__ import annotations

import os

import uvicorn

from .app import app

if __name__ == "__main__":
    port = int(os.environ.get("CNSTOCK_API_PORT", "8000"))
    # 注意：reload=False，避免开发服务器重复 import 带来单例/状态库问题
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
