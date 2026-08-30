# -*- coding: utf-8 -*-
"""
启动脚本（替代 run.bat 的首次建 venv + 装依赖 + 启动应用逻辑）。

``run.bat`` 退化成纯 ASCII 引导后，所有逻辑（含中文状态信息、venv 引导、
依赖安装、启动 ``python -m cnstock``）都集中在本脚本里：

  - 首次运行：若 ``.venv`` 不存在，则用系统 ``python`` 创建一个 venv，
    再用 venv 的 ``pip`` 装 ``requirements.txt``；
  - 之后用 venv 里的 ``python -m cnstock`` 启动 GUI；
  - 全部 Unicode 消息由 Python 输出，绕过 CMD 的 codepage 解析。

用法
----
直接双击 ``run.bat``；或手工执行：``python run.py``。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PY_WIN = VENV_DIR / "Scripts" / "python.exe"
VENV_PY_NIX = VENV_DIR / "bin" / "python"
REQ = ROOT / "requirements.txt"


def info(msg: str) -> None:
    print(f">>> {msg}", flush=True)


def system_python() -> str:
    """run.bat 用系统 ``python`` 调起本脚本，所以 ``sys.executable`` 即可。"""
    return sys.executable


def venv_python() -> str:
    if VENV_PY_WIN.exists():
        return str(VENV_PY_WIN)
    if VENV_PY_NIX.exists():
        return str(VENV_PY_NIX)
    raise FileNotFoundError(f"未找到 venv 解释器：{VENV_PY_WIN} / {VENV_PY_NIX}")


def create_venv(py: str) -> None:
    info(f"首次运行：创建虚拟环境 {VENV_DIR} ...")
    subprocess.check_call([py, "-m", "venv", str(VENV_DIR)])


def install_requirements(py: str) -> None:
    info("安装依赖 requirements.txt（首次约 2-5 分钟，请耐心等待）...")
    t0 = time.time()
    subprocess.check_call(
        [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)]
    )
    info(f"依赖装好啦，用时 {time.time() - t0:.1f}s")


def launch_app(py: str) -> int:
    info("启动 A股模拟交易终端 ...")
    return subprocess.call([py, "-m", "cnstock"], cwd=str(ROOT))


def main() -> int:
    sys_py = system_python()

    if not VENV_DIR.exists():
        create_venv(sys_py)

    vpy = venv_python()

    # 探测 venv 里是否已装依赖：尝试 import 一个核心包
    probe_ok = True
    try:
        subprocess.check_call(
            [vpy, "-c", "import PyQt6, pyqtgraph, akshare, pandas, numpy"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        probe_ok = False

    if not probe_ok:
        install_requirements(vpy)

    rc = launch_app(vpy)
    if rc != 0:
        print(f"\n!!! 程序异常退出（code={rc}），请查看上方错误信息。", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
