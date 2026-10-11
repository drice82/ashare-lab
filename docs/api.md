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

## 历史任务删除

四类任务详情路径支持 DELETE：`/api/v1/screening/runs/{job_id}`、`/api/v1/backtest/runs/{job_id}`、`/api/v1/dca/runs/{job_id}`、`/api/v1/data/sync/{job_id}`。成功返回 HTTP 200 和 `{"deleted":true,"id":"任务ID"}`。不存在或类型不匹配返回 404；queued、running、cancel_requested 返回 409（task_active），应先取消并等待结束。succeeded、failed、cancelled、partial 均可永久删除，包含旧任务和自动同步任务。

删除清理任务、日志、同步检查点及该任务 results 目录（含未发布的临时结果）。已发布数据版本、行情文件、其他任务快照及调度时段领取记录保留。文件清理失败返回 503（cleanup_failed）并保留任务记录，用户可重试；中断后也可重试，已缺失文件不阻止删除。数据库与文件系统不构成跨介质事务，清理中断可能已移除部分结果文件。

接口沿用 API 访问密钥；同步删除另需 `X-Data-Management-Password`，研究任务删除不要求数据管理密码。

`GET /api/v1/jobs` 新增 `offset`（默认 0，非负），沿用 `limit`（默认20，最大100）及原数组响应，按 created_at、id 降序排列。四类历史页面每页20条，可访问全部旧任务。

四类任务集合路径支持批量 DELETE：`/api/v1/screening/runs`、`/api/v1/backtest/runs`、`/api/v1/dca/runs`、`/api/v1/data/sync`。清除该类型所有分页的已结束任务，保留活动任务；响应 `{"deleted":true,"ids":["任务ID"],"skipped_active":0}`，空历史和重复清空仍返回200。鉴权、数据版本保留与文件清理失败重试语义沿用逐条删除。批量文件清理中断后数据库记录回滚，但已删除文件不能回滚；重试可完成清理。

单股回测汇总新增 `annualized_return`，为单笔初始投入按365天/年的CAGR；完整性不足、零时长或数值溢出返回null。读取旧完整结果时从保存的汇总和参数补算，不改写发布文件。

## 多策略比较

`POST /backtest/compare`返回202，公共字段与原回测相同，但不接受旧strategy/short_window/long_window顶层字段；新增`strategies`（1–6项、ID唯一）。配置为`{id, ...策略适用参数}`，不适用参数拒绝。ID为buy_hold、ma_cross、macd_cross、rsi_reversal、donchian_breakout、bollinger_reversion，参数及预热定义见backtest-rules.md。周期2–250，双均线沿用2≤短<长≤250；MACD快<慢；RSI 0<lower<upper<100；deviations为0.5–5。NaN/Infinity禁止。

`GET /backtest/options`保留原defaults/strategies，新增comparison_defaults.strategies及comparison_strategies（默认参数、预热日数、参数名称、范围和步长）。比较任务仍为kind=backtest，详情/历史/取消/删除沿用原入口。

`GET /backtest/runs/{id}/results`：旧任务保持原响应；比较任务返回mode=comparison、summary（complete/strategy_count/complete_count）、strategies（strategy_id/label/parameters/summary/curve/warnings/assumptions/excess_return及版本）、公共参数、数据版本、fixture_mode。curve新增return_rate/drawdown；excess_return为比例差，前端乘100展示百分点。比较概览不返回完整账本和K线。

`GET /backtest/runs/{id}/strategies/{strategy_id}?page=1&page_size=50`返回对应策略汇总/曲线、prices（未复权OHLC）、trades（完整买卖记录）及分页items/total/page/page_size。未知策略或旧单策略任务返回404；未发布返回409，分页大小最大200。

`GET /backtest/runs/{id}/export?view=summary|curves|ledger`导出全部策略对应CSV，默认ledger；非法view返回422。旧任务导出格式保持原样。


## 组合回测

`GET /portfolio/options`返回公共默认资金/费用、allocation=equal、rebalance=monthly、comparison_strategies、固定数据版本候选、交易日历、fixture_mode及maximum_members=20。

