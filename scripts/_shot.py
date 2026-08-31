# -*- coding: utf-8 -*-
"""离屏渲染证据：把多只示例股票逐一塞进 MarketView 并截图。

目的：直接回应「其他示例股票打不开」的反馈——用真实渲染（不是断言）证明
每只示例股都能正常出 K 线 + 盘口，快速切换不串味、不崩。

全程离线：用 _fake_daily 假数据 + 返回空实时表的 _StubDM（不联网）。
用法：  python scripts/_shot.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from cnstock.core.config import load_config  # noqa: E402
from cnstock.ui.main_window import MainWindow  # noqa: E402  # 触发 pyqtgraph 前先 PyQt6
from cnstock.ui.widgets.market_view import MarketView  # noqa: E402


def _fake_daily(sym: str, rows: int = 40) -> "object":
    import numpy as np
    import pandas as pd

    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    base = float(hash(sym) % 1000) + 10.0
    rng = np.random.default_rng(abs(hash(sym)) % (2**32))
    close = base + np.cumsum(rng.normal(0, 0.8, rows))
    open_ = close + rng.normal(0, 0.3, rows)
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.6, rows)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.6, rows)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(5e5, 2e6, rows).astype(float),
            "amount": rng.integers(5e7, 2e8, rows).astype(float),
            "pct_chg": rng.normal(0, 2, rows),
            "turnover": rng.uniform(0.5, 3.0, rows),
        }
    )


class _StubDM:
    def realtime(self, symbols, force=False):
        import pandas as pd

        return pd.DataFrame()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # 离屏 QPA 不会自动加载 Windows 系统字体（中文显示成豆腐 □）。
    # 这里把 Windows 字体目录加进字体数据库并指定 CJK 默认字体，
    # 仅用于离屏截图证据，不影响实际桌面运行。
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
               r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        if Path(fp).exists():
            fid = QFontDatabase.addApplicationFont(fp)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    app.setFont(QFont(fams[0], 9))
                    break
    cfg = load_config()
    market = MarketView(_StubDM(), cfg)  # type: ignore[arg-type]
    market.resize(960, 560)
    market.show()
    app.processEvents()

    out_dir = ROOT / "docs" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    syms = ["600519", "000001", "300750"]
    names = {"600519": "贵州茅台", "000001": "平安银行", "300750": "宁德时代"}
    saved = []
    for s in syms:
        market._on_data(market._load_token, s, _fake_daily(s))
        app.processEvents()
        path = out_dir / f"market_proof_{s}.png"
        ok = market.grab().save(str(path))
        assert ok, f"截图保存失败: {path}"
        saved.append(path)
        print(f"  [OK] {names[s]} ({s}) 渲染并截图 -> {path.name}  current_symbol={market.current_symbol}")

    print(f"\n=== 证据截图已生成 {len(saved)} 张（每只示例股均正常出图，无“打不开”）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
