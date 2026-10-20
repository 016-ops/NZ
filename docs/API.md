# 实际本地 HTTP API

2026-10-16初装与商城兼容更新：当前目录下`character.create`在同一事务保存角色、钱包和`cn2020-starter-loadout-v1`初装回执。包含万飞/万阳、六个背包各独立默认刀，以及首包M4A1/USP/手雷；请求重放不重复发放。装备接口保留轻量`equipment_state`，仅发生默认刀回填时增加`inventory_delta`和`default_equipment`，原GID保留。背包0、过期或位置冲突是业务失败，原生返回A009失败后保持连接。

`shop.quote`增加`shop_rule_version=cn2020-shop-package-period-v2`。`PeriodArrayForSuite`按价格档位取值，-2继承主购买期限；计数型礼包材料数量取主档位，装备期限取套装档位。固定目录1752000小时作为明确兼容规则归为永久。整车报价、扣款、完整子物品及订单仍一次事务提交；旧报价需重新获取。

原生期限使用`ObtainTime + AvailbilityHour*3600`精确保留数据库到期秒数，不在重登时续期。NZ余额额外使用Reason42独立通知，不改变材料/制造通知的原因码。

这是自有控制面的HTTP接口，不是逆战原生协议。默认地址 `http://127.0.0.1:8765`，服务仅监听回环；显式公网Origin及HTTPS反代可开放Web。普通POST要求 `Content-Type: application/json` 和 `X-NZ-Request: 1`；专用图片上传路由使用受限multipart，见末尾媒体合同。认证后携带 `Authorization: Bearer <session>`，无Cookie会话。

## 路由

| 路由 | 用途 | 授权 |
|---|---|---|
| `GET /health`、`GET /api/status` | 服务与能力状态 | 公开只读，无凭据 |
| `POST /api/admin/login-code` | `{login,password}`，只发固定管理员邮箱 | 密码正确后申请独立登录邮件码，失败关闭 |
| `POST /api/admin/step-up-code` | `{action,payload,metadata,current_password}` | 当前Owner Bearer；绑定操作的独立邮件码 |
| `POST /api/login` | `{kind,login,password,challenge_id,code}` | admin需原密码和登录邮件码；player需ACTIVE且未制裁，旧otp_code无效 |
| `POST /api/register` | 邮箱注册兼容入口，拒绝login字段 | 同自助邮箱规则，验证即ACTIVE并初始化角色 |
| `POST /api/player/register` | `{email,password,nickname,challenge_id,code,...元数据}` | register邮件码；免邀请/免审批，原子创建ACTIVE账号、角色、钱包及配置初装 |
| `POST /api/player/login` | `{email,password}`；独立邮箱密码，不读取QQ凭据 | ACTIVE且未制裁；kind不能提权；character_required提示历史无角色补建 |
| `POST /api/player/email-code` | `{email,purpose}`；purpose为register/reset/verify | 同源公开入口，持久发送额度；register不需要邀请码 |
| `POST /api/player/password-reset` | `{email,password,challenge_id,code,...可选完整元数据}`；password 为新密码 | reset 邮件验证码；消费与改密/撤销会话同事务 |
| `POST /api/player/email-verify` | `{email,challenge_id,code,...可选完整元数据}` | verify 邮件验证码，只验证既有绑定邮箱 |
| `GET /api/launcher/status` | 独立登录与原生适配实际能力 | 公开只读，原生启动仍 false |
| `POST /api/launcher/tickets` | `{protocol,client_sha256,verifier_sha256,...元数据}`；60 秒一次票据 | 邮箱玩家 Bearer |
| `POST /api/launcher/redeem` | `{protocol,client_sha256,ticket,verifier}`；兑换独立游戏会话 | 单次票据及持有证明，不使用 QQ |
| `POST /api/game/session` | `{protocol,client_sha256}`；读取本地游戏身份 | 独立游戏 Bearer，不能用于普通 API 或节点接口 |
| `POST /api/game/settings/get` | `{protocol,client_sha256,metadata_sha256}`；读取当前版本原生设置 | 独立游戏 Bearer，实时检查来源会话与客户端绑定 |
| `POST /api/game/settings/set` | 上述字段加`payload_base64,expected_revision`；CAS保存不透明设置 | 同账号/客户端/TDR隔离，提交后才可应答原生A203 |
| `POST /api/logout` | 撤销当前会话 | 当前会话 |
| `GET /api/views/{view}` | 下表列出的读取视图 | 按服务端权限检查 |
| `POST /api/command` | 下表允许的命令及元数据 | 按每个 action 检查 |
| `POST /api/asset-preview` | 使用真实校验代码做单人发放预览，并回滚 savepoint | asset.preview |
| `POST /api/node/heartbeat` | match_id/epoch 续租 | 独立节点令牌 |
| `POST /api/node/settlement` | match_id/epoch/outcome | 合成模式、独立节点令牌，冻结规则定奖励 |

请求体最大 64 KiB，拒绝重复 JSON key、NaN、Transfer-Encoding 和跨 Origin/Host。Django 校验请求与授权，单进程 Waitress 使用 16 个 HTTP 工作线程；登录/注册每 IP/路径 5 分钟 20 次，其他写入每分钟 120 次。这些是本地保护策略，未作容量保证。页面入口保持 `/`，构建后的资源位于 `/assets/`；原 `/app.js`、`/style.css` 已由 Vite 哈希资源替代，业务路由保持不变。

## 读取视图

`me` 返回当前玩家自己的脱敏账号/角色/钱包/库存/装备/制裁/席位；管理员只取得自己的 ID/角色。`users` 支持字面包含 `search`、`limit`（1–100）、`offset`；`user`/`assets` 用 account_id；`operations` 用 account_id 查询流水；`audit`/`outbox`/`invitations`/`dashboard` 返回实际记录。`catalog`/`rooms` 允许已经登录的身份读取。

Owner 有全部权限；users 角色可邀请/审核/重置/制裁，不能发资产；resources 可有限额发资产，不能改账号；auditor 只读。用户/资源管理员的审计读取限定自己的操作。`admins` 视图与管理员生命周期命令仅 Owner 可用；`batches`/`reviews` 允许 Owner 和 auditor 读取；`pve-runs` 的玩家视图只包含自己的对局。

## 命令

`user.email.set` 接受 target_account_id/email 及命令元数据，权限为 user.reset；Owner必须原密码和独立操作绑定邮件码再认证。保留账号、game_id、角色和资产，撤销旧会话并禁用该玩家的旧用户名登录入口。邮箱唯一、统一小写；不合并加号或点别名。管理员绑定邮箱不代表所有权验证，verified_at 保持空；玩家可用 verify 邮件码补验，也可在持有 reset 邮件码时找回密码并完成验证。改变绑定邮箱或安全版本会使此前恢复码失效。

### 管理员邮件认证合同（2026-10-20）

收件地址固定为 `2633034921@qq.com`，请求字段及环境变量不能覆盖。登录取码仅接受正确管理员密码；高风险取码只允许当前Owner会话，并绑定最终 `action/payload/metadata`。`metadata`准确包含request_id/idempotency_key/reason，业务payload不能夹带认证、action或元数据字段。提交时仍提供原密码与challenge_id/code，登录码和高风险码不能互换。

六位码有效300秒，冷却60秒，最多5次错误，登录错密码与错码共用当前所属挑战额度，其他login不能消耗该挑战；来源20次/5分钟与60次/小时、每管理员每用途10次/小时、业务库发送100次/小时独立持久化。SMTP在部署锁外执行，失败关闭、不自动重发。返回challenge_id/expires_in/retry_after/recipient_hint，高风险另有operation_summary:{action,confirmation:12位十六进制指纹}，邮件显示相同动作及指纹，不包含业务正文/密码。挑战安全版本、Session和操作绑定使角色/密码变化、换会话、改动作/参数/键全部失效；消费与业务/Session、审计同事务，错误次数独立持久化。成功同键重试仍验证当前权限后恢复回执。

