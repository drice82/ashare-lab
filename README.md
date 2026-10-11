# ashare-lab

个人使用的沪深 A 股量化研究与筛选平台 V0.1。Python 3.12 / FastAPI、React / TypeScript、DuckDB / Parquet、SQLite WAL。六类规则、历史日期、参数方案、异步任务、CSV 导出及 K 线详情。

研究采用指定交易日收盘后的数据，技术指标使用该日为基准的复权价格。分红仅使用可确认公开、已实施且已派息事件；缺失数据、历史 ST 不明、送转后的每股口径不明不会被默认为命中。历史回填按公告日期保守重建，**不声称拥有供应商当时的完整修订快照**。

## Debian 13 / Docker Compose 部署

安装 Docker Engine 和 Compose 插件后，在仓库根目录执行（Compose 2.24+）：

```bash
cp .env.example .env
docker compose up -d --build
docker compose run --rm api ashare init
docker compose ps
```

打开 http://127.0.0.1:8080 。若端口被占用，在 `.env` 修改 `WEB_PORT`；当前工作环境使用 18080。远程服务器通过隧道访问：

```bash
ssh -N -L 8080:127.0.0.1:8080 user@your-server
```

API 未发布宿主端口，仅由容器内 nginx 代理。网页端口绑定 `0.0.0.0`，也可通过 `http://服务器IP:WEB_PORT` 访问；当前端口为18080。公网访问应设置下文的访问控制，并限制源站端口访问。默认预留至少 4GiB 可用内存，API 上限 1GiB、Worker 上限 2GiB、Web 上限 128MiB；市场历史数据按日下载，因子只查询所需日线窗口，不下载分钟线。镜像包含测试/静态检查工具，方便个人服务器验收。

## 无 Token 的可运行验证流程

在独立的测试部署/数据卷中加载固定数据，不能向已有真实数据仓库导入：

```bash
docker compose stop worker
docker compose run --rm api ashare fixture
docker compose up -d worker
```

网页会明确显示“固定测试数据”。筛选日期选择 **2024-06-28**，A–F 均可执行，点击股票查看 K 线与实际条件数值。测试行情是确定性合成数据，交易日历用于测试，不代表实际交易所日历。

默认预期命中：A=000001；B=000001、000002；C=000003、000005、000006；D=000004；E=000005（均为 .SZ）。C 默认不排除 ST，可勾选剔除；A 默认剔除。

测试数据与真实行情严格隔离。要保留演示同时运行真实系统，可复制工作目录并使用不同 Compose 项目名及网页端口；每个项目有独立命名数据卷。不要删除已有数据卷来切换模式。

## Tushare 配置与同步

当前部署通过 `compose.override.yaml` 将 API 与 Worker 指向独立真实数据卷 `ashare-lab-real-data`，原测试卷保留。配置 Token 不会自动同步；切换验收见 [记录](docs/real-data-switch.md)。

当前2000积分账号部署使用总限速200次/分钟、请求并发4（override中的 `REQUESTS_PER_MINUTE`、`SYNC_CONCURRENCY`）。历史名称、分红和每日行情接口有界并发读取，版本仍串行发布；重启Worker会复用已发布检查点。调整上限须按账号及接口实际额度设置，线程数不会增加账号额度。

常驻 Worker 同时执行一个数据更新和最多两个研究任务（筛选、定投、回测合计）；同步任务之间仍排队，研究名额用满后按提交时间排队。`WORKER_RESEARCH_CONCURRENCY` 默认 `2`，可设为 `1`；修改后重启 Worker 生效。研究始终读取提交时固定的数据版本，数据更新不阻塞研究通道。DuckDB 单次查询仍互斥以控制内存，并行任务不保证同比例加速。`ashare worker --once` 仍只执行一个任务。Worker 收到 SIGTERM/SIGINT 后停止领取并等待活动任务完成；Compose 最多等待 60 秒，超时强制中断后从既有检查点／固定快照恢复。

在 `.env` 中设置 `TUSHARE_TOKEN`。可选 `TUSHARE_HTTP_URL` 支持兼容 Pro 协议的自定义接入；不把密钥写入 URL。Token 不进入 Git、镜像构建参数、前端或日志。若接入地址只支持 HTTP，需要自行确认传输环境；默认使用 HTTPS。

