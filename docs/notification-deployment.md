# 通知部署与真实接入验收

## 多自选列表与共用登录会话（0009，开发服务器已部署）

2026-10-12按用户授权协调更新API／Worker／Web，后端9c801feb9719、Web38e358141524；自选入口 https://niu.kaistar.net/watchlists ，原域名兼容。0008→0009新增watchlists，复用稳定邮箱账号与会话，原25张业务表结构与旧应用一致，旧研究8个响应保持一致；quick_check=ok、外键检查无错误，服务健康、重启0、OOM=false。

按本次明确要求没有备份或复制开发数据库。原行情卷、.env、加密密钥、SMTP、Secure Cookie和现有自动发送true保持不变。新Cookie为ashare_user_session，Path=/api/v1；旧通知Cookie在现有会话查询中验证后迁移并删除旧path，不延长原有效期。部署前后的旧研究摘要及上线验收期间关键数据核对、检查范围限制详见docs/development.md。

主域名／旧域名桌面1440px和手机390px公网自选维护／筛选和组合调用／登录弹窗检查通过，私有401、跨源403、页面错误0、溢出0，fixture=false。验收只维护独立浏览器本机列表，未创建线上账号／研究任务或主动发送验证码／测试邮件；真实账号登录和SMTP实收留待用户主动核对。报告及截图在私有/tmp/niuniu-watchlists-deploy，不含数据库备份或环境秘密。

旧镜像保留pre-watchlists-20261012标签；旧迁移代码不识别0009，不能直接对新库启动，仅用于受控兼容重建参考。历史备份要求见以下历史章节，本次以用户明确“不备份数据库”指令为准。

## Webhook及三页签版本（0006，开发服务器已部署）

部署时先备份一致SQLite及保留旧镜像，协调停止旧API/Worker后迁移，再以匹配版本启动API、Worker和Web，避免旧Bark发送器在退役过程中继续投递。保留现有Fernet密钥、SMTP和自动发送设置，不重新生成密钥。0006停用旧Bark、取消其待发送记录，保留账号、会话、检查、历史发送、邮件修订和排队邮件；仅Bark的订阅暂停。用户需在第三页签重新绑定Webhook并明确修改订阅。上线核对迁移标记、邮件继续投递、旧历史可查、私有401和同源保护。0006降级不会恢复旧凭据或队列；回退不得直接启用旧Bark发送器或覆盖有新业务的数据库。

2026-10-11按用户明确指令部署到当前开发服务器，入口为 https://niuniu.kaistar.net/notifications 。API与Worker使用镜像dd4e895ed657，Web使用90b44e30377f；三个服务运行、重启0、OOM=false，API健康。0005→0006升级前后23张业务表的结构和行数均保留，关键内容摘要及每条渠道/订阅均核对，只发生预期的旧Bark停用和相关订阅调整。账号、会话、邮件及历史保留，原渠道均可用原密钥解密；quick_check及foreign_key_check通过。

原.env逐字节保留，API/Worker配置均与Compose一致；SMTP、Fernet密钥、HTTPS入口、Secure Cookie及NOTIFICATION_AUTO_SEND=true保持原值，fixture_mode=false，公开发送渠道为webhook/email。公网健康、通知及退订页200，未登录私有资源401，跨源发码403。公网网关拒绝默认Python请求代理，使用浏览器请求代理及真实Chromium验收通过。四类原研究的8个详情/结果响应摘要完全一致；桌面1440px与手机390px核对三页签、底部数据管理入口、不同研究图标、退订fragment清除通过，无溢出或页面异常，业务写请求0。未创建验收账号、订阅或发送验证码/通知。