缺挑战为REAUTH_REQUIRED/403，无效/过期/已用/超次为ADMIN_EMAIL_CODE/403，额度RATE_LIMIT/429，未配置MAIL_NOT_CONFIGURED/503，投递失败MAIL_DELIVERY_FAILED/503。验证码/挑战ID/当前密码不进入管理员命令的审计或幂等业务摘要；新密码用服务端HMAC参与摘要。SMTP mock仅验证程序路径，不代表固定QQ收件箱已收到；真实投递仍需配置与人工验收。

### 邮件验证码合同

发码返回 challenge_id、expires_in=600、retry_after=60 和通用提示；六位验证码只发到请求邮箱，数据库仅保存带服务端密钥的 HMAC 摘要。每个码绑定邮箱、用途及挑战 ID，恢复/补验另绑定账号与安全版本。最多五次错误验证，失败计数持久保存；同用途重新申请使旧码失效。码过期、已用或无效统一返回 EMAIL_CODE/403。成功注册的原幂等请求可重读结果，不会二次消费验证码、创建账号/角色或发初装。

密码恢复和旧邮箱补验可携带完整 `request_id`、`idempotency_key`、`reason`；若提供其中任意字段，三项都必须合法。相同用途/邮箱、同键和相同业务内容重读已提交结果，包括响应丢失、服务重启或并发消费后，不再次改密或撤销会话；同键改内容返回 IDEMPOTENCY_CONFLICT。验证码和新密码仅以服务端 HMAC 参与持久幂等摘要，不记录明文。不带元数据的旧调用仍为一次性消费，成功后重复提交返回 EMAIL_CODE；不要为重试临时换键。页面已携带完整元数据，并在请求期间锁定输入、身份和用途切换，防止旧响应覆盖新表单。

新邮箱账号的内部登录别名使用随机值，不再由邮箱确定；已换绑释放的邮箱可重新注册一个独立账号。历史账号内部别名、原 ID/game_id、角色和资产不重写，历史注册幂等标识保持兼容；内部别名不能用于公开登录绕过邮箱认证。

注册同键并发时，即使另一请求在首个幂等查询之后、验证码预检之前已经提交，也应读回原成功结果；验证码预检失败后再次查验已提交回执，无结果则仍拒绝。此交错已用真实MySQL固定调度回归；历史DEV发布不代表2026-10-20新增功能已部署。

每邮箱跨用途 60 秒冷却、每小时 5 次，每来源每小时 20 次，每独立业务库每小时 100 次；额度在 SMTP 前持久预留，发送失败也不释放。配置缺失返回 MAIL_NOT_CONFIGURED/503；超过额度返回 RATE_LIMIT/429。SMTP 拒收/超时留固定运维错误码，公开响应仍为通用提示，客户端不应自动重发。未知邮箱的 reset/verify 请求同样投递邮件并返回相同形状，但无绑定账号时不能消费为恢复或新账号。

新注册默认启用NZ_EMAIL_VERIFICATION_REQUIRED=1，邮箱验证后即ACTIVE并原子初始化角色/钱包/配置初装，不需邀请码或审批。0024不改变任何既有玩家状态，不批量激活待审、停用或封禁账号；旧PENDING即使已验证邮箱，登录仍拒绝；补验/找回密码不改变状态，须管理员显式批准。ACTIVE无角色可登录后补建，但character.create要求当前绑定邮箱已验证，否则EMAIL_UNVERIFIED/403。昵称NFC后1–24字符且UTF-8最多48字节，冲突/失败不消耗验证码或留下半注册。找回密码使用正常强度与慢哈希，原子撤销旧Web/游戏会话；补验/恢复不改变旧待审、停用、制裁状态或角色资产。只有显式合成旧测试夹具可以保留邀请路径，正常profile不能用关闭验证配置绕过邮箱。所有入口保留Host/Origin及X-NZ-Request保护。

独立交接协议为 `nz-email-v1`，不等于现有客户端原生网络帧。所有游戏身份采用本地账号命名空间，无 QQ 号码映射。票据、证明和游戏令牌均只存 SHA256，票据绑定原玩家会话、安全版本、profile 和客户端声明指纹；该指纹是请求绑定，不是二进制来源认证。新票据替换未消费票据，兑换时撤销旧游戏会话。兑换并发至多一次，已消费返回 TICKET_CONSUMED/409，网络结果未知需申请新票据，不重放原始令牌。票据/兑换响应的秘密不写入通用幂等结果；这些路由不自动重试。

源会话注销、重登、管理员撤销/停用/重置或封禁后，每次游戏身份读取都会拒绝。独立游戏 Bearer 不能授权玩家命令、管理员命令、节点事件或结算。`native_launch_ready=false` 保持关闭：Windows 原生 IPC 自测不等于游戏大厅/目录/区服协议已实现。

