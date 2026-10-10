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

状态：queued、running、cancel_requested、cancelled、succeeded、partial、failed。partial 专用于同步的可选数据降级，筛选发布必须整体完成。数据库启用 WAL、外键和 busy_timeout。

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