切换前行情同步尚在运行，旧Worker未在60秒内结束，由Docker停止（exit137，OOM=false）。新Worker恢复同一任务为attempt2，原5591个检查点及其版本ID全部保留；最终观察5703个检查点，任务继续运行。原行情卷未替换，升级及重复初始化先在隔离一致副本演练通过。私密备份位于/var/backups/ashare-lab/webhook-dev-20261011-062539/，目录700，数据库、环境及报告600；旧镜像保留为ashare-lab-backend:pre-webhook-dev-20261011-062539和ashare-lab-web:pre-webhook-dev-20261011-062539，已验证旧后端及nginx可用且镜像不含运行时环境秘密。回退需先停写并核对上线后业务变化，禁止用旧备份直接覆盖当前库或重新启用旧Bark发送器。

完整pytest338、Vitest79、Ruff、mypy、tsc及构建此前通过，新后端镜像内101项通知/迁移/同步恢复测试及Ruff/mypy复验通过，Web镜像内tsc/构建通过。真实公网Webhook/Bark设备及SMTP实收仍需实际接收端核对；隔离TLS接收器与fixture验收不等同实收。旧Bark用户需在「账号与渠道」重新绑定Webhook并修改订阅。以下0004/0005及开启自动发送内容保留为历史部署记录。

## 邮箱登录版本（0005，已部署）

2026-10-11已按用户授权完成生产0004→0005升级，API、Worker和Web协调切换。上线前已备份SQLite、保留并验证回退镜像；API与Worker使用同一原Fernet密钥，禁止重新生成。升级保留owner_id、订阅、Bark、检查及发送历史；旧匿名Cookie和恢复码失效，所有用户需通过邮箱验证码重新登录。旧已验证邮箱首次登录接续原身份，无已验证邮箱的旧数据保留；邮箱归属冲突由管理员处理。升级中断回滚及重复启动已纳入隔离测试。

部署时协调API、Worker及Web版本，避免旧匿名鉴权与新邮箱鉴权同时对外服务。迁移后旧鉴权字段保留失效占位值，旧版本应用不能用于继续登录；不要直接覆盖为迁移前数据库备份，先核对上线后的业务变化再制定回退。NOTIFICATION_AUTO_SEND继续保持原设置，本次鉴权改动不构成自动发送实收验收。

上线人工检查：真实邮箱验证码到达、首次/再次登录、同邮箱跨设备读取同一订阅、单设备退出、验证码过期/重发/尝试限制、更换邮箱前业务不变及成功后旧设备退出、旧订阅/Bark保留、停用邮件不影响登录、登录不恢复已退订邮件。真实实收由用户在页面主动发码核对，隔离SMTP替身测试不算真实接入。

默认 `NOTIFICATION_AUTO_SEND=false`。新增 Alembic `0004`，API与Worker启动执行迁移；上线前按既有部署流程备份SQLite及不可变数据版本，保留旧镜像。2026-10-11已按用户要求部署API、Worker和Web，并完成生产0004迁移；自动发送仍关闭，真实接收验收未完成。

API与Worker使用同一 `NOTIFICATION_ENCRYPTION_KEY`（Fernet密钥），通过部署环境秘密配置提供，生成时不要把值写入终端日志、Git或测试报告。禁止自动生成替代密钥。备份并妥善保管；丢失后已有渠道不可解密，换密钥使旧邮件退订凭据失效。用户必须重新绑定渠道。新增依赖cryptography已进入pyproject.toml及requirements.lock。

设置 `NOTIFICATION_PUBLIC_URL` 为网页的准确origin（协议、域名、端口），用作写请求同源检查和邮件链接。生产HTTPS用 `NOTIFICATION_COOKIE_SECURE=true`；HTTP站点须显式设置false以使浏览器会话工作，切换HTTPS后改为true。默认会话180天、每身份20条，可用 `NOTIFICATION_SESSION_DAYS`（1–365）、`NOTIFICATION_SUBSCRIPTION_LIMIT`（1–100）配置。