```bash
docker compose up -d --force-recreate api worker
docker compose exec api ashare sync --start 2023-10-09 --end 2026-10-08
# 命令返回任务 ID；Worker 自动执行
docker compose exec api ashare job TASK_ID
docker compose exec api ashare sync --incremental
```

也可在网页“数据管理”中选择范围初始化或启动增量同步。默认初始化日期为最近三年，额外补足120天预热。基础信息保留退市状态用于排除；新任务仅为未退市证券逐股查询分红。增量回查最近10个日历日，捕获补录，但不获取分红；初始化仍获取完整分红历史。常驻Worker每天北京时间16:00提交增量任务（明确同步至当天，不含分红和历史名称；需先初始化真实行情），每天北京时间01:00（含周末、节假日）提交独立分红任务，每月第一个星期六05:00提交历史名称任务。任务到时入队，在同步通道内等待前一个数据更新完成；研究通道可同时执行。错过计划后仅补交最近一次，每日／每月去重记录持久化；失败或部分完成需从原任务恢复。网页提供“立即更新分红”和“立即更新历史名称”；CLI为 `docker compose exec api ashare sync --dividend` 和 `docker compose exec api ashare sync --namechange`。已有同步任务保持原策略。40次/分钟的默认限速可配置 `REQUESTS_PER_MINUTE`，大范围初始化可能需要数小时。

没有 Token 时任务明确失败。必需基础信息/日历权限不足则失败；复权、分红、每日指标、历史名称及可选交易状态接口权限不足时任务标为“部分完成”，相应规则依赖返回未知。返回达到接口行数上限时拆分查询；无法证明完整性的分片拒绝发布。复权/分红缺失不能用零或未复权价格替代。

九个接口已完成单股票真实响应及标准化验证，完整范围和限制见 [验证记录](docs/verification.md)。可运行以下命令重新探测；权限错误分别报告，单股票验证不等同于三年全市场同步验收。

