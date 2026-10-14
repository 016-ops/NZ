# 实际本地 HTTP API

这是自有研究控制面的接口，不是逆战原生协议。默认地址 `http://127.0.0.1:8765`，只有 loopback 可以监听。所有 POST 要求 `Content-Type: application/json` 和 `X-NZ-Request: 1`；认证后携带 `Authorization: Bearer <session>`。无 Cookie/CORS 会话。

## 路由

| 路由 | 用途 | 授权 |
|---|---|---|
| `GET /health`、`GET /api/status` | 服务与能力状态 | 公开只读，无凭据 |
| `POST /api/login` | `{kind,login,password,otp_code}` | admin 要 MFA；player 需已批准/未制裁 |
| `POST /api/register` | 邀请注册，含 login/password/invitation 及命令元数据 | 有效邀请，PENDING_REVIEW |
| `POST /api/logout` | 撤销当前会话 | 当前会话 |
| `GET /api/views/{view}` | 下表列出的读取视图 | 按服务端权限检查 |
| `POST /api/command` | 下表允许的命令及元数据 | 按每个 action 检查 |
| `POST /api/asset-preview` | 使用真实校验代码做单人发放预览，并回滚 savepoint | asset.preview |
| `POST /api/node/heartbeat` | match_id/epoch 续租 | 独立节点令牌 |
| `POST /api/node/settlement` | match_id/epoch/outcome | 合成模式、独立节点令牌，冻结规则定奖励 |

请求体最大 64 KiB，拒绝重复 JSON key、NaN、Transfer-Encoding 和跨 Origin/Host。Django 校验请求与授权，单进程 Waitress 使用 16 个 HTTP 工作线程；登录/注册每 IP/路径 5 分钟 20 次，其他写入每分钟 120 次。这些是本地保护策略，未作容量保证。页面入口保持 `/`，构建后的资源位于 `/assets/`；原 `/app.js`、`/style.css` 已由 Vite 哈希资源替代，业务路由保持不变。

## 读取视图

`me` 返回当前玩家自己的脱敏账号/角色/钱包/库存/装备/制裁/席位；管理员只取得自己的 ID/角色。`users` 支持字面包含 `search`、`limit`（1–100）、`offset`；`user`/`assets` 用 account_id；`operations` 用 account_id 查询流水；`audit`/`outbox`/`invitations`/`dashboard` 返回实际记录。`catalog`/`rooms` 允许已经登录的身份读取。

Owner 有全部权限；users 角色可邀请/审核/重置/制裁，不能发资产；resources 可有限额发资产，不能改账号；auditor 只读。用户/资源管理员的审计读取限定自己的操作。第一版只有 Owner 的受保护初始化入口，其他管理员生命周期管理未提供。

## 命令

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
