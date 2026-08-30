# -*- coding: utf-8 -*-
"""
数据源抽象层。

所有 provider 必须输出**统一 schema**，上层（UI/回测/撮合）只认这个 schema，
从而可以在 akshare 与 Tushare 之间无缝切换。

统一 K 线 schema（英文列名，``date`` 升序）：
    date      日期，``YYYY-MM-DD`` 字符串
    open      开盘价
    high      最高价
    low       最低价
    close     收盘价
    volume    成交量（手）
    amount    成交额（元）
    pct_chg   涨跌幅（%，如 1.23 表示 +1.23%）
    turnover  换手率（%，可能为空）

统一实时快照 schema：
    symbol, name, price, open, high, low, prev_close,
    change, pct_chg, volume, amount, turnover
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd

#: 统一 K 线列顺序
DAILY_COLUMNS: list[str] = [
    "date", "open", "high", "low", "close",
    "volume", "amount", "pct_chg", "turnover",
]

#: 统一实时快照列顺序
REALTIME_COLUMNS: list[str] = [
    "symbol", "name", "price", "open", "high", "low", "prev_close",
    "change", "pct_chg", "volume", "amount", "turnover",
]

#: 复权方式
ADJUST_NONE = ""
ADJUST_QFQ = "qfq"
ADJUST_HFQ = "hfq"


class DataError(Exception):
    """数据源异常（网络失败、接口变动、无数据等）。"""


class DataProvider(ABC):
    """行情数据源基类。"""

    #: provider 标识，用于日志与 UI 展示
    name: str = "base"

    #: 是否支持分钟线
    support_minute: bool = False

    # ---------- 历史 K 线 ----------

    @abstractmethod
    def daily(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        adjust: str = ADJUST_QFQ,
        period: str = "daily",
    ) -> pd.DataFrame:
        """
        获取日线数据。

        :param symbol: 6 位代码，如 ``600519``
        :param start: 开始日期 ``YYYYMMDD``，空表示上市首日
        :param end: 结束日期 ``YYYYMMDD``，空表示最新
        :param adjust: 复权方式，见 ``ADJUST_*``
        :param period: ``daily`` / ``weekly`` / ``monthly``
        :return: 统一 schema 的 DataFrame，``date`` 升序
        :raises DataError: 获取失败
        """

    def minute(
        self,
        symbol: str,
        period: str = "5",
        adjust: str = ADJUST_QFQ,
    ) -> pd.DataFrame:
        """
        获取分钟线数据（可选实现）。

        :param period: ``1``/``5``/``15``/``30``/``60``
        """
        raise NotImplementedError(f"{self.name} 不支持分钟线")

    # ---------- 实时行情 ----------

    @abstractmethod
    def realtime(self, symbols: Iterable[str]) -> pd.DataFrame:
        """
        获取实时快照。

        :param symbols: 6 位代码列表
        :return: 统一 schema 的 DataFrame，索引为 symbol
        :raises DataError: 获取失败
        """

    # ---------- 基础信息 ----------

    @abstractmethod
    def stock_list(self) -> pd.DataFrame:
        """
        获取 A 股股票列表。

        :return: 至少包含 ``symbol``、``name`` 两列的 DataFrame
        """

    # ---------- 可用性 ----------

    def is_available(self) -> bool:
        """provider 是否可用（Tushare 未配置 token 时应返回 False）。"""
        return True


def normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    将任意 DataFrame 规整为统一日线 schema。

    - 缺失列补 0 / NaN
    - ``date`` 统一为 ``YYYY-MM-DD`` 字符串
    - 数值列强制转 float
    - 按日期升序、去重
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    out = df.copy()

    for col in DAILY_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    # 日期规整
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")

    # 数值规整
    for col in DAILY_COLUMNS[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[DAILY_COLUMNS]
    out = out.drop_duplicates(subset=["date"], keep="last")
    out = out.sort_values("date").reset_index(drop=True)
    return out


def normalize_realtime(df: pd.DataFrame) -> pd.DataFrame:
    """将任意 DataFrame 规整为统一实时快照 schema。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=REALTIME_COLUMNS)

    out = df.copy()
    for col in REALTIME_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out["symbol"] = out["symbol"].astype(str).str[-6:]
    for col in REALTIME_COLUMNS[1:]:
        if col != "name":
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[REALTIME_COLUMNS]
    out = out.drop_duplicates(subset=["symbol"], keep="last")
    return out.reset_index(drop=True)
