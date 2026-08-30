# -*- coding: utf-8 -*-
"""
应用配置管理。

配置文件存放于 ``~/.cn-stock-desktop/config.json``，首次运行自动生成默认配置。
打包成 exe 后同样写入用户目录，保证可写且不受安装位置影响。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from .constants import (
    COMMISSION_MIN,
    COMMISSION_RATE,
    DEFAULT_SLIPPAGE,
    STAMP_DUTY_RATE,
    TRANSFER_FEE_RATE,
)

#: 应用名称（同时作为配置目录名）
APP_NAME: str = "cn-stock-desktop"


def user_data_dir() -> Path:
    """返回用户数据目录（跨平台）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class FeeConfig:
    """手续费配置。"""

    commission_rate: float = COMMISSION_RATE
    commission_min: float = COMMISSION_MIN
    stamp_duty_rate: float = STAMP_DUTY_RATE
    transfer_fee_rate: float = TRANSFER_FEE_RATE
    slippage: float = DEFAULT_SLIPPAGE


@dataclass
class DataConfig:
    """数据源与缓存配置。"""

    primary: str = "akshare"           # akshare | tushare
    fallback: str = "tushare"
    tushare_token: str = ""            # Tushare Pro token（可选）
    cache_enabled: bool = True
    cache_ttl_days: int = 7            # 历史数据缓存有效期
    realtime_ttl_seconds: int = 15     # 实时快照缓存有效期
    request_timeout: int = 20


@dataclass
class AccountConfig:
    """模拟账户配置。"""

    initial_cash: float = 1_000_000.0
    allow_short: bool = False          # 是否允许卖空（A 股不允许）
    enforce_t1: bool = True            # 是否严格执行 T+1


@dataclass
class UIConfig:
    """界面配置。"""

    theme: str = "dark"
    up_color: str = "#F5222D"          # 涨 —— 红（A 股约定）
    down_color: str = "#00B96B"        # 跌 —— 绿（A 股约定）
    flat_color: str = "#8C8C8C"
    font_family: str = "Microsoft YaHei UI"
    font_size: int = 9
    auto_refresh: bool = True
    refresh_interval: int = 10         # 行情自动刷新间隔（秒）


@dataclass
class AppConfig:
    """顶层配置聚合。"""

    fee: FeeConfig = field(default_factory=FeeConfig)
    data: DataConfig = field(default_factory=DataConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    watchlist: list[str] = field(default_factory=lambda: [
        "600519", "000001", "300750", "601318", "600036", "000858",
    ])

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        if "fee" in d:
            cfg.fee = FeeConfig(**{k: v for k, v in d["fee"].items()
                                   if k in FeeConfig.__dataclass_fields__})
        if "data" in d:
            cfg.data = DataConfig(**{k: v for k, v in d["data"].items()
                                     if k in DataConfig.__dataclass_fields__})
        if "account" in d:
            cfg.account = AccountConfig(**{k: v for k, v in d["account"].items()
                                           if k in AccountConfig.__dataclass_fields__})
        if "ui" in d:
            cfg.ui = UIConfig(**{k: v for k, v in d["ui"].items()
                                 if k in UIConfig.__dataclass_fields__})
        if isinstance(d.get("watchlist"), list):
            cfg.watchlist = [str(x) for x in d["watchlist"]]
        return cfg


# ============================================================
# 全局单例访问
# ============================================================

_config: AppConfig | None = None


def config_path() -> Path:
    return user_data_dir() / "config.json"


def load_config() -> AppConfig:
    """加载配置；文件不存在或解析失败时回退到默认配置。"""
    global _config
    if _config is not None:
        return _config

    path = config_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                _config = AppConfig.from_dict(json.load(f))
            return _config
        except Exception:
            pass  # 配置损坏 -> 用默认值，不阻塞启动

    _config = AppConfig()
    save_config(_config)
    return _config


def save_config(cfg: AppConfig | None = None) -> bool:
    """持久化配置。"""
    global _config
    cfg = cfg or _config
    if cfg is None:
        return False
    try:
        with config_path().open("w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        _config = cfg
        return True
    except Exception:
        return False


def db_path() -> Path:
    """SQLite 数据库文件路径。"""
    return user_data_dir() / "data.db"
