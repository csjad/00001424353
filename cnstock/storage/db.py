# -*- coding: utf-8 -*-
"""
SQLite 持久化。

- ``account`` 表：账户资金（单行）
- ``positions`` 表：持仓（按 symbol）
- ``orders`` / ``trades`` 表：订单与成交（JSON 整存，便于保持字段完整）

实现 ``engine.broker.Persister`` 协议，可直接传给 ``SimBroker``。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..core.config import db_path
from ..core.models import Account, Order, Position, Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    total_qty   INTEGER,
    locked_qty  INTEGER,
    avg_cost    REAL,
    last_price  REAL,
    realized_pnl REAL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    data     TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT,
    data     TEXT
);
"""


class SqliteStorage:
    """账户持久化（线程安全）。"""

    def __init__(self, db_file: Path | str | None = None) -> None:
        self.db_file = str(db_file or db_path())
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_db()

    # ---------- 连接 ----------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_file, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._lock:
            try:
                self._conn().executescript(_SCHEMA)
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    # ---------- 账户 ----------

    def save_account(self, account: Account) -> None:
        with self._lock:
            try:
                self._conn().execute(
                    "INSERT OR REPLACE INTO account (key, value) VALUES (?, ?)",
                    ("initial_cash", account.initial_cash),
                )
                self._conn().execute(
                    "INSERT OR REPLACE INTO account (key, value) VALUES (?, ?)",
                    ("cash", account.cash),
                )
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    def load_account(self, default_initial: float = 1_000_000.0) -> Account:
        with self._lock:
            try:
                cur = self._conn().execute("SELECT key, value FROM account")
                rows = {r["key"]: r["value"] for r in cur.fetchall()}
            except sqlite3.Error:
                rows = {}

        if not rows:
            return Account(initial_cash=default_initial, cash=default_initial)

        initial = float(rows.get("initial_cash", default_initial))
        cash = float(rows.get("cash", initial))
        return Account(initial_cash=initial, cash=cash)

    # ---------- 持仓 ----------

    def save_position(self, pos: Position) -> None:
        with self._lock:
            try:
                self._conn().execute(
                    """INSERT OR REPLACE INTO positions
                       (symbol, name, total_qty, locked_qty, avg_cost, last_price,
                        realized_pnl, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (pos.symbol, pos.name, pos.total_qty, pos.locked_qty,
                     pos.avg_cost, pos.last_price, pos.realized_pnl, pos.updated_at),
                )
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    def load_positions(self) -> list[Position]:
        with self._lock:
            try:
                cur = self._conn().execute(
                    "SELECT * FROM positions WHERE total_qty > 0"
                )
                return [Position.from_dict(dict(r)) for r in cur.fetchall()]
            except sqlite3.Error:
                return []

    def delete_position(self, symbol: str) -> None:
        with self._lock:
            try:
                self._conn().execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    # ---------- 订单 / 成交 ----------

    def save_order(self, order: Order) -> None:
        with self._lock:
            try:
                self._conn().execute(
                    "INSERT OR REPLACE INTO orders (order_id, data) VALUES (?, ?)",
                    (order.order_id, json.dumps(order.to_dict(), ensure_ascii=False)),
                )
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    def load_orders(self) -> list[Order]:
        with self._lock:
            try:
                cur = self._conn().execute("SELECT data FROM orders")
                return [Order.from_dict(json.loads(r["data"])) for r in cur.fetchall()]
            except sqlite3.Error:
                return []

    def save_trade(self, trade: Trade) -> None:
        with self._lock:
            try:
                self._conn().execute(
                    "INSERT OR REPLACE INTO trades (trade_id, order_id, data) VALUES (?, ?, ?)",
                    (trade.trade_id, trade.order_id,
                     json.dumps(trade.to_dict(), ensure_ascii=False)),
                )
                self._conn().commit()
            except sqlite3.Error:  # pragma: no cover
                pass

    def load_trades(self) -> list[Trade]:
        with self._lock:
            try:
                cur = self._conn().execute("SELECT data FROM trades ORDER BY trade_id")
                return [Trade.from_dict(json.loads(r["data"])) for r in cur.fetchall()]
            except sqlite3.Error:
                return []

    # ---------- 维护 ----------

    def reset_all(self) -> None:
        """清空账户、持仓、订单、成交（保留 K 线缓存）。"""
        with self._lock:
            try:
                conn = self._conn()
                conn.execute("DELETE FROM account")
                conn.execute("DELETE FROM positions")
                conn.execute("DELETE FROM orders")
                conn.execute("DELETE FROM trades")
                conn.commit()
            except sqlite3.Error:  # pragma: no cover
                pass
