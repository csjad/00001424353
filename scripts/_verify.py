# -*- coding: utf-8 -*-
"""回归测试：持久化往返 / 交易视图渲染 / 离线冷却。

由两份一次性诊断脚本（_debug_persist.py、_debug_trade.py）沉淀而来，补齐
``_smoke.py`` 未覆盖的两块：

* SQLite 写入 → 关闭 → 按 ``main.py`` 的真实恢复序列重新打开 → 一致性比对；
* 复现下单序列后，交易视图三张表实际渲染的行数与单元格内容。

外加一层确定性断言：``DataManager`` 的失败冷却窗口（离线体验修复的第 2 道
防线）——预置失败状态后无需联网即可验证。

断言式，任一失败即非零退出。全程离线可跑。

用法：  python scripts/_verify.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnstock.core.config import load_config
from cnstock.core.constants import OrderSide, OrderType
from cnstock.core.models import Order, OrderStatus
from cnstock.data.base import DataError
from cnstock.data.manager import DataManager
from cnstock.engine.broker import SimBroker
from cnstock.storage.db import SqliteStorage

PASSED = 0


def _ok(msg: str) -> None:
    """记一条通过的断言。"""
    global PASSED
    PASSED += 1
    print(f"    [OK] {msg}")


# ============================================================
# [1] SQLite 持久化往返
# ============================================================

def test_persist_roundtrip() -> None:
    """写入 → 关闭 → 按 main.py 恢复序列重开 → 比对 → 续单 → 清空。"""
    print("[1] SQLite 持久化往返 ...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="cnstock_persist_"))
    db = tmp_dir / "persist_test.db"
    try:
        cfg = load_config()
        dm = DataManager(cfg)

        # ---- 写入 ----
        st = SqliteStorage(db)
        broker = SimBroker(dm, cfg, persister=st)

        q_maotai = {"price": 100.0, "prev_close": 99.0, "name": "贵州茅台",
                    "open": 99.5, "high": 101.0, "low": 98.5,
                    "volume": 1e6, "amount": 1e8}
        q_pab = {"price": 12.50, "prev_close": 12.30, "name": "平安银行",
                 "open": 12.3, "high": 12.6, "low": 12.2,
                 "volume": 5e7, "amount": 6.3e8}

        assert broker.submit_order("600519", OrderSide.BUY, 200, 100.0, OrderType.LIMIT, q_maotai).status == OrderStatus.FILLED
        assert broker.submit_order("000001", OrderSide.BUY, 1000, 12.50, OrderType.LIMIT, q_pab).status == OrderStatus.FILLED
        broker.daily_settlement(force=True)  # T+1 解锁
        q_pab_up = {"price": 12.80, "prev_close": 12.30, "name": "平安银行",
                    "open": 12.3, "high": 12.9, "low": 12.2,
                    "volume": 5e7, "amount": 6.4e8}
        assert broker.submit_order("000001", OrderSide.SELL, 400, 12.80, OrderType.LIMIT, q_pab_up).status == OrderStatus.FILLED

        expect = {
            "cash": broker.account.cash,
            "positions": {p.symbol: (p.total_qty, p.avg_cost, p.realized_pnl) for p in broker.positions},
            "n_orders": len(broker.all_orders()),
            "n_trades": len(broker.trades()),
        }
        assert len(expect["positions"]) == 2, expect["positions"]
        st.close()
        _ok(f"写入：现金={expect['cash']:,.2f} 持仓={list(expect['positions'])} 订单={expect['n_orders']} 成交={expect['n_trades']}")

        # ---- 按 main.py 的真实恢复序列重新打开 ----
        st2 = SqliteStorage(db)
        account = st2.load_account(cfg.account.initial_cash)
        broker2 = SimBroker(dm, cfg, persister=st2, account=account)
        broker2.load_orders(st2.load_orders())
        broker2.load_trades(st2.load_trades())
        for pos in st2.load_positions():
            broker2.account.positions[pos.symbol] = pos

        # ---- 一致性比对 ----
        assert abs(broker2.account.cash - expect["cash"]) < 1e-6, \
            f"现金漂移：{expect['cash']:,.4f} -> {broker2.account.cash:,.4f}"
        got = {p.symbol: (p.total_qty, p.avg_cost, p.realized_pnl) for p in broker2.positions}
        assert got == expect["positions"], f"持仓不一致：{expect['positions']} vs {got}"
        assert len(broker2.all_orders()) == expect["n_orders"], "订单数不一致"
        assert len(broker2.trades()) == expect["n_trades"], "成交数不一致"
        _ok(f"读出一致：现金={broker2.account.cash:,.2f} 持仓={list(got)} 订单={len(broker2.all_orders())} 成交={len(broker2.trades())}")

        # ---- 恢复后仍能正确交易（成本 / T+1 未丢）----
        q_sell = {"price": 101.0, "prev_close": 99.0, "name": "贵州茅台",
                  "open": 100.0, "high": 102.0, "low": 99.0,
                  "volume": 1e6, "amount": 1e8}
        o4 = broker2.submit_order("600519", OrderSide.SELL, 100, 101.0, OrderType.LIMIT, q_sell)
        assert o4.status == OrderStatus.FILLED, o4.message
        _ok(f"恢复后续单成交：600519 卖出 100 股 @101.00  已实现盈亏={broker2.positions[0].realized_pnl:+.2f}")

        # ---- 清空 ----
        st2.reset_all()
        acc3 = st2.load_account(cfg.account.initial_cash)
        assert abs(acc3.cash - cfg.account.initial_cash) < 1e-6, acc3.cash
        assert st2.load_positions() == [] and st2.load_orders() == [] and st2.load_trades() == []
        st2.close()
        _ok(f"reset_all：资金回到 {acc3.cash:,.2f}，持仓/订单/成交全部清空")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("    Persist OK\n")


# ============================================================
# [2] 交易视图真实渲染行数
# ============================================================

def test_trade_view_rows() -> None:
    """复现下单序列后，核对三张表渲染行数与单元格内容。"""
    print("[2] 交易视图渲染（offscreen） ...")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtWidgets import QApplication
    from cnstock.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = load_config()
    dm = DataManager(cfg)
    broker = SimBroker(dm, cfg)  # 无 persister
    win = MainWindow(dm, broker, cfg)
    win._status_timer.stop()
    win.trade.timer.stop()
    win.trade.sync_market = lambda: win.trade.refresh()  # 屏蔽联网

    # ---- 复现下单序列 ----
    last_close, prev_close = 1563.70, 1488.98
    q_600519 = {"price": last_close, "prev_close": prev_close, "name": "贵州茅台",
                "open": 1500.0, "high": 1580.0, "low": 1490.0,
                "volume": 3e6, "amount": 4.7e9}
    o1 = broker.submit_order("600519", OrderSide.BUY, 100, 0.0, OrderType.MARKET, q_600519)
    assert o1.status == OrderStatus.FILLED, o1.message

    q_000001 = {"price": 12.50, "prev_close": 12.30, "name": "平安银行",
                "open": 12.30, "high": 12.60, "low": 12.20,
                "volume": 5e7, "amount": 6.3e8}
    o2 = broker.submit_order("000001", OrderSide.BUY, 1000, 12.50, OrderType.LIMIT, q_000001)
    assert o2.status == OrderStatus.FILLED, o2.message

    # 手工注入一笔待成交单，验证委托表能容纳未完成订单
    broker._orders["X-DEMO"] = Order(symbol="300750", name="宁德时代", side=OrderSide.BUY,
                                     quantity=100, order_type=OrderType.LIMIT, price=210.00,
                                     status=OrderStatus.PENDING, order_id="X-DEMO")
    broker.daily_settlement(force=True)

    q_000001_up = {"price": 12.72, "prev_close": 12.30, "name": "平安银行",
                   "open": 12.30, "high": 12.80, "low": 12.20,
                   "volume": 5e7, "amount": 6.4e8}
    o3 = broker.submit_order("000001", OrderSide.SELL, 500, 12.72, OrderType.LIMIT, q_000001_up)
    assert o3.status == OrderStatus.FILLED, o3.message

    broker.refresh_prices({
        "600519": {**q_600519, "price": last_close * 1.006},
        "000001": {**q_000001_up, "price": 12.68},
    })

    # ---- 渲染 ----
    win.trade.refresh()

    pos_rows = win.trade.pos_table.rowCount()
    order_rows = win.trade.order_table.rowCount()
    trade_rows = win.trade.trade_table.rowCount()
    assert pos_rows == 2, f"持仓表应 2 行（600519 + 000001），实际 {pos_rows}"
    assert order_rows == 4, f"委托表应 4 行（600519买/000001买/000001卖/X-DEMO），实际 {order_rows}"
    assert trade_rows == 3, f"成交表应 3 行（2 买 + 1 卖），实际 {trade_rows}"
    _ok(f"渲染行数：持仓={pos_rows} 委托={order_rows} 成交={trade_rows}")

    # ---- 单元格内容抽样：两行持仓都真实存在，不只是行数对 ----
    pos_symbols = {
        win.trade.pos_table.item(r, 0).text() for r in range(pos_rows)
    }
    assert pos_symbols == {"600519", "000001"}, f"持仓代码缺失：{pos_symbols}"
    by_sym = {
        win.trade.pos_table.item(r, 0).text(): {
            "持股": win.trade.pos_table.item(r, 2).text(),
            "可用": win.trade.pos_table.item(r, 3).text(),
            "成本价": win.trade.pos_table.item(r, 4).text(),
            "现价": win.trade.pos_table.item(r, 5).text(),
        }
        for r in range(pos_rows)
    }
    assert by_sym["600519"]["持股"] == "100", by_sym["600519"]
    assert by_sym["000001"]["持股"] == "500", by_sym["000001"]  # 1000 买入 - 500 卖出
    _ok("持仓内容：600519 持股=100 / 000001 持股=500（卖出扣减正确）")

    # 委托表状态分布：3 笔已成交 + 1 笔待成交
    statuses = {win.trade.order_table.item(r, 5).text() for r in range(order_rows)}
    assert OrderStatus.PENDING.value in statuses, f"缺少待成交单：{statuses}"
    assert statuses >= {OrderStatus.FILLED.value, OrderStatus.PENDING.value}, statuses
    _ok(f"委托表状态：{statuses}")

    win.close()
    app.processEvents()
    print("    TradeView OK\n")


# ============================================================
# [3] 数据源失败冷却窗口
# ============================================================

def test_offline_cooldown() -> None:
    """预置失败状态后，realtime() 应立刻快速失败，不白等一个完整超时。"""
    print("[3] 数据源失败冷却 ...")
    cfg = load_config()
    dm = DataManager(cfg)
    dm._mark_fail("模拟：连接被拒")
    assert dm.source_label() == "akshare 离线", dm.source_label()
    _ok(f"source_label 反映真实健康状态：{dm.source_label()}")

    t0 = time.perf_counter()
    try:
        dm.realtime(["600519"])
    except DataError as exc:
        elapsed = time.perf_counter() - t0
        assert "冷却中" in str(exc), str(exc)
        assert elapsed < 0.05, f"冷却分支应即刻返回，实际 {elapsed*1000:.1f}ms"
        _ok(f"冷却窗口内快速失败：{elapsed*1000:.1f}ms — {str(exc)[:48]}")
    else:
        raise AssertionError("冷却窗口内应抛 DataError")

    # 第二次同样快速失败（可重复触发，不会因状态变化而变慢）
    t0 = time.perf_counter()
    try:
        dm.realtime(["600519"])
        raise AssertionError("冷却窗口内应抛 DataError")
    except DataError:
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05, f"第二次应依旧快速失败，实际 {elapsed*1000:.1f}ms"
        _ok(f"重复调用仍快速失败：{elapsed*1000:.1f}ms")

    # 冷却过期后应放行（不再走快速失败分支）——用假实现替代真实取数
    dm._last_fail_ts = time.time() - dm.FAIL_COOLDOWN - 1
    import pandas as pd
    dm._realtime_impl = lambda symbols, force=False: pd.DataFrame(
        [{"symbol": "600519", "price": 100.0}]
    )
    df = dm.realtime(["600519"])
    assert dm._last_ok is True, "冷却过期后成功应标记健康"
    assert dm.source_label() == "akshare 在线", dm.source_label()
    assert df.loc[0, "price"] == 100.0
    _ok(f"冷却过期放行并恢复健康：{dm.source_label()}")
    print("    Cooldown OK\n")


# ============================================================
# [4] 全市场快照缓存：TTL 对齐与降级路径开销
# ============================================================

def _fake_spot_frame() -> "pd.DataFrame":
    """构造一份符合 akshare stock_zh_a_spot_em 输出形态的假数据。"""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "代码": "600519", "名称": "贵州茅台", "最新价": 1680.0,
                "今开": 1660.0, "最高": 1690.0, "最低": 1655.0, "昨收": 1650.0,
                "涨跌额": 30.0, "涨跌幅": 1.82, "成交量": 12345.0,
                "成交额": 2.07e8, "换手率": 0.98,
            },
            {
                "代码": "000001", "名称": "平安银行", "最新价": 11.5,
                "今开": 11.4, "最高": 11.6, "最低": 11.3, "昨收": 11.45,
                "涨跌额": 0.05, "涨跌幅": 0.44, "成交量": 987654.0,
                "成交额": 1.13e9, "换手率": 0.51,
            },
        ]
    )


class _FakeAk:
    """只暴露被测代码会用到的两个接口，并统计各自被调用次数。"""

    def __init__(self, list_ok: bool = True) -> None:
        self.spot_calls = 0
        self.list_calls = 0
        self._list_ok = list_ok

    def stock_zh_a_spot_em(self) -> "pd.DataFrame":
        # 全市场快照：约 5900 只票按 pz=100 分页，一次就是 59 个 HTTP 请求。
        self.spot_calls += 1
        return _fake_spot_frame()

    def stock_info_a_code_name(self) -> "pd.DataFrame":
        import pandas as pd

        self.list_calls += 1
        if not self._list_ok:
            raise RuntimeError("模拟：stock_info_a_code_name 不可用")
        return pd.DataFrame([{"code": "600519", "name": "贵州茅台"},
                             {"code": "000001", "name": "平安银行"}])


def test_snapshot_cache() -> None:
    """快照缓存的 TTL 必须 >= manager 的实时 TTL，否则缓存形同虚设。

    背景：``stock_zh_a_spot_em`` 是分页接口，一次调用约 59 个 HTTP 请求
    （约 5900 只票 / 每页 100 条）。此前 provider 的 ``spot_ttl`` 硬编码 10s，
    而 manager 的 ``realtime_ttl_seconds`` 是 15s——provider 缓存永远先过期，
    每次刷新都真实穿透，一分钟能吃掉 200+ 个请求。
    """
    print("[4] 全市场快照缓存")
    from cnstock.core.config import DataConfig
    from cnstock.data.akshare_provider import AkShareProvider

    cfg = load_config()

    # --- 4.1 配置层面的对齐 ---
    assert cfg.data.spot_ttl_seconds >= cfg.data.realtime_ttl_seconds, (
        f"spot_ttl_seconds({cfg.data.spot_ttl_seconds}) 必须 >= "
        f"realtime_ttl_seconds({cfg.data.realtime_ttl_seconds})，"
        "否则 provider 层缓存会先于 manager 层过期，缓存永远命中不了"
    )
    _ok(
        f"TTL 对齐：spot_ttl={cfg.data.spot_ttl_seconds}s >= "
        f"realtime_ttl={cfg.data.realtime_ttl_seconds}s"
    )

    # --- 4.2 provider 默认值本身也不能小于 manager 默认值 ---
    bare = AkShareProvider()
    assert bare.spot_ttl >= DataConfig().realtime_ttl_seconds, (
        f"provider 默认 spot_ttl({bare.spot_ttl}) 小于 manager 默认 "
        f"realtime_ttl({DataConfig().realtime_ttl_seconds})"
    )
    _ok(f"provider 默认 spot_ttl={bare.spot_ttl}s 不小于 manager 默认 TTL")

    # --- 4.3 TTL 内重复取数应命中缓存，不再发请求 ---
    p = AkShareProvider(timeout=5, spot_ttl=30)
    fake = _FakeAk(list_ok=True)
    p._ak = fake  # 注入假 akshare，避免联网

    df1 = p.realtime(["600519"])
    assert fake.spot_calls == 1, f"首次应真实取数，实际 {fake.spot_calls} 次"
    assert len(df1) == 1 and df1.loc[0, "symbol"] == "600519", df1

    for _ in range(5):
        p.realtime(["600519", "000001"])
    assert fake.spot_calls == 1, (
        f"TTL 内重复取数应命中缓存，但实际又取了 {fake.spot_calls} 次"
    )
    _ok(f"TTL 内 6 次取数只触发 1 次真实请求（省下 5×59 = 295 个 HTTP 请求）")

    # --- 4.4 TTL 过期后应重新取数 ---
    p._spot_ts -= (p.spot_ttl + 1)
    p.realtime(["600519"])
    assert fake.spot_calls == 2, f"TTL 过期后应重新取数，实际 {fake.spot_calls} 次"
    _ok("TTL 过期后正常刷新（缓存不会永久不更新）")
    print("    Snapshot cache OK\n")


def test_stock_list_fallback_cost() -> None:
    """股票列表的降级路径不能比主路径更贵。

    主路径 ``stock_info_a_code_name`` 是 1 次请求；一旦失败，旧实现会回退到
    ``_spot_snapshot()``（59 次分页请求）——降级反而更贵，且主路径失败通常
    意味着网络有问题，此时更应该快速失败而不是转头发一轮更重的请求。
    """
    print("[5] 股票列表降级路径开销")
    from cnstock.data.akshare_provider import AkShareProvider

    # --- 5.1 主路径可用时不碰快照 ---
    p = AkShareProvider(timeout=5, spot_ttl=30)
    fake = _FakeAk(list_ok=True)
    p._ak = fake
    df = p.stock_list()
    assert fake.list_calls == 1 and fake.spot_calls == 0, (
        f"主路径可用时不应触发快照：list={fake.list_calls} spot={fake.spot_calls}"
    )
    assert set(df["symbol"]) == {"600519", "000001"}, df
    _ok("主路径可用：1 次请求，未触发全市场快照")

    # --- 5.2 主路径失败且无缓存快照：应快速失败，绝不触发 59 次请求 ---
    p2 = AkShareProvider(timeout=5, spot_ttl=30)
    fake2 = _FakeAk(list_ok=False)
    p2._ak = fake2
    try:
        p2.stock_list()
    except DataError as exc:
        assert fake2.spot_calls == 0, (
            f"降级路径不得触发全市场快照，实际触发 {fake2.spot_calls} 次"
            "（每次约 59 个 HTTP 请求）"
        )
        assert "不会为此触发全市场快照拉取" in str(exc), str(exc)
        _ok("主路径失败：快速失败，未触发 59 次分页请求")
    else:
        raise AssertionError("主路径失败且无快照时应抛 DataError")

    # --- 5.3 已有内存快照时可零成本复用 ---
    p3 = AkShareProvider(timeout=5, spot_ttl=30)
    fake3 = _FakeAk(list_ok=False)
    p3._ak = fake3
    p3.realtime(["600519"])                 # 先让快照进缓存
    assert fake3.spot_calls == 1
    df3 = p3.stock_list()                   # 再取列表，应直接复用
    assert fake3.spot_calls == 1, (
        f"已有快照应零成本复用，实际又拉了 {fake3.spot_calls} 次"
    )
    assert set(df3["symbol"]) == {"600519", "000001"}, df3
    _ok("已有内存快照时零成本复用（1 次请求拿到代码+名称）")
    print("    Stock list fallback OK\n")


# ============================================================

def main() -> int:
    started = time.perf_counter()
    tests = (
        test_persist_roundtrip,
        test_trade_view_rows,
        test_offline_cooldown,
        test_snapshot_cache,
        test_stock_list_fallback_cost,
    )
    for t in tests:
        t()
    print(f"=== 全部回归测试通过：{PASSED} 项断言，耗时 {time.perf_counter()-started:.2f}s ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\n=== 回归测试失败：{exc} ===")
        sys.exit(1)
