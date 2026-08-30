# -*- coding: utf-8 -*-
"""
akshare 数据源（主源）。

优点：免费、无需注册、覆盖 A 股全量日线/分钟线/实时快照。
缺点：接口随上游网页变动，需做好异常兜底。

akshare 为重量级依赖，采用**延迟导入**以避免拖慢应用启动。
"""
from __future__ import annotations

import time
from typing import Iterable

import pandas as pd

from .base import (
    ADJUST_QFQ,
    DataError,
    DataProvider,
    normalize_daily,
    normalize_realtime,
)

#: akshare 日线原始列名 -> 统一 schema
_DAILY_MAP = {
    "日期": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_chg",
    "换手率": "turnover",
}

#: akshare 分钟线原始列名 -> 统一 schema
_MINUTE_MAP = {
    "时间": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_chg",
}

#: akshare 东财实时快照原始列名 -> 统一 schema
_SPOT_MAP = {
    "代码": "symbol",
    "名称": "name",
    "最新价": "price",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "昨收": "prev_close",
    "涨跌额": "change",
    "涨跌幅": "pct_chg",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover",
}


class AkShareProvider(DataProvider):
    """akshare 数据源实现。"""

    name = "akshare"
    support_minute = True

    def __init__(self, timeout: int = 20, spot_ttl: int = 10) -> None:
        self.timeout = timeout
        self.spot_ttl = spot_ttl          # 全市场快照缓存秒数
        self._ak = None
        self._spot_df: pd.DataFrame | None = None
        self._spot_ts: float = 0.0
        self._list_df: pd.DataFrame | None = None

    # ---------- 延迟导入 ----------

    @property
    def ak(self):
        if self._ak is None:
            try:
                import akshare  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise DataError(
                    "未安装 akshare，请执行 pip install -r requirements.txt"
                ) from exc
            self._ak = akshare
        return self._ak

    # ---------- 历史 K 线 ----------

    def daily(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        adjust: str = ADJUST_QFQ,
        period: str = "daily",
    ) -> pd.DataFrame:
        symbol = str(symbol).strip()[-6:]
        start = self._fmt_date(start, default="19700101")
        end = self._fmt_date(end, default=time.strftime("%Y%m%d"))

        if period not in ("daily", "weekly", "monthly"):
            period = "daily"

        try:
            raw = self.ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start,
                end_date=end,
                adjust=adjust or "",
            )
        except Exception as exc:
            raise DataError(f"[akshare] 获取 {symbol} 日线失败：{exc}") from exc

        if raw is None or raw.empty:
            raise DataError(f"[akshare] {symbol} 无日线数据（请检查代码或日期区间）")

        df = raw.rename(columns=_DAILY_MAP)
        return normalize_daily(df)

    def minute(
        self,
        symbol: str,
        period: str = "5",
        adjust: str = ADJUST_QFQ,
    ) -> pd.DataFrame:
        symbol = str(symbol).strip()[-6:]
        if period not in {"1", "5", "15", "30", "60"}:
            raise DataError(f"不支持的分钟周期：{period}")

        try:
            raw = self.ak.stock_zh_a_hist_min_em(
                symbol=symbol, period=period, adjust=adjust or ""
            )
        except Exception as exc:
            raise DataError(f"[akshare] 获取 {symbol} {period}分钟线失败：{exc}") from exc

        if raw is None or raw.empty:
            raise DataError(f"[akshare] {symbol} 无分钟线数据")

        df = raw.rename(columns=_MINUTE_MAP)
        if "turnover" not in df.columns:
            df["turnover"] = pd.NA
        return normalize_daily(df)

    # ---------- 实时行情 ----------

    def realtime(self, symbols: Iterable[str]) -> pd.DataFrame:
        wanted = {str(s).strip()[-6:] for s in symbols if s}
        if not wanted:
            return pd.DataFrame(columns=["symbol"])

        df = self._spot_snapshot()
        got = df[df["symbol"].isin(wanted)].copy()

        if got.empty:
            raise DataError(f"[akshare] 未匹配到实时行情：{sorted(wanted)}")
        return got.reset_index(drop=True)

    def _spot_snapshot(self) -> pd.DataFrame:
        """拉取全市场快照（带短时缓存，避免高频请求被限）。"""
        now = time.time()
        if self._spot_df is not None and now - self._spot_ts < self.spot_ttl:
            return self._spot_df

        try:
            raw = self.ak.stock_zh_a_spot_em()
        except Exception as exc:
            # 缓存兜底：上游挂了，返回旧数据而不是直接抛错
            if self._spot_df is not None:
                return self._spot_df
            raise DataError(f"[akshare] 获取实时行情失败：{exc}") from exc

        if raw is None or raw.empty:
            if self._spot_df is not None:
                return self._spot_df
            raise DataError("[akshare] 实时快照返回为空")

        df = raw.rename(columns=_SPOT_MAP)
        df = normalize_realtime(df)
        self._spot_df = df
        self._spot_ts = now
        return df

    # ---------- 股票列表 ----------

    def stock_list(self) -> pd.DataFrame:
        if self._list_df is not None:
            return self._list_df

        try:
            raw = self.ak.stock_info_a_code_name()
            df = raw.rename(columns={"code": "symbol", "name": "name"})
            df["symbol"] = df["symbol"].astype(str).str[-6:]
            df = df[["symbol", "name"]]
        except Exception:
            # 兜底：从实时快照里抽取代码与名称
            try:
                spot = self._spot_snapshot()
                df = spot[["symbol", "name"]].copy()
            except Exception as exc:
                raise DataError(f"[akshare] 获取股票列表失败：{exc}") from exc

        df = df.dropna(subset=["symbol"]).drop_duplicates("symbol")
        self._list_df = df.reset_index(drop=True)
        return self._list_df

    # ---------- 工具 ----------

    @staticmethod
    def _fmt_date(value: str, default: str) -> str:
        """把 ``YYYY-MM-DD`` / ``YYYYMMDD`` 统一成 akshare 需要的 ``YYYYMMDD``。"""
        if not value:
            return default
        return str(value).replace("-", "").replace("/", "").strip()
