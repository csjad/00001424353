# -*- coding: utf-8 -*-
"""API 层无头冒烟测试（带 Qt 阻断器）。

作为后端边界守卫动态验证的一部分被 ``scripts/_verify.py`` 的 [9] 以子进程方式调用；
也可单独运行：``python scripts/api_smoke.py``。

做法：先装 ``sys.meta_path`` 阻断器（模拟没有 Qt 的服务端镜像），再 import
``cnstock.api.app``。若 API 层偷偷引了 GUI 依赖，import 阶段就抛 ImportError、
进程退出非 0。随后用 FastAPI TestClient 把核心接口都打一遍，确认返回 200 且结果合理。

退出码：0 = API 层零 GUI 依赖且核心接口可用；2 = fastapi 未安装（提示安装）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 状态库隔离到临时目录，避免污染仓库 / 桌面端 data.db
os.environ.setdefault(
    "CNSTOCK_API_DB",
    str(Path(tempfile.mkdtemp(prefix="cnstock_api_")) / "state.db"),
)

# ---------- 第 0 步：装阻断器（必须在任何业务/API import 之前） ----------
BLOCKED_ROOTS = ("PyQt6", "pyqtgraph")


class _QtBlocker:
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED_ROOTS:
            raise ImportError(f"[api-smoke] 纯逻辑层/API 层禁止导入 GUI：{fullname}")
        return None


sys.meta_path.insert(0, _QtBlocker())

# fastapi 缺失时给明确提示，而不是直接 ImportError 混淆
try:
    from fastapi.testclient import TestClient
except ImportError:
    print("SKIP: fastapi 未安装，请先 `pip install -r requirements-api.txt`")
    sys.exit(2)

# 这一步若 API 层引了 Qt，会被上面的阻断器拦下（ImportError -> 非 0 退出）
from cnstock.api.app import app  # noqa: E402


def _main() -> int:
    with TestClient(app) as client:
        # 健康检查
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok", r.text
        print("[1] health OK")

        # 策略目录
        r = client.get("/api/strategies")
        assert r.status_code == 200, r.text
        strats = r.json()
        assert any(s["name"] == "双均线交叉" for s in strats), strats
        print(f"[2] strategies OK（{len(strats)} 个策略）")

        # 回测：合成 60 根「波动」日线（非单调），让双均线交叉能产生买卖配对
        import math

        bars = [
            {
                "date": f"2024-01-{i:02d}" if i <= 31 else f"2024-02-{i-31:02d}",
                "open": 100 + 15 * math.sin(i * 0.3),
                "high": 100 + 15 * math.sin(i * 0.3) + 1,
                "low": 100 + 15 * math.sin(i * 0.3) - 1,
                "close": 100 + 15 * math.sin(i * 0.3),
                "volume": 1_000_000,
            }
            for i in range(1, 61)
        ]
        r = client.post(
            "/api/backtest",
            json={"strategy": "双均线交叉", "symbol": "600519",
                  "name": "贵州茅台", "bars": bars},
        )
        assert r.status_code == 200, r.text
        bt = r.json()
        assert "metrics" in bt and "equity_curve" in bt, bt
        assert bt["metrics"]["trade_count"] >= 1, bt["metrics"]
        print(f"[3] backtest OK：总收益 {bt['metrics']['total_return']*100:+.2f}%"
              f" | 交易 {bt['metrics']['trade_count']} 笔")

        # 批量回测（所有策略）
        r = client.post(
            "/api/backtest/all",
            json={"symbol": "600519", "name": "贵州茅台", "bars": bars},
        )
        assert r.status_code == 200, r.text
        allres = r.json()
        assert len(allres) == len(strats), (len(allres), len(strats))
        ok = sum(1 for x in allres if "error" not in x)
        print(f"[4] backtest/all OK（{len(allres)} 个策略跑通 {ok} 个）")

        # 账户快照
        r = client.get("/api/account")
        assert r.status_code == 200 and "total_value" in r.json(), r.text
        print("[5] account OK")

        # 下单（离线必须带 quote，否则以「未获取到行情」拒单）
        r = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "买入", "quantity": 100,
                  "price": 100.0,
                  "quote": {"price": 100.0, "prev_close": 99.0, "name": "贵州茅台"}},
        )
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["status"] in ("已成交", "已拒绝", "待成交"), o
        print(f"[6] order OK：状态={o['status']}")

        # 涨跌停工具
        r = client.get("/api/price-limit", params={
            "symbol": "600519", "prev_close": 100.0, "name": "贵州茅台"})
        assert r.status_code == 200 and r.json()["up"] > 100.0, r.text
        print(f"[7] price-limit OK：涨停 {r.json()['up']} / 跌停 {r.json()['down']}")

        # 负向：本进程装了阻断器，sys.modules 不得出现 Qt
        leaked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED_ROOTS)
        assert not leaked, f"Qt 泄漏：{leaked[:5]}"
        print("[8] 零 Qt 依赖（sys.modules 中 PyQt6/pyqtgraph = 0）")

    print("=== API 无头冒烟通过：health/strategies/backtest/backtest-all/"
          "account/order/price-limit 全部 200，且 API 层零 GUI 依赖 ===")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
