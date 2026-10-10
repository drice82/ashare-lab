# API 约定

前缀 /api/v1。GET /instruments、/market/bars、/market/status、/data/datasets；POST /data/sync；GET /data/sync/{id}；POST /data/sync/{id}/cancel、/data/sync/{id}/resume；GET /jobs；GET /screening/rules；POST /screening/run；GET /screening/runs/{id}、/results、/export；POST /screening/runs/{id}/cancel；GET/POST /screening/presets。

POST /screening/run：as_of_date（ISO）、universe（type=all/custom，codes）、rule_id（A…F）、parameters。返回202与id/status。提交时固定版本。results 支持 page/page_size（最大200）、sort_by/order、match_status/search、filter_factor/min_value/max_value；数值过滤因子来自白名单，缺失值不进入数值区间，稳定股票代码次排序。export 返回UTF-8 BOM CSV，完整匹配结果，不受当前页影响。

bars：stock_code/start_date/end_date、adjustment=raw/asof（默认asof）、run_id可选。与run_id关联时固定任务数据版本，不能请求该任务日期后的行情；返回OHLC、volume、ma20/ma60及单位。默认最多1500日。

任务 status：queued/running/cancel_requested/cancelled/succeeded/partial/failed；包含 progress、error、日志与结果摘要。错误422非法输入、404不存在、409数据未就绪/状态冲突；错误对象包含code/message/details。OpenAPI为实际字段依据。

localhost研究接口默认无全站认证，管理接口仍要求独立密码；API_HOST非回环时强制API_ACCESS_TOKEN，Bearer保护所有API（健康检查不返回业务数据）。Token仅输入会话内存，导出使用认证fetch。CORS不允许任意来源。

## 每日收盘后增量同步

`GET /market/status`增加`incremental_schedule`，字段与dividend_schedule一致。北京时间每天16:00提交`mode: "incremental"`，明确end_date为到期日期，不使用22:00前默认截止到前一天的手动同步缺省逻辑。每天含非交易日，闭市日只回查交易日行情；数据未就绪时失败／部分完成，不能视为完整更新。需配置Token、完成真实行情初始化且无fixture混入后启用。日期去重与入队同事务，Worker重启只补交最近一次，不自动重试已失败日期；用户可从原任务恢复。无需新增提交参数或更改旧同步策略。

## 分红、名称增量与管理认证

所有 `/data/*` 与 `/jobs`、`/jobs?kind=sync` 必须携带独立 `X-Data-Management-Password` 请求头。未配置、缺失或错误密码返回403和 `code: data_management_locked`；配置使用 `DATA_MANAGEMENT_PASSWORD`，与全站 Bearer 访问密钥独立。`/jobs?kind=screening`、`/jobs?kind=dca` 及研究接口不要求管理密码。管理页面使用 `GET /data/status`；`GET /market/status` 仍返回研究摘要和 `latest_sync_status`，`recent_sync` 为空，不暴露同步任务列表。

`POST /data/sync` 的 `mode: dividend | namechange` 支持 `history_refresh: incremental | full`；新任务默认incremental，其他模式不接受此参数。省略end_date时在提交时固定北京时间前一天。初始化保留完整分红；行情增量不获取分红或名称。旧任务按原 `_sync_policy` 执行原策略。CLI使用 `ashare sync --dividend --history-refresh full` 或 `--namechange`，无需页面密码。

分红每天01:00、名称每天02:00增量扫描；每月首个周六03:00全量分红、05:00全量名称；行情仍每天16:00。凌晨任务截止到到期时刻的前一天。停机只提交最近一次到期任务，其扫描范围从原成功水位向前30天开始，包含整个遗漏区间。首次上线时已有完整基线涵盖的月度槽位只登记、不重取历史。fixture或无Token禁用自动调度。

状态包含 `dividend_schedule`、`namechange_schedule`、`dividend_full_schedule`、`namechange_full_schedule`；字段为enabled/timezone/schedule/next_run/last_completed/latest_job/limitation/last_full_coverage。每日和全量成功任务分别统计，partial、失败、取消不算成功。任务结果包含实际history_refresh、scan_start、end_date、stock_count和refreshed_count；缺失基线时自动逐股完整补齐，实际策略为full。

日期扫描发现变化后刷新相关股票完整历史；新股或缺失历史另行补齐。按股票替换记录，其他股票保留，旧版本可读取。已有记录的股票此次返回空结果时失败并保留记录。日期扫描、目标列表和股票发布分别持久保存；失败或取消不提交观察水位。最终覆盖、检查点及任务成功在同一事务内提交。