本机研究 IPC 已从 `nz-email-ipc-research-v1` 更正为 v2：当前游戏代码消费证据确认 tag2 承载 Uin（本项目填独立 game_id），tag1 为 ServiceId，服务器 sub2/sub3 为主连接地址/端口。旧自测把 tag1 标为 LocalGameId，故旧报告只能证明 DLL 能读取字节，不能证明账号身份处于正确业务字段。v2 修正后已完成原生 AUTH/challenge/换钥；当前客户端已消费单条回环 GEO Ping/Zone 目录并在第二条连接发出 Zone A000，20/21 使用真实 DEV 邮箱身份。第 22/23 次 A001 固定研究响应已被消费，返回 A204/FF05；完整玩家资料、角色和大厅尚未通过。当前 TDR 元数据仅为编解码核对材料，不新增 HTTP 游戏业务路由；上述 HTTP 协议 `nz-email-v1` 及 DEV 邮箱发布版本均不因此改名。实际范围见[原生交接](delivery/2026-10-15-aliyun-email-and-native-launch.md#原生启动研究)。

所有命令带 `action`、`request_id`、`idempotency_key`、`reason`。同操作者同键不同内容报 `IDEMPOTENCY_CONFLICT`；同键相同内容返回原结果。返回结果不记录明文凭据。

| action | 业务字段 | 权限 |
|---|---|---|
| `invite.create` | max_uses (1–20), expires_at (UTC Unix 秒) | invite.create |
| `invite.revoke` | invitation_id | invite.create |
| `user.approve/activate/disable` | target_account_id | user.approve |
| `user.reset` | target_account_id, new_password | user.reset |
| `session.revoke` | target_account_id | session.revoke |
| `sanction.create` | target_account_id, expires_at (null 为永久) | sanction.create |
| `sanction.revoke` | sanction_id | sanction.create |
| `character.create` | nickname | 玩家本人 |
| `room.create` | map_key | 玩家本人 |
| `room.join/leave/cancel` | room_id | 成员/房主规则 |
| `room.ready` | room_id, ready (布尔) | 成员 |
| `room.configure` | room_id, map_key, expected_revision | 房主 |
| `room.start` | room_id, expected_revision | 房主；全部成员准备 |
| `asset.grant` | target_account_id, catalog_version, changes | asset.grant |
| `asset.batch` | target_account_ids (冻结 1–20 个唯一 ID), catalog_version, changes | Owner + 再认证 |
| `asset.compensate` | operation_id, catalog_version | Owner + 再认证 |
| `shop.purchase` | 旧平台购买动作 | 已关闭，鉴权后幂等前PLATFORM_SHOP_CLOSED/403；原生game命令不受影响 |
| `inventory.consume` | catalog_version, instance_id, quantity | 玩家本人，已确认可消耗类型 |
| `inventory.equip` | catalog_version, instance_id, slot | 玩家本人，按目录槽与期限 |

货币变化为 `{type:"currency",code:"GP",delta:100}`，物品为 `{type:"item",item_key:"test-token",quantity:2,duration_seconds:3600}`；省略 duration_seconds 表示目录允许的永久实例。默认当前国服目录为空，只在 test-only-v1 模式可使用这些示例。

Owner大额发放（币种绝对增量和>10,000）、批量、补偿、管理员生命周期、邮箱换绑和节点发证另带 `current_password/challenge_id/code`，先申请独立操作邮件码。资源管理员超限拒绝。凭据不放命令行、URL或日志。同键已提交请求可重读结果而不二次消费码，仍验证当前会话和权限。

2026-10-20策略替代历史测试例外：所有管理员均使用原密码和固定邮箱邮件码，无固定码、免码、TOTP或本地恢复码后门。0024清除旧TOTP/恢复码、提升管理员安全版本并撤销全部旧管理员会话；Session仍为900秒。

旧create_test_admin/create_demo_admin/recover_admin_mfa和DEV自动认证工具已退役，在读取私有状态/密码或连接前拒绝。bootstrap_owner仍可交互创建初始Owner；新密码统一12–128字符，旧密码哈希保持验证兼容。

## 状态与错误

- `COMMITTED`：资产落库完成，和同步状态是两个概念。
- `PENDING`：通知未得到当前客户端 transport 确认。
- `MANUAL_REVIEW`：原发放已经花费或物品溯源不能自动撤销，无负余额或删除历史。
- `LOCAL_EQUIVALENT`：本地账号/昵称行为，不代表原客户端首登。
- `TEST_PROFILE`：所有合成地图、币种、物品、节点和结算。
- `COMPATIBILITY_BLOCKED`、`UNVERIFIED_RESOURCE`：当前客户端缺少证据，默认拒绝。
- `AUTH`/401、`FORBIDDEN`/403、`CONFLICT`/409、`RATE_LIMIT`/429 等失败不产生半扣款半发货。

邀请令牌只在首次成功创建响应中返回，重试仅返回邀请 ID/有效期/次数，避免把可用令牌写进幂等结果或审计。若首次响应丢失，按 ID 撤销并建立新邀请。

合成节点结算严格只接受 `{match_id,epoch,outcome:"VICTORY"|"FAILURE"}`，客户端不能带 reward 值。奖励按开始时冻结的目录和玩家资格提交，之后可以重复读取结果。未接入原生节点，不应调用它来宣称真实通关。

## 2026-10-15 增量合同

以下命令继续使用 `/api/command` 和既有元数据、错误格式。管理员命令、批次入队/重试、复核和节点发证需Owner原密码和本次操作绑定邮件码；批次预览不消费码。

| action | 字段与约束 |
|---|---|
| `admin.create` | login/new_password/role；返回admin_id/role/authentication=FIXED_EMAIL_CODE，不生成密钥或恢复码 |
| `admin.update` | admin_id/role/active（布尔）；保护最后一位启用 Owner |
| `admin.password.reset` | admin_id/new_password；撤销旧会话 |
| `admin.mfa.reset`、`admin.recovery.rotate` | 已从命令分派删除，NOT_FOUND/404；旧本机恢复函数AUTH_METHOD_RETIRED/403 |
| `asset.batch.preview`、`asset.batch.enqueue` | target_account_ids（1–500 唯一 ID）/catalog_version/changes；只允许 currency/item 增量 |
| `asset.batch.retry` | batch_id，可选 target_account_ids；必须是原批失败目标 |
| `asset.review.resolve` | operation_id/resolution（KEEP_ASSETS 或 EXTERNAL_RESOLUTION）；只追加决定 |
| `room.create/configure` | map_key、channel、difficulty、password、invite_only；configure 另需 room_id/expected_revision |
| `room.invite` | room_id/target_account_id/expires_at；仅房主，令牌绑定目标账号且首次返回 |
| `room.join` | room_id、可选 password/invitation_token；实际验证访问规则 |
| `node.enroll` | match_id；仅合成模式的新分配，首次返回单局 node_token |
| `pve.revive` | match_id/stage_id/attempt_id/instance_id/catalog_version；玩家自己永久测试道具，失败窗口内原子扣减 |

`GET /api/views/rooms` 新增 map_key/channel/difficulty/state/limit/offset 过滤；口令摘要不返回。运行目录决定可用频道、难度与人数，未知值拒绝。

节点新路由均为 POST JSON，Bearer 必须是单局节点凭据：

| 路由 | 请求及结果 |
|---|---|
| `/api/node/ready` | match_id/instance_id/epoch/snapshot_hash/map_key/health；三个健康值必须是布尔 true；当前仅合成契约 |
| `/api/node/events` | match_id/epoch/event_id/sequence/stage_id/attempt_id/kind/payload；序号从 1 连续，事件结果持久保存 |
| `/api/node/pending` | match_id/epoch；只返回指派给该局的通知 |
| `/api/node/ack` | match_id/epoch/event_key，加 security_version 或 operation_id；所有指派节点确认后才全局 DELIVERED |

事件白名单：tick、attack、move、objective.activate、disconnect、vehicle.enter/leave/move、restart.ready/failed；不接受 reward/victory 或任意伤害值。伤害、目标、波次和胜败由冻结测试规则计算。PvE 分配只能在持久状态终局后结算；旧合成 arena 合同保持兼容。相同 event_id 内容不变可重读结果；换内容冲突。重开使用新 attempt_id，旧尝试事件拒绝。

玩家、管理员与节点令牌不可互换；节点绑定实例、对局、epoch、租约和快照。当前客户端模式的节点路径继续返回 COMPATIBILITY_BLOCKED，不将健康声明当作真实原生启动证据。


## 原生不透明设置（0007）

当前TDR SHA为`c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba`。客户端SHA必须与独立游戏会话签发时相同；玩家或管理员Bearer不具备这些接口权限。账号来自游戏会话，不能通过目标账号参数替他人读写。

读取返回`revision,payload_base64,payload_sha256,bytes,metadata_sha256`；从未保存的该版本为revision0、空正文。保存要求规范base64和显式整数`expected_revision`，原始正文最多3200B；不解析或改写客户端内部字段。未知TDR返回`SETTINGS_FORMAT`，旧的不同正文/未来revision返回`SETTINGS_REVISION_CONFLICT`。相同正文的旧revision重试返回`UNCHANGED`及当前版本，不重复审计/outbox；新提交返回`SAVED`。

设置、摘要、版本、审计和`native.settings.changed` outbox在同一MySQL事务提交。审计/outbox不含正文。来源会话退出、改密/制裁、游戏会话过期及客户端不符时重新拒绝；不把设置凭据提升为节点或资产权限。空正文是显式可保存值，不代表清空角色、库存、钱包或战斗快照。

本机原生探针显式`--native-settings`后，把A202的严格长度不透明正文送入该接口；只有回执中的正文/摘要/版本一致才发A203。A205重放同版本保存数据。此功能不代表稳定大厅或完整原生适配已完成，状态接口的`native_launch_ready`仍为false。

## 原生兼容库存与装备（0008）

`POST /api/game/inventory/get`、`POST /api/game/inventory/equip`共用游戏Bearer与`protocol,client_sha256,rules_version`。规则版本固定为`cn2020-full-inventory-compat-v1`；身份只能来自当前有效游戏会话，玩家登录令牌、其他账号GID、来源已撤销会话或错误版本均拒绝。

读取返回`game_id,super_account,rules_version,item_count,items,wallet`；每个实例包含稳定`gid`、当前固定资源定义、`quantity,expires_at,equipment_slot`。`wallet`包含独立`rules_version,balances`，与库存一起在同一MySQL事务读取。目录有4323个唯一ID，但拥有权益不等于原生战斗可用。初始投影仅覆盖ConsumeType 0/2共2466项，其余类型保持显式未验证；商城报价、宝箱消费与M6奖励不由此接口开放。

装备另需`operation`（0穿戴/1卸下）、`subject`（本人道具GID）、`target`（角色根1或本人背包GID）、`location`（0/1/2/3/10/11/64）。64是当前猎场角色槽，10/11是两阵营角色，三者独立存储。核对固定槽、类型、期限、隐藏标记和归属，在同一事务写装备、审计及outbox；重复操作返回`UNCHANGED`，不重复事件。同一实例换背包同时释放旧槽。隐藏默认匕首100013是实收卸刀回退请求的明确例外，其余隐藏项继续拒绝。

客户端A008的自动槽`-1`仅在本机可信桥由固定目录和本人GID归一，业务接口仍要求显式合法槽。A009应答和当前CN A00B通知只在提交及快照回读一致后发送；不能套用其他区域A00A装备结构。未知原生错误码不伪造成功。

可选`projection=cn2020-equipment-state-v1`用于轻量装备状态：get返回`projection,rules_version,game_id,equipment_count,equipment,wallet`，每个装备仅有`gid,equipment_slot`；equip在原返回上增加`equipment_state`，与换装、审计、outbox在同一事务回读，提交失败不发成功。没有该参数仍沿用原合同，未知投影版本拒绝。客户端登录仍校验全量目录，此后只有本人已知GID、唯一槽及正确父对象可更新缓存；异常响应不提前更新缓存。换装不再追加一次4323件全仓下载，TP查询也使用轻量状态，且每次仍重新鉴权。此视图不负责新增物品通知，新获得物品仍需后续资产增量协议或重登全量刷新。

当前实机另有`operation=1,target=0,location=0`的自动卸下请求，Location不代表实际资源槽。此时只解析本人subject的现有持久位置；已未装备返回`UNCHANGED`，存在唯一合法原生位置则移除该位置，其他物品不受影响。`operation=0,target=0`仍拒绝，不能以此穿戴任意物品或跨账号访问。

全目录发放只有受控主机管理命令`grant_native_inventory --account-id ... --rules-version cn2020-full-inventory-compat-v1 --reason ...`，没有玩家HTTP入口。它沿用统一资产操作、逐项流水/详情和outbox；部署事务内补齐永久权益及稳定GID，保留有限期旧实例和所有历史。重复执行不增发、不重置玩家装备。此权益不授予后台管理员权限、不发邮件、不创建新账号。

## 当前兼容钱包

版本`cn2020-wallet-width-compat-v1`，币种为`cn2020:TP/GP/MP/HP/EP/SP/MECHA`；前五者上限2147483647、SP为65535、MECHA为9223372036854775807。采用当前协议字段与数据库宽度交集，不等于原版运营上限；合成币种、经验、VIP和局内资源保持独立。超限写入拒绝，不截断。

`maximize_native_wallet --account-id ... --rules-version cn2020-wallet-width-compat-v1 --reason ...`仅为已有启用超级权益的明确账号补足差额。命令复用统一资产事务、不可改写流水、审计及outbox；重试不写重复记录，无新增HTTP管理入口。

A001映射真实TP/GP，A005映射七类钱包。当前A50E TP余额查询要求8字节Uin等于鉴权游戏身份，再回读钱包并返回A00A属性通知：TP标志1、TPBALANCE原因42，无道具/天赋修改标志。70字节线格式及空天赋版本3已由当前DLL往返核对。原生商城购买/续费、特殊活动币和RP含义尚未实现或确认。

## Launcher短租约（0016，更新上述旧会话说明）

`POST /api/launcher/redeem`可显式携带`session_lease: "launcher-lease-v1"`。该模式将本次游戏和来源会话设为180秒短租约，响应返回`session_lease,expires_at,lease_max_expires_at`（Unix秒），单次登录最长12小时；没有该字段的旧调用保持原合同。

`POST /api/game/session/renew`使用游戏Bearer，正文为`protocol,client_sha256,session_lease`。每次重新核对来源会话、账号状态、制裁、安全版本及客户端绑定，只续期仍有效的会话。返回相同租约字段；过期、注销或绑定失效返回`AUTH`，不能复活过期凭据。来源和游戏到期时间、审计同事务。Launcher每30秒续期；失联或强杀后最多180秒失效，正常退出仍执行注销和两项拒绝回读。

本机Launcher的`POST /launcher/api/start`只接受可选`graphics_mode`：`frame-copy`为当前电脑默认兼容显示，`system`为系统标准显示；旧`d3d9ex-flip`仅保留诊断兼容，不再作为界面选项。不能传DLL路径、命令行或服务地址。`frame-copy`按固定SHA256安装本项目编译的单个临时显示组件，由持久配置守卫恢复。该本机入口要求精确Host/Origin、当前随机能力与JSON正文。三分钟启动超时只限制进程启动至首次`INVENTORY_SENT`，超时执行清理后返回`CLIENT_STARTUP_TIMEOUT`，不会把同步成功标为大厅已绘制。组件缺失、摘要不匹配或目标冲突分别返回`FRAME_BRIDGE_COMPONENT_MISSING`、`FRAME_BRIDGE_COMPONENT_MISMATCH`、`FRAME_BRIDGE_FILE_CONFLICT`。


## 功能读视图与工坊券（0018）

`POST /api/game/view`、`POST /api/game/command`使用游戏Bearer以及当前`protocol,client_sha256,rules_version,feature_version`，命令另含`request_id,idempotency_key,reason`。新请求失败仍区分业务失败与认证损坏；已提交但响应不确定时先通过`view=operation,idempotency_key`查询。

- `view=session.sync`：`after_revision`、可选严格布尔`include_inventory`、事件`after_id/kinds`。返回`inventory,friends,clan,events`。读取更新真实心跳但不确认事件；事件附带发送者仅有公开game_id/nickname。开奖动画期间传false只延迟库存投影，社交仍同步。
- `view=draw.state,pool`：同一事务返回规则和本人的最近/待确认回执，不跨账号读奖。`inventory.recipes,item_id`返回当前升级配方及规则版本。
- `view=workshop.state`：返回当前工坊规则或null、`last_free_at,free_available,pending`。即使规则停用，已提交待确认回执仍可恢复。
- `action=workshop.draw`：`rule_id,mode`（free/paid）,`count`（1/10）。free表示礼品池，不保证此次免费；服务端按北京时间当日使用情况决定。十连按十次券费计。结果包含不可重抽`receipt_id`、规则、逐项award/refunded、实际免费状态和资产操作号及增量。
- `action=workshop.confirm,receipt_id`：仅本人原结果回读；原生FA21没有独立真人确认包，当前桥接在全批sendall成功后记录传输确认，不等同阅读。

工坊`draw:workshop`定义保留`recipes`并增加`vouchers`（free_material,paid_material,pool,ten_min_tier,refund_owned）。候选包含key/weight/tier/free/paid/show_id/pic_id/reward。发布校验有限数量、正权重、可表达整小时期限、双池和付费保底候选。原生FA20–FA24使用v8固定TDR派生布局，已独立DLL往返；真实玩法验证另列。
### 工程操作（0019）

游戏读视图 `engineering` 返回本人熟练度、全部37条配方、268项可分解目录及显式兼容规则。
四个命令 `engineering.decompose`、`engineering.compose`、`engineering.material_decompose`、`engineering.material_compose` 均要求 `rule_version=cn2020-engineering-compat-v1`。
装备分解传 `gids`（1–60且唯一）；部件分解传 `gids`（1–30，重复表示多个单位）；部件合成传产出 `item_id`；工程制造传 `recipe_id`、`mode`（1直接/2付费）、`count`（1–5）。成本与产物不接收客户端自报值。

固定配置提供材料、成功率、NZ点费用和熟练度上限。缺失规则显式定义为：分解每种允许部件各1个；部件5合1/1拆5；配置制造期限按小时；每成功1次熟练度加1；冷却0秒；随机配方描述中的4把黄金武器等概率。所有扣料、扣费、随机结果、产物、熟练度、回执、审计和通知在同一MySQL事务提交。幂等键重放保持原结果；不会改写原有GM库存。

当前工程原生F401–F409经DLL完整往返核验；实机入口及规则显示仍需单独验收，API/DLL通过不代表可玩版本完成。

### 初装、角色礼包与角色装饰（2026-10-16续）

正式建角事务使用`cn2020-starter-loadout-v2`：万阳/万飞及其四件默认头发/墨镜，六个背包各独立默认小刀，1号包M4A1/USP/手雷，共21个实例。旧v1初装回执保留，不通过重登重新发放。

商城`cn2020-shop-package-period-v2`按价格档位读取`PeriodArrayForSuite`，-2继承主期限；永久权益保存NULL，限期保存绝对到期秒数。客户端日历角色的永久期限用当前目录1752000小时占位投影，界面按角色100年阈值显示永久，不把占位期限写回数据库。

角色装饰规则`cn2020-avatar-components-v1`冻结164个角色与158个装饰的ResID家族及槽位。A008验证本人有效GID、同家族角色父级和准确槽；装饰不能挂背包，武器不能挂角色。购买/赠礼与角色换装事务会将已有未装备默认组件挂入角色空槽，保留所有GID、期限和已有搭配。结果`avatar_equipment`包含准确父子关系；资产增量包含没有新增资产流水的旧组件挂接。身体/手臂资源按角色配置加载，不为Location=-1组件编造装备槽。

### 耐久维修、出售与期限续费（2026-10-16续）

`cn2020-item-maintenance-v1`冻结全部4323项的维修/出售字段。`inventory.repair`接收1–100个唯一`gids`，仅允许本人有效ConsumeType=2、RepairType=2的耐久物品，按当前客户端单精度公式计算缺损GP费用；满耐久返回UNCHANGED。`inventory.sell`接收单个`gid`，要求配置允许出售、非默认物品且未装备，按客户端剩余耐久四分之一价格向上取整返GP。扣费或退款、状态、库存、资产流水、回执、事件共用事务，失败全部回滚；不能把期限物品的nRepairPrice商品ID当维修金额。

原生维修A50B/A50C、出售A363/A364和期限续费A509/A50A已接入。续费报价使用`shop.quote`的`renew_gid`，必须是本人持有实例及固定目录的nRepairPrice/nRepairPrice1关联商品；允许仓库专属隐藏商品，不放开商城普通购买。`shop.renew`重新核价并保持GID，从`max(现在,原到期)`延长，永久实例拒绝扣费。当前仅单实例期限续费；角色多部件套装续费仍待实现，不作为通过项。已有规则、期限及历史回执不重置。

桥接在发起变更前保存幂等请求，提交结果不明先查本人操作回执；维修、出售及续费失败返回对应原生负应答，认证或数据损坏继续拒绝。DLL编解码与MySQL回归各自记录，不能替代按钮实际表现和局内耐久验收。

续费商品恢复兼容规则 `cn2020-renewal-offer-restore-v1`：固定192个被物品续费字段引用的隐藏商品允许正常报价/购买，`shop.quote.restored_offer_rule`返回该版本。价格、币种、期限、销售窗口仍由冻结服务目录校验；其余隐藏商品维持拒绝。Launcher在受控副本恢复对应两个开关，退出恢复原配置。此规则替代此前“这些隐藏续费商品普通购买均拒绝”的描述。


`inventory.open`（cn2020-container-compat-v1）：请求container_protocol、count、rule_version，继续携带原游戏协议protocol和feature_version。首批307对应104047鼠年礼包，依据物品说明发放104002/104003/104004三张卡，每张1个永久为显式兼容规则；不接受客户端预测奖励或他人GID。消耗、产物、回执、事件、审计在同一事务，回执保存本次grants快照供原生A376及丢应答重试。140个入口已盘点，其他奖池规则继续待补，未知规则拒绝且不扣材料。来源唯一性标记不擅自改变当前可重复独立实例合同。
# 角色默认身体资源兼容规则（2026-10-17）

`cn2020-avatar-body-compat-v1` 冻结当前物品表296个隐藏默认手臂/身体资源。游戏票据兑换会在同一鉴权事务补齐本人正在使用的角色缺项，保留既有GID、装饰和历史初装回执；新建角色使用`cn2020-starter-loadout-v3`。购买角色包复用同期限已有默认部件，角色续费同步其部件期限。

库存读视图的`equipment_slot`新增内部形式`native:<本人角色GID>:body1`和`body2`。它们用于区分两个资源父关系；原生A006/A00A/A00B仍使用真实角色`OwnerGID`及原表`Location=-1`，不向客户端发送虚构数字槽。身体部件只能挂同家族本人角色，不允许单独卸下或移走另一角色的已挂部件。所有新增资产、挂接、期限、审计和事件共用MySQL事务；重复准备不新发资产。

## 百宝箱和功能卡（2026-10-18）

`/api/game/view`新增本人`pve.benefits`视图，返回`rule_version=cn2020-pve-benefits-v1`、真实成长等级、有效性和四个原生VIP字段。101296的成长记录由资产事务维护于`state.pve_store`，10500点达到原配置3级。永久资产仍保存NULL到期时间，原生有符号32位VIP日期投影最多2147483647；该线上边界不决定服务端永久权益有效性。

`pve.store.claim`游戏命令接收`benefit_id`和`prop_ids`。例如10014必须精确对应[100482]，10015对应[100786]，10016对应[100924]；等级、有效百宝箱及奖励期限均由服务端原配置决定，不接受客户端指定数量/时长。当前仍持有有效同类武器时拒绝重复发放，过期后可重新领用。奖励、历史标记、回执和事件共用同一事务，同一幂等键重试只返回原结果。

原生3601/3602查询与3603/3604领取均用当前TDR。3603的`Seq`实际是权益ID，`PropIds`是真实奖励ID列表；不把Seq当成无业务意义的流水号。协议目录v11只增加五个VIP结构，旧结构不变。

功能卡100591/101210仅根1槽36、100713/101211仅根1槽42、100810/101149仅根1槽50，分别为限时/永久专家卡、金币卡、猎魔执照。自动槽请求由服务端目录解析，错误父级、过期和跨账号GID拒绝。管理命令`prepare_acceptance_loadout`仅对显式账号集合配装，不改变普通新账号的M4A1初装策略，也不授予GM。

库存分页、原读取和增量实例新增只读`gain_type`：普通来源48，已由本人不可改写的`pve.store.claim`流水及同版本成功确认回执证明的领用实例为35（原生VIPTAKE）。同款普通购买/GM实例不改标；旧领取无需重新发放或回填历史。A006与A00A使用相同来源字段，桥接拒绝未知值、布尔及溢出。该投影不改变期限、数量、归属或领取资格。

`engineering.decompose`沿原客户端规则拒绝VIPTAKE实例，混入普通武器的整单也不扣材料。服务端按相同来源回读判断，不能绕过按钮限制把免费领用兑换成工程材料；同款普通实例仍可按原工程配方分解。

### 功能卡与五背包验收配装（2026-10-18续）

`pve.benefits`保留原`rule_version=cn2020-pve-benefits-v1`，新增`functional_rule_version=cn2020-pve-functional-v2`和102000陷阱全能体验卡。该卡原始槽为-1、父级固定为角色根1；仅此已核实功能卡允许此根槽，不能将所有-1槽物品当作可装备。过期、错误父级和他人GID仍拒绝，与36/42/50三个功能槽共存。

主机管理命令`prepare_acceptance_loadout --profile five-bags`采用`cn2020-acceptance-five-bags-v2`，显式账号集合依次配置诸神、魔鲲、天鲲、创世、天神四件武器，以及百宝箱三级、猎魔执照、专家/金币卡和全能体验卡。旧默认profile及v1回执保留，新请求使用不同request-id。整批账号在同一资产事务准备，重复请求回放原结果，不合并旧GID、不修改钱包或初装规则。

`--profile six-bags`使用新回执版本`cn2020-acceptance-six-bags-v3`，追加六号元素永久套。二号包使用四个独立天鲲主体实例及原生魔化卡状态103726–103729，三号包保持原始天鲲，原五套方案的直接魔鲲产物与旧回执保留。新增103438至尊深海誓约和七种永久核心，94槽仍只选择一个主动核心，不覆盖玩家已有选择。账号的有效不同核心种类按原配置阈值0/1/2/3/4推导进化等级1–5，过期、零数量及同种重复核心不累计；没有主体武器则等级0。原生A005的KunTaoEvolutionLevel来自该库存投影，不把所有玩家固定设成满级。

### 猎场宝箱兼容奖池（2026-10-18续）

另见文末原生频道/房间增量接口；该接口当前处于开发验证，节点尚未可玩。

`inventory.open`当前版本为`cn2020-container-compat-v2`，新增37个可由当前`ADBossBoxMappings`核实候选的猎场箱。每箱由服务端从去重后的物品/数量/期限候选中等概率选一项；原配置只提供展示分组，没有可核实的商业服概率，因此分组展平与等概率是明确的自定义兼容规则。计数物品的数值按数量（如圣人碎片5个）处理；日历型角色1752000小时哨兵保存为永久NULL。

响应保留`receipt_id,rule_version,container_protocol,item_id,count,rewards,grants,operations`，随机箱另有逐箱`draws`（key与奖励）。资产流水只以正向发奖项构建`grants`，材料叠加返回本次增量，不是新的持有总数。抽取、扣箱、入库、回执和事件共用事务，未知提交先恢复原操作，不再开奖。旧v1客户端仍可执行未变化的307三卡礼包，其余新增箱要求v2；已保存回执不改写。其余102个入口仍缺少已核实或已发布的奖励规则，保持失败不扣箱，尚未完成全仓库验收。


### 原生频道与房间（2026-10-18开发中）

沿用游戏Bearer、protocol/client_sha256/rules_version/feature_version，增加room_rule_version=cn2020-native-room-compat-v1。读视图：room.catalog、room.list（start/count/mode_id/map_id）、room.current、room.peer（有效好友的peer_game_id）。命令：room.create/join/leave/ready/configure/kick/start/cancel/in_match；非建房/加入带expected_revision和当前native_room_id，仍带统一幂等元数据。

房间名CP936最多31字节，口令最多7字节；人数/地图/难度由固定目录核定，不信任客户端DS地址、MapString或初始金币等覆盖。native_room_id是稳定64位ID，display_id只是频道短展示号。当前开始仅产生ALLOCATING和180秒分配租约，native_ready=false，未完成节点不能把入局置真。room.current仅对实际房主返回owner_password，用于原生移交；公开列表、命令回执、审计不包含该字段。安全版本/游戏或来源会话到期撤销释放席位。

session.sync可选择include_room获得本人房间增量；room.changed/room.left按接收者保存，编码发送确认后送达。原生协议v12在原v11上增加37条房间消息，保留旧接口。A11A/A116成功端点及A11B结算尚未接通；0020/0021迁移和本批代码尚未发布，不可将接口回归当作可玩证明。详见docs/delivery/2026-10-18-native-channel-rooms.md。


原生房间后续已发布：规则cn2020-native-room-compat-v2、协议v13，DEV发布20261018T134255-f2eb539b82d7已执行0020/0021。增加room.rename（name）、room.transfer（peer_game_id）、room.invite（peer_game_ids，房主、五分钟定向授权），以及接收者room.invitation视图。room.ready支持已核实阵营camp=0/1，观战仍由observer布尔表达；原生ObserverNone脚本-1由桥接以byte255编解码。席位以0/16两组的各半战斗容量布局，失败A330保持连接。node_ready仍为false，原生开局/加入/结算成功路径未完成；前文未发布状态是此前历史。
# 指定房间节点资源搜索配置补充（2026-10-18）

`tools.native_room_server_config.variant`现为`cn2020-native-room-engine-v2-single-resource-root`。
固定原配置校验、地图目录和端口合同保持不变，`Core.System.SeekFreePCPaths`直接指向经校验的
共享CookedPC根；不再同时扫描私有副本和共享池内相同包。私有原文件不删除，临时配置仍由
`NativeConfigGuard`按摘要恢复。此配置不是节点就绪或地图验收接口；400实机推进到D3D9显示模式
初始化失败，仍拒绝宣称可入局。详情见`docs/delivery/2026-10-18-native-channel-rooms.md`。

原生好友决定码补充（2026-10-19）：A307的Result必须为SNS_ADDFRID_AGREE=33537或SNS_ADDFRID_REFUSE=769；SNS_ERR_SUCC=33536不是玩家决定。未知决定返回业务失败且保留申请，连接继续。A308事件通知使用相同专用决定码；持久关系提交、接收事件及传输后确认合同保持不变。API发送端加原生接收端已实机验证，尚不代表双原生客户端通过。

## Launcher / 门户运营平台（2026-10-19）

本节为本批新增合同。普通命令继续走 `POST /api/command`，使用 Bearer、`request_id`、`idempotency_key`、`reason`；权限、幂等回执、资产、审计在既有 MySQL 事务内处理。管理员登录与Owner高风险确认统一要求原密码和独立固定邮箱邮件码。owner、users、resources、auditor 的入口依实际权限显示；审计员只有读权限。所有版本冲突须先刷新，不能自动覆盖其他管理员的修改。

### 门户与受控 Launcher

| 接口 | 身份 / 请求 | 返回与约束 |
|---|---|---|
| `GET /api/public/portal` | 匿名 | 首发地图、政策版本、已发布活动和能力状态；`native_gameplay=false`、正式成绩能力仍关闭 |
| `GET /api/public/campaigns` | 匿名 | 已发布活动，不包含个人进度或未发布草稿 |
| `GET /api/public/leaderboards` | 匿名；`campaign_id,map_id,difficulty,player_count` | 各地图/难度/实际战斗人数榜；公开成员昵称，不公开账号ID |
| `POST /api/launcher/login` | 与玩家邮箱登录相同字段 | 专用 Launcher 登录渠道成功记当日登录；Web `/api/player/login` 不累计此奖励 |
| `POST /api/player/view` | 玩家 Bearer；`{view,query}` | `me,player-inventory,campaigns,campaign-detail,leaderboards,feedback,feedback-detail`固定白名单；player-shop已移除，统一返回 `{data:...}` |
| `GET /api/views/<view>` | 已登录的对应身份 | 下述运营视图；查询字符串中的分页和地图数字严格转换，不接受任意模型查询 |

Web `/`、`/player`、`/activities`、`/leaderboards`、`/guide` 提供同源门户，`/admin` 提供管理员工作台。`me` 与用户管理中的 `level=null,level_status=PENDING_NATIVE_SOURCE` 表示尚缺原生军衔来源，不按余额或装备推算等级。

桌面本机新增 `POST /launcher/api/view`、`POST /launcher/api/command`，使用已有随机能力头和精确 Origin。Python 服务保存远程会话；渲染器只传白名单 view/action，不传远程URL、数据库连接或业务 Bearer。可读本人仓库、活动/专题、榜单和反馈；商城读取及player.shop.quote/player.shop.purchase已关闭，命令仅允许签到/奖励领取、补建角色及本人反馈等显式白名单。专用登录 API 表示服务端渠道分类，不是桌面二进制远程证明。

### 用户、商城与地图

| 命令 / 视图 | 主要字段 | 合同 |
|---|---|---|
| `users` / `user`、既有发放/制裁命令 | 既有账号ID、资产变更和制裁字段 | 沿用真实玩家资产、审计和撤销会话；军衔保持待接入状态 |
| `player-inventory` | `limit,after_gid,revision` | 原生实例分页；revision锁定后继续游标，变化需重新加载，不能把4323目录当作本人已持有 |
| `asset.remove` | `target_account_id,instance_id,quantity,expected_version` | 管理员删除本人目标账号的准确实例；库存并发版本冲突拒绝，装备/皮肤/吞噬依赖需先解除；过期实例可由管理员清理 |
| `shop-admin` | `limit,offset`等过滤 | 管理员返回revision/total/items及冻结目录或已发布覆盖 |
| `player-shop` | 原玩家商品视图 | Service层PLATFORM_SHOP_CLOSED/403；POST玩家view及本机代理白名单也移除 |
| `shop.offer.save` | `commodity_id,expected_revision,enabled,sort_order`；可选 `prices,period_hours` | 发布已有商品的运营覆盖；不修改原始目录，价格/期限数组需匹配；全局版本冲突拒绝 |
| `player.shop.quote` / `player.shop.purchase` | 原平台报价/购买动作 | 已关闭；鉴权后幂等前PLATFORM_SHOP_CLOSED/403，历史成功回执不能绕过 |
| `play-policy` | 无 | 返回55张原始房间目录及实际启用状态、合法难度 |
| `play-policy.save` | `expected_revision,maps:[{map_id,enabled,submodes}]` | Owner发布白名单，省略项关闭。创建/加入/改图/开始均校验最新政策，拒绝时沿既有原生负应答返回警告 |

默认七图：猎场大都会113、黑暗复活节164、丛林魅影191；塔防沙漠神殿141、蔷薇庄园140、空间站168、联盟大厦96。同名联盟大厦160默认关闭，Owner可在合法目录中自行启用。白名单开放仅表示运营允许选择，不能据此认定原生节点或地图已验收。原生商城的静态展示资源保留；实际报价/购买也服从已发布商品策略。

### 活动、榜单与奖励

`campaign.save` 接收 `title,definition`，修改草稿另需 `campaign_id,expected_revision`。definition包括 `starts_at,ends_at,timezone,rewards`；每个奖励具有稳定 `key`、`title`、`type`、道具 `changes`，普通奖励含正整数 `threshold`。五种类型：`CHECKIN_DAYS`、`LAUNCHER_LOGIN_DAYS`、`PLAYTIME_SECONDS`、`CLEAR_COUNT`、`LEADERBOARD`；榜奖另有 `priority,rank_from,rank_to` 与可选 `map_ids,difficulties,player_counts`。道具与期限必须符合服务目录，不能由玩家指定发奖内容。

`campaign.publish/pause/end` 使用 `campaign_id`。已发布规则冻结；草稿不产生玩家权益。`campaign.checkin` 为北京时间/配置时区当日签到，`campaign.claim` 接收 `campaign_id,reward_key`；普通奖励须达标且点击领取，唯一回执防重复发放。网页登录不累计 Launcher 登录天数。

`campaigns` 返回可参与活动与本人进度；`campaign-admin` 返回规则和 `payouts:{pending,failed,succeeded}`。`leaderboards` 按 `campaign_id,map_id,difficulty,player_count` 查询。榜以**地图 × 难度 × 实际战斗人数**分区，猎场1–8人，塔防1–4人；观战席不计入人数，同一成员集合只保留最快一次。通关毫秒升序，平局按结算时间与记录ID稳定排序。猎场要求任务成功，塔防要求击杀boss且成功结算；中途成员变化不入榜。

Owner执行 `leaderboard.finalize` 冻结快照。每名玩家在一个活动中只获得最高优先级一份榜奖；后台 `maintain` 逐人自动发放、失败单独重试，成功目标不重发。榜奖不接受手动claim。奖励读视图用 `delivery=AUTOMATIC/CLAIM`、`delivery_status` 区分领取方式与到账状态。

`POST /api/runtime/results` 的合成合同校验节点令牌、实例/epoch、冻结地图/难度/成员及完整结算字段，成绩与游玩时长只接受服务端证据。当前正式profile明确返回拒绝，只有隔离测试profile可验证该合同；尚未接入可信原生战斗结算，不能用浏览器上报或手工模拟填充正式榜单。

### 服务器监控

Owner命令 `monitor.node.enroll` 接收 `name,platform`、可选阈值，返回只显示一次的 `node_token`；`monitor.node.update` 使用 `node_id,expected_revision,enabled/thresholds`。节点令牌在数据库仅保存摘要；不进入命令回执或审计详情。监控读视图要求 `audit.read`，登记/修改要求Owner。

`POST /api/monitoring/samples` 使用独立节点 Bearer，接收同层字段 `node_id,boot_id,sequence,measured_at,cpu_percent,memory_total,memory_used,rx_bytes,tx_bytes,disks,services`（没有嵌套metrics）。有限数值、时间窗口、序列与载荷哈希严格校验。同序列同载荷回放不重复计数，异载荷冲突；重启/计数器复位的带宽为未知。

`monitoring` 视图返回节点、告警和历史；可传 `node_id,hours`（1–720）。实时ONLINE同时校验采样和收到时间，60秒后为STALE且不显示旧值为健康实时值；短窗口原始样本、24小时内分钟聚合、更长窗口按数据库小时加权聚合。原始数据保留7天、分钟30天，维护任务定时清理离线节点历史。链路占用告警仅在配置 `link_mbps` 时计算。

公网仅在显式 `NZ_PUBLIC_ORIGIN` 后启用精确Host/Origin。使用本机Nginx时另设 `NZ_TRUST_LOOPBACK_PROXY=1`，只在回环peer+精确公网Host下信任校验后的 `X-Real-IP`，用于请求和邮件限流；默认忽略所有转发头。实际Web发布步骤见 `docs/WEB_DEPLOYMENT.md`，服务器配置未提供前不连接或替换既有服务。

## 反馈、私有截图与活动独立页面（2026-10-20）

本节是当前工作树的增量合同。新增结构为 `0025_feedback_and_campaign_presentation`，依赖 `0024_email_admin_auth`；新增反馈工单、消息、附件、公共媒体、活动展示五表。只追加新表和旧活动展示元数据，不改活动的已发布奖励快照。浏览器/Windows实机、真实SMTP收件、发行包与部署状态以本轮交接证据为准，不能沿用首轮包或CI结果。

### 通用身份与请求边界

- 反馈创建仅限有效玩家会话，工单始终属于当前玩家，客户端不能指定账号或消息作者。玩家只能列出、读取、回复及上传自己的反馈。
- Owner/users可读取、回复和处理全部反馈；auditor只读；resources无反馈读取或处理权限。反馈权限与活动运营权限独立。
- 普通命令使用 `POST /api/command` 的 Bearer、`X-NZ-Request: 1` 和 JSON 正文，包含 `action,request_id,idempotency_key,reason` 及业务字段。相同操作者、幂等键和业务内容重放原结果，不重复创建工单或追加消息；同键不同业务内容409。管理员降权后仍先校验写权限，不能靠历史回执恢复处理权。
- 读取沿用 `GET /api/views/<view>`；桌面桥可用玩家 Bearer 调用 `POST /api/player/view`，正文 `{view,query}`，返回 `{data:...}`。该白名单已包括 `feedback,feedback-detail,campaigns,campaign-detail,leaderboards`；反馈分页的URL整数文本会规范为整数，布尔值拒绝。资源查询不是任意模型过滤器。
- 未登录401；其他玩家工单或附件统一404；resources反馈访问与auditor写入403。失效、撤销或制裁会话仍受统一会话校验。

### 反馈命令与四态

| action | 业务字段 | 成功结果 |
|---|---|---|
| `feedback.create` | `title`：1–120字符；`body`：1–5000字符 | `{ticket_id,revision:1,state:"OPEN"}` |
| `feedback.reply` | `ticket_id,expected_revision,body` | `{ticket_id,message_id,revision,state}` |
| `feedback.status` | `ticket_id,expected_revision,state`；仅Owner/users | `{ticket_id,revision,state}` |

正文保存纯文本，允许换行和制表符，拒绝空正文、NUL等控制字符；不会解释HTML。初始正文与工单在同一事务提交。正文不进入业务响应、通用命令回执或审计详情，消息只从授权工单详情读取。

状态为 `OPEN,IN_PROGRESS,RESOLVED,CLOSED`。非关闭工单可由Owner/users设置这四种状态。`RESOLVED`收到玩家或有处理权管理员的新回复后自动变回`OPEN`；其他非关闭状态回复后保持原状态。`CLOSED`为终态，只读，拒绝新增回复、状态变更和截图上传。已成功请求重放不产生新内容。

回复与状态修改必须提供当前正整数 `expected_revision`，版本不一致返回`VERSION_CONFLICT`409并要求刷新；不自动覆盖其他人的更新。每次回复、状态修改、成功新增附件都会增加工单revision。附件不是普通命令，不使用旧revision提交；上传后以前端回读的最新revision继续回复。消息按服务端单调序列排序，秒内多条消息不会按随机ID倒序。

### 反馈读取结构

| view | query | 返回 |
|---|---|---|
| `feedback` | 可选`offset,limit,state` | 本人`{tickets,total,offset,limit}` |
| `feedback-admin` | 同上；Owner/users/auditor | 全部工单，信封同上 |
| `feedback-detail` | `ticket_id` | 工单字段平铺，另加`messages,attachments` |

分页默认offset=0、limit=25，limit范围1–100；列表按更新时间降序、工单ID稳定排序。state只接受四态之一。

```json
{
  "id": "服务端32位工单ID",
  "account_id": "所属账号ID",
  "title": "反馈标题",
  "state": "OPEN",
  "revision": 1,
  "created_at": 0,
  "updated_at": 0,
  "message_count": 1,
  "attachment_count": 0,
  "messages": [{"id": "消息ID", "body": "纯文本", "author_kind": "player", "created_at": 0}],
  "attachments": []
}
```

上例时间0仅示意Unix秒字段，不是生产记录。列表ticket不带messages/attachments。消息作者只返回`player/admin`类别，不返回管理员登录名、邮箱或作者内部路径。

### 私有截图与公共封面

上传必须是同源 `multipart/form-data`，带 `X-NZ-Request: 1` 和当前Bearer；表单恰好一个`file`及一个`upload_key`，重复同名字段、多文件、额外字段均拒绝。中间件在读取FILES/解码图片前完成会话与工单权限校验。

| HTTP | 身份 | 返回 |
|---|---|---|
| `POST /api/feedback/{ticket_id}/attachments` | 本人或Owner/users；工单未关闭 | `{id,url,mime,width,height,bytes,created_at,ticket_revision}` |
| `GET /api/feedback/attachments/{id}` | 每次Bearer与工单权限校验；auditor可读 | 重编码后的图片二进制 |
| `POST /api/admin/media` | 仅Owner/resources | 公共封面`{id,url,mime,width,height,bytes}` |
| `GET /api/media/{id}` | 公共封面可匿名 | 公共图片二进制；不能用私有附件ID读取 |

约束如下：

- 每份反馈**总共最多3张**，包含玩家和管理员上传。输入原图及重编码输出各不超过5MiB，最多1600万像素且单边最多8192；拒绝动画。
- 只按真实文件解码接受PNG/JPEG/WebP，不相信扩展名或浏览器MIME。图片验证、旋转校正后重新构建像素图，去除EXIF等元数据并统一编码为`image/webp`；原图不永久落盘。
- `upload_key`为1–128位安全ASCII标识，只接受字母、数字、点、下划线、冒号、连字符。私图按工单+当前操作者隔离；同原图同键返回既有附件，不占第二名额；同键换原图返回`IDEMPOTENCY_CONFLICT`409。公共封面按上传管理员+key隔离。不能根据“看起来相同”跨不同原文件重用key。
- 每次新增私图增加工单revision，重放返回当前`ticket_revision`。正文先独立提交，图片失败后可保留正文并以同key重试失败图片。三图额度由同一部署事务锁保护，并发上传不能超限。
- 私图URL为`/api/feedback/attachments/{id}`，不含token；浏览器认证fetch后转Blob或走受限本机代理，及时撤销object URL。私图响应`Cache-Control: private, no-store`、`X-Content-Type-Options: nosniff`。公共封面URL固定`/api/media/{id}`。
- 图片记录与磁盘分区固定，客户端不能选择路径或把私有附件改为公共图。文件缺失或摘要不匹配返回404，不返回内部路径。普通事务失败清理新文件；提交应答未知先回读引用，避免删掉已提交附件。数据库也不可用时保留不可直接访问的待对账文件。

常见错误：`INVALID`400（字段或multipart结构）、`INVALID_IMAGE`400、`IMAGE_SIZE/IMAGE_DIMENSIONS`413、`ATTACHMENT_LIMIT`409、`FEEDBACK_CLOSED`409、`MEDIA_STORAGE`503。大文本表单字段触发Django正文门禁时413，损坏multipart或可预期请求结构异常400；不会以500代替这些请求拒绝。普通JSON仍限制64KiB，不能因图片入口放宽整个API。

领域入口位于 `revival/media.py`：`upload_feedback`、`upload_public_cover`拥有上传事务；HTTP层不要再包外部业务事务。`read_feedback_attachment`、`read_public_cover`返回`(bytes,mime)`。本机Launcher图片桥只接受固定工单/附件操作和有界图片数据，不提供任意URL代理；业务Bearer留在本机服务中，不交给React渲染器。

Launcher公共活动封面使用 `POST /launcher/api/campaign-cover-image`，正文只收`{media_id}`，仍要求精确本机Host/Origin、随机能力头与已登录控制器。Python客户端只匿名GET固定`/api/media/{32位小写hex ID}`，不附加玩家Bearer，不跟随跳转，限制5MiB且只接受WebP。响应为`{content_base64,content_type}`；`RuntimeApi.readCampaignCover(mediaId)`转换为有界Blob，`CampaignCover`组件只接受严格公共媒体路径，切换或卸载时撤销object URL。Web入口沿用同源图片src，不让Launcher浏览器直接请求本机不存在的`/api/media`，也不开放任意URL代理。

### 活动展示配置与独立地址

活动规则继续使用既有`campaign.save/publish/pause/end`；展示信息放独立`campaign_presentations`，使用独立revision，不改变冻结的奖励条件、内容、领取状态或封榜快照。旧活动迁移回填`slug=campaign.id`，新草稿初始规则相同。

`campaign.presentation.save`仅Owner/resources可写，命令字段：

```json
{
  "action": "campaign.presentation.save",
  "campaign_id": "活动ID",
  "expected_revision": 1,
  "presentation": {
    "slug": "opening-season",
    "title": "开服活动",
    "summary": "活动简介",
    "theme": "default",
    "cover_id": null,
    "sections": [{"key": "intro", "kind": "text", "title": "活动说明", "body": "纯文本正文"}]
  },
  "request_id": "独立请求ID",
  "idempotency_key": "独立幂等键",
  "reason": "更新活动展示"
}
```

展示对象允许部分字段更新。slug为1–96位小写字母/数字及内部连字符、全局唯一；**首次发布后永久锁定**，暂停或结束也不能改。title非空最多120字符，summary最多500且不带控制字符；theme为`default/tactical/anniversary`。cover_id只能引用管理员已上传的公共媒体，null或空字符串清除，不能填外链或私图ID。

sections最多12个。每项只接受`key,kind,title,body?`，key为唯一的1–32位ASCII字母/数字/下划线/连字符；kind为`text/rewards/leaderboards`。title最多120，body可空、最多5000字符且只作纯文本；奖励/榜单模块展示服务端规则，不接受客户端自定义发奖内容。未知展示字段拒绝。

成功返回 `{slug,title,summary,theme,cover_id,cover_url,sections,revision}`；revision增加1，cover_url为空或`/api/media/{id}`。陈旧展示修订`VERSION_CONFLICT`409；重复slug为`SLUG_CONFLICT`409；发布后改slug为`SLUG_LOCKED`409。该expected_revision针对presentation，而非活动草稿revision。

`campaigns/campaign-admin`的每个活动追加上述presentation。`GET /api/public/campaign-detail?slug=...`与认证`GET /api/views/campaign-detail?slug=...`返回单个活动与presentation，也接受`campaign_id`查询。游客/玩家看不到草稿，返回404；Owner/resources/auditor可读草稿。游客只读公开定义，不包含个人progress或领取资格；有效玩家读到本人的progress/rewards。网页独立地址为`/activities/{slug}`，不是奖励领取接口。

### 本轮媒体部署增量

当前开发阶段不因此自动部署或覆盖已有发行包。实际发布前以本轮测试、包清单与目标环境再确认：

1. 安装`requirements.txt`固定的Pillow依赖，按Django依赖向前执行0024/0025。0024撤销旧管理员会话且退出旧TOTP；先确认新邮箱认证可用，不能把旧认证页面与新API混搭。
2. 生产设置`NZ_MEDIA_ROOT=/var/lib/nz-web/media`等绝对、独立可写的持久目录，运行用户可写且只给予必要权限。该路径处于现有Web unit的`ReadWritePaths=/var/lib/nz-web`范围。数据库与图片同步备份，源码目录和发行包不收录私图。
3. Nginx只对`/api/admin/media`及32位工单ID的`/api/feedback/{id}/attachments`设置约6m传输上限以容纳5MiB图片及multipart开销；普通路由保持64k。应用继续校验真正的5MiB文件、格式、像素和权限；不要把`private_feedback`目录配置为静态资源。
4. 应用回退保留0024/0025、新媒体目录及业务数据；不反向删除表、不复活旧管理员秘密或会话。提交应答未知保留的潜在孤儿文件需离线按数据库引用对账清理，不能直接删除仍被引用图片。
