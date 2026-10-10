# 远端开发迁移验收

目标：`root@180.178.62.82:/root/niuniu`，保留 Git 历史、环境配置、真实及 fixture 数据卷；依赖和构建产物在远端重建。SSH 私钥留在本机。

人工核对步骤（迁移前确定）：

1. 停止源项目 API、Worker、Web，确认数据库无写入；不停止其他项目服务。
2. 记录两个数据卷的文件清单、尺寸、SHA-256，以及 SQLite quick_check、各表行数、数据版本和任务状态。
3. 使用可重试的 rsync 传输；远端服务启动前逐文件核对 SHA-256、SQLite 完整性及业务表行数。保留旧版本和已发布结果，不执行 fixture 导入或初始化同步。
4. 在远端运行 pytest、Ruff、mypy、Vitest、tsc 和生产构建；测试使用隔离数据。
5. 启动远端 API 和 Web，验证真实市场状态及原研究结果可读，再启动唯一 Worker；本机服务保持停止，避免调度重复。
6. 检查网页、容器健康、Worker 任务状态；本机项目和数据保留作为迁移回退来源，不删除卷。

迁移运行结果记录在 `docs/development.md`。`.env` 仅经 SSH 传输，权限 600，不写入报告或 Git。

## 在远端继续开发

迁移完成后以远端目录为开发主副本，本机保留迁移前代码和数据作为回退来源，不应再用本机旧目录覆盖远端新变更。

```bash
cd /root/niuniu
source .venv/bin/activate
pytest -q
ruff check backend scripts
mypy
cd frontend
npm test
npm run typecheck
npm run build
```

部署沿用 `docker compose build && docker compose up -d`，网页端口 18080。真实数据位于 `/var/lib/docker/volumes/ashare-lab-real-data/_data`；fixture 卷为 `ashare-lab_lab-data`，两者保留隔离。

若直接运行 Python 源码并使用真实数据，先停止 Compose 的 API 和 Worker，然后在各开发终端设置：

```bash
export DATA_DIR=/var/lib/docker/volumes/ashare-lab-real-data/_data
export DATABASE_PATH="$DATA_DIR/business.db"
# API 和 Worker 分别在不同终端运行，且只允许一个 Worker。
.venv/bin/ashare serve
# 另一终端设置相同路径后：.venv/bin/ashare worker
```

`.env` 中的供应商和管理配置保留；默认项目 `data/business.db` 不是生产真实数据。Git 远端地址随仓库迁移，GitHub SSH 登录凭据未迁移，需要使用远端自身的 GitHub 身份。
