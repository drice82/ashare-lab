# 真实数据部署切换验收

人工核对步骤（执行切换前记录）：

1. API、Worker 均挂载新卷 `ashare-lab-real-data` 至 `/app/data`，原 `ashare-lab_lab-data` 测试卷保留。
2. 重建服务后 `/api/v1/market/status` 返回 `token_configured=true`、`fixture_mode=false`；空仓库的最新行情日期应为 null。
3. 提交最近三年初始化，只提交一次；Worker 实际取得 Tushare 基础信息，任务保持运行或报告真实错误。
4. 新仓库发布的数据来源必须是 `tushare`；同步完成前不得宣称全市场覆盖完成。

默认 Compose 自动加载 `compose.override.yaml`，API 与 Worker 使用真实数据卷。原测试卷保留，可在独立测试部署中读取。切换不复制测试行情、方案和结果。

2026-10-09 实际验收：两服务挂载新卷，旧卷仍存在；Token已识别，测试模式已关闭。初始化任务 `8cc5439c-049d-4490-9e70-e2b6af589198` 范围2023-10-09至2026-10-08（另补120天预热）。首次因退市特殊代码校验失败；修复后恢复同一任务，复用已发布上市分片，新增退市339行并持续发布历史名称。当前运行中，尚未完成行情覆盖。pytest 42项通过，Ruff检查/格式与mypy通过；前端在Node22容器内Vitest 5项、tsc及构建通过（宿主Node18不兼容工具链）。
