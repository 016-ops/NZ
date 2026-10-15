# 独立邮箱认证与 Win32 身份交接

后续记录：本文件保留初次邮箱登录交接时的状态；阿里云邮件验证、找回密码及完整启动研究的后续进展见[邮件与原生启动交接](2026-10-15-aliyun-email-and-native-launch.md)，最新 DEV 为 `20261015T061412-1cfd594907ca`。下文“尚无邮件验证/找回”是当时边界，已由后续批次更新；完整游戏大厅仍未通过。

**原生字段语义更正（2026-10-15 后续）：**本文件所用 IPC v1 将 tag1 自测输出标为 LocalGameId；后续当前游戏消费代码证实 tag1 是 ServiceId，tag2 才是 Uin。服务器主地址/端口也应为 sub2/sub3。下文及关联 v1 JSON 保留原始记录，只证明 DLL 可读取构造字节，不能作为正确游戏身份映射的证据。本机已升级 IPC v2；第 13 次捕获首包，第 15 次通过 challenge，第 16/17 次换钥后取得 GEO PingList，其中第 17 次使用真实 DEV 邮箱身份。后续 18/19 消费单条回环 GEO 目录，20/21 真实邮箱身份到达 Zone A000；第 22/23 次消费 A001 研究响应后返回 A204/FF05；角色/大厅未通过。后续交接记录这些有限进展，下文保留初次交付历史。

## 基本信息

- 日期：2026-10-15（北京时间）。用户确认没有另一份客户端，要求用自建邮箱登录替代腾讯 QQ 登录机制；不再以取得腾讯原启动器作为前提。
- 目标：实现可用的邮箱身份服务、保留旧账号资产、版本化启动交接，并推进当前原生组件适配。**完整 TGame 登录/区服/大厅尚未接通。**
- 涉及 `revival/security.py`、`service.py`、`launcher.py`、模型和 migration 0005、HTTP/限流、React 邮箱入口、`tools/email_launcher.py`、Win32 IPC 研究适配及测试。遵循 nz-development 技能的技术栈、中文注释及交接要求。

## 已实现功能

1. **邮箱注册与密码登录**：新玩家用邮箱、独立密码及邀请注册，人工批准后登录；不收集 QQ 账号/密码、Cookie 或官方登录材料。ASCII 邮箱统一小写，最长 254，本地部分最长 64，不合并点/加号别名；密码继续随机盐 scrypt。新邮箱注册重试摘要增加服务端 HMAC 保护，避免数据库形成另一个便宜的密码猜测校验器；既有账号及资产幂等记录不改写。
2. **旧账号迁移**：`user.email.set` 按 user.reset 权限绑定邮箱，Owner 需当前密码和新 MFA。账号 ID、game_id、角色、资产和账本保留；撤销旧会话，禁用已绑定账号的隐藏旧用户名登录入口。管理员仍用原账号加 MFA。邮箱搜索支持完整长度。
3. **独立启动协议 `nz-email-v1`**：邮箱玩家 Bearer 申请 60 秒一次票据，绑定源会话、安全版本、profile、声明的客户端指纹和持有证明；兑换产生独立游戏会话。数据库只存秘密摘要，并发只成功一次；重复兑换不能取回原秘密。新票据替换待用票据，新兑换撤销旧游戏会话。所有授权重查源会话/期限/制裁；注销、重登、封禁或重置使交接失效。游戏令牌不能充当普通玩家、管理员或节点令牌。
4. **独立启动器诊断**：密码仅交互输入，不放 URL/argv/环境或临时文件；只访问回环 HTTP/SSH 隧道，不跟随 HTTP 跳转。成功诊断结束自动注销源会话。原生适配未完成时强制 `--handshake-only`，不会显示假“进入游戏”按钮或运行腾讯 Client.exe。
5. **实际 Win32 组件交接**：从当前 TenProxy.dll 的导出/虚表和读取代码核对 IPC，32 位独立进程中调用原 DLL 的 `Invoke`/`GetLoginInfo`，实际读到本地 ID、独立游戏令牌和每会话标识。DLL SHA256 固定为 `788bea4e34d6aaea8187df482ea2c859f7a6c8802b226b1ab7c2d9a6a10bc047`，签名 Valid；包装器及脚本均检查当前文件身份。进程属于本次 Job，到期只清理自身子树；不修改其他进程代码或安装/关闭保护驱动。
6. **增量持久化**：migration `0005_independent_email_login` 新建 player_emails、launch_tickets、game_sessions；旧 accounts 字段与历史表不重建。现有库未按用户名猜测邮箱，verified_at 默认空。迁移前当前库/PvE 库分别完成私有 SQL 备份，在临时库恢复并对账 32 张业务表，之后仅删除本次临时库。