`POST /portfolio/run`返回202。公共字段为start_date/end_date/initial_cash/commission_rate/minimum_commission/transfer_rate/stamp_rate/data_versions，范围沿用单股回测；不接受顶层stock_code或单股策略字段。members为1–20项、股票代码唯一，每项stock_code、strategy（沿用StrategyConfig，默认buy_hold）、可选weight。allocation=equal（默认）时不得指定非空weight；custom要求每股weight>0且≤1、最多六位小数、合计精确为1。rebalance=none|weekly|monthly|quarterly|yearly，默认monthly。数据版本、存续证券和交易日历在应用服务验证，非法输入422、未就绪409。

`GET /portfolio/runs/{id}`、`POST /portfolio/runs/{id}/cancel`、`GET /jobs?kind=portfolio`沿用研究认证。`DELETE /portfolio/runs/{id}`和`DELETE /portfolio/runs`沿用结束状态限制、活动任务保留及数据版本保留语义，只删除组合类型历史。

`GET /portfolio/runs/{id}/results?page=1&page_size=50&account=portfolio`返回实际summary/curve、benchmark（summary/curve/warnings）、excess_return、公共参数/版本/fixture/assumptions和所选账户的分页items/total/page/page_size。account=portfolio|benchmark只切换分页账本，不更改顶层实际摘要；page_size最大200。完整性逐账户判断，succeeded不代表收益完整。

`GET /portfolio/runs/{id}/positions?date=YYYY-MM-DD&account=portfolio`返回该账户指定确认交易日的date/cash/cash_weight/items；不传date取最后确认日，完全未知时items为空。显式日期无确认持仓返回404。逐股字段包括stock_code/name/strategy/shares/price/valuation_date/position_value/receivable/tax_liability/configured_weight/target_weight/actual_weight/eligible/pending_order，pending_order保存signal_date与target_value。

`GET /portfolio/runs/{id}/stocks/{stock_code}?account=portfolio&page=1&page_size=50`返回固定版本原始OHLC、该股完整实际成交trades、逐股分页账本和账户警告。股票不属于组合404；无合成行情。股票名称使用保存的基础版本，不以当前名称推定历史ST。

`GET /portfolio/runs/{id}/export?view=summary|curves|positions|ledger`默认ledger，UTF-8 BOM CSV导出两个账户的全量数据，不受账本分页或当前账户选择影响。每行含account、完整性、fixture_mode、参数、data_versions、engine_version、strategy_versions、警告和假设；未知空结果也提供元数据。

账本字段date/event/stock_code/signal_date/shares_delta/price/amount/fee/tax/cash/shares/reason。buy的amount为负成交额，sell为正，费用和已到账权益卖出补税分别扣除。dividend的amount为税前到账金额，tax为本次派息扣除的已确定税负，因此该事件净现金变动为amount−tax。entitlement仅登记权益，不改变现金。新增rebalance周期调仓和below_lot单位不足；insufficient_cash说明未完成目标本次结束。规则详见portfolio-rules.md。

## 邮箱验证码信号通知

以下准确路径和方法豁免共享访问密钥（不豁免任意 `/notifications/*`）。登录验证码、选项和专用退订公开，其余管理操作需邮箱账号Cookie和记录归属；退出允许幂等调用。所有写请求必须携带同源 `Origin`，未知路由仍受原共享密钥保护。登录失效401使用 `notification_unauthorized`，前端清空私有状态并显示邮箱登录，不弹出共享访问密钥框。通知响应禁止缓存。

