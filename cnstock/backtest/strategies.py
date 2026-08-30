# -*- coding: utf-8 -*-
"""
内置回测策略库。

每个策略都声明 ``param_spec``（参数表单规范），UI 据此自动生成可调参数控件。

    param_spec = {
        "fast":  (5,  2,  60,  "快线周期"),
        "slow":  (20, 5,  250, "慢线周期"),
    }
    #  (默认值, 最小值, 最大值, 中文标签)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategy import Strategy


# ============================================================
# 1. 买入持有（基准）
# ============================================================


class BuyHoldStrategy(Strategy):
    name = "买入持有（基准）"
    params: dict = {}
    param_spec: dict = {}

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def warmup(self) -> int:
        return 1

    def on_bar(self, ctx) -> None:
        if ctx.i == self.warmup() and ctx.qty == 0:
            ctx.buy(percent=0.98, reason="首日建仓")


# ============================================================
# 2. 双均线交叉
# ============================================================


class MACrossStrategy(Strategy):
    name = "双均线交叉"
    params: dict = {"fast": 5, "slow": 20}
    param_spec: dict = {
        "fast": (5, 2, 60, "快线周期"),
        "slow": (20, 5, 250, "慢线周期"),
    }

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        df = df.copy()
        df["ma_fast"] = df["close"].rolling(fast, min_periods=fast).mean()
        df["ma_slow"] = df["close"].rolling(slow, min_periods=slow).mean()
        return df

    def warmup(self) -> int:
        return int(self.params["slow"]) + 1

    def on_bar(self, ctx) -> None:
        f_now = ctx.indicator("ma_fast")
        f_prev = ctx.indicator("ma_fast", -1)
        s_now = ctx.indicator("ma_slow")
        s_prev = ctx.indicator("ma_slow", -1)
        if np.isnan(f_prev) or np.isnan(s_prev) or np.isnan(f_now) or np.isnan(s_now):
            return
        # 金叉
        if f_prev <= s_prev and f_now > s_now and ctx.qty == 0:
            ctx.buy(percent=0.95, reason="金叉买入")
        # 死叉
        elif f_prev >= s_prev and f_now < s_now and ctx.qty > 0:
            ctx.sell(percent=1.0, reason="死叉卖出")


# ============================================================
# 3. RSI 超买超卖
# ============================================================


class RSICrossStrategy(Strategy):
    name = "RSI 超买超卖"
    params: dict = {"period": 14, "oversold": 30, "overbought": 70}
    param_spec: dict = {
        "period": (14, 2, 60, "RSI 周期"),
        "oversold": (30, 5, 50, "超卖阈值"),
        "overbought": (70, 50, 95, "超买阈值"),
    }

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        period = int(self.params["period"])
        df = df.copy()
        delta = df["close"].diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.rolling(period, min_periods=period).mean()
        avg_loss = loss.rolling(period, min_periods=period).mean()
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)
        df["rsi"] = rsi
        return df

    def warmup(self) -> int:
        return int(self.params["period"]) + 1

    def on_bar(self, ctx) -> None:
        rsi = ctx.indicator("rsi")
        if np.isnan(rsi):
            return
        oversold = float(self.params["oversold"])
        overbought = float(self.params["overbought"])
        if rsi < oversold and ctx.qty == 0:
            ctx.buy(percent=0.95, reason=f"RSI={rsi:.1f} 超卖买入")
        elif rsi > overbought and ctx.qty > 0:
            ctx.sell(percent=1.0, reason=f"RSI={rsi:.1f} 超买卖出")


# ============================================================
# 4. 布林带
# ============================================================


class BollingerStrategy(Strategy):
    name = "布林带通道"
    params: dict = {"period": 20, "k": 2.0}
    param_spec: dict = {
        "period": (20, 5, 120, "均线周期"),
        "k": (2.0, 1.0, 4.0, "标准差倍数"),
    }

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        period = int(self.params["period"])
        k = float(self.params["k"])
        df = df.copy()
        mid = df["close"].rolling(period, min_periods=period).mean()
        std = df["close"].rolling(period, min_periods=period).std(ddof=0)
        df["boll_mid"] = mid
        df["boll_upper"] = mid + k * std
        df["boll_lower"] = mid - k * std
        return df

    def warmup(self) -> int:
        return int(self.params["period"]) + 1

    def on_bar(self, ctx) -> None:
        lower = ctx.indicator("boll_lower")
        upper = ctx.indicator("boll_upper")
        if np.isnan(lower) or np.isnan(upper):
            return
        price = ctx.close
        if price < lower and ctx.qty == 0:
            ctx.buy(percent=0.95, reason="跌破下轨买入")
        elif price > upper and ctx.qty > 0:
            ctx.sell(percent=1.0, reason="突破上轨卖出")


# ============================================================
# 5. MACD
# ============================================================


class MACDStrategy(Strategy):
    name = "MACD 背离"
    params: dict = {"fast": 12, "slow": 26, "signal": 9}
    param_spec: dict = {
        "fast": (12, 5, 60, "快线 EMA"),
        "slow": (26, 10, 120, "慢线 EMA"),
        "signal": (9, 3, 30, "信号 EMA"),
    }

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        signal = int(self.params["signal"])
        df = df.copy()
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        df["macd_dif"] = dif
        df["macd_dea"] = dea
        return df

    def warmup(self) -> int:
        return int(self.params["slow"]) + int(self.params["signal"]) + 2

    def on_bar(self, ctx) -> None:
        dif_now = ctx.indicator("macd_dif")
        dif_prev = ctx.indicator("macd_dif", -1)
        dea_now = ctx.indicator("macd_dea")
        dea_prev = ctx.indicator("macd_dea", -1)
        if any(np.isnan(x) for x in (dif_now, dif_prev, dea_now, dea_prev)):
            return
        if dif_prev <= dea_prev and dif_now > dea_now and ctx.qty == 0:
            ctx.buy(percent=0.95, reason="DIF 上穿 DEA（金叉）")
        elif dif_prev >= dea_prev and dif_now < dea_now and ctx.qty > 0:
            ctx.sell(percent=1.0, reason="DIF 下穿 DEA（死叉）")


# ============================================================
# 6. 网格交易
# ============================================================


class GridStrategy(Strategy):
    name = "网格交易"
    params: dict = {"grid_pct": 0.03, "trade_pct": 0.9}
    param_spec: dict = {
        "grid_pct": (0.03, 0.005, 0.1, "单格涨跌幅"),
        "trade_pct": (0.9, 0.1, 1.0, "每格仓位比例"),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._base: float = 0.0
        self._level: int = 0

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        self._base = float(df.iloc[0]["close"]) if not df.empty else 0.0
        self._level = 0
        return df

    def warmup(self) -> int:
        return 1

    def on_bar(self, ctx) -> None:
        if self._base <= 0:
            self._base = ctx.close
        grid_pct = float(self.params["grid_pct"])
        trade_pct = float(self.params["trade_pct"])
        level = int((ctx.close - self._base) / self._base / grid_pct)

        if level < self._level and ctx.qty == 0:
            ctx.buy(percent=trade_pct, reason=f"跌至第 {level} 格，买入")
        elif level > self._level and ctx.qty > 0:
            ctx.sell(percent=trade_pct, reason=f"涨至第 {level} 格，卖出")
        self._level = level


# ============================================================
# 策略注册表
# ============================================================

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    s.name: s for s in [
        BuyHoldStrategy,
        MACrossStrategy,
        RSICrossStrategy,
        BollingerStrategy,
        MACDStrategy,
        GridStrategy,
    ]
}


def list_strategies() -> list[str]:
    """返回所有内置策略名称。"""
    return list(STRATEGY_REGISTRY.keys())


def get_strategy(name: str, params: dict | None = None) -> Strategy:
    """按名称实例化策略。"""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"未知策略：{name}")
    return cls(**(params or {}))
