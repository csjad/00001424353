# -*- coding: utf-8 -*-
"""
PyInstaller 入口包装脚本（位于包外，作为顶层脚本运行）。

直接用 ``pyinstaller cnstock/main.py`` 打包时，PyInstaller 会把 main.py 当作
顶层 ``__main__`` 模块执行，导致包内相对导入（如 ``from .core.config import ...``）
在冻结环境里报 “attempted relative import with no known parent package”。

本脚本放在项目根目录（cnstock 包之外），以绝对导入 ``from cnstock.main import main``
启动，使 cnstock 作为正常包被加载，包内相对导入全部可解析。
等价于 ``python -m cnstock`` 的冻结版入口。
"""
from cnstock.main import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
