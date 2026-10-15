# 实际本地 HTTP API

这是自有研究控制面的接口，不是逆战原生协议。默认地址 `http://127.0.0.1:8765`，只有 loopback 可以监听。所有 POST 要求 `Content-Type: application/json` 和 `X-NZ-Request: 1`；认证后携带 `Authorization: Bearer <session>`。无 Cookie/CORS 会话。

## 路由

| 路由 | 用途 | 授权 |
|---|---|---|
| `GET /health`、`GET /api/status` | 服务与能力状态 | 公开只读，无凭据 |
| `POST /api/login` | `{kind,login,password,otp_code}` | admin 要 MFA；player 需已批准/未制裁 |
| `POST /api/register` | 兼容注册入口；默认必须提供邮箱及验证码，拒绝 login 字段 | 同邮箱注册规则，PENDING_REVIEW |
| `POST /api/player/register` | `{email,password,invitation,challenge_id,code,...元数据}`；邮箱大小写统一，审批前不可登录 | 有效邀请及 register 邮件验证码；不接受 login 字段 |
| `POST /api/player/login` | `{email,password}`；独立邮箱密码，不读取 QQ 凭据 | 已批准邮箱玩家；kind 参数不能提升为管理员 |
| `POST /api/player/email-code` | `{email,purpose}`；purpose 为 register/reset/verify，register 另需 invitation | 同源公开入口，持久发送额度与邀请校验 |
| `POST /api/player/password-reset` | `{email,password,challenge_id,code,...可选完整元数据}`；password 为新密码 | reset 邮件验证码；消费与改密/撤销会话同事务 |
| `POST /api/player/email-verify` | `{email,challenge_id,code,...可选完整元数据}` | verify 邮件验证码，只验证既有绑定邮箱 |
| `GET /api/launcher/status` | 独立登录与原生适配实际能力 | 公开只读，原生启动仍 false |
| `POST /api/launcher/tickets` | `{protocol,client_sha256,verifier_sha256,...元数据}`；60 秒一次票据 | 邮箱玩家 Bearer |
| `POST /api/launcher/redeem` | `{protocol,client_sha256,ticket,verifier}`；兑换独立游戏会话 | 单次票据及持有证明，不使用 QQ |
| `POST /api/game/session` | `{protocol,client_sha256}`；读取本地游戏身份 | 独立游戏 Bearer，不能用于普通 API 或节点接口 |
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

`user.email.set` 接受 target_account_id/email 及命令元数据，权限为 user.reset；Owner 必须当前密码和新 MFA 再认证。保留账号、game_id、角色和资产，撤销旧会话并禁用该玩家的旧用户名登录入口。邮箱唯一、统一小写；不合并加号或点别名。管理员绑定邮箱不代表所有权验证，verified_at 保持空；玩家可用 verify 邮件码补验，也可在持有 reset 邮件码时找回密码并完成验证。改变绑定邮箱或安全版本会使此前恢复码失效。

### 邮件验证码合同

发码返回 challenge_id、expires_in=600、retry_after=60 和通用提示；六位验证码只发到请求邮箱，数据库仅保存带服务端密钥的 HMAC 摘要。每个码绑定邮箱、用途及挑战 ID，恢复/补验另绑定账号与安全版本。最多五次错误验证，失败计数持久保存；同用途重新申请使旧码失效。码过期、已用或无效统一返回 EMAIL_CODE/403。成功注册的原幂等请求可重读结果，不会二次消费邀请或创建账号。

密码恢复和旧邮箱补验可携带完整 `request_id`、`idempotency_key`、`reason`；若提供其中任意字段，三项都必须合法。相同用途/邮箱、同键和相同业务内容重读已提交结果，包括响应丢失、服务重启或并发消费后，不再次改密或撤销会话；同键改内容返回 IDEMPOTENCY_CONFLICT。验证码和新密码仅以服务端 HMAC 参与持久幂等摘要，不记录明文。不带元数据的旧调用仍为一次性消费，成功后重复提交返回 EMAIL_CODE；不要为重试临时换键。页面已携带完整元数据，并在请求期间锁定输入、身份和用途切换，防止旧响应覆盖新表单。

