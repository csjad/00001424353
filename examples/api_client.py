# -*- coding: utf-8 -*-
"""A 股模拟交易终端 · REST API 最小客户端示例（stdlib-only，零额外依赖）。

演示如何把 ``cnstock.api`` 暴露的 13 个端点全部打一遍。先起服务：

    # 终端 A：启动服务（默认 8000）
    python -m cnstock.api
    # 或：uvicorn cnstock.api.app:app --port 8000

    # 终端 B：跑本示例
    python examples/api_client.py
    # 自定义地址：CNSTOCK_API_URL=http://127.0.0.1:8123 python examples/api_client.py

所有请求走标准库 ``urllib``，不依赖 ``requests``。A 股规则（T+1 / 涨跌停 /
整百 / 佣金印花税）由服务端 ``SimBroker`` 强制执行；离线下单必须随请求带
``quote:{price, prev_close, name?}``，否则服务端以「未获取到行情」拒单。
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("CNSTOCK_API_URL", "http://127.0.0.1:8000").rstrip("/")


# ============================================================
# 底层请求封装
# ============================================================

def _request(method: str, path: str, *, json_body: Any | None = None,
             params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """发一次请求，返回 (status_code, parsed_json_or_text)。"""
    url = BASE + path
    if params:
        q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url += ("&" if "?" in url else "?") + q
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:  # 服务端返回 4xx/5xx
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _show(step: str, method: str, path: str, status: int, body: Any) -> None:
    """打印一步的结果。"""
    ok = "✓" if 200 <= status < 300 else "✗"
    print(f"  {ok} [{step}] {method} {path} -> {status}")
    if isinstance(body, dict):
        preview = {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>")
                   for k, v in body.items()}
        print(f"      {json.dumps(preview, ensure_ascii=False)[:240]}")
    elif isinstance(body, list):
        print(f"      [list] len={len(body)}")
    elif body:
        print(f"      {str(body)[:240]}")


# ============================================================
# 样本数据
# ============================================================

def _wavy_bars(n: int = 60, base: float = 100.0, amp: float = 15.0) -> list[dict]:
    """一段有涨有跌的日线，保证双均线交叉策略能产生成交（单调上涨会 0 笔成交）。"""
    import datetime as _dt

    start = _dt.date(2024, 1, 1)
    bars = []
    for i in range(n):
        price = base + amp * math.sin(i * 0.3)
        d = (start + _dt.timedelta(days=i)).isoformat()
        bars.append({
            "date": d,
            "open": round(price - 0.5, 2),
            "high": round(price + 1.0, 2),
            "low": round(price - 1.0, 2),
            "close": round(price, 2),
            "volume": 1_000_000,
        })
    return bars


# ============================================================
# 主流程：打满 13 个端点
# ============================================================

def main() -> int:
    print(f"=== cnstock API 客户端示例 @ {BASE} ===\n")

    # 1) 健康检查
    s, body = _request("GET", "/api/health")
    _show("health", "GET", "/api/health", s, body)

    # 2) 策略目录
    s, body = _request("GET", "/api/strategies")
    _show("strategies", "GET", "/api/strategies", s, body)
    strategy_names = [x["name"] for x in body] if isinstance(body, list) else []

    # 3) 单策略回测（双均线交叉，用波动样本数据）
    bars = _wavy_bars()
    bt_req = {
        "strategy": "双均线交叉",
        "params": {"fast": 5, "slow": 20},
        "symbol": "600519",
        "name": "贵州茅台",
        "initial_cash": 1_000_000,
        "bars": bars,
    }
    s, body = _request("POST", "/api/backtest", json_body=bt_req)
    _show("backtest", "POST", "/api/backtest", s, body)
    equity_curve = body.get("equity_curve") if isinstance(body, dict) else None
    bt_trades = body.get("trades") if isinstance(body, dict) else None

    # 4) 批量回测（全部策略）
    s, body = _request("POST", "/api/backtest/all",
                       json_body={"symbol": "600519", "name": "贵州茅台",
                                  "initial_cash": 1_000_000, "bars": bars})
    _show("backtest-all", "POST", "/api/backtest/all", s, body)

    # 5) 账户
    s, body = _request("GET", "/api/account")
    _show("account", "GET", "/api/account", s, body)

    # 6) 下单（离线必须带 quote）
    # 6a) 市价可成交的限价买入（price=quote.price）-> 立即 FILLED，演示成交与手续费
    order_req = {
        "symbol": "600519",
        "side": "买入",            # 也接受 buy
        "quantity": 100,
        "price": 100.0,
        "order_type": "限价",       # 也接受 limit
        "quote": {"price": 100.0, "prev_close": 99.0, "name": "贵州茅台"},
    }
    s, body = _request("POST", "/api/orders", json_body=order_req)
    _show("place-order(filled)", "POST", "/api/orders", s, body)

    # 6b) 非市价可成交的限价买入（price 远低于 quote.price）-> PENDING，留给第 9 步撤单演示 200
    pending_req = {
        "symbol": "000001",
        "side": "buy",             # 英文别名同样接受
        "quantity": 100,
        "price": 10.0,             # 低于行情价 12.5 -> 挂单待成交
        "order_type": "limit",
        "quote": {"price": 12.5, "prev_close": 12.3, "name": "平安银行"},
    }
    s, body = _request("POST", "/api/orders", json_body=pending_req)
    _show("place-order(pending)", "POST", "/api/orders", s, body)
    pending_order_id = body.get("order_id") if isinstance(body, dict) else None

    # 7) 委托列表
    s, body = _request("GET", "/api/orders")
    _show("list-orders", "GET", "/api/orders", s, body)

    # 8) 成交列表
    s, body = _request("GET", "/api/trades")
    _show("list-trades", "GET", "/api/trades", s, body)

    # 9) 撤单（第 6b 步挂的是 PENDING 单，撤单应返回 200；已成交的单撤单会 404）
    if pending_order_id:
        s, body = _request("POST", f"/api/orders/{pending_order_id}/cancel")
        _show("cancel-order", "POST", f"/api/orders/{pending_order_id}/cancel", s, body)

    # 10) 结算
    s, body = _request("POST", "/api/settlement", params={"force": "false"})
    _show("settlement", "POST", "/api/settlement", s, body)

    # 11) 重置账户
    s, body = _request("POST", "/api/account/reset", params={"initial_cash": "1000000"})
    _show("reset-account", "POST", "/api/account/reset", s, body)

    # 12) 涨跌停价
    s, body = _request("GET", "/api/price-limit",
                       params={"symbol": "600519", "prev_close": "1680.0", "name": "贵州茅台"})
    _show("price-limit", "GET", "/api/price-limit", s, body)

    # 13) 绩效指标（用回测产出的权益曲线 / 成交）
    if equity_curve and bt_trades is not None:
        metrics_req = {
            "equity_curve": equity_curve,
            "trades": bt_trades,
            "initial_cash": 1_000_000,
            "benchmark_return": 0.0,
        }
        s, body = _request("POST", "/api/metrics", json_body=metrics_req)
        _show("metrics", "POST", "/api/metrics", s, body)
    else:
        print("  - [metrics] 跳过：回测未返回权益曲线/成交")

    # 持仓（附带，便于观察）
    s, body = _request("GET", "/api/positions")
    _show("positions", "GET", "/api/positions", s, body)

    print("\n=== 示例结束 ===")
    return 0


if __name__ == "__main__":
    import sys

    # urllib.parse 在 _request 里用到，确保已导入
    import urllib.parse  # noqa: F401

    sys.exit(main())
