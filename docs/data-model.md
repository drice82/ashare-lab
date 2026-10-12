# 数据模型

SQLite 由 Alembic 迁移管理，目前包含以下业务表。筛选和同步共用 jobs，不设独立 screening_runs 表。

|表|关键字段及用途|
|---|---|
|alembic_version|已应用迁移版本|
|jobs|id、kind、status、payload、snapshot、progress、cancel_requested、heartbeat、created_at、updated_at、error、result、attempt；筛选版本和结果引用保存在JSON中|
|job_logs|id、job_id、at、level、message；任务日志，job_id外键|
|sync_checkpoints|job_id/key联合主键、version_id；已发布分片|
|dataset_versions|id、dataset、manifest、coverage、created_at、source、batch_id；不可变分区清单，coverage保存追加记录水位|
|dataset_heads|dataset主键、version_id；当前已发布版本|
|coverage_entries|id、dataset、key、version_id、value；追加覆盖记录，按dataset/key/id索引读取水位内最新值|
|presets|id、name、definition、created_at；筛选方案|
|rule_definitions|id、version、definition；规则定义与参数|
|settings|key、value；不含秘密的运行配置，Token只读环境变量|

状态：queued、running、cancel_requested、cancelled、succeeded、partial、failed。partial 用于同步的可选数据降级及通知计算等待覆盖，筛选发布必须整体完成。数据库启用 WAL、外键和 busy_timeout。

Parquet 数据集：stock_basic、namechange、trade_cal、daily、adj_factor、daily_basic、dividend、stk_limit、suspend_d。原始 Tushare 代码规范化为 stock_code；daily 的 vol（手）转为 volume（股），amount（千元）转为 amount（元）。百分数输入转成小数。日期为 ISO 日期字符串。source、batch_id、schema_version、fetched_at 列记录来源。

行情逻辑键为 stock_code/trade_date；日历为 exchange/cal_date；股票为 stock_code；名称为 stock_code/start_date；分红为 stock_code/end_date/ann_date/imp_ann_date/ex_date，保留修订版本；停复牌为 stock_code/trade_date/suspend_type/suspend_timing。

分红保留 cash_div_tax、cash_div、stk_div、ann_date、imp_ann_date、record_date、ex_date、pay_date、div_proc。不得把同一年度多次分红合为一条。缺失字段不伪造。

|Parquet数据集|主要标准字段|
|---|---|
|stock_basic|stock_code、symbol、name、area、industry、market、exchange、list_status、list_date、delist_date|
|trade_cal|exchange、cal_date、is_open、pretrade_date|
|daily|stock_code、trade_date、open/high/low/close/pre_close、daily_return、volume、amount|
|adj_factor|stock_code、trade_date、adj_factor；单独保存，不改写daily价格|
|daily_basic|stock_code、trade_date、close、turnover_rate、volume_ratio、pe/pe_ttm/pb、dv_ratio/dv_ttm、total_share/float_share、total_mv/circ_mv|
|dividend|stock_code、end_date、ann_date、imp_ann_date、div_proc、stk_div、cash_div/cash_div_tax、record_date、ex_date、pay_date、base_date、base_share|
|stk_limit|stock_code、trade_date、pre_close、up_limit、down_limit|
|suspend_d|stock_code、trade_date、suspend_timing、suspend_type|
|namechange|stock_code、name、start_date、end_date、ann_date、change_reason|

数据列保留供应商空值，代码/日期为字符串，市场数值为64位浮点，schema_version为64位整数。上述各市场数据集均附来源字段。精确映射和校验以 providers.py 中 FIELDS/normalize 为准；筛选结果另存 stock_code、stock_name、as_of_date、match_status、rule_id 及 JSON 字符串 matched_factors/factor_values/condition_results/data_quality_flags。

行情按年月和 16 个稳定哈希桶分区，桶内按股票和日期排序。其他小数据集按月份或全量分区。版本清单映射分区到不可变文件，提交任务固定全数据集版本映射。结果 Parquet 保存因子及逐条件解释（JSON），SQLite 只保存引用和统计。

业务日期、公告可见日和 fetched_at 分开。历史回填的可见性按公告日期保守重建，不能证明供应商从未修订。相同数据内容重复同步不产生新行情行。

## V0.2 定投结果

沿用jobs，kind增加dca；payload保存DcaRequest（包括显式data_versions），snapshot保存相同版本映射及_dca_version/_universe_policy。result保存summary各字段、warnings、engine_version和不可变JSON路径。JSON内容包括summary、逐日curve、完整ledger、参数和数据版本、假设、fixture_mode；详见api.md及dca-rules.md。不新增现金/持仓数据库表，账本属于任务结果，可重复计算。

