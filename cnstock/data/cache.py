# -*- coding: utf-8 -*-
"""
SQLite K 线缓存。

目的：
1. 避免重复网络请求（历史日线基本不变，缓存 7 天足够）
2. 主备源全挂时，用过期缓存兜底，保证界面不白屏
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

from .base import DAILY_COLUMNS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kline (
    symbol      TEXT NOT NULL,
    period      TEXT NOT NULL,
    adjust      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    pct_chg     REAL,
    turnover    REAL,
    updated_at  REAL,
    PRIMARY KEY (symbol, period, adjust, date)
);

CREATE TABLE IF NOT EXISTS kline_meta (
    symbol      TEXT NOT NULL,
    period      TEXT NOT NULL,
    adjust      TEXT NOT NULL,
    last_fetch  REAL,
    row_count   INTEGER,
    PRIMARY KEY (symbol, period, adjust)
);

CREATE INDEX IF NOT EXISTS idx_kline_lookup
    ON kline (symbol, period, adjust, date);
"""


class KlineCache:
    """线程安全的 SQLite 缓存。"""

    def __init__(self, db_file: Path | str) -> None:
        self.db_file = str(db_file)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_db()

    # ---------- 连接 ----------

    def _conn(self) -> sqlite3.Connection:
        """每线程独占连接（SQLite 连接不可跨线程共享）。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_file, timeout=15, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._lock:
            try:
                self._conn().executescript(_SCHEMA)
                self._conn().commit()
            except sqlite3.Error:
                pass  # 磁盘不可写时降级为「不缓存」，不影响主流程

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    # ---------- 读 ----------

    def get(
        self,
        symbol: str,
        period: str = "daily",
        adjust: str = "qfq",
        start: str = "",
        end: str = "",
    ) -> pd.DataFrame | None:
        """读取缓存区间；无数据返回 None。"""
        sql = """
            SELECT date, open, high, low, close, volume, amount, pct_chg, turnover
            FROM kline
            WHERE symbol=? AND period=? AND adjust=?
        """
        params: list = [symbol, period, adjust]

        if start:
            sql += " AND date >= ?"
            params.append(self._norm_date(start))
        if end:
            sql += " AND date <= ?"
            params.append(self._norm_date(end))
        sql += " ORDER BY date ASC"

        with self._lock:
            try:
                df = pd.read_sql_query(sql, self._conn(), params=params)
            except Exception:
                return None

        if df is None or df.empty:
            return None
        return df[DAILY_COLUMNS]

    def last_fetch(self, symbol: str, period: str = "daily", adjust: str = "qfq") -> float:
        with self._lock:
            try:
                cur = self._conn().execute(
                    "SELECT last_fetch FROM kline_meta WHERE symbol=? AND period=? AND adjust=?",
                    (symbol, period, adjust),
                )
                row = cur.fetchone()
                return float(row[0]) if row and row[0] else 0.0
            except Exception:
                return 0.0

    def is_fresh(
        self,
        symbol: str,
        period: str = "daily",
        adjust: str = "qfq",
        ttl_days: int = 7,
    ) -> bool:
        ts = self.last_fetch(symbol, period, adjust)
        return ts > 0 and (time.time() - ts) < ttl_days * 86400

    # ---------- 写 ----------

    def put(self, symbol: str, period: str, adjust: str, df: pd.DataFrame) -> int:
        """写入缓存（REPLACE 语义）。返回受影响行数。"""
        if df is None or df.empty:
            return 0

        now = time.time()
        rows = []
        for _, r in df.iterrows():
            rows.append((
                symbol, period, adjust, str(r["date"]),
                self._f(r.get("open")), self._f(r.get("high")),
                self._f(r.get("low")), self._f(r.get("close")),
                self._f(r.get("volume")), self._f(r.get("amount")),
                self._f(r.get("pct_chg")), self._f(r.get("turnover")),
                now,
            ))

        sql = """
            INSERT OR REPLACE INTO kline
            (symbol, period, adjust, date, open, high, low, close,
             volume, amount, pct_chg, turnover, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._lock:
            try:
                conn = self._conn()
                conn.executemany(sql, rows)
                conn.execute(
                    """INSERT OR REPLACE INTO kline_meta
                       (symbol, period, adjust, last_fetch, row_count) VALUES (?,?,?,?,?)""",
                    (symbol, period, adjust, now, len(rows)),
                )
                conn.commit()
                return len(rows)
            except sqlite3.Error:
                return 0

    def clear(self, symbol: str | None = None) -> int:
        """清空缓存；``symbol`` 为空则清空全部。"""
        with self._lock:
            try:
                conn = self._conn()
                if symbol:
                    cur = conn.execute("DELETE FROM kline WHERE symbol=?", (symbol,))
                    conn.execute("DELETE FROM kline_meta WHERE symbol=?", (symbol,))
                else:
                    cur = conn.execute("DELETE FROM kline")
                    conn.execute("DELETE FROM kline_meta")
                conn.commit()
                return cur.rowcount or 0
            except sqlite3.Error:
                return 0

    # ---------- 工具 ----------

    @staticmethod
    def _f(value) -> float | None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return None if f != f else f      # NaN -> None

    @staticmethod
    def _norm_date(value: str) -> str:
        return str(value).replace("/", "-").strip()
