# -*- coding: utf-8 -*-
"""
数据源调度器。

对外提供统一入口，内部按以下优先级降级，任何一层成功即返回：

    1. 新鲜缓存（SQLite，TTL 内）
    2. 主源（默认 akshare）
    3. 备源（默认 tushare）
    4. 过期缓存（保证界面不白屏，同时记录告警）

全部失败才抛出 ``DataError``。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd

from ..core.config import AppConfig, db_path, load_config
from .akshare_provider import AkShareProvider
from .base import ADJUST_QFQ, DataError, DataProvider
from .cache import KlineCache
from .tushare_provider import TushareProvider

logger = logging.getLogger(__name__)


class DataManager:
    """行情数据统一调度入口。"""

    #: 实时行情连续失败后的快速失败冷却窗口（秒）
    FAIL_COOLDOWN: int = 10

    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        dcfg = self.cfg.data

        self.cache = KlineCache(db_path().parent / "kline.db")

        self.providers: dict[str, DataProvider] = {
            "akshare": AkShareProvider(
                timeout=dcfg.request_timeout,
                spot_ttl=dcfg.spot_ttl_seconds,
            ),
            "tushare": TushareProvider(token=dcfg.tushare_token, timeout=dcfg.request_timeout),
        }

        self.primary = self.providers.get(dcfg.primary) or self.providers["akshare"]
        self.fallback = self.providers.get(dcfg.fallback)

        # 实时快照内存短缓存
        self._rt_cache: pd.DataFrame | None = None
        self._rt_ts: float = 0.0
        # 股票列表缓存
        self._list_cache: pd.DataFrame | None = None
        self._list_ts: float = 0.0

        # 最近一次取数的健康状态：None=尚未尝试 / True=成功 / False=失败
        self._last_ok: bool | None = None
        self._last_error: str = ""
        self._last_fail_ts: float = 0.0

    # ============================================================
    # 配置热更新
    # ============================================================

    def reload_config(self, cfg: AppConfig | None = None) -> None:
        """配置变更后重建 provider（例如用户填入了 Tushare token）。"""
        self.cfg = cfg or load_config()
        dcfg = self.cfg.data
        ts: TushareProvider = self.providers["tushare"]  # type: ignore[assignment]
        if ts.token != dcfg.tushare_token:
            ts.token = dcfg.tushare_token
            ts.reset()
        # 快照 TTL 也可热更新（改配置后无需重启）
        ak: AkShareProvider = self.providers["akshare"]  # type: ignore[assignment]
        ak.spot_ttl = dcfg.spot_ttl_seconds
        self.primary = self.providers.get(dcfg.primary) or self.providers["akshare"]
        self.fallback = self.providers.get(dcfg.fallback)

    # ============================================================
    # 历史 K 线
    # ============================================================

    def daily(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        adjust: str = ADJUST_QFQ,
        use_cache: bool = True,
        period: str = "daily",
    ) -> pd.DataFrame:
        """取日线，并同步记录数据源健康状态。"""
        try:
            df = self._daily_impl(symbol, start, end, adjust, use_cache, period)
        except Exception as exc:
            self._mark_fail(str(exc))
            raise
        self._mark_ok()
        return df

    def _daily_impl(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        adjust: str = ADJUST_QFQ,
        use_cache: bool = True,
        period: str = "daily",
    ) -> pd.DataFrame:
        symbol = self._sym(symbol)
        if period not in ("daily", "weekly", "monthly"):
            period = "daily"
        dcfg = self.cfg.data

        # 1) 新鲜缓存
        if use_cache and dcfg.cache_enabled:
            if self.cache.is_fresh(symbol, period, adjust, dcfg.cache_ttl_days):
                df = self.cache.get(symbol, period, adjust, start, end)
                if df is not None and not df.empty:
                    logger.debug("缓存命中 %s %s", symbol, period)
                    return df

        # 2) 主源
        if self.primary.is_available():
            try:
                df = self.primary.daily(symbol, start, end, adjust, period)
                if df is not None and not df.empty:
                    if use_cache and dcfg.cache_enabled:
                        self.cache.put(symbol, period, adjust, df)
                    return df
            except DataError as exc:
                logger.warning("主源 %s 失败：%s", self.primary.name, exc)
            except Exception as exc:  # 兜底，避免未知异常炸掉 UI
                logger.exception("主源 %s 异常：%s", self.primary.name, exc)
        else:
            logger.info("主源 %s 不可用，直接走备源", self.primary.name)

        # 3) 备源
        if self.fallback is not None and self.fallback.is_available():
            try:
                df = self.fallback.daily(symbol, start, end, adjust, period)
                if df is not None and not df.empty:
                    if use_cache and dcfg.cache_enabled:
                        self.cache.put(symbol, period, adjust, df)
                    return df
            except DataError as exc:
                logger.warning("备源 %s 失败：%s", self.fallback.name, exc)
            except Exception as exc:
                logger.exception("备源 %s 异常：%s", self.fallback.name, exc)

        # 4) 过期缓存兜底
        df = self.cache.get(symbol, period, adjust, start, end)
        if df is not None and not df.empty:
            logger.warning("%s 全部数据源失败，使用过期缓存", symbol)
            return df

        raise DataError(
            f"获取 {symbol} 日线失败：主源({self.primary.name})与备源均已失效，且无缓存"
        )

    def minute(
        self,
        symbol: str,
        period: str = "5",
        adjust: str = ADJUST_QFQ,
    ) -> pd.DataFrame:
        symbol = self._sym(symbol)
        for provider in (self.primary, self.fallback):
            if provider is None or not provider.support_minute:
                continue
            if not provider.is_available():
                continue
            try:
                df = provider.minute(symbol, period, adjust)
                if df is not None and not df.empty:
                    return df
            except DataError as exc:
                logger.warning("%s 分钟线失败：%s", provider.name, exc)
        raise DataError(f"{symbol} 分钟线不可用（当前数据源不支持或请求失败）")

    # ============================================================
    # 实时行情
    # ============================================================

    def realtime(self, symbols: Iterable[str], force: bool = False) -> pd.DataFrame:
        """取实时行情，并同步记录数据源健康状态。

        连续失败后进入 ``FAIL_COOLDOWN`` 秒的快速失败窗口：期间除显式
        ``force=True`` 外直接抛错，不再重复发起注定失败的网络请求。
        否则离线时每次自动刷新 / 下单都会白等一个完整超时，界面看起来像卡死。
        """
        now = time.time()
        if (
            not force
            and self._last_ok is False
            and self._last_error
            and (now - self._last_fail_ts) < self.FAIL_COOLDOWN
        ):
            raise DataError(
                f"数据源 {self.primary.name} 离线，{self.FAIL_COOLDOWN}s 冷却中"
                f"（上次失败：{self._last_error[:60]}）"
            )
        try:
            df = self._realtime_impl(symbols, force)
        except Exception as exc:
            self._mark_fail(str(exc))
            raise
        self._mark_ok()
        return df

    def _realtime_impl(self, symbols: Iterable[str], force: bool = False) -> pd.DataFrame:
        wanted = [self._sym(s) for s in symbols if s]
        if not wanted:
            return pd.DataFrame()

        ttl = self.cfg.data.realtime_ttl_seconds
        if not force and self._rt_cache is not None and (time.time() - self._rt_ts) < ttl:
            cached = self._rt_cache
            if set(wanted).issubset(set(cached["symbol"])):
                return cached[cached["symbol"].isin(wanted)].reset_index(drop=True)

        for provider in (self.primary, self.fallback):
            if provider is None or not provider.is_available():
                continue
            try:
                df = provider.realtime(wanted)
                if df is not None and not df.empty:
                    self._merge_rt_cache(df)
                    return df.reset_index(drop=True)
            except DataError as exc:
                logger.warning("%s 实时行情失败：%s", provider.name, exc)
            except Exception as exc:
                logger.exception("%s 实时行情异常：%s", provider.name, exc)

        # 兜底：过期内存缓存
        if self._rt_cache is not None and not self._rt_cache.empty:
            cached = self._rt_cache
            hit = cached[cached["symbol"].isin(wanted)]
            if not hit.empty:
                return hit.reset_index(drop=True)

        raise DataError("实时行情获取失败：所有数据源均不可用")

    def _merge_rt_cache(self, df: pd.DataFrame) -> None:
        if self._rt_cache is None or self._rt_cache.empty:
            self._rt_cache = df.copy()
        else:
            merged = pd.concat([self._rt_cache, df], ignore_index=True)
            self._rt_cache = merged.drop_duplicates(subset=["symbol"], keep="last")
        self._rt_ts = time.time()

    # ============================================================
    # 股票列表与搜索
    # ============================================================

    def stock_list(self, force: bool = False) -> pd.DataFrame:
        if not force and self._list_cache is not None and (time.time() - self._list_ts) < 3600:
            return self._list_cache

        for provider in (self.primary, self.fallback):
            if provider is None or not provider.is_available():
                continue
            try:
                df = provider.stock_list()
                if df is not None and not df.empty:
                    self._list_cache = df
                    self._list_ts = time.time()
                    return df
            except DataError as exc:
                logger.warning("%s 股票列表失败：%s", provider.name, exc)

        if self._list_cache is not None:
            return self._list_cache
        raise DataError("股票列表获取失败：所有数据源均不可用")

    def search(self, keyword: str, limit: int = 20) -> list[tuple[str, str]]:
        """
        按代码前缀或名称关键字搜索。

        :return: ``[(symbol, name), ...]``
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        try:
            df = self.stock_list()
        except DataError:
            return []

        mask = df["symbol"].str.startswith(kw) | df["name"].str.contains(kw, na=False)
        hit = df[mask].head(limit)
        return [(str(r.symbol), str(r.name)) for r in hit.itertuples()]

    # ============================================================
    # 状态与维护
    # ============================================================

    def provider_status(self) -> dict[str, bool]:
        """配置层面的可用性（是否已安装/已配置 token），不代表网络连通。"""
        return {name: p.is_available() for name, p in self.providers.items()}

    # ============================================================
    # 数据源健康状态（供状态栏展示真实联网情况）
    # ============================================================

    def _mark_ok(self) -> None:
        self._last_ok = True
        self._last_error = ""
        self._last_fail_ts = 0.0

    def _mark_fail(self, msg: str) -> None:
        self._last_ok = False
        self._last_error = msg
        self._last_fail_ts = time.time()

    def source_label(self) -> str:
        """
        返回人类可读的数据源状态文案。

        与 ``provider_status()`` 的区别：后者只反映"是否配置"，
        这里反映**最近一次真实取数是成功还是失败**，避免离线时状态栏仍显示"可用"。
        """
        name = self.primary.name
        if self._last_ok is None:
            return f"{name} 待测试"
        if self._last_ok:
            return f"{name} 在线"
        return f"{name} 离线"

    @property
    def last_error(self) -> str:
        return self._last_error

    def clear_cache(self, symbol: str | None = None) -> int:
        self._rt_cache = None
        self._rt_ts = 0.0
        return self.cache.clear(symbol)

    def shutdown(self) -> None:
        self.cache.close()

    @staticmethod
    def _sym(symbol: str) -> str:
        return str(symbol).strip()[-6:]
