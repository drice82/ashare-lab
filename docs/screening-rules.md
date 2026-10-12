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

## 当天策略信号筛选（1.0.0）

筛选方式为 rule（原 A–F）、signal（独立信号）、rule_signal（规则与信号同时满足）。旧请求缺少 mode 时仍为 rule。信号仅判断指定交易日收盘，不筛选“已有多头/空头趋势”，不模拟成交，也不要求卖出信号对应实际持仓。

支持双均线、MACD、RSI、唐奇安、布林带五种技术策略，每种最多一组参数，任一策略触发目标方向即满足信号条件；不支持买入持有。参数、预热长度、递归种子、复权锚点及交叉容差复用现有共享信号算法。添加其他策略不改变原策略窗口与结果。direction 为 buy、sell、either，默认 buy。

独立信号 parameters 仅允许 exclude_st/exclude_suspended，默认 false/true；叠加时使用原规则参数和排除设置。停牌、零成交量当天不触发信号，即使关闭排除停牌也不会生成技术买卖信号。排除历史 ST 时，状态未知不能放行。

需要 stock_basic、trade_cal、daily、adj_factor、suspend_d 固定版本；缺少依赖版本返回409，日期非交易日返回422。窗口日历、行情、复权因子或完整覆盖不足时，对应策略返回 insufficient_data。多策略 OR：有 true 即 true，否则有 unknown 即 unknown，否则 false；规则叠加 AND：有 false 即 false，否则有 unknown 即 unknown，否则 true。与信号无关的股息缺失不改变独立信号命中判断。

signal_results 保存每种策略的状态、方向、trade_date、parameters、matched、reason、evidence 和版本。所有数值行情读取止于筛选日。共享日历和窗口覆盖，按256只分批读取行情/复权/停牌；算法仍逐策略验证自己的窗口。任务快照保存 _signal_version 和各 _strategy_version_*；中断恢复沿用原快照，不兼容版本拒绝执行。

历史任务名称说明规则含义，如“规则 A：高股息 + 单日下跌”，副行展示保存的实际阈值与排除设置。信号任务展示策略、方向、参数；叠加任务同时说明两类条件。F保留启用条件的 AND/OR 层级，长条件可展开。不能用当前默认值补填旧任务，缺失参数或未知规则版本明确显示未知。旧结果不补算信号、不改写文件；新增CSV信号信息及固定版本/fixture标记。

## 近期分红（计算版本1.0.0）

独立筛选及规则/策略叠加条件，默认未来30个自然日，可调1–365。窗口为`筛选日 < 股权登记日 <= 筛选日+days`；仅纳入公告日及实施公告日均早于筛选日、进度为实施、税前每股现金为正的安排。筛选日当天公告按保守口径不放行，预案及未来公告不影响结果或质量标记。重复事件去重，冲突金额/除息日/派息日返回未知；覆盖不足且无可靠事件时为数据不足，有可靠正例可确认存在近期安排。

每10股派息=`cash_div_tax*10`；万元预计税前分红=`10000/筛选日未复权收盘价*cash_div_tax`，按金额比例允许小数股，四舍五入保留两位小数，不扣税费。无筛选日有效价格时未知，不回退历史价格。送转不计入预计现金。万元估算不代表持股登记资格或到账金额。待派息安排不进入历史股息率、回测或到账分红；旧任务结果不重写，新筛选继续排除固定证券版本中的已退市股票。
