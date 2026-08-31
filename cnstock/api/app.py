# -*- coding: utf-8 -*-
"""FastAPI 应用：A 股模拟交易终端 REST API。

把 ``cnstock.api.service.TradingService`` 暴露成 HTTP 接口。仅依赖纯逻辑层，
不 import 任何 GUI 模块（后端边界守卫会验证）。

运行：``uvicorn cnstock.api.app:app --port 8000`` 或 ``python -m cnstock.api``。
交互式文档：启动后访问 ``http://localhost:8000/docs``。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .schemas import (
    BacktestAllRequest,
    BacktestRequest,
    MetricsRequest,
    OrderRequest,
)
from .service import get_service, parse_order_type, parse_side

VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 预热单例（加载/创建状态库），首个请求不再有冷启动延迟
    get_service()
    yield


app = FastAPI(
    title="A股模拟交易终端 - REST API",
    description="把零 GUI 依赖的纯逻辑层（撮合/回测/指标/存储/策略）暴露为 HTTP 接口。",
    version=VERSION,
    lifespan=lifespan,
)

# 允许任意来源（桌面端、网页端、脚本均可调用）；生产可收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "cnstock-api", "version": VERSION}


@app.get("/api/strategies")
def list_strategies() -> list[dict]:
    return get_service().strategies()


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict:
    try:
        return get_service().run_backtest(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 数据不足 / 指标预计算失败等
        raise HTTPException(status_code=400, detail=f"回测失败：{exc}") from exc


@app.post("/api/backtest/all")
def backtest_all(req: BacktestAllRequest) -> list[dict]:
    try:
        return get_service().run_backtest_all(req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"批量回测失败：{exc}") from exc


@app.get("/api/account")
def get_account() -> dict:
    return get_service().account_dict()


@app.get("/api/positions")
def get_positions() -> list[dict]:
    return get_service().positions_dict()


@app.post("/api/orders")
def place_order(req: OrderRequest) -> dict:
    try:
        side = parse_side(req.side)
        order_type = parse_order_type(req.order_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return get_service().submit_order(
            req.symbol, side, req.quantity, req.price, order_type, req.quote
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"下单失败：{exc}") from exc


@app.get("/api/orders")
def list_orders() -> list[dict]:
    return get_service().list_orders()


@app.get("/api/trades")
def list_trades() -> list[dict]:
    return get_service().list_trades()


@app.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str) -> dict:
    ok = get_service().cancel(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail="订单不存在或已终结（不可撤）")
    return {"cancelled": order_id}


@app.post("/api/settlement")
def settlement(force: bool = Query(False)) -> dict:
    return {"settled": get_service().settlement(force=force)}


@app.post("/api/account/reset")
def reset_account(initial_cash: float | None = Query(None)) -> dict:
    return get_service().reset(initial_cash)


@app.get("/api/price-limit")
def price_limit(
    symbol: str = Query(..., description="6 位代码"),
    prev_close: float = Query(..., description="前收盘价"),
    name: str = Query("", description="证券简称（ST 判定用）"),
) -> dict:
    return get_service().price_limit(symbol, name, prev_close)


@app.post("/api/metrics")
def metrics(req: MetricsRequest) -> dict:
    try:
        return get_service().compute_metrics(req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"指标计算失败：{exc}") from exc