0006使用用户填写的公网HTTPS Webhook URL，不再读取BARK_SERVER_URL；遗留环境项可移除，应用会忽略。完整URL加密保存，HTTP/TLS验证证书并固定连接至经验证的公网IP，超时15秒，不跟随跳转，不使用环境代理。Bark接入填写`https://api.day.app/<Device Key>`，参见[官方教程](https://github.com/Finb/Bark/blob/master/docs/tutorial.md)。

邮件统一设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURITY`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM_EMAIL`、`SMTP_FROM_NAME`。默认starttls/587，SSL部署设置ssl/465；禁止明文模式。证书由系统CA校验，TLS失败停止，不降级，不开启SMTP debug。适配器使用 [Python SMTP](https://docs.python.org/3.12/library/smtplib.html) 和 [EmailMessage](https://docs.python.org/3.12/library/email.message.html)。认证可留空（仅适用于管理员配置的支持TLS且无需认证的邮件网关），密码/授权码只来自环境。配置错误持续显示不可用；修改配置、重启API及Worker后按新配置重新测试。

默认不信任X-Forwarded-For或X-Real-IP。内置Uvicorn禁用自动代理头解析。nginx覆盖X-Real-IP，只有 `NOTIFICATION_TRUSTED_PROXIES` 中指定的代理IP/CIDR才信任该头；应由部署者按自己的代理网络设置，禁止不加区分信任所有互联网地址。代理Host应保留端口；公网origin以配置值为准。共享访问密钥仍保护原研究/数据接口，通知只豁免文档列出的准确路径和方法。

上线验收逐项记录证据（脱敏），不能以单元测试的上游替身替代：

1. 在隔离环境完成pytest、Ruff、mypy、Vitest、tsc和构建；检查迁移/恢复、重复调度、原始版本重算及旧任务结果不变。
2. 在真实数据仓库固定最近已收盘日期和五个依赖版本，人工核对五类策略窗口、指标值、方向；确认未来数据未读取、已退市证券排除。此过程仅计算验证，自动发送保持关闭。
3. 在账号与渠道填写自己的Webhook URL；Bark使用含Device Key的URL绑定，核对手机收到带“测试”的通知以及API只记录服务端接受。
4. 配置真实SMTP，核对邮箱验证码到达、过期/重发、已验证邮箱的测试邮件到达、TLS和发件身份配置。
5. 在隔离验收库中以真实行情计算一条确实触发的信号，通过发送适配器验证手机与邮箱收到各一条真实信号（无伪造成功结果），核对文本/HTML、参数、日期、版本、Message-ID。使用真实收件人且避免公共报告记录地址或凭据。
6. 打开邮件退订页，确认GET未退订、POST后只移除邮件而Webhook保留；更换邮箱后旧链接不可用。核对邮箱验证码登录、多设备退出/更换邮箱撤销及其它邮箱账号无法访问。
7. 验收通过后管理员设置 `NOTIFICATION_AUTO_SEND=true` 并重启API/Worker。观察最近交易日已完成检查、等待覆盖原因、独立重试和过期结果。fixture环境即使配置true也禁止自动推送。

当前部署已获用户授权；SMTP配置现已加载，真实Bark设备接收、真实行情人工上线审核、Bark手机实收、SMTP验证码及信号邮件实收、真实退订验收均列为启用自动发送前的待办，不宣称已完成。只读真实仓库的独立NumPy方向核对通过也不能替代这些实收验收。

可运行 `scripts/verify_notifications_real.py --data-dir <仓库路径> --trade-date <交易日> --versions-file <JSON文件>` 进行只读方向核对。JSON必须明确包含stock_basic/trade_cal/daily/adj_factor/suspend_d五个版本ID，禁止临时回退当前版本；日期必须显式给出。脚本只迁移临时SQLite备份，生产市场文件只读，不启动API/Worker、不调用供应商或发送通知；用既有独立NumPy算法比较三个指定股票的五类默认策略，并输出数值依据、数据/算法版本和方向结果。

## 2026-10-11 首次通知部署（0004，历史记录）

线上入口：http://180.178.62.82:18080/notifications 。API及Worker使用新后端镜像4a13c9a98f4e，Web使用ab0f31fec815；三容器运行、重启0、OOM=false，API健康。数据库0003→0004，原12张表的定义、行数和数据版本指针在迁移前后完全一致，仅新增8张通知表；恢复Worker后原行情同步按检查点继续发布新数据。

首次部署时由管理员显式生成Fernet密钥写入未跟踪的.env，API与Worker共用；没有应用运行时自动替代密钥。SMTP尚未配置，邮件选项显示不可用。PUBLIC_URL对应当前公网origin，HTTP会话Secure=false、HttpOnly与SameSite=Lax保留；仅信任当前专用Docker桥172.19.0.0/16中的代理，公网API端口仍未发布。NOTIFICATION_AUTO_SEND=false，fixture_mode=false；没有创建生产验收身份、订阅或发送记录。

旧镜像保留为ashare-lab-backend:pre-notifications-20261011-1149和ashare-lab-web:pre-notifications-20261011-1149。迁移前SQLite通过backup API得到一致副本，quick_check=ok，位于/var/backups/ashare-lab/notifications-20261011-1149/business.before.db；该私有目录700，数据库、环境备份及报告600。不可变行情文件仍保留于原real-data卷，未替换或删除。环境备份含部署密钥，仅限管理员读取，禁止上传或输出。回退前须停止写入、核对备份之后的业务变化；现有同步已继续，禁止直接用旧备份覆盖当前生产库。

原同步未能在60秒停止期限内完成，旧Worker由Docker结束（exit137、OOM=false）；新Worker恢复同一任务为attempt2。原5265个完成检查点和版本ID全部保留，观察时已新增237个检查点，任务继续运行。恢复后的百分比按当前计划重新计算，不以百分比变化判定检查点丢失。

新镜像隔离复验96项pytest、Ruff检查/63文件格式、mypy37文件通过，前端镜像内tsc及构建通过；此前完整pytest300项与Vitest72项通过。公网通知页、退订页、健康及公开选项HTTP200；无凭据读取私有通知资源401，跨源创建403。原四类研究的8个详情/结果响应SHA-256一致。Chromium在1440px与390px只读核对通知页、导航、退订fragment清除、股票详情预填入口和既有回测参数订阅入口通过，无横向溢出、共享密钥弹窗或页面错误，业务写请求0。浏览器验收使用原成功回测读取股票/策略配置，不创建回测任务。

### SMTP配置后续生效

2026-10-11按用户指令重新创建API、Worker及Web，加载用户填写的SMTP配置。当前公开选项channels.bark=true、channels.email=true，auto_send=false、fixture_mode=false；API及Worker配置与.env一致，服务运行正常。配置完整不代表SMTP账户已成功发送，尚未执行真实验证码或邮件实收验收，请在通知页面发送验证码、验证邮箱并测试邮件后再完成真实信号及退订验收。报告位于/tmp/niuniu-smtp-activation-20261011-122327/，不包含原始配置或凭据。

### 当前HTTPS入口

2026-10-11根据用户实际访问方式，将NOTIFICATION_PUBLIC_URL改为https://niuniu.kaistar.net、NOTIFICATION_COOKIE_SECURE=true，并重新创建API/Worker/Web。当前正式通知入口为https://niuniu.kaistar.net/notifications 。HTTPS浏览器真实创建及读取匿名身份通过，Secure/HttpOnly/Lax Cookie生效，跨源请求仍403；邮件退订链接将使用此HTTPS地址。自动发送继续关闭，Bark/邮件配置可用，真实邮件及手机实收验收仍待完成。

## 2026-10-11 邮箱验证码鉴权部署（0005，历史记录）

当前入口：https://niuniu.kaistar.net/notifications 。API与Worker镜像c3a7182bbfe3，Web镜像dd5c63891667，三服务运行、重启0、OOM=false，API健康。切换时无运行或排队任务。原17张业务表定义、行数及已记录的关键表内容摘要保持一致，数据版本指针、原1条订阅和2个通知渠道均保留，渠道可用原密钥解密；SQLite quick_check=ok。旧匿名会话及恢复码已失效，旧已验证邮箱首次验证码登录接续原订阅和Bark。筛选、定投、单股及组合的8个原详情/结果响应SHA-256一致。

.env与部署前私密备份逐字节一致，PUBLIC_URL保持HTTPS域名，Secure Cookie开启，SMTP及加密密钥保留，fixture_mode=false、NOTIFICATION_AUTO_SEND=false。公网健康/通知/退订/公开选项200，未登录私有资源401且返回notification_unauthorized，跨源发码403。Chromium1440px/390px邮箱登录页均通过，无横向溢出、页面错误或业务写请求；未创建验收账号、订阅或发送验证码。真实SMTP验证码到达仍待用户主动发码核对。

完整本地pytest312、Vitest74、Ruff/77文件格式、mypy39文件、tsc/build已通过；新后端镜像内48项通知/鉴权/传输测试、Ruff及mypy复验通过，前端镜像构建通过。隔离生产库副本0004→0005及重复启动通过。私密备份目录/var/backups/ashare-lab/email-auth-20261011-051516/（目录700、报告/环境/数据库文件600），包含一致SQLite备份、原环境及脱敏验收记录。原行情卷未替换。回退镜像ashare-lab-backend:pre-email-auth-20261011-051516与ashare-lab-web:pre-email-auth-20261011-051516由原运行容器导出保存，未导入运行时环境秘密，分别验证旧鉴权及nginx配置可用。回退须先停写并核对上线后变化，不直接覆盖当前库。

## 2026-10-11 自动信号推送开启

用户明确要求配置并启动自动信号推送。私密备份原.env后，将NOTIFICATION_AUTO_SEND改为true并重新创建API/Worker，未修改SMTP、加密密钥或订阅。两个服务均已加载true，公网通知选项auto_send=true、fixture_mode=false、Bark/邮件可用；三个服务运行、重启0、OOM=false，API健康。当前有1条active订阅及active的Bark/邮件渠道，开启前无排队或运行任务。调度每60秒检查一次，在上海时间16点后按最近已收盘交易日与数据覆盖执行，满足订阅规则才生成推送。当前开启时未到16点，不强行提前执行收盘检查；真实邮件/手机实收未宣称验证通过。

## 2026-10-11 通知体验与订阅测试：开发服务器上线

入口：https://niuniu.kaistar.net/notifications。已升级至0007并切换API/Worker/Web，后端镜像6aabf4a46454、Web镜像5b8b9f3f3722；三个服务正常，API健康，重启0且非OOM。用户说明开发服务器不一定需要数据库备份，本轮未额外备份；保留pre-notification-usability-20261011旧镜像标签。迁移重复初始化、外键检查、10张旧表行数/内容摘要及8个原研究响应一致性核对通过，新手动测试表为空。原密钥/SMTP/自动发送true及数据卷保留。

Docker内84项通知相关测试、Ruff、mypy、前端tsc/build通过；安装源码与工作区41文件完全一致。公网HTTP200、私有401、跨源403和1440/390px只读浏览器通过，页面错误/横向溢出/业务写请求均0。此次未主动发送验证码或通知，公网Webhook和真实邮箱实收需用户通过新测试入口核对。脱敏报告及截图仅位于/tmp/niuniu-notification-usability-deploy，非数据库备份。


2026-10-11主域名更新：NOTIFICATION_PUBLIC_URL=https://niu.kaistar.net，用于通知退订等链接及同源验证；PUBLIC_URL_ALIASES=https://niuniu.kaistar.net显式允许原域名作为兼容入口。别名为逗号分隔的纯HTTP(S) origin，禁止路径、查询、fragment或用户名；浏览上报和通知写请求共同检查主域名、别名或请求base_url，未知来源仍拒绝。浏览器Cookie保持Secure/HttpOnly/Lax及原path，两个域名的浏览器存储和登录Cookie各自独立，邮箱账号及后台密码共用原数据库。
