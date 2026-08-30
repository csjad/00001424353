@echo off
chcp 65001 >nul
cd /d %~dp0

if not exist .venv (
    echo 首次运行：创建虚拟环境并安装依赖（约 2-5 分钟）...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    call .venv\Scripts\activate.bat
)

echo 启动 A股模拟交易终端...
python -m cnstock
if errorlevel 1 (
    echo.
    echo 程序异常退出，请查看上方错误信息。
)
pause
