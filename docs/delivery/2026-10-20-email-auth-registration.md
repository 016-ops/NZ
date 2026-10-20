# 本轮开发交接：邮箱注册、管理员邮件认证及平台商城关闭

## 基本信息

- 日期：2026-10-20。
- 范围：用户确认后的后端账号策略变更；Web/Launcher 共用认证领域。HTTP 适配、前端页面和反馈/专题实现由并行模块交付。
- 代码：`revival/admin_email_auth.py`、`service.py`、`email_verification.py`、`mailing.py`、`security.py`、`administration.py`、`models.py`、`test_accounts.py`、管理 CLI、迁移 `0024_email_admin_auth.py`、退役DEV工具及相关测试；`docs/API.md`同步最终合同。
- 本轮未连接部署目标或远程业务库、未部署、未读取运行时凭据、未发送真实邮件；数据库验证只使用本机 3307 的独立可销毁测试库。

## 已实现功能

### 邮箱自助注册和角色初始化

- 正常注册仅要求邮箱、密码、昵称和一次性 register 邮件码，不检查也不消费邀请码，无人工审批。`ACTIVE` 账号、已验证邮箱、角色、钱包、按当前配置启用的原生初装、成功审计与幂等回执在同一 MySQL 事务内提交；昵称冲突、初装或审计失败全部回滚，验证码不会被半消费。
- 注册返回 `account_id/status/game_id/character_id/character_required=false/email_verified=true` 等原字段。前端用同一内存密码立即调用玩家登录实现自动登录，注册本身不额外签发 Session。
- 迁移不改变任何既有玩家状态，不批量激活历史待审、停用或封禁账号；历史 `PENDING_REVIEW` 即使邮箱已验证仍需管理员批准，登录/补验/恢复不再隐式激活。停用与封禁不被升级解除。已有角色/账号ID/game_id/钱包和历史资产保留。
- `ACTIVE` 无角色玩家允许登录，响应 `character_required=true`；只有当前绑定邮箱已验证才可调用 `character.create` 补齐昵称、钱包及初装，否则返回 `EMAIL_UNVERIFIED/403`。昵称为 NFC 后 1–24 字符、UTF-8 最多48字节；空值、重名、非法字符和已有不同角色均有中文提示。
- 只有显式 `test_profile=True` 且关闭邮箱验证的旧合成夹具保留邀请路径；正常 profile 即使设置关闭验证也不能用用户名绕过邮箱注册。

### 管理员固定邮箱及操作级邮件码

- 登录 `POST /api/admin/login-code`：`{login,password}`；密码验证通过后只向代码固定管理员邮箱投递6位码，请求自带 `email` 不能覆盖收件地址。
- 登录 `POST /api/login`：`{kind:"admin",login,password,challenge_id,code}`；密码与挑战共同确认，旧 `otp_code` 无效。
- Owner 高风险取码 `POST /api/admin/step-up-code`，Bearer 为当前管理员 Session，正文为 `{action,payload,metadata:{request_id,idempotency_key,reason},current_password}`；payload 不能夹带认证或元数据字段。
- 提交仍走 `POST /api/command`，原业务及元数据加 `current_password/challenge_id/code`。挑战绑定管理员、安全版本、用途、Session 哈希、动作、业务内容、原因和幂等键。换 Session、动作、目标、金额、新密码或键都不能借码提交。
- 登录和高风险用途独立，5分钟过期、60秒冷却、最多5次错误；登录确认的错误密码与错误验证码共享同一所属挑战额度，其他login不能锁定该挑战；来源20次/5分钟与60次/小时、每管理员每用途10次/小时、发送全局100次/小时在 MySQL 独立持久化。错误密码也占来源额度；SMTP 不在部署锁内执行，失败不激活码、不自动重试未知投递。
- 取码返回 `challenge_id/expires_in=300/retry_after=60/recipient_hint`；高风险另返回 `operation_summary:{action,confirmation}`。邮件仅展示公共动作名和12位HMAC确认指纹，不含业务正文或密码。
- 错误码：缺认证 `REAUTH_REQUIRED/403`；无效、已用、过期或超次挑战 `ADMIN_EMAIL_CODE/403`；发送额度 `RATE_LIMIT/429`；SMTP 未配置/发送失败 `MAIL_NOT_CONFIGURED` 或 `MAIL_DELIVERY_FAILED/503`。
- 最终码消费与 Session 或业务/审计同事务；错码次数独立提交。并发消费最多成功一次。已提交的同键同业务重试恢复原结果，无需二次消费邮件码，但仍验证当前 Session 和权限。
- 管理员原密码哈希保留；新创建/重置密码统一12–128字符。旧短密码仍可验证。无 TOTP 算法、固定码或免码认证入口，管理恢复函数返回 `AUTH_METHOD_RETIRED/403`；旧 `admin.mfa.reset/admin.recovery.rotate` 命令从分派表删除，返回404。
- `create_test_admin/create_demo_admin/recover_admin_mfa`、旧 `test_accounts` helper 以及 `provision_dev_owner/verify_dev/verify_email_dev/mail_acceptance` 的旧自动化认证入口在读取私密文件/密码或连接前明确退役；不生成例外配置或重用旧运行时TOTP记录。初始Owner仍通过 `bootstrap_owner` 交互创建，后续从门户邮件认证。

