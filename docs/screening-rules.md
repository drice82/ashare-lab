# 筛选与公式

交易日 D 收盘后筛选。非交易日拒绝，不自动迁移日期。窗口按交易日历计算，缺失行情不前填；上市不足与缺口均产生质量标记。信号价格 P*t=P_t adj_t/adj_D；原始收盘价单独展示，复权基准仅为 D。

daily_return=close/pre_close-1（除权昨收）；return_Nd=P*D/P*(D-N)-1，需要 N+1 日；maN 最近 N 日收盘均值（含 D）；volume_ratio_Nd=V_D/mean(V_D-N…V_D-1)，分母不含 D；distance_ma20=P*D/ma20-1；ma20_change_5d=ma20_D/ma20_D-5-1；drawdown_60d=1-P*D/max(最近60日复权high，含D)；breakout_20d=P*D>max(此前20日复权high，不含D)。成交额最近20日含 D，单位元。

股息率：过去12个日历月内已派息现金股息（税前每股）/当日未复权收盘价。事件须已实施、ann_date/imp_ann_date/ex_date/pay_date 有效且不晚于 D；公告只有日期时从下一交易日可见。送转/股本口径无法确认，缺少公告日期、缺少完整同步均返回未知。存在可疑已实施事件时不能只忽略事件后继续声称股息完整。同期送转直接标记 unsupported_share_basis（首版不推断换算）。

历史名称按公开且有效区间解析 ST；不能用当前名称反推。无法确认历史状态则要求排除 ST 的规则未知。历史数据标记 conservative_pit，供应商修订仍是限制。无未来行情访问与按公告日期过滤有测试；不声称拥有完整历史供应商快照。

默认 A：股息≥4%、日收益≤-3%、排除ST/停牌。B：股息≥4%、回撤≥10%、20日平均额≥3000万元。C：股息≥4%、20日≥5%、60日≥10%、close>ma20。D：前5日量比≤0.7、|distance|≤2%、ma20五日变化>0，窗口完整。E：20日突破、前20日量比≥1.5、close>ma20。所有阈值在 registry 提供合法范围并可配置，指标窗口固定为上述定义；改变窗口需登记新因子和版本。B–F默认不排除ST，所有规则默认排除停牌，两个开关均可配置。

|规则|参数（API值）|默认值|数据依赖及最短窗口|
|---|---|---|---|
|A|dividend_min、daily_return_max|0.04、-0.03|当前daily、过去12个月可靠现金事件、历史名称、停牌或当日有成交证明|
|B|dividend_min、drawdown_min、amount_min|0.04、0.10、30000000|60日daily/adj_factor、20日成交额、分红|
|C|dividend_min、return_20d_min、return_60d_min、price_ma_min|0.04、0.05、0.10、1|61日daily/adj_factor、分红|
|D|volume_ratio_max、distance_max、ma_growth_min|0.7、0.02、0|25日daily/adj_factor（此前MA20需要额外5日）|
|E|volume_ratio_min、breakout_ratio_min、price_ma_min|1.5、1、1|21日daily/adj_factor（此前最高和量比分母不含当前日）|
|F|group.logic/children、factor/operator/value/enabled|当日收益≥0 AND 前5日量比≥1|由启用条件的因子决定|

共同参数 exclude_st/exclude_suspended 控制排除，布尔状态未知时不能通过对应排除条件。字段取值范围由规则JSON Schema返回，例如股息阈值0–1、成交额0–1e13、量比0–100；禁止NaN/无穷、未知字段和未知因子。daily_basic 的供应商股息字段保存供研究，不用于这些历史高股息规则。

F：条件树支持 AND/OR、gt/gte/lt/lte/eq、enabled。白名单数值/布尔因子，最大深度4、最多64个节点（含组）；禁止代码执行；空树/全部禁用返回422。三值逻辑：AND 有 false 即 false，否则有 unknown 即 unknown；OR 有 true 即 true，否则有 unknown 即 unknown。缺失关键值不能默认为命中。

输出 match_status=matched/not_matched/insufficient_data，factor_values、matched_factors、condition_results、data_quality_flags。比例在 API 为小数，网页显示 %；missing 为 null/—。每个任务固定参数、版本、股票池和结果排序。
