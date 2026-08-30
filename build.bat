@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat

echo 安装打包工具 PyInstaller...
pip install -r requirements-build.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo 开始打包为单文件 exe（首次打包耗时较长，请耐心等待）...
pyinstaller --noconfirm --onefile --windowed --name "A股模拟交易终端" ^
  --hidden-import cnstock ^
  --collect-all pyqtgraph ^
  --collect-all PyQt6 ^
  --collect-all akshare ^
  --collect-all tushare ^
  cnstock\main.py

if exist "dist\A股模拟交易终端.exe" (
    echo.
    echo 打包成功！可执行文件位于：
    echo   dist\A股模拟交易终端.exe
    echo 双击即可运行，无需安装 Python。
) else (
    echo.
    echo 打包失败，请查看上方日志。
)
pause
