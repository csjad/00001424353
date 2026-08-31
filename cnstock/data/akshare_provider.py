# -*- coding: utf-8 -*-
"""
akshare 数据源（主源）。

优点：免费、无需注册、覆盖 A 股全量日线/分钟线/实时快照。
缺点：接口随上游网页变动，需做好异常兜底。

akshare 为重量级依赖，采用**延迟导入**以避免拖慢应用启动。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd

from .base import (
    ADJUST_QFQ,
    DataError,
    DataProvider,
    REALTIME_COLUMNS,
    normalize_daily,
    normalize_realtime,
    run_with_timeout,
)

logger = logging.getLogger(__name__)

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

#: akshare 东财单票实时（stock_bid_ask_em）长表的 item -> 统一 schema。
#: 该接口以 item/value 两列返回一只票的全量盘口与实时字段，逐只调用只发
#: 1 个 HTTP 请求，远轻于全市场快照（一次约 59 个分页请求）。
_BID_ASK_MAP = {
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

    def __init__(self, timeout: int = 20, spot_ttl: int = 30) -> None:
        self.timeout = timeout
        #: 全市场快照缓存秒数。**必须 >= DataManager 的
        #: ``realtime_ttl_seconds``（默认 15）**，否则每次刷新都会穿透到网络——
        #: 而一次 ``stock_zh_a_spot_em`` 就是约 59 个分页 HTTP 请求。
        #: 真正生效的值由 ``DataManager`` 从 ``DataConfig.spot_ttl_seconds`` 传入。
        self.spot_ttl = spot_ttl
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
            raw = run_with_timeout(
                self.ak.stock_zh_a_hist,
                self.timeout,
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
            raw = run_with_timeout(
                self.ak.stock_zh_a_hist_min_em,
                self.timeout,
                symbol=symbol, period=period, adjust=adjust or "",
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

    def realtime_per_symbol(self, symbols: Iterable[str]) -> pd.DataFrame:
        """单票实时快路径（默认关闭，由 manager 按配置决定是否调用）。

        逐只调用东财 ``stock_bid_ask_em``（每只 1 个 HTTP 请求），避免拉全市场
        快照（``stock_zh_a_spot_em`` 一次约 59 个分页请求）。仅对少量自选股有意义。

        **安全保证**：任意异常或空返回，一律回退到 ``realtime()``（全市场快照），
        因此开启它绝不会比现状更差——最坏情况就是退回到今天的默认行为。
        名称优先从股票列表缓存（``_list_df``）取，长表里的「名称」字段兜底。
        """
        wanted = [str(s).strip()[-6:] for s in symbols if s]
        if not wanted:
            return pd.DataFrame(columns=REALTIME_COLUMNS)

        rows: list[dict] = []
        for sym in wanted:
            try:
                raw = run_with_timeout(
                    self.ak.stock_bid_ask_em, self.timeout, symbol=sym
                )
            except Exception as exc:
                logger.warning(
                    "[akshare] 单票 %s 实时失败，回退全市场快照：%s", sym, exc
                )
                return self.realtime(wanted)
            if raw is None or raw.empty:
                return self.realtime(wanted)
            row = self._bid_ask_row(raw, sym)
            if row is None:
                return self.realtime(wanted)
            rows.append(row)

        if not rows:
            return self.realtime(wanted)
        return normalize_realtime(pd.DataFrame(rows))

    def _bid_ask_row(self, raw: pd.DataFrame, sym: str) -> "dict | None":
        """把 ``stock_bid_ask_em`` 的 item/value 长表解析成一行统一 schema。

        返回 ``None`` 表示长表形态不可识别，调用方应据此回退到全市场快照。
        """
        if raw is None or "item" not in raw.columns or "value" not in raw.columns:
            return None

        kv = dict(zip(raw["item"].astype(str), raw["value"]))
        row: dict = {"symbol": sym}
        for cn, en in _BID_ASK_MAP.items():
            row[en] = kv.get(cn)

        # 名称优先用股票列表缓存（更可靠，启动时已拉过）；长表也有「名称」字段时兜底
        name = None
        if self._list_df is not None and not self._list_df.empty:
            hit = self._list_df[self._list_df["symbol"] == sym]
            if not hit.empty:
                name = hit.iloc[0]["name"]
        if name is None:
            name = kv.get("名称")
        row["name"] = name
        return row

    def _spot_snapshot(self) -> pd.DataFrame:
        """拉取全市场快照（带短时缓存，避免高频请求被限）。"""
        now = time.time()
        if self._spot_df is not None and now - self._spot_ts < self.spot_ttl:
            return self._spot_df

        try:
            raw = run_with_timeout(self.ak.stock_zh_a_spot_em, self.timeout)
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
            raw = run_with_timeout(self.ak.stock_info_a_code_name, self.timeout)
            df = raw.rename(columns={"code": "symbol", "name": "name"})
            df["symbol"] = df["symbol"].astype(str).str[-6:]
            df = df[["symbol", "name"]]
        except Exception:
            # 兜底：复用**已在内存中**的实时快照抽取代码与名称。
            #
            # 注意这里刻意不去调 _spot_snapshot() 触发新的网络请求：
            # stock_zh_a_spot_em 是约 5900 只票按每页 100 条分页的接口，
            # 拉一次就是 59 个 HTTP 请求，远比主路径 stock_info_a_code_name
            # （1 次请求）昂贵。降级路径比主路径还贵是不合理的——
            # 主路径失败时通常意味着网络有问题，此时更应该快速失败，
            # 而不是转头去发起一轮更重的请求。
            #
            # 若快照此前已被 realtime() 拉过（缓存未过期），这里可以零成本复用。
            if self._spot_df is not None and not self._spot_df.empty:
                df = self._spot_df[["symbol", "name"]].copy()
            else:
                raise DataError(
                    "[akshare] 获取股票列表失败：stock_info_a_code_name 不可用，"
                    "且无内存快照可复用（不会为此触发全市场快照拉取）"
                ) from None

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
