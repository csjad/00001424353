# -*- coding: utf-8 -*-
"""
Tushare Pro 数据源（备源）。

需在设置中填写 token 才会启用；未配置时 ``is_available()`` 返回 False，
调度器会自动跳过而不报错。
"""
from __future__ import annotations

import time
from typing import Iterable

import pandas as pd

from .base import ADJUST_QFQ, DataError, DataProvider, normalize_daily, normalize_realtime


def to_ts_code(symbol: str) -> str:
    """
    6 位代码转 Tushare 证券代码。

    >>> to_ts_code("600519")
    '600519.SH'
    >>> to_ts_code("300750")
    '300750.SZ'
    """
    code = str(symbol).strip()[-6:]
    if code.startswith(("60", "68", "51", "58", "11")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "12", "15", "16", "18")):
        return f"{code}.SZ"
    if code.startswith(("8", "4", "9")):
        return f"{code}.BJ"
    return f"{code}.SH"


def from_ts_code(ts_code: str) -> str:
    """``600519.SH`` -> ``600519``。"""
    return str(ts_code).split(".")[0]


class TushareProvider(DataProvider):
    """Tushare Pro 数据源实现。"""

    name = "tushare"
    support_minute = False            # 分钟线需高额积分，本期不启用

    _DAILY_MAP = {
        "trade_date": "date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
        "amount": "amount",
        "pct_chg": "pct_chg",
    }

    def __init__(self, token: str = "", timeout: int = 20) -> None:
        self.token = (token or "").strip()
        self.timeout = timeout
        self._pro = None
        self._list_df: pd.DataFrame | None = None

    # ---------- 连接 ----------

    @property
    def pro(self):
        if self._pro is None:
            if not self.token:
                raise DataError("Tushare token 未配置")
            try:
                import tushare as ts  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise DataError("未安装 tushare，请执行 pip install -r requirements.txt") from exc
            ts.set_token(self.token)
            self._pro = ts.pro_api(timeout=self.timeout)
        return self._pro

    def is_available(self) -> bool:
        return bool(self.token)

    def reset(self) -> None:
        """token 变更后调用，强制重建连接。"""
        self._pro = None

    # ---------- 历史 K 线 ----------

    def daily(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        adjust: str = ADJUST_QFQ,
        period: str = "daily",
    ) -> pd.DataFrame:
        ts_code = to_ts_code(symbol)
        start = self._fmt_date(start, "19700101")
        end = self._fmt_date(end, time.strftime("%Y%m%d"))

        if period != "daily":
            raise DataError(f"[tushare] 备源暂不支持 {period} 周期（仅日线）")

        try:
            raw = self.pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        except Exception as exc:
            raise DataError(f"[tushare] 获取 {symbol} 日线失败：{exc}") from exc

        if raw is None or raw.empty:
            raise DataError(f"[tushare] {symbol} 无日线数据")

        df = raw.rename(columns=self._DAILY_MAP)

        # tushare amount 单位为「千元」，统一成「元」以对齐 akshare
        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000

        if "turnover" not in df.columns:
            df["turnover"] = pd.NA

        # tushare 返回日期降序，normalize_daily 会统一升序
        return normalize_daily(df)

    # ---------- 实时行情 ----------

    def realtime(self, symbols: Iterable[str]) -> pd.DataFrame:
        wanted = [str(s).strip()[-6:] for s in symbols if s]
        if not wanted:
            return pd.DataFrame(columns=["symbol"])

        ts_codes = ",".join(to_ts_code(s) for s in wanted)

        try:
            raw = self.pro.realtime_quote(ts_code=ts_codes)
        except Exception as exc:
            # realtime_quote 需要较高积分，失败时降级到「最近一个交易日日线」
            return self._realtime_from_daily(wanted, reason=str(exc))

        if raw is None or raw.empty:
            return self._realtime_from_daily(wanted, reason="realtime_quote 返回空")

        df = raw.rename(columns={
            "TS_CODE": "symbol",
            "NAME": "name",
            "PRICE": "price",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "PRE_CLOSE": "prev_close",
            "VOLUME": "volume",
            "AMOUNT": "amount",
            "TRADE_DATE": "date",
        })

        if "symbol" not in df.columns:
            return self._realtime_from_daily(wanted, reason="返回字段缺少 TS_CODE")

        df["symbol"] = df["symbol"].map(from_ts_code)

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce") / 100  # 股 -> 手
        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

        if "change" not in df.columns:
            df["change"] = pd.to_numeric(df.get("price"), errors="coerce") - \
                pd.to_numeric(df.get("prev_close"), errors="coerce")
        if "pct_chg" not in df.columns:
            prev = pd.to_numeric(df.get("prev_close"), errors="coerce")
            df["pct_chg"] = pd.to_numeric(df.get("price"), errors="coerce") / prev * 100 - 100
        if "turnover" not in df.columns:
            df["turnover"] = pd.NA

        return normalize_realtime(df)

    def _realtime_from_daily(self, symbols: list[str], reason: str = "") -> pd.DataFrame:
        """兜底：用最近一个交易日的日线充当实时快照。"""
        rows = []
        for sym in symbols:
            try:
                df = self.daily(sym)
            except DataError:
                continue
            if df.empty:
                continue
            last = df.iloc[-1]
            prev = float(df.iloc[-2]["close"]) if len(df) > 1 else float(last["close"])
            price = float(last["close"])
            rows.append({
                "symbol": sym,
                "name": sym,
                "price": price,
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "prev_close": prev,
                "change": round(price - prev, 4),
                "pct_chg": round((price / prev - 1) * 100, 4) if prev else 0.0,
                "volume": float(last["volume"]),
                "amount": float(last["amount"]),
                "turnover": last.get("turnover"),
            })

        if not rows:
            raise DataError(f"[tushare] 实时行情不可用（{reason}）")
        return normalize_realtime(pd.DataFrame(rows))

    # ---------- 股票列表 ----------

    def stock_list(self) -> pd.DataFrame:
        if self._list_df is not None:
            return self._list_df

        try:
            raw = self.pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,industry,market,list_date",
            )
        except Exception as exc:
            raise DataError(f"[tushare] 获取股票列表失败：{exc}") from exc

        if raw is None or raw.empty:
            raise DataError("[tushare] 股票列表返回为空")

        df = raw.rename(columns={"symbol": "symbol", "name": "name"})
        df["symbol"] = df["symbol"].astype(str).str[-6:]
        self._list_df = df[["symbol", "name"]].drop_duplicates("symbol").reset_index(drop=True)
        return self._list_df

    # ---------- 工具 ----------

    @staticmethod
    def _fmt_date(value: str, default: str) -> str:
        if not value:
            return default
        return str(value).replace("-", "").replace("/", "").strip()