stock_basic新增可选字符串cnspell（供应商拼音首字母）；已有版本缺少字段时使用名称本地拼音匹配。发布时为旧表缺失的新列补null进行变更对比，重复发布幂等，旧文件不可变。

## V0.3 回测结果

jobs新增kind=backtest，payload为BacktestRequest，snapshot固定完整data_versions、_backtest_version/_strategy_version/_universe_policy。result保存summary、warnings、engine_version及不可变JSON引用；完整文件保存逐日curve、ledger、参数、假设和strategy_version。不新增表，持仓批次和分红税负仅在领域计算中构造，恢复按原版本重算。账本字段与汇总见api.md和backtest-rules.md。

## 多策略结果

新增回测JSON变体`mode: comparison`，公共metadata固定参数、数据版本、fixture_mode及comparison_version；summary记录策略数量、完整数量和整体完整性。strategies数组分别保存strategy_id、label、策略参数/版本、summary、curve、ledger、warnings、assumptions及excess_return。prices保存回测区间原始OHLC供K线使用；不作为信号输入回读。curve中return_rate为总资产/初始资金−1，drawdown为总资产/max(初始资金,截至当日资产峰值)−1。不新增数据库表，不改变旧结果结构。


## V0.4 组合结果

jobs新增kind=portfolio，payload为PortfolioRequest（members、allocation、rebalance及公共研究参数），snapshot固定完整data_versions、_portfolio_version、_backtest_version、每种信号的_strategy_version_*及_universe_policy=survivors_v1。无业务表迁移，不改变旧任务语义。

不可变JSON的mode=portfolio。顶层summary/curve/positions/ledger/warnings为实际账户，benchmark包含独立基准账户同类字段；公共parameters/data_versions/fixture_mode/engine_version/strategy_versions/assumptions/prices随结果固定，excess_return仅两者完整时计算。positions按日期保存现金、现金占比和逐股持仓，持仓包含策略、资格、目标/配置/实际权重、估值日期、权益、税负及待执行目标。账本增加stock_code，contribution/rebalance/unknown可为组合事件，buy/sell只对应实际模拟成交。

FIFO买入批次、权益的剩余未确定税股份/金额、已到账状态和已确定税负只属于领域计算，不新增业务表。部分卖出分摊登记金额，最后片段吸收残余分币；中断恢复从原请求重新构造账户，不重复到账或扣税。

## 信号通知（Alembic0004 / 0005 / 0006）

|表|用途与约束|
|---|---|
|notification_owners|内部owner_id及旧结构兼容字段；0005使session_hash/recovery_hash和expires_at失效，新鉴权不读旧字段|
|notification_channels|owner_id/type联合主键，Fernet ciphertext、目标摘要、verified/status、目标revision|
|notification_subscriptions|归属、股票、规范策略/方向/渠道、策略版本、status/revision、effective_after、last_check/waiting_reason；软删除保留记录|
|notification_checks|不可变outcome包含日期、固定数据/算法版本、方向、依据和数值；订阅/revision/交易日唯一|
|notification_deliveries|owner/subscription/check外键，信号日期/方向/渠道、配置和目标修订、status/attempts/next_attempt/error/accepted_at；订阅/交易日/方向/渠道唯一|
|notification_verifications|0004旧渠道验证表，0005清空并停止使用|
|notification_accounts|owner_id主键、规范化唯一email_hash、Fernet邮箱ciphertext；接续旧身份保留owner_id|
|notification_sessions|SHA-256 session_hash主键、owner_id外键及索引、expires_at；每设备独立会话|
|notification_auth_challenges|用途/账号/邮箱摘要生成key主键、purpose、可空owner_id、email_hash、验证码HMAC、expires_at、attempts；重发覆盖、成功消费与业务变更同事务|
|notification_limits|摘要key/窗口联合主键，持久计数，旧窗口清理|
|notification_service_state|SMTP故障码及配置摘要，变更配置解除旧故障|

outbox状态pending/sending/accepted/failed/cancelled/expired。accepted仅指服务端已接受。网络发送不持有写事务，检查与每个选择渠道的outbox同事务。验证码正文不保存，失效后用户重新请求；不会记录验证码或原始上游异常。notification_signal任务partial表示等待完整覆盖，已有同步/研究状态语义不变。

0006仅迁移通知业务数据：旧Bark渠道停用、撤销验证并递增目标修订；取消其pending/sending投递，从非删除订阅中移除bark，无剩余渠道的暂停并提示重新配置。仍有邮件的订阅修订不变，保留排队邮件。历史检查与已接受/失败投递、账号、会话和研究数据保留。迁移与版本标记同事务，中断回滚、重复启动不重复变更；downgrade不自动恢复凭据或取消过的消息。新渠道type为webhook/email，旧bark行仅保留历史兼容，列表不返回。stock_name为接口展示字段，不写入订阅表；历史名称仍在固定版本的outcome中。


