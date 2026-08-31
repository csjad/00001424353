# A股模拟交易终端（cn-stock-desktop）

桌面级 A 股模拟交易终端：**行情看盘 · 模拟交易 · 量化回测**，开箱即用、双击即跑。

> 纯本地、纯模拟，**不涉及任何真实资金与券商接口**。所有交易为「纸面撮合」，用于学习 A 股交易规则与策略验证。

---

## 界面预览

| 总览（行情中心） | 模拟交易 | 量化回测 |
|:---:|:---:|:---:|
| ![总览](docs/screenshots/00-overview.png) | ![模拟交易](docs/screenshots/02-trade.png) | ![量化回测](docs/screenshots/03-backtest.png) |
| ![行情看盘](docs/screenshots/01-market.png) | | |

暗色金融主题、涨红跌绿 A 股配色；K 线 + 均线 + 成交量叠合；盘口实时更新。

### 多只自选股离屏渲染证据（`scripts/_shot.py`）

直接回应「其他示例股票打不开」的真实渲染快照（**离屏、无需联网**）：

| 600519 贵州茅台 | 000001 平安银行 | 300750 宁德时代 |
|:---:|:---:|:---:|
| ![贵州茅台](docs/screenshots/market_proof_600519.png) | ![平安银行](docs/screenshots/market_proof_000001.png) | ![宁德时代](docs/screenshots/market_proof_300750.png) |

每只票独立标题 / 独立价格区间 / 独立 K 线形态 / 独立盘口数字——快速切换不串味、不打不开。
本地重跑：`python scripts/_shot.py`（输出到 `docs/screenshots/market_proof_*.png`）。

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

## 配置说明（可选）

配置文件位于 `%APPDATA%\cn-stock-desktop\config.json`，首次启动自动生成，修改保存即生效（部分项支持运行时热更新，无需重启）。

`data` 相关核心配置项：

| 配置项 | 默认 | 说明 |
|------|------|------|
| `primary` / `fallback` | `akshare` / `tushare` | 主/备数据源，主失败自动降级 |
| `tushare_token` | 空 | 备源 token（可选，留空则 Tushare 不可用） |
| `cache_enabled` / `cache_ttl_days` | `true` / `7` | 历史数据 SQLite 缓存（天） |
| `realtime_ttl_seconds` | `15` | manager 层实时行情内存缓存有效期（秒） |
| `spot_ttl_seconds` | `30` | provider 层全市场快照缓存有效期（秒），**必须 ≥ `realtime_ttl_seconds`**，否则缓存穿透、每次刷新都重拉约 59 个分页请求 |
| `request_timeout` | `20` | 单次行情请求超时（秒）；超时后进入失败冷却，不再傻等一个完整连接 |
| `realtime_per_symbol` | `false` | **实验性**单票实时快路径（见下） |

### 实验性：`realtime_per_symbol`（默认关闭）

默认关闭。开启后，实时行情对自选股（通常个位数）**逐只**调用东财 `stock_bid_ask_em`（每只 1 个 HTTP 请求），不再拉全市场快照（一次约 59 个分页请求）——请求量随自选股数量线性增长，与全市场总票数无关。

- **安全回退**：任意异常或空返回都会自动回退到全市场快照，因此开启它**绝不会比现状更差**；
- **需联网验证**：该接口返回长表（item/value），字段映射在离线环境无法端到端确认，故默认关闭。联网后如想降低延迟，可手动设为 `true` 并观察日志确认报价正确后再长期使用；
- 未实现该接口的备源（如 Tushare）会自动退回全市场快照，不受影响。

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

> 本地已有的 `dist/A股模拟交易终端.exe`（≈72MB）可直接手动上传到 Release，无需等待 CI。

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
│   │   └── widgets/   # 行情 / 交易 / 回测 三个视图
│   ├── __main__.py    # python -m cnstock 入口
│   └── main.py        # 应用主入口（setup logging + QApp + MainWindow）
├── scripts/
│   ├── cli_demo.py    # 命令行回测（无需 GUI）
│   ├── make_screenshots.py  # README 截图生成（离屏、零联网）
│   ├── _smoke.py      # 冒烟测试：撮合/回测/绩效（离线可跑）
│   ├── _verify.py     # 回归测试：持久化/视图渲染/缓存/降级/单票快路径/多标加载（离线可跑）
│   └── _shot.py       # 离屏渲染证据：多只自选股逐一截图，证明切换不串味（离线可跑）
├── docs/
│   └── screenshots/   # README 截图 + 自选股渲染证据（market_proof_*.png）
├── launcher.py        # PyInstaller 冻结入口（修复包内相对导入）
├── build.py           # PyInstaller 打包脚本（绕过 Windows .bat 中文 codepage 炸）
├── run.py             # 首启建 venv + 装依赖 + 启动
├── build.bat          # 纯 ASCII 引导：调用 build.py
├── run.bat            # 纯 ASCII 引导：调用 run.py
├── requirements.txt
├── requirements-build.txt
└── .github/workflows/build.yml   # GitHub Actions：自动打包并发布 exe
```

> **README 截图如何本地重新生成**：
> ```bash
> python scripts/make_screenshots.py   # 输出到 docs/screenshots/00-03-*.png
> python scripts/_shot.py              # 输出到 docs/screenshots/market_proof_*.png
> ```
> 完全离屏运行、不联网、不写用户真实账户库。

---

## 免责声明

本软件仅供学习与策略研究使用，**不构成任何投资建议**。模拟撮合基于历史/实时行情近似，
与真实券商撮合（逐笔、排队、集合竞价）存在差异。据此操作风险自负。

---

如果本项目对你有帮助，欢迎 Star ⭐ 与 Issue。
