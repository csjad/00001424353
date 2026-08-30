# -*- coding: utf-8 -*-
"""
A 股交易规则常量。

所有费率、涨跌停、T+1 等规则集中在此，避免散落在撮合/回测/UI 各处导致口径不一致。
修改本文件即全局生效。
"""
from __future__ import annotations

from enum import Enum
from typing import Final

# ============================================================
# 交易费用（截至 2026 年现行标准）
# ============================================================

#: 佣金费率（万分之 2.5 为行业常见水平，可在设置中调整）
COMMISSION_RATE: Final[float] = 0.00025
#: 单笔佣金最低收取（元），不足按此收取
COMMISSION_MIN: Final[float] = 5.0
#: 印花税率（2023-08-28 起减半征收，仅卖出方收取）
STAMP_DUTY_RATE: Final[float] = 0.0005
#: 过户费率（2022-04-29 起沪深两市统一，双边收取）
TRANSFER_FEE_RATE: Final[float] = 0.00001
#: 回测默认滑点（按成交金额比例）
DEFAULT_SLIPPAGE: Final[float] = 0.0002

# ============================================================
# 交易单位与制度
# ============================================================

#: 最小买入单位（股）—— A 股买入必须为 100 股整数倍
LOT_SIZE: Final[int] = 100
#: T+1 制度：当日买入的股份当日不可卖出
T_PLUS_ONE: Final[bool] = True

# ============================================================
# 涨跌停限制
# ============================================================

#: 主板涨跌幅限制（60xxxx / 00xxxx 非 ST）
LIMIT_MAIN: Final[float] = 0.10
#: ST / *ST 股票涨跌幅限制
LIMIT_ST: Final[float] = 0.05
#: 创业板（30xxxx）与科创板（688xxx）涨跌幅限制
LIMIT_GROWTH: Final[float] = 0.20
#: 北交所涨跌幅限制
LIMIT_BSE: Final[float] = 0.30

# ============================================================
# 交易时段（北京时间）
# ============================================================

MARKET_OPEN_MORNING: Final[str] = "09:30"
MARKET_CLOSE_MORNING: Final[str] = "11:30"
MARKET_OPEN_AFTERNOON: Final[str] = "13:00"
MARKET_CLOSE_AFTERNOON: Final[str] = "15:00"


class Board(str, Enum):
    """股票所属板块。"""

    SH_MAIN = "沪市主板"      # 60xxxx
    SZ_MAIN = "深市主板"      # 00xxxx
    GEM = "创业板"            # 30xxxx
    STAR = "科创板"           # 688xxx
    BSE = "北交所"            # 8xxxxx / 4xxxxx
    UNKNOWN = "未知"


class OrderSide(str, Enum):
    BUY = "买入"
    SELL = "卖出"


class OrderType(str, Enum):
    MARKET = "市价"
    LIMIT = "限价"


class OrderStatus(str, Enum):
    PENDING = "待成交"
    PARTIAL = "部分成交"
    FILLED = "已成交"
    CANCELLED = "已撤单"
    REJECTED = "已拒绝"


class Period(str, Enum):
    """K 线周期。"""

    DAILY = "日线"
    WEEKLY = "周线"
    MONTHLY = "月线"
    MIN_60 = "60分钟"
    MIN_30 = "30分钟"
    MIN_15 = "15分钟"
    MIN_5 = "5分钟"


# ============================================================
# 板块判定与涨跌停计算
# ============================================================


def detect_board(symbol: str, name: str = "") -> Board:
    """
    根据证券代码判定所属板块。

    :param symbol: 6 位证券代码，如 ``600519``
    :param name: 证券简称，用于识别 ST 股（ST 不改变板块但影响涨跌停）
    """
    code = str(symbol).strip()[-6:]
    if code.startswith("60"):
        return Board.SH_MAIN
    if code.startswith("688"):
        return Board.STAR
    if code.startswith("30"):
        return Board.GEM
    if code.startswith("00"):
        return Board.SZ_MAIN
    if code.startswith(("8", "4", "9")):
        return Board.BSE
    return Board.UNKNOWN


def is_st(name: str) -> bool:
    """判断是否为 ST / *ST 股票（ST 股涨跌停限制为 ±5%）。"""
    if not name:
        return False
    upper = str(name).upper().replace(" ", "")
    return "ST" in upper


def price_limit(symbol: str, name: str, prev_close: float) -> tuple[float, float]:
    """
    计算涨跌停价格。

    :param symbol: 6 位证券代码
    :param name: 证券简称（用于 ST 判定）
    :param prev_close: 前收盘价
    :return: ``(涨停价, 跌停价)``，保留 2 位小数
    """
    if prev_close <= 0:
        return 0.0, 0.0

    board = detect_board(symbol, name)

    if is_st(name):
        rate = LIMIT_ST
    elif board in (Board.GEM, Board.STAR):
        rate = LIMIT_GROWTH
    elif board == Board.BSE:
        rate = LIMIT_BSE
    else:
        rate = LIMIT_MAIN

    up = round(prev_close * (1 + rate), 2)
    down = round(prev_close * (1 - rate), 2)
    return up, down


def round_price(price: float) -> float:
    """价格规范化：A 股报价最小单位为 0.01 元。"""
    return round(float(price), 2)


def buy_fee(
    amount: float,
    commission_rate: float = COMMISSION_RATE,
    commission_min: float = COMMISSION_MIN,
    transfer_fee_rate: float = TRANSFER_FEE_RATE,
) -> dict[str, float]:
    """
    计算买入总费用。

    :return: ``{commission, stamp_duty, transfer_fee, total}``
    """
    commission = max(amount * commission_rate, commission_min) if amount > 0 else 0.0
    stamp_duty = 0.0                                    # 买入免征印花税
    transfer_fee = amount * transfer_fee_rate           # 过户费双边
    total = commission + stamp_duty + transfer_fee
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "total": total,
    }


def sell_fee(
    amount: float,
    commission_rate: float = COMMISSION_RATE,
    commission_min: float = COMMISSION_MIN,
    stamp_duty_rate: float = STAMP_DUTY_RATE,
    transfer_fee_rate: float = TRANSFER_FEE_RATE,
) -> dict[str, float]:
    """
    计算卖出总费用。

    :return: ``{commission, stamp_duty, transfer_fee, total}``
    """
    commission = max(amount * commission_rate, commission_min) if amount > 0 else 0.0
    stamp_duty = amount * stamp_duty_rate               # 卖出单边征收
    transfer_fee = amount * transfer_fee_rate
    total = commission + stamp_duty + transfer_fee
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "total": total,
    }


def is_trading_time(now=None) -> bool:
    """
    判断当前是否处于 A 股连续竞价时段（不考虑节假日）。

    :param now: ``datetime.time``，默认取当前本地时间
    """
    from datetime import datetime, time

    if now is None:
        now = datetime.now().time()
    morning = time(9, 30) <= now <= time(11, 30)
    afternoon = time(13, 0) <= now <= time(15, 0)
    return morning or afternoon