0007新增notification_test_requests（id、owner_id、timestamp）记录所有实际发起的手动渠道/订阅测试额度，按账号滚动窗口读取；BEGIN IMMEDIATE事务原子检查并预留。notification_test_deliveries保存订阅示例的request_id、owner_id、subscription_id、trade_date、direction、channel、status、attempts、error、created_at、accepted_at及outcome快照；不关联真实notification_checks，不受真实信号去重唯一约束。进程中断保留unknown，不进入Worker恢复/重试队列。新表与Alembic版本标记同事务，中断可回滚重试，历史记录和旧版本研究数据保持可读。

### 日线OHLC异常隔离（schema_version=2）

新股票池任务标准化日线时增加可空字符串quality_reason/raw_ohlc。OHLC关系矛盾时quality_reason=ohlc_inconsistent，raw_ohlc为保留原始open/high/low/close/pre_close的JSON字符串；所有可计算市场数值（含volume/amount/daily_return）置空，保留股票/日期及来源信息。健康行两个字段为空。旧schema_version=1文件及严格标准化接口继续可读，混合文件按列名合并。

覆盖记录的complete仍表示接口范围获取完成，quality_status=degraded和quarantined_ohlc记录已隔离条目；不能将complete解释为所有报价有效。任务完成时按指定范围当前覆盖条目汇总异常数，存在异常则partial；恢复跳过已发布检查点也不会丢失异常警告。供应商后续修正可覆盖新版本的隔离记录，旧版本仍保留原始证据及未知值。

## 企业资料缓存与近期分红结果

无结构迁移。新筛选任务snapshot保存`_dividend_version`，Parquet JSON列`recent_dividend`保存事件列表、三值判断及不足标记，兼容缺列的旧结果。副标题日期在读取全体结果时聚合，不改写旧文件。

企业资料使用jobs.kind=`company_profile`，payload保存股票代码和明确观察日期，snapshot保存本地市场版本及`_company_version`。result.sections存区块的原始单位数据/公告日/报告期/获取时间/来源，pending_sections为持久未完成列表。最终结果在results/{job_id}/company.json原子发布；同一任务恢复保持文件内容不变，刷新创建新任务和结果，原任务仍可读取。公司静态信息为获取时快照，非历史公司资料；利润/现金流为合并年初累计报表，资产负债为期末余额；原币种主营构成不跨币种相加。

单位：本地daily_basic入库已将total_mv/circ_mv从供应商万元换成元，total_share/float_share从万股换成股；company valuation直接使用该标准化值。stock_company注册资本保留万元，财务报表金额及主营构成金额保留原币种基本单位，财务指标百分数保持原值。详情仅在展示时换算注册资本到元，金额绝对值严格超过10亿用亿保留1位小数、超过100万用万取整数；每个报表金额独立携带单位，悬停显示完整金额，不改写缓存及计算值。


## 匿名浏览事件（0008）

|表|字段与用途|
|---|---|
|analytics_pageviews|event_id UUID主键；visitor_hash为浏览器随机UUID的SHA-256摘要；page为固定页面类别；occurred_at为服务端UTC接收时间；local_date为站点时区自然日。索引(local_date,page)、(local_date,visitor_hash)。|

只保存最近90个自然日（含当天）；每日首次采集/查询时清理，不保留更早累计汇总。所有页面不重复事件UUID，重复提交不增计数；服务端不接受客户端时间。浏览器标识独立于通知账号，换设备或清除存储后重新打开会作为新访客；受限存储按当前页面生命周期识别。不上报邮箱、IP、用户代理、股票代码、完整URL、查询参数或fragment。


## 多自选列表（0009）

| 表 | 字段与约束 |
|---|---|
| watchlists | id UUID主键；owner_id关联notification_owners.id；name 1–80字符；codes JSON代码数组；revision正整数；created_at/updated_at UTC时间；唯一(owner_id,name)，owner_id索引。 |

列表属于稳定owner主体，更换登录邮箱不改变归属。更新与删除通过owner、ID、revision共同匹配，避免多设备覆盖。0009只新增此表／索引，在显式SQLite事务中完成DDL和版本标记；中断回滚及重复初始化安全，不改账号、通知、行情版本、旧任务或研究结果。

本机同结构列表位于独立版本化localStorage键 `ashare-watchlists-v1`，账号列表只缓存于页面内存。两者无导入、上传或合并流程；本机存储失败只保留编辑草稿并提示无法持久保存。账号退出／失效清理账号列表缓存，不清空本机列表。研究任务仍保存调用当时的明确代码与原有固定数据版本；修改列表不追溯改变研究。