## 月度定投

`GET /dca/options`返回当前data_versions、latest_market_date、fixture_mode及费用/金额默认值。

`POST /dca/run`返回202与持久任务；请求必须包含stock_code、start_date、end_date、data_versions（dataset→version_id）。monthly_amount默认3000、monthly_day默认1、commission_rate默认0.0003、minimum_commission默认5、transfer_rate默认0.00001。金额与费率可用十进制字符串，费率为小数而不是百分数。金额上限1亿元，日期1–31，佣金与过户费率0–0.01，最低佣金0–10000；额外字段和非有限数禁止。

`GET /dca/runs/{id}`读取状态/进度/日志；`POST /dca/runs/{id}/cancel`取消。`GET /jobs?kind=dca`读取历史任务。

`GET /dca/runs/{id}/results?page=1&page_size=50`返回summary、curve、items（账本页）、total/page/page_size、warnings、data_versions、parameters、engine_version、fixture_mode、universe_policy及assumptions。page_size最大200。summary包含complete、confirmed_through、contributed、cash、shares、position_value、receivable、dividends、fees、dividend_tax_paid、total_assets、profit、return_rate、valuation_date和pending_plans。

账本事件为contribution/buy/dividend/entitlement/deferred/insufficient_cash/unknown，字段为date、planned_dates、shares_delta、price、amount、fee、cash、shares、reason；buy的amount为负成交额，fee另外扣除，entitlement的amount为权益登记金额而非现金流。curve逐日返回date、total_assets、contributed、cash、shares和valuation_date。

计算过程完成时任务为succeeded，即便数据不足；必须同时检查summary.complete。完整收益未知时total_assets/profit/return_rate为null，曲线为可计算部分；失败/取消任务没有可访问结果。`GET /dca/runs/{id}/export`导出UTF-8 BOM完整账本CSV，包含版本、参数及未知原因，不受分页影响。规则详见dca-rules.md。

## 股票联想

`GET /instruments`支持search（名称、代码、拼音首字母、全拼，忽略大小写/空格）、as_of_date、可选data_version（stock_basic版本）、page/page_size，返回items和data_version。精确匹配优先，其次前缀、包含，代码稳定次排序。定投按开始日期及选定基础版本查询，筛选按筛选日查询；结果搜索支持同样拼音匹配。

## 单股回测

`GET /backtest/options`返回固定版本候选、最新行情日、fixture_mode、策略列表及默认参数。`POST /backtest/run`返回202任务：stock_code/start_date/end_date/data_versions显式指定；initial_cash默认100000元、上限1亿元；strategy=buy_hold|ma_cross，short_window默认20、long_window默认60且2≤short<long≤250；commission_rate/minimum_commission/transfer_rate与定投一致，新增stamp_rate默认0.0005、范围0–0.01，仅卖出收取。额外字段、非有限数及非法日期禁止。

`GET /backtest/runs/{id}`、`POST /backtest/runs/{id}/cancel`、`GET /jobs?kind=backtest`读取研究任务，不要求管理密码，仍遵循全站Bearer认证。`GET /backtest/runs/{id}/results?page=1&page_size=50`及`/export`沿用定投分页（最大200）及UTF-8 BOM CSV约定。

结果含summary/curve/items、版本/参数、engine_version/strategy_version、fixture_mode、assumptions与warnings。summary沿用定投资产字段，pending_plans替换为pending_orders和pending_order（side/signal_date或null），新增tax_liability/max_drawdown/buy_count/sell_count。contributed是一次投入的initial_cash。账本增加signal_date/tax，事件增加sell/signal/order_cancelled/dividend_tax；buy/sell的amount为带符号成交额，fee/tax另扣；entitlement金额只登记应收、不改变现金。策略信号reason注明交叉方向。

结果不完整时完整收益指标为空，曲线和账本仅为可计算部分。取消及失败无可访问结果。详细口径见backtest-rules.md。

状态接口的URL与字段保持兼容，统计按不可变版本读取摘要，任务状态仍实时读取。研究任务第三次运行中断后为failed，error说明“任务反复中断”，不擅自判定OOM；用户可重新提交研究任务，同步恢复接口及检查点语义不变。筛选计算批次更新progress，结果发布成功后才达到1。