### 入口和边界

HTTP 详见 [API](../API.md)：`/api/player/register`、`/api/player/login`、`/api/launcher/status`、`/api/launcher/tickets`、`/api/launcher/redeem`、`/api/game/session`。保持原 JSON/错误、同源及请求体规则。专用玩家入口忽略 kind 提权，不能以用户名/QQ 凭据绕过邮箱校验。

React 默认显示“玩家邮箱”，管理员使用独立页签；旧账号可在用户管理中设置邮箱。保留邀请审核流程。**未配置邮箱所有权验证或邮箱找回**，邀请与人工审批不等于验证邮件，因此不允许凭未验证邮箱自动重置密码。

### 原生差异记录

`config/email-ipc-research.json` 冻结 `nz-email-ipc-research-v1`：版本下限 32、32 位偏移、长度字段、UTF-16 字符串来自当前 DLL；身份槽使用本地 game_id，令牌槽使用自建服务短期游戏令牌，16 字节会话标识从带版本命名空间的令牌摘要派生，两个账号不共用固定身份或固定会话。

产品槽 1、未知标量默认值和空服务器列表均是**研究兼容值**；尚未验证完整业务含义。不生成 QQ 签名、官方认证材料或接入腾讯认证。现有组件愿意读取结构不代表它验证了自建账号，更不代表服务端可以信任客户端结算；真正授权仍需自建服务校验游戏令牌。当前结构没有用于启动完整 TGame。