| `/api/v1/notifications` 下的路径 | 方法及输入 |
|---|---|
| `/auth/email/verification` | POST `{address}`；统一发送首次/再次登录验证码，返回status/message/expires_in=600/resend_after=60，不泄露账号是否存在 |
| `/auth/email/verify` | POST `{address,code}`，code为六位数字字符串；首次成功创建账号或接续已验证邮箱的旧身份，写Cookie；返回owner_id、脱敏email、expires_at，不返回会话Token |
| `/session` | GET返回owner_id、脱敏email、expires_at；DELETE撤销当前会话并清Cookie，返回signed_out，其他设备会话保留 |
| `/account/email/verification` | POST `{address}`，当前账号申请更换邮箱验证码，不改变原渠道 |
| `/account/email/verify` | POST `{address,code}`，原子更换账号/收件邮箱并写新Cookie；返回同登录，撤销其他会话，邮箱冲突409 |
| `/options` | GET策略参数、默认值、版本、渠道可用性、fixture/自动发送/订阅上限，以及auth.email_available、auth.session_days |
| `/instruments` | GET `search`，仅存续沪深A股，返回items和data_versions |
| `/channels` | GET脱敏渠道及验证/停用状态 |
| `/channels/webhook` | PUT `{target}`绑定并测试，target为1–2048字符公网HTTPS URL；DELETE停用并取消Webhook待发消息 |
| `/channels/email` | DELETE停用邮件通知，不删除登录账号；取消邮件待发消息 |
| `/channels/{webhook,email}/enable` | POST启用保留的渠道；邮件使用已验证账号邮箱，Webhook先测试验证；不恢复已退订订阅或旧队列，重复启用不改变修订 |
| `/channels/{webhook,email}/test` | POST，已验证且启用渠道手动测试，正文“这是一条测试通知” |
| `/subscriptions` | GET自己的订阅，附加stock_name（当前固定基础快照的展示名称，缺失为null）；POST `{stock_code,strategy,directions?,channels}`，channels至少一个，重复返回已有 |
| `/subscriptions/{id}` | PATCH stock_code/strategy/directions/channels/status（active/paused），DELETE软删除 |
| `/deliveries` | GET `channel?`（webhook/email/bark，bark仅查旧记录）, `limit`（默认50，1–100）, `offset`，返回items/total和不可变outcome |
| `/email/unsubscribe` | POST `{token}`，仅移除此订阅邮件渠道，token从fragment读取并清除 |

旧POST `/session`、`/recover`、`/channels/email/verification`、`/channels/email/verify`已移除，PUT `/channels/email`不再提供。旧Bark绑定、删除和测试接口也已移除。

Webhook固定POST JSON `{title,body,group:"ashare-lab"}`，普通接收端HTTP 2xx表示接受；Bark官方api.day.app还要求JSON业务code=200。URL不能含userinfo或fragment，DNS全部地址必须公网，发送时重新解析且TCP连接固定到验证过的IP，TLS仍验证原域名；15秒超时、不跟随重定向、不使用环境代理。完整地址只加密保存，不在列表、异常或日志中回显。

策略输入复用StrategyConfig，仅支持ma_cross/macd_cross/rsi_reversal/bollinger_reversion/donchian_breakout及适用参数。绑定成功、验证码发送和测试返回accepted仅指上游接受；不保证最终实收。验证码输入错误/过期/尝试超限统一invalid_verification；限流429，渠道未验证409，管理员未配置服务503，上游固定错误码502。秘密不回显，原始上游异常不返回或记录。详见 [信号口径](notification-rules.md) 与 [部署验收](notification-deployment.md)。


`POST /notifications/subscriptions/{sid}/test` 接受 `{direction:"buy"|"sell"}`，必须为该订阅已选方向，暂停订阅也可测试。返回 `{message,results:[{channel,status,message?}]}`；每渠道status为accepted/failed/unknown/cancelled/skipped，不可用渠道给出跳过原因，无可用渠道409，不属于当前账号或已删除订阅404。测试通知为明确标注的示例，不执行真实信号计算。所有新增接口沿用账号会话、同源Origin及准确路径/方法的共享密钥豁免。

渠道及订阅测试共享账号额度：滚动一小时前5次连续发送，之后间隔至少60秒，每小时最多20次；多渠道一次测试计一次，实际发送失败计次，校验失败和限流拒绝不计次，429消息含等待秒数。Webhook重新启用时的测试也计额度，现有IP保护保留。

`GET /notifications/deliveries` 合并正式信号与独立订阅测试记录，新增 `is_test` 布尔标识。测试记录的created_at为测试时间，trade_date为“示例日期”，check_id/revision/channel_revision/next_attempt为空，outcome包含股票、策略快照、明确示例依据和空数据版本。status新增unknown（发送状态未知，无自动重试）；正式记录原字段保持兼容。统一按created_at降序和id排序，渠道筛选、total、limit、offset均覆盖合并结果。Bark查询参数保留兼容，前端仅提供全部、邮件和Webhook选项。