新邮箱账号的内部登录别名使用随机值，不再由邮箱确定；已换绑释放的邮箱可重新注册一个独立账号。历史账号内部别名、原 ID/game_id、角色和资产不重写，历史注册幂等标识保持兼容；内部别名不能用于公开登录绕过邮箱认证。

注册同键并发时，即使另一请求在首个幂等查询之后、验证码预检之前已经提交，也应读回原成功结果；验证码预检失败后会再次查验已提交幂等结果，无原结果则仍拒绝。此交错已用真实 MySQL 固定调度复现和回归。当前 DEV 发布 `20261015T061412-1cfd594907ca` 已包含上述合同。

每邮箱跨用途 60 秒冷却、每小时 5 次，每来源每小时 20 次，每独立业务库每小时 100 次；额度在 SMTP 前持久预留，发送失败也不释放。配置缺失返回 MAIL_NOT_CONFIGURED/503；超过额度返回 RATE_LIMIT/429。SMTP 拒收/超时留固定运维错误码，公开响应仍为通用提示，客户端不应自动重发。未知邮箱的 reset/verify 请求同样投递邮件并返回相同形状，但无绑定账号时不能消费为恢复或新账号。

新注册默认启用 NZ_EMAIL_VERIFICATION_REQUIRED=1，完成验证后仍为 PENDING_REVIEW。找回密码采用正常密码强度与慢哈希，原子撤销旧 Web/游戏会话；不改变审核、停用、制裁、管理员权限、角色或资产。补验旧邮箱仅更新 verified_at，不更改密码。邮件入口沿用 Host/Origin、JSON 和 X-NZ-Request 保护，不使用 Cookie 或 URL 凭据。

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
| `shop.purchase` | catalog_version, offer_key | 玩家本人，服务端定价 |
| `inventory.consume` | catalog_version, instance_id, quantity | 玩家本人，已确认可消耗类型 |
| `inventory.equip` | catalog_version, instance_id, slot | 玩家本人，按目录槽与期限 |

货币变化为 `{type:"currency",code:"GP",delta:100}`，物品为 `{type:"item",item_key:"test-token",quantity:2,duration_seconds:3600}`；省略 duration_seconds 表示目录允许的永久实例。默认当前国服目录为空，只在 test-only-v1 模式可使用这些示例。

大额发放（单次请求所有币种绝对增量之和 >10,000）、批量和补偿另带 `current_password`、新的 `otp_code`。资源管理员超限拒绝。不要把凭据写到命令行或公开示例；通过受保护的本地程序交互获取。同键成功重试不需要重新消费 MFA，但仍需有效会话和权限。

2026-10-14 用户另行要求单个测试管理员使用永久固定测试验证码：只有配置明确匹配的账号 ID、合成数据库名且处于 TEST_PROFILE 时，其登录与再认证允许重复该六位码。其他账号、其他库及当前客户端模式保持正常 TOTP 防重放；密码和权限校验不变。会话仍为 900 秒。固定码仅保存摘要，凭据不进入本接口文档或公开证据。

用户还要求另一个指定测试管理员登录时免动态验证码：例外同样绑定唯一账号 ID、合成数据库名和 TEST_PROFILE，仅用于登录；停用、密码、会话、安全版本及权限仍校验，高风险业务再认证不继承此例外。六位测试密码只由受控本地创建命令允许，普通 HTTP 注册/重置继续要求十二位以上。

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

以下命令继续使用 `/api/command` 和既有元数据、错误格式。管理员命令、批次入队/重试、复核和节点发证均需 Owner 密码及新的 TOTP；批次预览不消费 TOTP。

| action | 字段与约束 |
|---|---|
| `admin.create` | login/new_password/role；首次响应含 MFA 密钥、八个恢复码 |
| `admin.update` | admin_id/role/active（布尔）；保护最后一位启用 Owner |
| `admin.password.reset` | admin_id/new_password；撤销旧会话 |
| `admin.mfa.reset`、`admin.recovery.rotate` | admin_id；首次响应显示新密钥或恢复码，幂等重试不再返回秘密 |
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
