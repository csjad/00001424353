# -*- coding: utf-8 -*-
"""REST API 服务层（FastAPI）。

把已证明「零 GUI 依赖」的纯逻辑层（撮合 / 回测 / 指标 / 存储 / 策略）暴露成
HTTP 接口，作为桌面端之外的另一个消费者。本包**不得** import 任何 PyQt6 /
pyqtgraph / ui 模块——后端边界守卫（scripts/_verify.py [8]/[9]）会静态 + 动态
验证这一点。

启动：
    python -m cnstock.api                 # 默认 :8000
    CNSTOCK_API_PORT=9000 python -m cnstock.api
    uvicorn cnstock.api.app:app --port 8000

状态库：默认当前目录 ``server_state.db``，可用环境变量 ``CNSTOCK_API_DB`` 覆盖，
以免与桌面端的 ``data.db`` 互相干扰。
"""
from __future__ import annotations

__version__ = "1.0.0"
