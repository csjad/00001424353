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
打包前会自动跑 ``scripts/_smoke.py`` 与 ``scripts/_verify.py`` 两套自检，
任一未通过即中止（用 ``python build.py --skip-tests`` 跳过）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# ---------- 配置 ----------
ROOT = Path(__file__).resolve().parent
APP_NAME = "A股模拟交易终端"  # 中文 exe 名
ENTRY = "launcher.py"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# PyInstaller 参数（抽成模块级常量，便于 ../_archive/build_slim_probe.py
# 之类的一次性分析脚本直接复用，避免两边配置漂移）。
#
# 关于 --collect-all 的取舍（实测 2026-08-31：exe 124.7 MB -> 71.3 MB，-42.8%）
# ---------------------------------------------------------------------------
# - **不要** ``--collect-all PyQt6``：它会把 PyQt6 安装目录下**整个 Qt6 目录**
#   塞进包里，包括本项目一个都没用到的
#
#       Qt6/qml              2243 个文件 / 13.7 MB   QML 运行时
#       Qt6/translations      217 个 .qm / 10.1 MB   多语言（本项目只用中文）
#       Qt6/bin/Qt6Quick*.dll、Qt6Qml、Qt6Designer、Qt6Pdf   约 35 MB
#       Qt6/bin/avcodec-61、avformat-61...           约 17 MB（QtMultimedia 的 FFmpeg）
#       Qt6/qsci                                      1.7 MB（QScintilla）
#
#   去掉后改由 PyInstaller 官方 PyQt6 hook 按需收集：只收被 import 的
#   QtCore / QtGui / QtWidgets 三个模块所需的 DLL 与平台插件
#   （platforms/qwindows、imageformats、styles）。
#   代码实际用到的 Qt 模块只有 QtCore / QtGui / QtWidgets 三个。
#
# - **保留** ``--collect-all pyqtgraph``：pyqtgraph 带一批色表数据文件
#   （``colors/maps/*.csv``），压缩后只占约 1.2 MB。留着可避免将来用到
#   ``pg.colormap.get('CET-...')`` 时静默炸掉；另用
#   ``--exclude-module pyqtgraph.examples`` 剔掉纯示例目录。
#
# - PyInstaller 官方 Qt hook 会额外收 ANGLE（libEGL/libGLESv2）和
#   opengl32sw.dll。PyQt6 6.11 已不带 ANGLE（改走 D3D 后端），
#   opengl32sw.dll 是 Mesa 软渲染（19.7 MB），是可进一步瘦身的最大单项，
#   但要改 hook 才能剔除，暂保留。
PYINSTALLER_BASE_ARGS = [
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", APP_NAME,
    "--hidden-import", "cnstock",
    "--collect-all", "pyqtgraph",
    "--exclude-module", "pyqtgraph.examples",
    "--collect-all", "akshare",
    "--collect-all", "tushare",
]
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
    """用列表参数调 PyInstaller，Unicode 直达。

    参数取舍详见模块顶部 ``PYINSTALLER_BASE_ARGS`` 的注释。
    """
    cmd = [py, "-m", "PyInstaller"] + PYINSTALLER_BASE_ARGS + [ENTRY]
    info("PyInstaller 命令：")
    print("    " + " ".join(f'"{a}"' if " " in a or any(ord(c) > 127 for c in a) else a
                          for a in cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def run_prebuild_tests(py: str) -> int:
    """打包前跑一遍本地自检，失败则中止，避免把坏版本发出去。

    两个脚本都是离线可跑、断言式、失败非零退出：
      - scripts/_smoke.py   核心逻辑（撮合 / 回测 / 绩效指标）
      - scripts/_verify.py  持久化往返 / 交易视图渲染 / 数据源失败冷却

    用 ``--skip-tests`` 可跳过（例如 CI 里已有独立测试步骤时）。
    """
    scripts = ["scripts/_smoke.py", "scripts/_verify.py"]
    for rel in scripts:
        path = ROOT / rel
        if not path.exists():
            info(f"跳过 {rel}（文件不存在）")
            continue
        info(f"打包前自检：{rel}")
        rc = subprocess.call([py, str(path)], cwd=str(ROOT))
        if rc != 0:
            print(f"\n!!! {rel} 未通过（退出码 {rc}），已中止打包", flush=True)
            return rc
    return 0


def main() -> int:
    skip_tests = "--skip-tests" in sys.argv[1:]

    py = resolve_python()
    info(f"使用 Python: {py}")

    if not skip_tests:
        rc = run_prebuild_tests(py)
        if rc != 0:
            return rc
    else:
        info("跳过打包前自检（--skip-tests）")

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
