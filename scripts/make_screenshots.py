# -*- coding: utf-8 -*-
"""
生成 README 截图（离屏渲染 PNG，零联网、零副作用）。

设计要点（踩坑后修正）：
- 离屏平台（offscreen）默认没有 CJK 字体 -> 必须显式注册系统字体，否则全是豆腐块。
- MainWindow / DataManager 在构造期与切换页时会触发真实网络请求（akshare 实时行情），
  离屏环境会卡死在代理重试风暴 -> 必须彻底隔离网络：
    * 不调用 market.select_symbol（它内部 _load 会联网拉 K 线）；改为直接喂 chart + 盘口。
    * 把 trade.sync_market 替换成纯本地 refresh（否则切到交易页会拉实时行情）。
    * 停掉 MainWindow 状态栏定时器与 TradeView 自动刷新定时器。
    * 覆盖 _update_status，避免状态栏里的 ✓/✗ 变成豆腐块。
- broker 不传 persister，演示订单只活在内存，绝不写入用户真实账户 SQLite。

用法：python scripts/make_screenshots.py
前置：必须先 pip install -r requirements.txt
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# 必须最先 import PyQt6.QtWidgets，再 import pyqtgraph（由 cnstock 间接 import）。
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QFontDatabase, QPixmap
from PyQt6.QtWidgets import QApplication, QListWidgetItem

import pyqtgraph as pg  # noqa: E402 必须在 PyQt6.QtWidgets 之后

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnstock.core.config import load_config
from cnstock.data.manager import DataManager
from cnstock.engine.broker import SimBroker
from cnstock.ui.main_window import MainWindow
from cnstock.backtest.engine import BacktestEngine
from cnstock.backtest.strategies import get_strategy
from cnstock.core.constants import OrderSide, OrderType
from cnstock.core.models import Order, OrderStatus
from cnstock.ui.theme import BG, DARK_QSS, FONT_SIZE, TEXT_2

OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 离屏 CJK 字体注册（关键：否则中文全是豆腐块）
# ---------------------------------------------------------------------------

def install_cjk_fonts(app: QApplication) -> str:
    """注册系统 CJK 字体并返回选中的应用程序默认字体族。"""
    candidates = [
        (r"C:\Windows\Fonts\msyh.ttc", "Microsoft YaHei"),
        (r"C:\Windows\Fonts\msyhbd.ttc", "Microsoft YaHei"),
        (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
        (r"C:\Windows\Fonts\simsun.ttc", "SimSun"),
        (r"C:\Windows\Fonts\simkai.ttf", "KaiTi"),
    ]
    installed: list[str] = []
    for path, _name in candidates:
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                installed.extend(QFontDatabase.applicationFontFamilies(fid))

    for fam in ("Microsoft YaHei", "SimHei", "SimSun", "KaiTi"):
        if fam in installed:
            app.setFont(QFont(fam, FONT_SIZE))
            return fam
    return ""


# ---------------------------------------------------------------------------
# 数据合成
# ---------------------------------------------------------------------------

def synth_kline(seed: int = 42, n: int = 180, start: float = 1680.0) -> pd.DataFrame:
    """生成一段走势自然的合成日 K 线。"""
    np.random.seed(seed)
    rets = np.random.randn(n) * 0.018
    close = start * np.exp(np.cumsum(rets))
    open_ = close * (1 + np.random.uniform(-0.008, 0.008, n))
    high = np.maximum(close, open_) * (1 + abs(np.random.randn(n)) * 0.007)
    low = np.minimum(close, open_) * (1 - abs(np.random.randn(n)) * 0.007)
    vol = 1.2e6 * (0.6 + abs(np.random.randn(n)) * 0.6)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": vol, "amount": vol * close,
    })


def synth_backtest_df(n: int = 300, seed: int = 7) -> pd.DataFrame:
    np.random.seed(seed)
    price = 100 + np.cumsum(np.random.randn(n) * 1.6)
    vol = np.full(n, 1.2e6)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d"),
        "open": price + 0.5, "high": price + 1.2, "low": price - 1.2,
        "close": price, "volume": vol, "amount": vol * price.mean(),
    })


# ---------------------------------------------------------------------------
# 渲染工具
# ---------------------------------------------------------------------------

def grab(widget, path: Path, size: QSize) -> None:
    widget.resize(size)
    QApplication.processEvents()
    pix: QPixmap = widget.grab()
    pix.save(str(path), "PNG")
    kb = path.stat().st_size / 1024
    print(f"  -> {path.relative_to(ROOT)}  ({pix.width()}x{pix.height()}, {kb:.1f} KB)")


def quote_of(row, prev_row) -> dict:
    return {
        "price": float(row["close"]),
        "prev_close": float(prev_row["close"]),
        "name": "贵州茅台",
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "volume": float(row["volume"]),
        "amount": float(row["amount"]),
    }


def _safe_update_status(win: MainWindow) -> None:
    """ASCII 版状态栏（避开 ✓/✗ 豆腐块，且完全不联网）。"""
    acct = win.broker.account
    win.statusBar().showMessage(
        f"数据源 [akshare 可用]  |  总资产 {acct.total_value:,.2f}  |  "
        f"可用 {acct.cash:,.2f}  |  持仓市值 {acct.market_value:,.2f}  |  "
        f"总盈亏 {acct.total_pnl:+,.2f}"
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    app = QApplication(sys.argv)
    # 暗色主题 + 中文字体（关键修复项）
    app.setStyleSheet(DARK_QSS)
    font_family = install_cjk_fonts(app)
    pg.setConfigOptions(background=BG, foreground=TEXT_2, antialias=True)
    print(f"[字体] 已注册应用字体：{font_family or '(未找到系统 CJK 字体，可能仍是豆腐块)'}")

    cfg = load_config()
    dm = DataManager(cfg)
    # 注意：不传 persister -> 演示订单只活内存，绝不污染用户真实账户库
    broker = SimBroker(dm, cfg)

    win = MainWindow(dm, broker, cfg)
    # ---- 彻底隔离网络 ----
    win._status_timer.stop()                         # 停状态栏 5s 轮询
    win.trade.timer.stop()                            # 停交易页自动刷新定时器
    win.trade.sync_market = lambda: win.trade.refresh()  # 切到交易页不再拉实时行情
    win._update_status = lambda: _safe_update_status(win)  # 状态栏不联网、无 ✓/✗

    win.resize(1280, 800)
    win.show()
    QApplication.processEvents()
    win._update_status()

    # ---------- 1. 行情中心 ----------
    print("[1/4] 行情中心（合成 K 线 + 盘口）")
    df = synth_kline(seed=42, n=180, start=1680.0)
    last, prev = df.iloc[-1], df.iloc[-2]

    # 直接喂数据，绝不调用 select_symbol（否则会联网拉 K 线）
    win.market.current_symbol = "600519"
    win.market.chart.set_data(df, ma_list=(5, 10, 20))
    win.market._set_quote(
        "贵州茅台", float(last["close"]), float(prev["close"]),
        float(last["close"] - prev["close"]),
        float((last["close"] - prev["close"]) / prev["close"] * 100),
        open=float(last["open"]), high=float(last["high"]), low=float(last["low"]),
        volume=float(last["volume"]), amount=float(last["amount"]), turnover=0.45,
    )
    # 自选股列表（清空后重新填入"代码  名称"，避免被 config 初始的单代码项占位）
    win.market.watch_list.clear()
    for sym, name in [("600519", "贵州茅台"), ("000001", "平安银行"),
                       ("300750", "宁德时代"), ("601318", "中国平安"),
                       ("600036", "招商银行"), ("000858", "五粮液")]:
        item = QListWidgetItem(f"{sym}  {name}")
        item.setData(Qt.ItemDataRole.UserRole, sym)
        win.market.watch_list.addItem(item)
    QApplication.processEvents()

    win._switch(0)
    QApplication.processEvents(); time.sleep(0.2)
    grab(win, OUT / "00-overview.png", QSize(1280, 800))
    grab(win.market, OUT / "01-market.png", QSize(1280, 800))

    # ---------- 2. 模拟交易 ----------
    print("[2/4] 模拟交易（合成订单 + 持仓 + 成交）")
    # 600519 市价买入 -> 成交（演示持仓与 T+1 锁定）
    q_600519 = quote_of(last, prev)
    broker.submit_order("600519", OrderSide.BUY, 100, 0.0, OrderType.MARKET, q_600519)
    # 000001 限价买入 -> 成交
    q_000001 = {"price": 12.50, "prev_close": 12.30, "name": "平安银行",
                "open": 12.30, "high": 12.60, "low": 12.20,
                "volume": 5e7, "amount": 6.3e8}
    broker.submit_order("000001", OrderSide.BUY, 1000, 12.50, OrderType.LIMIT, q_000001)
    # 300750 挂一单未成交（pending）
    pending = Order(symbol="300750", name="宁德时代", side=OrderSide.BUY,
                    quantity=100, order_type=OrderType.LIMIT, price=210.00,
                    status=OrderStatus.PENDING, order_id="X-DEMO")
    broker._orders["X-DEMO"] = pending

    # 日终结算解除 T+1 锁定，再卖出一部分 000001
    broker.daily_settlement(force=True)
    q_000001_up = {"price": 12.72, "prev_close": 12.30, "name": "平安银行",
                   "open": 12.30, "high": 12.80, "low": 12.20,
                   "volume": 5e7, "amount": 6.4e8}
    broker.submit_order("000001", OrderSide.SELL, 500, 12.72, OrderType.LIMIT, q_000001_up)

    # 用最新盘口刷新持仓市值（纯本地字典，不联网）
    broker.refresh_prices({
        "600519": {**q_600519, "price": float(last["close"]) * 1.006},
        "000001": {**q_000001_up, "price": 12.68},
    })

    win.trade.refresh()
    win._update_status()
    win._switch(1)
    QApplication.processEvents(); time.sleep(0.2)
    grab(win, OUT / "02-trade.png", QSize(1280, 800))

    # ---------- 3. 量化回测 ----------
    print("[3/4] 量化回测（合成 RSI 策略结果）")
    bdf = synth_backtest_df(n=300, seed=7)
    eng = BacktestEngine(cfg)
    res = eng.run(get_strategy("RSI 超买超卖"), bdf, "999999",
                  "上证指数", 100000.0)
    win.backtest.set_symbol("999999")
    # 让顶部策略下拉框与实际运行结果一致（默认第一项是"买入持有"）
    win.backtest.strategy_combo.setCurrentText("RSI 超买超卖")
    win.backtest._on_result(res)
    win._switch(2)
    QApplication.processEvents(); time.sleep(0.2)
    grab(win, OUT / "03-backtest.png", QSize(1280, 800))

    print("\n=== 截图生成完毕 ===")
    for p in sorted(OUT.glob("*.png")):
        kb = p.stat().st_size / 1024
        print(f"  {p.relative_to(ROOT)}  ({kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