### 平台商城关闭与反馈权限接入

- Web/Launcher 的 `shop.purchase`、`player.shop.quote`、`player.shop.purchase` 均在认证后、幂等读取前返回 `PLATFORM_SHOP_CLOSED/403`，历史成功回执不能绕过新门禁；`player-shop` Service 视图同样关闭。
- 玩家本机代理及 POST view 白名单由 HTTP/Launcher 适配层收紧；管理员商品管理、共享原生购物车提交函数以及 `/api/game/command` 独立分派保持可用。测试分别验证平台关闭及原生购物车成功，不能将领域夹具调用当成公开购买入口。
- Service 接入 `feedback.create/reply/status`、`feedback/feedback-admin/feedback-detail` 及 `campaign.presentation.save/campaign-detail`。Owner/users 处理、auditor只读、resources拒绝；跨身份 `feedback.reply` 的管理员写权限在幂等前复查，模块继续逐工单检查归属。

## 未实现内容

- 无本轮已授权后端功能缺项。真实QQ收件验证未执行，不把 SMTP mock 当作邮件送达；前端与本机Launcher页面验证由对应交接记录提供。
- 原生实玩、干净Windows安装和远端部署不属于本轮验收；本轮不触碰远端配置。

## 当前阻塞

- 真实邮件验收依赖操作者配置有效 `NZ_MAIL_*` 和实际固定邮箱收件确认；当前未读取私密配置，也未发送真实邮件。未配置时管理员认证明确失败关闭，不能使用旧TOTP或测试账号例外替代。

## 验证记录

所有数据库命令显式选择空环境文件 `NZ_ENV_FILE=/private/tmp/nz-empty-auth-test.env`、`NZ_MAIL_ENV_FILE=/private/tmp/nz-empty-auth-test.env`，指定本机专用连接与 `NZ_TEST_DB_NAME=nz_fix_auth_tests`；未接触预览业务库。使用实际 MySQL 26.7.0。SMTP只有投递端mock；挑战、密码、事务、幂等、并发和迁移均真实执行。

