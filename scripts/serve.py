# -*- coding: utf-8 -*-
"""启动 A 股模拟交易终端 REST API 服务的便捷脚本。

用法：
    python scripts/serve.py                       # 默认 :8000
    CNSTOCK_API_PORT=9000 python scripts/serve.py
    CNSTOCK_API_DB=/path/to/state.db python scripts/serve.py

该脚本只是把项目根加入 sys.path 后调用 uvicorn 跑 ``cnstock.api.app:app``，
等价于 ``uvicorn cnstock.api.app:app --port 8000``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from cnstock.api.app import app

if __name__ == "__main__":
    port = int(os.environ.get("CNSTOCK_API_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