原生自测使用 Windows 自带 32 位 Windows PowerShell/.NET Interop；这是本地 ABI 适配工具，业务服务仍为 Django，网页仍为 React/TypeScript/shadcn/ui。Cdecl 工厂与 ThisCall 实例调用分别处理，依据当前代码及 [Microsoft CallingConvention 文档](https://learn.microsoft.com/en-us/dotnet/api/system.runtime.interopservices.callingconvention)。可选静态反汇编固定 capstone 5.0.7，不进入 DEV 运行依赖。

## 未实现内容

- 完整 TGame 的自建启动入口、实际游戏进程的身份交接、原生目录/区服/角色/大厅消息编解码及网关尚未实现。当前 HTTP JSON 不会自动变成原客户端网络协议。
- 游戏内邮箱登录至大厅、单图出生/战斗、资产投影、可信结算及 M5 全图闭环未验收。
- 未恢复 Win64；现有归档候选仅 Win32。Windows 系统隔离恢复和双端验收仍未完成。
- 邮件验证码、邮箱所有权确认、自助找回/更换邮箱未实现；不将未验证邮箱当成恢复凭据。

## 当前阻塞

| 项目 | 证据与影响 | 解除条件/下一步 |
|---|---|---|
| 原生网络/启动适配 | 已能把自建身份送入 TenProxy 自测，但服务器记录为空、完整字段语义及当前原生帧未证实 | 实现绑定自建网关的完整游戏启动交接，取得当前样本实际请求响应，再逐消息族接区服/角色/大厅；不能猜 PH 消息号或补丁地址 |
| 实机运行上下文 | 上批 TGame 仅到启动图，requireAdministrator；本批只运行普通权限独立 DLL 自测 | 正常游戏启动及隔离恢复单独验证；组件自测不能替代 |
| 多人/PvE | 真实游戏节点与双端共同对象尚未接通 | 单端完整路径后再配合执行双端 M5-T01–T12，保持 NOT_RUN |

腾讯 Client.exe 的 HashMismatch 是旧路线的历史证据，已经不作为“必须提供另一份启动器才能继续邮箱研发”的要求。新路线仍有上表中实际未实现的原生工作。

## 验证记录

| 验证 | 实际结果/证据 | 边界 |
|---|---|---|
| MySQL 邮箱首轮专项 | 用户 MySQL 8.0.30-txsql，12 项通过；`evidence/email-login-mysql-tests.json` | 真实事务/并发/幂等/制裁及 Waitress HTTP，不是进游戏 |
| 最终邮箱安全专项 | 新增 HMAC 摘要保护后，13 个邮箱业务测试通过；原生整合曾因子进程模块路径失败 | 原始失败保留在 evidence/failures/email-login-final-native-attempt-01；修复后单独重跑原场景 1 项通过，`evidence/email-login-final-native-mysql-tests.json` |
| 原生自测 | 当前已签名 DLL，GetLoginInfo 成功、ID/令牌/会话标识一致、尾部哨兵完整 | `evidence/email-login-native-ipc-self-test.json`；仅独立组件 |
| 邮箱至原生整合 | 两个真实 MySQL 邮箱经 HTTP 签发/兑换后，原生分别读到 10001/10002、各自令牌/会话，源会话已注销 | `evidence/email-login-native-bridge.json`；没有运行完整 TGame，不是双端实机 |
| 工具 | 23 项通过；`evidence/email-login-tooling-tests.json` | 含跳转不转发密码、URL 限制、IPC 边界及 Job 生命周期 |
| 前端 | 类型检查/构建通过，2 个 Vitest 通过 | 初次沙箱 esbuild 上级路径拒绝，正常用户权限重跑通过；不是业务代码失败 |
| 备份 | 两库分别备份并在临时库恢复，对账 32 张业务表通过 | SQL 只在忽略的 runtime/backups，公开报告只有计数 |
| 全量 | 用户 MySQL 上完整套件 119 项通过，1,237.609 秒；`evidence/email-login-full-mysql-tests.json` | 该轮含 20 个工具检查；末尾邮箱摘要加固及 IPC 修复由上述最终专项覆盖 |
| 去重汇总 | 全量、最终邮箱专项、原生修复及最新工具报告合并，**124 个不同测试 ID 均有通过证据**，无未解决失败 | 其中 101 项使用真实 MySQL（含原生整合），23 项为独立工具；重复运行不重复计数 |
| DEV | 发布 `20261015T050503-a7827976c028` 成功；两环境邮箱注册/审批/登录/角色、原生交接、重启/重登通过，四服务 active | `evidence/email-login-dev-deploy.json`、`email-login-dev-verification.json`；临时账号已停用，邀请已撤销，Owner 凭据未重置 |
| DEV 数据与页面 | 两库完整性正常、各六个历史保护触发器保留，仅回环监听；浏览器核对邮箱登录/邀请注册及管理员验证码入口 | `evidence/email-login-dev-integrity.json`、`email-login-dev-ui.jpg`；页面无凭据，仍标注原客户端能力未接入 |

早期 IPC 脚本失败分别为 Windows PowerShell 5.1 的 UTF-8 BOM 要求、从 PowerShell 7 继承的模块路径缺少 32 位 Get-FileHash；已保留私人失败证据。修复采用 UTF-8 BOM 及仅本次子进程的系统 PSModulePath，保持签名/哈希门禁，不更改系统执行策略或用户配置。后续原生整合失败/修复结果按实际报告记录，不隐藏失败。

本批 Python AST 中文 docstring 检查、Django check、makemigrations --check、pip check、git diff --check 已执行通过；新增 C#/PowerShell/TypeScript 说明职责、输入输出及重要边界，不声称全仓库注释整改完成。

## 接手与恢复

1. 正常部署先执行 Django migrate；迁移只添加三表。保留现有 MySQL、审计、制裁、账本及稳定的 NZ_SECRET_KEY。更换密钥后未决邮箱注册重试可能返回幂等冲突，应通过正常登录/审批查询确认已有账号，不能重做资产。
2. Windows 诊断：`.venv/Scripts/python.exe -m tools.email_launcher --server http://127.0.0.1:18765 --workspace runtime/native-recovery/coherent-2020/nz --handshake-only --proxy-self-test`。本机/SSH 隧道均为明确端口回环地址，邮箱密码交互输入；不要放入命令行、聊天或提交文件。
3. 原始 nz/ 与 RAR 保持不变，IPC 只创建本次进程的共享映射，不补丁游戏二进制。新增源代码、无秘密证据可交接；客户端、SQL 备份、凭据、完整反汇编和原始错误日志不提交。
4. 回退优先前向修复。旧版本不支持邮箱路由或绑定后的别名限制，不能直接回退并继续开放新登录；需要维护隔离、兼容性检查，保留全部新邮箱/会话/审计记录。禁止以旧 SQL 覆盖新增业务写入。
5. 下一步按当前版本验证完整游戏进程与自建网关之间的原生协议，再实现大厅与角色，后续接房间、实例及 PvE。`native_launch_ready` 和 M5 实机状态在证据齐备前保持关闭。

DEV 访问保持用户选择的 SSH 隧道：[当前环境](http://127.0.0.1:18765)、[独立 PvE 测试环境](http://127.0.0.1:18767)。玩家通过邮箱邀请注册/审批，管理员使用原 dev-owner 资料与正常 MFA。两环境 0005 增量结构已实际使用，旧 schema_version=1 只是保留的源格式标签，不代表 Django 迁移号。