```bash
docker compose exec api python scripts/verify_live.py
# 临时仓库中用最近150个日历日的单股票真实数据验证因子/API，不改动已有数据
docker compose exec api python scripts/verify_live_pipeline.py
```

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/ashare init
.venv/bin/ashare serve
```

另一个终端：

```bash
.venv/bin/ashare worker
```

前端需 Node 22.12+（或兼容新版）：

```bash
cd frontend
npm ci
npm run dev
```

Vite 默认代理本机8000端口。`DATA_DIR` 与 `DATABASE_PATH` 决定本地持久目录；Compose 内固定为 `/app/data` 与 `/app/data/business.db`。`TZ=Asia/Shanghai`。本地测试数据可用独立配置：

```bash
DATA_DIR=data/demo DATABASE_PATH=data/demo/business.db .venv/bin/ashare fixture
DATA_DIR=data/demo DATABASE_PATH=data/demo/business.db .venv/bin/ashare serve
# Worker 使用相同的两个变量启动
```

## 检查与测试

```bash
.venv/bin/pytest -q
.venv/bin/ruff check backend scripts
.venv/bin/ruff format --check backend scripts
.venv/bin/mypy
cd frontend
npm test
npm run typecheck
npm run build
npm audit
```

容器内可运行后端验收：

```bash
docker compose exec api pytest -q
docker compose exec api ruff check backend scripts
docker compose exec api mypy
```

测试不需要真实 Token。固定数据同时用于手算因子、规则、API和真实 Worker 执行流程。浏览器端到端测试在独立 fixture 部署上运行：

```bash
cd frontend
npx playwright install chromium
E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test
```

实际检查结果记录在 [验证记录](docs/verification.md)，开发进度记录在 [开发记录](docs/development.md)。

远端开发主机迁移和真实数据路径见 [迁移验收与开发说明](docs/remote-migration.md)。

## 启停、更新与备份

```bash
docker compose stop
docker compose start
docker compose logs --tail=100 api worker
# 更新代码后；数据库启动时运行 Alembic 迁移
docker compose build
docker compose up -d
```

命名卷 `lab-data` 保存数据库、配置方案、市场数据、版本清单和结果。`docker compose down` 不删除卷；**不要使用 `down -v`**。行情旧版本首版不自动清理，长期使用需留意磁盘空间。

一致性备份先停止写入者（此时网页暂不可用）：

```bash
docker compose stop api worker
docker compose run --rm --no-deps api tar -C /app/data -czf - . > ashare-backup.tar.gz
docker compose up -d api worker
```

备份文件含个人配置和研究结果，应存到受保护的位置。恢复到新卷时，同样停止写入者后通过一次性容器解压；不要覆盖正在使用的数据目录。

## 访问控制

默认只通过本机/SSH隧道访问。宿主直接绑定非回环 `API_HOST` 时必须设置随机 `API_ACCESS_TOKEN`，API 会验证 Bearer 密钥，网页右上角可输入密钥（只保存于页面内存）。

如改为公网域名：必须先设置随机 `API_ACCESS_TOKEN`，在 nginx 前部署 HTTPS 反向代理，再修改网页端口绑定；不要直接暴露后端端口。所有 `/api/v1` 业务端点、任务日志和 CSV 都受同一密钥保护。Compose 的 `INTERNAL_NETWORK_ONLY=true` 仅用于不发布端口的内部 API，不能用于宿主公网部署。

## 文档与边界

- [架构](docs/architecture.md)、[数据模型](docs/data-model.md)、[数据管道](docs/data-pipeline.md)
- [筛选公式](docs/screening-rules.md)、[API](docs/api.md)、[路线图](docs/roadmap.md)
- [任务依赖与开发记录](docs/development.md)、[开发约定](AGENTS.md)

已实现单只沪深A股月度定投模拟：显式选择交易区间和数据版本，配置投入与费用，查看现金/持仓账本、资产曲线及完整CSV。分红仅按确认事件计算，数据不足或复杂公司行动时完整收益未知；期末保留持仓，未计未来卖出补税。详细口径见 [定投规则](docs/dca-rules.md)。

“单股回测”入口支持买入持有、双均线、MACD、RSI、唐奇安突破与布林带回归六策略对比，默认全选、每策略一组可调参数；展示收益/回撤曲线、指标柱状图和未复权K线买卖点。统一次日开盘、T+1及停牌/涨跌停限制，支持持有期分红补税、历史恢复，以及指标/曲线/完整账本CSV。详细口径见 [单股回测规则](docs/backtest-rules.md)。真实仓库隔离验收可运行 `PYTHONPATH=backend/src .venv/bin/python scripts/verify_backtest_real.py --data-dir <真实数据目录>`。多策略真实快照验收可运行 `PYTHONPATH=backend/src .venv/bin/python scripts/verify_comparison_real.py --data-dir <真实数据目录>`（三只股票，独立核对信号/价格/资金/费用；茅台可加 `--initial-cash 300000` 验证成交）。组合回测支持最多20只固定自选股票、逐股六策略、共享现金、等权/自定义权重、周/月/季/年再平衡、独立买入持有基准和四类CSV，口径见[组合回测规则](docs/portfolio-rules.md)。真实快照验收使用 `PYTHONPATH=backend/src .venv/bin/python scripts/verify_portfolio_real.py --data-dir <真实数据目录>`，追加 `--resource` 验证独立fixture仓库20股五年及两个研究名额并发。动态选股、自定义策略和信号邮件仍计划开发；不执行用户代码、不接SMTP，也不包含自动交易。

新筛选排除数据版本中已退市证券，即使其在历史筛选日仍上市；历史结果存在幸存者偏差，CSV包含股票池策略。已存数据、旧版本和旧任务不删除。名称覆盖截止日早于筛选日时，ST状态仍为未知，相关规则可能返回数据不足；月度更新不代表月内名称状态已确认。

“知识库”页面（`/knowledge`）说明本站六种回测策略的思路、默认参数、预热、买卖信号及局限，附中文教学示例与机构英文延伸阅读。目录支持章节直达链接；单股回测“策略说明”在新窗口打开，保留研究草稿。知识库无需访问密钥，不读取行情或发起研究任务。
