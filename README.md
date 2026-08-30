# A股模拟交易终端（cn-stock-desktop）

桌面级 A 股模拟交易终端：**行情看盘 · 模拟交易 · 量化回测**，开箱即用、双击即跑。

> 纯本地、纯模拟，**不涉及任何真实资金与券商接口**。所有交易为「纸面撮合」，用于学习 A 股交易规则与策略验证。

---

## 功能特性

| 模块 | 能力 |
|------|------|
| **行情中心** | 代码/名称搜索、自选股、日/周/月/分钟线、前/后复权、K 线 + 均线 + 成交量、实时盘口 |
| **模拟交易** | 限价/市价委托、账户总览、持仓、当日委托、成交记录、撤单、账户重置 |
| **量化回测** | 内置 6 大策略（双均线/RSI/布林/MACD/网格/买入持有）、参数可调、绩效指标 + 资金曲线 + 成交明细 |

### 严格遵循 A 股规则
- **T+1**：当日买入次日方可卖出（持仓自动锁定）
- **涨跌停**：涨停拒买、跌停拒卖
- **最小单位**：买入须为 100 股整数倍
- **费用**：佣金（万 2.5，最低 5 元）+ 印花税（卖出单边千 0.5）+ 过户费（双边万 0.1）
- **配色**：涨红跌绿（A 股约定）

### 数据源
- **主源 akshare**（免费、无需注册）+ **备源 Tushare**（可选，填 token）
- 主备自动降级；SQLite 缓存历史数据，断网也能看旧图

---

## 安装与运行（开发模式）

需要 Python ≥ 3.10（已在 3.13.14 验证）。

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python -m cnstock
```

或直接使用 `run.bat`（首次运行会自动建环境并装依赖）。

---

## 打包为单文件 exe（交付形态）

目标：**双击即跑，无需安装 Python**。

```bash
build.bat
```

产物位于 `dist\A股模拟交易终端.exe`。配置与数据库写入用户目录
`%APPDATA%\cn-stock-desktop\`，不影响安装位置。

---

## 自动构建（GitHub Actions）

仓库已内置 `.github/workflows/build.yml`：

- 推送 `v*` 标签（如 `v1.0.0`）或在 Actions 页手动触发，**自动在 Windows runner 上打包 `A股模拟交易终端.exe`**；
- tag 触发时自动发布到对应 GitHub Release（作为附件上传），并生成变更说明；
- 任意触发都会保留一次构建产物（Artifact）供下载。

```bash
git tag v1.0.0
git push origin v1.0.0
```

> 本地已有的 `dist/A股模拟交易终端.exe`（≈124MB）可直接手动上传到 Release，无需等待 CI。

---

## 命令行回测（无需 GUI）

```bash
python scripts/cli_demo.py 600519 --strategy 双均线交叉 --years 3
python scripts/cli_demo.py 300750 --strategy MACD 背离 --cash 500000
```

---

## 目录结构

```
cn-stock-desktop/
├── cnstock/
│   ├── core/          # 配置、A股规则常量、领域模型
│   ├── data/          # 数据源抽象 + akshare/tushare + 缓存 + 调度器
│   ├── engine/        # 模拟撮合引擎（SimBroker）
│   ├── backtest/      # 回测引擎、策略库、绩效指标
│   ├── storage/       # SQLite 持久化
│   ├── ui/            # 主题、K线图表、主窗口、三个业务视图
│   └── main.py        # 应用入口
├── scripts/cli_demo.py
├── requirements.txt
├── requirements-build.txt
├── run.bat
├── build.bat
└── .github/workflows/build.yml   # GitHub Actions：自动打包并发布 exe
```

---

## 免责声明

本软件仅供学习与策略研究使用，**不构成任何投资建议**。模拟撮合基于历史/实时行情近似，
与真实券商撮合（逐笔、排队、集合竞价）存在差异。据此操作风险自负。

---

如果本项目对你有帮助，欢迎 Star ⭐ 与 Issue。