| 验证项 | 实际结果 | 证据/范围 |
|---|---|---|
| 邮件认证与开放注册首组 | 9通过，0失败/错误/跳过 | `evidence/2026-10-20-admin-email-auth-tests.json` |
| 认证及Accounts/Assets/管理生命周期兼容、原生商城定向 | 48通过，0失败/错误/跳过 | `evidence/2026-10-20-auth-compatibility-tests.json` |
| 邮箱验证/别名/登录、导入、PvE | 53通过，0失败/错误/跳过 | `evidence/2026-10-20-auth-email-pve-tests.json` |
| 最终邮件边界/迁移、退役工具、原生功能与初装商城（玩家迁移策略修正前） | 49通过，0失败/错误/跳过；116.26秒 | `evidence/2026-10-20-auth-native-final-tests.json` |
| 最终旧待审状态保护/ACTIVE verified角色初始化及邮箱验证专项 | 27通过，0失败/错误/跳过；45.865秒 | `evidence/2026-10-20-pending-policy-fix-tests.json` |
| 最终登录错误密码/验证码同挑战5次预算及完整认证专项 | 14通过，0失败/错误/跳过；43.062秒 | `evidence/2026-10-20-admin-password-budget-final-tests.json` |
| 完整MySQL回归（上述保守登录预算补丁前） | 619执行、605通过、14测试/6套件跳过、0失败/错误；823.358秒 | `evidence/2026-10-20-full-mysql-tests.json`，不包含后补错误密码专项 |
| 退役工具私密IO/网络防护 | 2通过 | 纯内存 unittest，不初始化数据库或SMTP |
| Python编译及迁移静态一致性 | compileall通过；makemigrations无变化 | 静态命令的本机迁移历史连接被沙箱拒绝，实际结构由数据库suite新建迁移验证 |
| 本轮中文注释 | 已人工检查所改模块职责、事务、失效/限流、夹具边界和退役原因 | 不宣称补齐全项目存量注释 |

最终四组数量可能包含重复测试ID，不能相加声称独立总覆盖；全量回归由主代理明确授权在独立 `nz_fix_v2_full_tests` 执行；首轮611项仅1项缺rarfile环境错误，596通过、14测试/6套件跳过，无断言失败。按仓库固定rarfile4.2补齐后图片工具5项（4通过/1 Windows跳过）复验通过。第二轮因纠正0024玩家批量激活策略主动中断，仅37项局部通过，不能作为全量证据。随后因补齐旧待审登录/恢复状态保护再次主动中断，80项局部通过也不是全量证据。最终旧待审/角色策略完整回归619项，605通过、14测试/6套件跳过、0失败/错误，823.358秒，报告 `evidence/2026-10-20-full-mysql-tests.json`。该轮开始UTC 08:39:17，不含其后新增登录错误密码预算修复；最终认证专项14项已通过（43.062秒，0失败/错误/跳过），报告 `evidence/2026-10-20-admin-password-budget-final-tests.json`；最终提交完整CI独立记录，不能将619项完整轮说成覆盖该新增测试。历次报告见 `evidence/2026-10-20-full-mysql-attempts.json`。

## 接手与回退

- 依赖顺序：先备份与停写评估，再应用 `0024_email_admin_auth`，随后反馈/专题 `0025_feedback_and_campaign_presentation`；本轮仅对测试库执行迁移。
- 0024 清空旧 TOTP 密钥及恢复码，所有管理员安全版本加一并撤销旧 Session；不改变任何玩家状态、制裁或资产。反向迁移不会恢复已撤销凭据或会话。
- 配置变量仅包括名称：`NZ_SECRET_KEY`、`NZ_MAIL_ENV_FILE`、`NZ_MAIL_PROVIDER`、`NZ_MAIL_HOST`、`NZ_MAIL_PORT`、`NZ_MAIL_FROM`、`NZ_MAIL_USERNAME`、`NZ_MAIL_PASSWORD`、`NZ_MAIL_SENDER_NAME`；固定管理员收件地址不通过环境覆盖。
- 发生新写入后禁止直接回滚到旧TOTP版本或恢复旧会话；保留数据库/审计后做前向修复。SMTP未配置应补齐配置并重新申请码，不能临时恢复免码认证。
- 本交接中的新策略由本轮用户明确选择，取代ADR-010的固定码/免码例外，以及旧API文档中邀请审批/TOTP相关陈述；主交接与Excel v2同步更新合同。
