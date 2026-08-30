# -*- coding: utf-8 -*-
"""
打包脚本（替代 build.bat 里的多行 pyinstaller 命令）。

为什么需要它
------------
Windows CMD 解析 .bat 时按系统 codepage（中文系统一般为 GBK/936）读文件，
若 .bat 本身是 UTF-8 无 BOM，文件中所有非 ASCII 字节会被解读为乱码：

  1. ``pyinstaller ... ^``  这种 ``^`` 续行符紧邻中文参数时直接失效，
     后续 ``--windowed`` / ``--collect-all`` / 包含中文 exe 名的路径
     全部被当成独立命令逐行执行（你截图里 ``'t'``、``'--collect-all'``、
     ``'dist\\A\u80a1\u6a21\u64ec\u4ea4\u6613\u7ec8\u7aef.exe'`` 报错就是
     这么来的，乱码就是 UTF-8 字节被当 GBK 解码的结果）。
  2. 就算 ``^`` 没断，``--name "A\u80a1\u6a21\u64ec\u4ea4\u6613\u7ec8\u7aef"``
     这种参数也会在 CMD → CreateProcess 路径上被 codepage 二次转码，
     最终到 PyInstaller 手里依然是乱码。

解决方案
--------
- ``build.bat`` 退化成 4 行纯 ASCII：仅负责 ``chcp 65001``、``cd``、
  调用系统 ``python`` 跑本脚本、``pause`` 留窗口；
- 本脚本以 ``subprocess`` **列表传参**调用 PyInstaller，
  完全不走 shell，Unicode 参数直达 PyInstaller 的 ``sys.argv``，
  CMD 的 codepage 解析链条被彻底切断。

用法
----
直接双击 ``build.bat``；或手工执行：``python build.py``。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------- 配置 ----------
ROOT = Path(__file__).resolve().parent
APP_NAME = "A股模拟交易终端"  # 中文 exe 名
ENTRY = "launcher.py"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
# --------------------------


def info(msg: str) -> None:
    print(f">>> {msg}", flush=True)


def resolve_python() -> str:
    """优先用 venv 里的 python；如果 .venv 还没建过则回退到当前 python。"""
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable


def ensure_pyinstaller(py: str) -> None:
    """确保 PyInstaller 已装在 venv 里。"""
    info("检查 PyInstaller ...")
    try:
        subprocess.check_call(
            [py, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        info("PyInstaller 已就绪")
        return
    except subprocess.CalledProcessError:
        pass

    info("安装 PyInstaller 到 venv ...")
    req = ROOT / "requirements-build.txt"
    if not req.exists():
        # 即便缺文件也兜底装
        subprocess.check_call([py, "-m", "pip", "install", "pyinstaller"])
    else:
        subprocess.check_call([py, "-m", "pip", "install", "-r", str(req)])


def clean_previous() -> None:
    """每次构建先清掉 build/ dist/ 旧的产物，避免增量构建带来的怪问题。"""
    for sub in ("build", "dist"):
        p = ROOT / sub
        if p.exists():
            info(f"清理 {p}")
            shutil.rmtree(p, ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        spec.unlink()


def run_pyinstaller(py: str) -> int:
    """用列表参数调 PyInstaller，Unicode 直达。"""
    cmd = [
        py,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--hidden-import", "cnstock",
        "--collect-all", "pyqtgraph",
        "--collect-all", "PyQt6",
        "--collect-all", "akshare",
        "--collect-all", "tushare",
        ENTRY,
    ]
    info("PyInstaller 命令：")
    print("    " + " ".join(f'"{a}"' if " " in a or any(ord(c) > 127 for c in a) else a
                          for a in cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    py = resolve_python()
    info(f"使用 Python: {py}")

    ensure_pyinstaller(py)
    clean_previous()

    rc = run_pyinstaller(py)
    if rc != 0:
        print("\n!!! 打包失败，请查看上方日志", flush=True)
        return rc

    exe = ROOT / "dist" / f"{APP_NAME}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(
            f"\n>>> 打包成功：{exe}\n    大小：{size_mb:.1f} MB\n    双击即可运行，无需安装 Python。",
            flush=True,
        )
        return 0

    print("\n!!! 打包流程返回 0 但未找到产物", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
