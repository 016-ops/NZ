# 阿里云邮件验证与原生启动续建

## 基本信息

- 日期：2026-10-15（北京时间）。续接[独立邮箱认证](2026-10-15-independent-email-login.md)及[原生客户端恢复](2026-10-15-native-client-recovery.md)。
- 目标：把用户已有阿里云发信能力接入项目，完成真实邮件验证码注册和找回密码，并继续推动独立身份进入原客户端。邮件业务已在 DEV 验证；完整原客户端角色、区服、大厅与战斗尚未通过。
- 业务文件：`revival/mailing.py`、`email_verification.py`、`service.py`、`models.py`、`backup.py`、`launcher.py`、HTTP/限流、`nzproject/settings.py`/`urls.py`、migration `0006_email_verification`、React `email-access.tsx`、邮件验证/登录/随机内部别名与原生交接测试。
- 工具与部署：`tools/deploy_dev.py`、`dev_install.sh`、`mail_acceptance.py`、`native_email_boot.py`、`native_email_probe.py`、`native_email_proxy.ps1`、配置恢复守卫与当前客户端 IPC 研究适配。遵循 nz-development 技能，不改原始 `../nz`，不把客户端、密码、验证码、令牌或私人运行日志提交到仓库。

最新原生进展：IPC v2 已修正当前身份及端点字段。第 18 次当前客户端消费 GEO PingList 响应并返回 ZoneList 请求；第 19 次消费唯一回环 ZoneList 条目，建立第二条连接并独立完成认证、challenge、换钥。第 20/21 次使用真实 DEV 邮箱身份收到当前 26 字节 A000 登录请求。第 22/23 次发送当前 DLL 独立核验的 41 字节 A001 研究响应，实际收到 A204/FF05，确认正常初始化分支；23 修正采集结束的连接保持后复验通过。完整玩家资料、角色、大厅、重连和 M5 未通过。下文早期运行均保留为历史；旧 v1 LocalGameId 标签语义错误不再作为正确身份映射证据。

用户随后将部署范围限定为一个可选区服入口、一个测试房间节点，保留同节点多房与原双房隔离验收。Windows Server 2025 目标已通过 SSH 认证、主机密钥固定和只读盘点；已建立专属 `C:\NZResearch`，仅放置无凭据标记、说明、计划与只读探针，尚未安装原生运行时或启动房间服务。见[单服务器交接](2026-10-15-single-room-server.md)。

## 已实现功能

### 邮件服务与业务闭环

1. 使用用户现有的阿里云 cn-hangzhou 触发邮件发信地址，专用 SMTP 密码保存在本机忽略的 `runtime/aliyun-mail.env`。未重置云密码、创建 AccessKey 或改变云资源。端点 `smtpdm.aliyun.com:465`，标准证书和主机名验证；实际认证使用 TLSv1.3。区域端点参照[阿里云官方说明](https://help.aliyun.com/zh/direct-mail/smtp-endpoints)。
2. 新玩家默认通过邮箱六位验证码注册；保留有效邀请和人工审核。验证码申请只验证邀请，注册事务才消耗邀请一次。邮箱统一规则和独立账号 ID/game_id 命名空间保持不变。
3. 提供找回密码与旧邮箱补验。密码恢复在同一事务内消费验证码、更新密码、撤销旧 Web/游戏会话并记录审计；不解除审批、停用或封禁，不改角色、资产和管理员 MFA。管理员改绑邮箱仍需玩家证明新邮箱所有权。
4. `email_challenges` 仅存带服务端密钥的验证码 HMAC、来源摘要、用途、账号/安全版本绑定、有效期、失败计数和投递状态。有效期十分钟、最多五次错误尝试；同用途新申请替代旧码。发送额度在 SMTP 前持久预留，供应商失败同样计额度，不自动重试未知结果。
5. 发码、补验、恢复继续使用同源 JSON 接口及原错误格式。未知恢复邮箱同样收到邮件和通用响应，但不能因此获得账号或恢复目标。错误响应不带 SMTP 原始异常、邮件正文或秘密。
6. React/shadcn 玩家入口已接入注册、找回密码、验证旧邮箱和倒计时。验证码挑战及会话只在页面内存中；倒计时不替代服务端持久限流。原生启动能力保持 `native_launch_ready=false`。
7. 恢复/补验新增可选完整幂等元数据；响应丢失、服务重启和同键并发时读回已提交结果，不再次改密或撤销会话。验证码、新密码只以服务端 HMAC 参与幂等摘要；旧无元数据调用保留一次性消费。前端请求期间锁定字段与身份/用途切换，避免旧响应覆盖新表单。
8. 新邮箱账号使用随机内部登录别名，解决旧账号换绑后原邮箱被确定性别名永久占用的问题。历史内部名、账号 ID、game_id、角色和资产不改写，历史注册幂等标识兼容；释放的邮箱重新注册时获得独立账号，隐藏别名仍不能用于公开登录。
9. 修复注册同键请求的确定性并发交错：另一连接在首个幂等查询之后、验证码预检之前提交时，后到请求重新读取已提交结果，不把已经成功的注册误报为验证码失效。修复前固定调度用例已实际失败，修复后原场景与相关回归通过。

具体路由、字段、限流与错误见 [API](../API.md#邮件验证码合同)，配置与操作见[运行手册](../LOCAL_RUNBOOK.md#邮件服务与邮箱入口)。

### MySQL、备份与 DEV

- migration `0006_email_verification` 在当前库和 PvE 库向前应用，仅新增邮箱挑战结构；不重建旧账号、审计、制裁或资产表。
- 修复实际备份对账发现的 BooleanField 表示差异：MySQL 原生读取得到 0/1、ORM 得到 false/true，恢复校验仅对布尔字段归一化，仍逐行比较全部业务内容。最初的未验证 SQL 保留为失败材料，不能用于恢复；带 `-v2` 的当前库/PvE 库备份已分别在临时 MySQL 库恢复，对账迁移前的 35 张业务表后清理本次临时库。
- 本批又完成当前库和 PvE 库的最终备份，覆盖 migration 0006 后全部 36 张业务表，均在临时 MySQL 库恢复并逐表对账成功，包含 email_challenges。旧 35 表报告保留为迁移前历史，最新验证以 `aliyun-before-*-final-backup.json` 为准。
- 初次邮件发布 `20261015T054145-873d60549d7d`，69 个归档文件，SHA256 为 `873d60549d7d3827c3f4d03abb8634993750a2793b2ccf5da8ab2ff69850c3f9`，保留作为本批早期部署历史。
- 最新 DEV 已部署 `20261015T061412-1cfd594907ca`，同为 69 个归档文件，SHA256 为 `1cfd594907ca3d2488f0764624d6e7bcb0e0746fa0b9268f6d7811b3bcb0722d`，包含恢复/补验幂等、前端锁定、随机内部别名及注册固定交错修复。两个发布都是 Django/React 控制面发布，原客户端游戏节点未运行。
- SMTP 设置单独上传，持久文件为 `/opt/nz-revival/shared/mail.env`，root:nz-revival/0640；四个 systemd 服务显式读取它。上传阶段 0600，替换前保留旧文件；未提供新邮件配置时保留现有配置。
- DEV 继续通过 SSH 隧道访问本机 18765/18767，未开放公网管理端口。既有 Owner 使用正常 MFA，未重置 Owner 凭据。
- 最新发布后四个服务 active、两个环境前端/资源/能力、匿名权限拒绝通过；两库实际 MySQL 8.0.30-txsql 完整性正常，各保留六个历史保护触发器，18765/18767 仅监听回环。原测试账号重登后的 game_id、角色、verified 状态及资产视图一致；本次部署核验未追加邮件。
- 随后新增的 Windows 本机配置守卫、TCP 取样及 IPC v2 工具目前用于本地研究，DEV 邮箱 release 保持 `20261015T061412-1cfd594907ca`；不把本机首包捕获算作 DEV 原生游戏服务已部署。

### 原生启动研究

- 固定源码参考 `armangido/af-emulator` 的 `4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495`；其目标为 PH 1.0.0.24，只阅读源码，未运行上游启动器或套用其补丁地址、消息号、密钥。
- 早期 TenProxy.dll v1 自测覆盖空服务器列表和单个回环服务器记录，getter 与输出缓冲区保护检查通过；但自测 LocalGameId 标签所指字段实际为 ServiceId。它只证明构造字节可被 DLL 读取，原先据此称游戏身份映射正确的解释撤回，原 JSON 不覆盖。
- 新增有界启动工具：仅接受已登记、固定指纹的独立工作副本；挂起创建本次 TGame，先加入 Job 并发布按 PID 命名的共享内存，再恢复运行。进程和子进程限时清理，秘密不进入命令行。临时目录/版本服务配置只指向本机受控监听，完成后按摘要恢复工作副本原配置。
- 当前 GameLoader 静态分析已确认自身 PID 映射读取、子进程交接均复制 0x80000 字节，首次异常 RVA 对应通用内存复制中的 `rep movsb`。共享内存从早期 64 KiB 调整为 0x80000 后，后续观察不再复现首次 0xC0000005，强力支持原映射容量冲突的判断；尚未通过当次运行的源/目的地址确认每个异常细节。该修复不等于角色、大厅或地图已成功加载。
- 第 04 次使用真实 DEV 邮箱身份观察 180 秒，第 05 次同样身份观察 90 秒，两次均完成本次进程清理和工作副本配置恢复，但回环监听没有收到请求，role/lobby 均为 false。第 05 次只读进程资料确认最终 TGame.exe 在交互 Session 1，`-q`、`-log`、`-FORCELOGFLUSH`、`-nomoviestartup`、`-ABSLOG` 均保留，GameLoader 更新分支另有 `-glupdate`。因此仍需定位原生初始化停点，不能把未出现日志解释为启动参数未传递。
- TGameBase.dll 属于受保护的启动路径，当前只确认模块加载；不能据此宣称已经进入 UE 主入口。不得将 DLL 自测、服务端角色数据或启动图当作原客户端大厅验收。
- 第 06/07 次主线程采样定位到 `MessageBoxW`。第 07 次仅采集本次自有进程的 512 字节可执行代码，与未修改 TGame.exe 中的静态常量对照，确认失败分支是 Pixel Shader 低于 2.0 的图形能力检查；对应中文提示为 DirectX runtime 可能错误、无法继续游戏。这个采样停点发生在成功初始化图形设备之前，不是已经收到某个登录错误响应。
- 只读 Direct3D9 对照使用相同 x86 架构、Session 1、NVIDIA GeForce RTX 4070 SUPER 和驱动 `32.0.15.9186`。受限执行环境 HAL/REF 的 GetDeviceCaps 均返回 `0x8876086A`；非沙箱环境均返回 S_OK，Vertex/Pixel Shader 为 3.0。探针没有创建窗口或设备、修改系统或替换 DLL，因此现有证据支持执行环境限制导致能力读取差异，不能归结为显卡硬件不支持或直接要求升级驱动。
- 第 08 次在非沙箱环境有界启动 60 秒，已越过上述图形错误，实际加载 TenProxy/CrossProxy。用户确认出现加载画面但没有大厅；观察时主线程位于 TGame 的 Sleep 调用路径，停点原因尚未确定，受控回环监听仍无请求。该次进程清理、工作副本配置恢复均通过，角色/大厅协议仍未通过。
- 第 09 次正常用户环境 180 秒研究身份观察发生在图形门禁实现前，只有此前单独执行的 D3D9 对照；仍仅加载画面、无大厅及受控回环请求。此记录不能标为已通过当时尚未加入的启动工具门禁。
- 启动工具随后增加同一执行环境的只读 x86 D3D9 前置检查。受限环境检查失败时返回 `D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT`，游戏子进程尚未创建；第 10 次 60 秒运行实际得到 `graphics_preflight_verified=true`，进程清理、配置恢复、采样线程恢复均通过，仍无受控回环请求或大厅证据。
- 第 10 次补充定点代码已确认 RVA `0xFB2600` 是帧时间/最大帧率控制：根据返回速率计算 `max(0, 1/rate - elapsed)`，结合粗等候和细等候后更新时间状态。先前所见 Sleep 在这一路径中按时间间隔等待，不是此处的登录完成标志等待。尚未恢复准确 C++ 符号，也未读取本次运行的实际速率；这不证明持续渲染、加载完成或大厅消息交换。
- GetLoginInfo 与其初始化代码读取的顶层/服务器字段原本均已存在；进一步核对当前 TGame 的输出基址、成功日志参数与连接代码后，确认实际语义为 tag2=Uin、tag1=ServiceId、服务器 sub2=主地址、sub3=主端口。v1 曾将 game_id 写到 tag1、并把服务器 sub2/sub3 当名称/标量，导致 getter 成功仍不能正确向主端点建连。
- 本机已升级 `nz-email-ipc-research-v2` 与 `nz-email-ipc-loopback-research-v2`：独立 game_id 写 tag2，ServiceId 暂用明确标注的研究值 1；主地址/端口使用本次回环监听。sub4/sub5 等后续字段及 ServiceId 的正确业务值仍未核验，不沿用 PH 值作为证明。
- 第 11 次仅将 Geo/Zone 两个 Host 和两个 Port 改为回环监听，90 秒仍无请求，其他认证/TDR 字段保留。第 12 次接入持久配置守卫，三份变体均恢复并核验，30 次 TCP 状态快照成功；从该次实际游戏代码补齐上述字段语义证据。
- 第 13 次使用修正后的 v2 观察 90 秒，首次从本次受控客户端连接收到 60 字节，SHA256 为 `10f207e546215bf6e1df120327888806591b0ddd7557a1e9ceda53075bc7ddb4`。该次监听器未发送响应；后续结合当前 DLL 已确认为 AUTH，不能将第 13 次单独捕获当作往返通过。
- 第 14 次实际校验 AUTH 并发送 SYN，但旧验证器只记下 `TransportError`，没有保存已读头或错误码。该失败不能证明客户端没有回包或拒绝 SYN；随后当前代码分析确认异步 SYNACK 还包含 4 字节加密头序号及 16 字节密文体，原验证器无法接受这一封装。保留原失败，修正后第 15 次 60 秒运行实际完成 AUTH 60 → SYN 45 → SYNACK 61 字节的 challenge 往返。
- 第 16 次研究身份完成换钥，下行换钥帧为 48 字节，客户端新密钥上行帧为 44 字节，解出 9 字节 GEO PingList 请求。实际包头按大端四个 16 位字段读取为 `Magic=0x8202, Cmd=0x1001, HeadLen=8, BodyLen=1`，与当前 TDR 的单字节 `IsForceLogin` 请求声明相符。公开证据仅保留形状和摘要，不输出原始业务帧。
- 第 17 次使用已完成真实邮件验证的 DEV 身份再次通过上述路径，AUTH 为 72 字节，其余 challenge/换钥帧与研究场景形状相同；首个业务请求仍为 9 字节，SHA256 为 `62c85c113786fadf47bfcb66180e9ba50eb1a443f051fbb02d2681618cda5324`。两次均完成进程清理、三份配置恢复和捕获工作线程停止，没有追加邮件。这两次尚未发送 GEO 业务响应，没有角色/大厅证据；后续 18–21 次另列。
- 第 18 次发送与当前 DLL 独立 golden 相同的 52 字节 PingList 研究响应，客户端随后返回 40 字节 ZoneList 请求（SHA256 `4e9b0f9151539016d31e14d3b0957340f1e2ecd60cb5708530a0ab8165ad44a3`）；这是本次响应被当前客户端消费的证据。
- 第 19 次再发送一条 70 字节、固定回环地址的 ZoneList 研究响应，客户端建立第二条 TCP 并独立通过 AUTH/challenge/换钥，首个 Zone 应用为 11 字节、Magic `0x3243`、命令 `0x1250`。这证明单一研究目录入口被消费，不证明远端房间节点可用。
- 第 20/21 次使用真实已验证 DEV 邮箱身份重现两连接路径，随后收到 26 字节 `C2ZN_ReqLogin`：头 8 字节、命令 `0xA000`、体 18 字节，SHA256 `4c00864169617cf28adfedffc566c8e6867471d1836e6546a44548679b106a38`。此时没有发送 `0xA001`，等待后续应用时超时；该超时不能解释为已收到登录拒绝。四次清理、三配置恢复及捕获线程停止均通过，未追加邮件。
- 第 22 次发送当前 DLL 独立打包/回解核验的 41 字节 A001，实际收到无 body 的 A204（ReqGetPlayerDefineInfo）及 12 字节 FF05，确认当前正常初始化分支。响应使用显式研究 profile：主/子频道各 1，时间、币值、免费道具、新手与区域为 0，不投影真实余额/角色或赠送资产。八条采集上限触发主动关闭后出现尚未支持的重连帧；这是工具关闭造成的干扰，不能算登录拒绝。工具随后改为采集结束仍保持连接至主探针清理，第 23 次复验通过；完整字段及证据见[Zone 登录研究交接](2026-10-15-native-zone-login-wire.md)。
- 第 23 次使用真实 DEV 邮箱身份复验 GEO、Zone、A001、A204/FF05 路径，采集八条达到上限后保持连接直到主探针清理，仅有预期两条连接，没有再次出现第 22 次主动关闭引出的 IDENT 干扰。进程清理、三配置恢复与捕获线程停止均通过；角色/资产通知仍未发送，不把连接保持视为重连或大厅验收。
- 本机 transport 工具现覆盖严格分帧、身份校验、异步 challenge、换钥和新密钥业务帧解码；GEO 请求解析器只接受当前已核对的头、命令及长度。工具测试验证边界，实机 15–21 提供按阶段列明的有限往返证据。`transport_handshake_verified=true` 只表示首次自有会话 AUTH/challenge/换钥/首业务请求；重连及持续游戏会话仍未验证。
- 新配置守卫在写入前持久化原件、摘要和恢复计划，逐文件独立恢复，检测外部修改后保留现有内容，支持中断后加载计划恢复。14 项故障测试无跳过，边界及操作方式见[配置守卫交接](2026-10-15-native-config-guard.md)。
- 当前恢复样本的 UTFrontEnd 主文件与旧提取目录样本不同，不能把旧目录的全零判定套到此构建。当前配置列出的四个 FrontEnd 子地图在本副本和归档清单中均缺失；这只是资源缺口记录，尚未证明本次运行必须分别加载这些路径或因此受阻，合包/旧配置残留仍待证据区分。

### 当前 GEO/ZONE 元数据核对

只读核对恢复工作副本 `runtime/native-recovery/coherent-2020/nz/TGame/TDR/`，没有使用旧提取目录替代当前文件。`proto_c2geo.tdr` 为 1,514,022 字节，SHA256 `6d75e846532bb506a858323b8a1c91fa67d3ed0c8766f1d1682f9a8d8afbc8e1`；`proto_c2zn.tdr` 为 3,024,238 字节，SHA256 `c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba`。两者与当前归档恢复清单一致，检查前后哈希不变。

[公开字段证据](../../evidence/aliyun-current-geo-zone-metadata.json)记录 25 个选定结构，所有 622 个 GEO、1,904 个 ZONE 名称索引、结构自指针、基础字段记录长度及范围交叉核对通过。相对指针基址为文件偏移 `0x114`，结构头 `0xB8`、字段记录 `0xB4`；ZONE 中 23 个结构另带扩展记录，因此基础长度使用实际吻合的头槽 41，未把头槽 5 一概等同基础长度。这里是元数据描述符读取，强于旧工具的候选符号扫描，但仍不等于完整 wire 解析。

| 当前样本声明 | 确证值或字段 | 使用边界 |
|---|---|---|
| GEO 目录命令 | Magic `0x8202`；ReqZoneList `0x1000`/Res `0x2000`，ReqPingList `0x1001`/Res `0x2001`；成功宏 `GEO_ERR_SUCC=0x8B00` | 数值来自当前宏记录及 union selector；18/19 已验证研究 PingList/ZoneList 往返 |
| GEO ResPingList | Result 2 字节、ServerCount 2、ServerArray、MaxDelayInMs 4、RankInfo 10 | ServerArray 与 ServerCount 互相引用；最大 192 条，不采用 PH 的旧容量 |
| PingInfo | Group 4、Ipv4 4、Domain type 21/容量 128、PingInMs 4 | Domain 无 count 引用；当前离线 golden 已确认本次回环字符串为 BE32 长度 10、包含尾部 NUL；任意业务输入未开放 |
| RankInfo | Rank 4、NextQueryGap 4、CloseConn 2，无嵌套或 count 引用 | 当前回复包含该结构，不能照旧参考样本省略；业务语义和合理值仍待核对 |
| GEO ZoneInfo | Ipv4、Domain、Port、OnlineNum、PingInMs、MainChannelId、LoadExts[6]；ZoneArray 上限 16 | 元数据容量不是已验收区服或玩家人数 |
| ZONE 入口声明 | Magic `0x3243`；Login `0xA000/0xA001`、CreateAccount `0xA002/0xA003`、CheckNickname `0xA144/0xA145`、ChangeRole `0xA146/0xA147` | 来自独立当前 ZONE 文件；20/21 已收到 A000，22 的 A001 研究响应已消费；建角/昵称/角色尚无完整往返 |

首个 GEO 请求已支持包头大端和单字节请求体的有限结论，见[16/17 实包与当前字段对照](../../evidence/aliyun-native-geo-first-request.json)及[严格请求解析 5 项测试](../../evidence/aliyun-native-geo-ping-parser-tests.json)。其他类型的有符号性、变长数组及嵌套业务结构不能仅凭元数据泛化。

随后[当前 DLL 离线探针](../../evidence/aliyun-native-tdr-offline.json)以固定指纹 `ProtocalHandler.dll` 加载当前 GEO TDR，在独立有界 x86 Job 内完成验证，不启动游戏或网络 Init。离线尝试 01–04 属于工具编码/BOM、x86 哈希命令问题；05–07 属于错误虚表槽位调用导致的 ABI 工具失败，均保留私人原始证据，不能算成新增游戏运行失败。当前确证 pack 为 `+0x5C`、unpack 为 `+0x60`、`+0x64` 为 error-text；主接口 RecvPackage 为 `+0x28`，之前分析的其他命名已更正。

离线尝试 08 将实收 9 字节请求 unpack 再 pack，逐字节保持。09 分别验证空目录及单条研究目录：先独立打包响应 body 得到实际长度，再回填头打包全包、回解完整 host 缓冲区并再次打包，所有状态码为 0，哨兵、完整 host 字节、包头长度及独立 body 对照均通过。单条响应为 52 字节、body 44，SHA256 `b67c020800b28039cd7ef19d31f7fa71a003e4f6ae16c6085b46ccf7944da8c2`。该过程确认 Domain 为 BE32(10) 加 `127.0.0.1` 与 NUL；IPv4 的 host 内存 `7f000001` 经当前 pack 得到 wire `0100007f`，只证明 DWORD 转换，尚未证明客户端如何解释该地址或是否优先用 Domain。

`adapters/cn_current/geo.py` 已提供显式版本化 PingList 与 ZoneList builder，只生成一条回环条目。PingList 为 52 字节；ZoneList 为 70 字节，唯一可变字段为本次受控监听端口（严格整数 1024–65535，拒绝 bool），其他值均为标注差异的研究参数。独立 DLL 尝试 10 已核对 Zone 请求及响应的完整 host、独立 body、头长度和再打包；端口 45678 的响应 SHA256 为 `520b80a6dcf77b4ab830dee4f7b11b5defdaa226c28735db3c7cbf591d0905d8`。Zone 请求解析按当前容量 192、Domain 长度最多 128 且包含结尾 NUL 严格检查，不固定为本次 40 字节。实机 18/19 另外证明了这两个特定响应被消费；不能泛化为所有端点、原版数值、远端房间服务或完整登录已通过。

## 未实现内容

- 当前特定单条 GEO 目录及 Zone 两连接认证路径已实测，真实 DEV 身份已到 A000，第 22 次固定 A001 研究回复已消费；完整玩家/资产通知、昵称/角色/大厅、重连和持续可玩会话仍待实现或运行验证。当前 HTTP JSON 不等于客户端原生帧。
- 游戏内邮箱登录、角色展示、地图加载、出生、真实战斗、装备效果、资产投影与可信结算未通过。真实邮件验收比较的是服务端读取视图；当前业务目录为空，不能据此证明游戏内装备效果一致。
- M5 生存全图、保卫目标/载具、围剿阶段目标/区域解锁与双端 T01–T12、双房隔离、连续十局等实机要求仍未验收。
- Win64 全零入口未恢复；当前仅推进有归档与固定指纹证据的 Win32 工作副本。完整系统快照/驱动隔离恢复尚未验收。
- 全项目中文注释整改不在本轮完成结论内；中文注释检查只覆盖各报告明确列出的本轮文件，新增工具需要随实际修改重新核对，不能沿用较早批次数量充当最终范围。

## 当前阻塞

| 阻塞 | 证据与影响 | 解除条件与下一步 |
|---|---|---|
| 原生资料/角色/大厅未完成 | GEO 单条目录已消费，22 的 A001 固定研究回复已触发 A204/FF05；尚未发送 A005 玩家资料与 A006 资产 | 核对当前 PlayerInfo/PropInfo 编码、合法资源映射与最低通知顺序，再接真实业务身份、昵称与角色；重连另行验收 |
| Win64 可执行内容缺失 | 原入口全零，现有归档未提供可恢复 Win64 入口；详见前批恢复报告 | 需要可核验对应资源；保留 Win32 路径，不将研究占位文件当作修复 |
| 原生玩法与双端验收依赖未闭合 | 当前样本的原生地图、实例网络、可信事件尚无完成证据 | 先完成单端实际登录/角色/大厅与地图运行，再按原计划执行双端验收 |

SMTP、邮箱验证码注册及密码恢复目前无已知阻塞。数据库 TLS、公网 HTTPS、容量和 Windows 系统级隔离恢复沿用此前未验证边界。

新增资源待核验项详见 [当前恢复样本配置报告](../../evidence/aliyun-native-current-config.json)：`TGame/CookedPC/Maps/UTFrontEnd_Objects.udk`、`UTFrontEnd_Light.udk`、`UTFrontEnd_PlayerPreview/UTFront_PlayerPreview.udk`、`UTFrontEnd_PlayerPreview/UTFrontEnd_PlayerCommon.udk` 不存在；另有 EnvyEntry 和区域 Startup 缺失路径。尚无实际加载失败证据，暂不将这些路径定义为已确定的大厅运行阻塞。

## 验证记录

| 验证项 | 环境及步骤 | 实际结果 | 未验证范围 |
|---|---|---|---|
| 邮箱验证业务第一组 | Python 3.12、真实 MySQL 8.0.30-txsql，`tools/run_tests.py --labels tests.test_email_verification.EmailVerification` 的当时 8 项 | 8 通过；并发消费/回滚、失败持久化、账号状态、旧会话撤销、HTTP 绕过拒绝等 | SMTP 使用替身，不代表真实邮件 |
| 原邮箱登录回归 | 同 MySQL，`--labels tests.test_email_login` | 13 通过，无失败或跳过 | 原生大厅未覆盖 |
| SMTP 与补充回归 | 两项 SMTP SimpleTestCase，加并发发码和布尔备份两个 MySQL 用例 | 4 通过；前述三组共 25 个不同测试，其中 23 个真实 MySQL 用例、2 个模拟 SMTP 用例 | 不把模拟 SMTP 算真实发送 |
| 邮箱专项回归（IPC v1 阶段） | MySQL 8.0.30-txsql；邮箱验证 14 项（含 2 项模拟 SMTP）+ 登录 13 项 + 内部别名 3 项 + 原生交接 1 项 | 31 项代码断言通过，无失败或跳过，318.562 秒；其中 29 项使用真实 MySQL | 原生 1 项的 v1 字段语义解释已更正，仅保留 getter 读取历史；v2 需单独回归 |
| 注册固定交错修复回归 | 同 MySQL；强制另一连接在注册幂等查询与验证码预检之间提交，另覆盖相关注册/别名边界 | 7/7 通过，82.485 秒；其中只有固定交错用例是上述 31 项之外新增 | 两组去重共 32 项后端/原生专项（30 项真实 MySQL + 2 项模拟 SMTP），不能相加称为 38 项 |
| 早期独立工具回归 | `tools/run_tests.py --tooling`，文件盘点、目录解析、工作副本、原生镜像与邮箱诊断 | 当时 23 项通过，无失败或跳过 | 保留历史；后续完整套件见下行记录 |
| 最新完整独立工具回归（截至第 23 次） | 当前 runner 包含基础/邮箱、守卫、捕获、transport、GEO、Zone 和离线探针边界 | 104/104 通过，0 失败、0 跳过；后续新增测试以同名报告刷新为准 | 不访问数据库或启动完整游戏；按测试 ID 去重，专项不再相加 |
| v2 邮箱工具回归 | 独立邮箱工具 3 项，包含 tag2 身份与服务器字段布局断言 | 3/3 通过，0.516 秒 | 与上述 23 项中的测试 ID 重复，不增加去重计数 |
| v2 MySQL/HTTP 到原生 DLL 桥接 | MySQL 8.0.30-txsql，两个独立账号分别测试默认与单回环记录 | 1/1 通过，19.484 秒；v2 正确字段分别读到 10001/10002，独立令牌/会话与保护/清理通过 | 与原生测试 ID 重复，不增加去重计数；未启动完整游戏，不是双端验收 |
| 配置恢复守卫故障回归 | Windows、本机锁定 Python；路径/摘要/计划/写入/恢复故障 | 14/14 通过，0 跳过，0.500 秒 | 不代表断电硬件持久性或并发编辑原子保证 |
| 早期 transport 边界专项 | framing 长度/分片/超时、认证字段、challenge 结构及回复校验 | 当时 8/8 通过，0.125 秒；已包含在最新完整套件中 | native_game_started=false、emails_sent=0；后续异步 SYNACK/换钥扩展以最新套件为准 |
| 前端 | `admin` 下 `npm run build`（包含类型检查）、`npm test -- --run` | 构建通过，业务测试 2 通过 | 原生游戏 UI 未覆盖 |
| SMTP 认证 | 实际阿里云既有地址，TLS 465 | TLSv1.3 认证通过；认证探针未发邮件 | SMTP 接受本身不等于收信 |
| 真实邮件注册与找回 | DEV 当前环境，用户指定收件邮箱，共三封授权内测试邮件；用户实际提供收件码 | 邮箱注册、密码恢复、旧 Web/游戏会话失效、同角色/game_id/资产视图通过 | 详见下述中断补测说明；没有第四封授权 |
| DEV 重启恢复 | 完成邮件验收后重启四个服务，再用新密码登录 | 同 game_id、同角色、verified 状态保持 | 原客户端崩溃恢复未覆盖 |
| 最新 DEV 发布核验 | release `20261015T061412-1cfd594907ca`；两环境前端、能力、匿名拒绝、四服务、MySQL 完整性/触发器、监听及原账号重登 | 所有核验通过；game_id/角色/verified/资产视图保持；追加邮件 0 封 | 不把配置标记替代真实收信，也不将服务端角色视图视为原生大厅 |
| 备份恢复 | 当前/PvE 的迁移前 `-v2` SQL，各在临时 MySQL 实际恢复 | 各 35 张业务表对账通过 | 不将旧失败 SQL 当有效备份 |
| 最终备份恢复 | 当前/PvE 的迁移后 SQL，各在临时 MySQL 实际恢复 | 各 36 张业务表对账通过，包含 email_challenges | 不授权覆盖验证之后的最新写入 |
| Win32 DLL 旧 v1 自测 | 空服务器记录及单个回环记录，实际固定指纹 TenProxy.dll | getter/保护检查与清理通过，但 LocalGameId 语义标签错误 | 仅保留布局读取证据，不作为正确原生身份映射 |
| Win32 DLL v2 默认自测 | 修正 tag2=game_id、tag1=研究 ServiceId，实际固定指纹 TenProxy.dll | getter、身份字段、令牌/会话、保护与清理检查通过 | game_process_started=false；ServiceId 研究值仍未获业务认可证据 |
| 完整原生启动 04/05 | 真实 DEV 邮箱身份，有界观察 180 秒 / 90 秒，仅研究工作副本 | 进程清理、配置恢复均通过，TGameBase.dll 已加载；无受控回环请求 | 角色、大厅均未通过；无原生游戏成功结论 |
| 原生停点 06/07 与图形对照 | 自有进程线程/代码采样和未修改文件静态分析；相同 x86、Session1、显卡/驱动的两种执行环境 | 定位 Pixel Shader 2.0 检查失败；非沙箱能力探针返回 Shader 3.0 | 不代表游戏已建立图形设备或进入大厅 |
| 完整原生启动 08 | 非沙箱环境 60 秒、真实 DEV 邮箱身份、受控工作副本 | 用户确认加载画面；越过图形错误，TenProxy/CrossProxy 已加载；清理/配置恢复通过 | 无受控回环请求、无大厅；Sleep 语义由第 10 次后续取证补充 |
| 完整原生启动 09 | 正常用户环境研究身份 180 秒；此时仅有独立 D3D9 对照，尚未集成前置门禁 | 游戏仍仅到加载画面 | 不当作门禁成功样本；无大厅或受控回环请求 |
| 图形门禁及完整原生启动 10 | 新前置检查在同一执行环境运行，观察 60 秒并定点取样 | graphics_preflight_verified=true；清理/配置恢复/采样线程恢复通过；时间控制代码确认 | 无受控回环请求或大厅；时间控制不证明持续渲染 |
| Geo/Zone 变体 11 | 只改两组 Host/Port 四行，90 秒研究观察 | 0 个受控回环请求，未改变认证/TDR 字段 | 不能据此认定配置已被运行时读取 |
| 守卫与 TCP 取样 12 | 三份临时配置、持久恢复计划、30 次 TCP 状态快照 | 配置恢复与 30 次取样通过，补充游戏消费代码证据 | 不证明所有原生流量仅发往回环 |
| IPC v2 首包 13 | 90 秒研究运行，本次受控回环监听 | 首次收到 60 字节，记录摘要；45 次 TCP 取样无错，清理/三配置恢复/线程恢复通过 | 本次未发送响应，认证/角色/大厅未通过 |
| challenge 初试 14 | AUTH 校验后发送 45 字节 SYN | AUTH 与运行字段对照通过，但旧验证器记录 TransportError | 缺少回包头/错误码，不能断言客户端没有回应；失败历史保留 |
| challenge 修复 15 | 60 秒研究身份，按当前异步封装验证 | AUTH 60 → SYN 45 → SYNACK 61，challenge_verified=true；清理/配置恢复通过 | 本次未换钥、未完成业务层 |
| 换钥与 GEO 首包 16 | 60 秒研究身份；通过 challenge 后发送 48 字节换钥帧 | 客户端以新密钥发出 44 字节帧，解出 9 字节 GEO PingList；清理/配置恢复/捕获线程停止通过 | 未发送 GEO 业务响应、无角色/大厅 |
| 真实邮箱身份原生首业务 17 | 60 秒，既有已验证 DEV 邮箱身份，0 封追加邮件 | AUTH 72 → SYN 45 → SYNACK 61 → 换钥 48 → 新密钥上行 44，解出同一 9 字节 GEO 请求；清理/配置恢复/捕获线程停止通过 | 首次自有会话路径通过；重连、完整登录及大厅未覆盖 |
| GEO Ping 18 | 52 字节当前 DLL golden 研究回复 | 客户端返回 40 字节 ZoneList 请求；清理/恢复通过 | 特定响应消费，不代表通用目录 |
| 单入口 Zone 19 | 70 字节唯一回环目录 | 第二条 TCP 认证/换钥通过；首个 Zone 应用 11 字节 0x1250 | 远端房间节点未部署 |
| 真实邮箱 Zone 登录请求 20/21 | 已验证 DEV 身份，0 封追加邮件 | 收到 26 字节 A000，清理/三配置恢复/捕获线程停止通过 | 未发 A001；后续等待超时，角色/大厅未通过 |
| 正常登录研究回复 22 | 当前 DLL 验证的 41 字节 A001，Result=0x8100 | 收到 A204 与 FF05；原生正常初始化分支已消费 | 尚无 A005/A006、角色/大厅；采集关闭导致的重连干扰另行复验 |
| 连接保持复验 23 | 真实 DEV 身份重复 GEO/Zone/A001 路径；八条采集结束后保持到清理 | 再次收到 A204/FF05，仅两条连接，无 22 的 IDENT 干扰；清理/恢复/线程停止通过 | 非重连验收，玩家资料/角色/大厅仍未通过 |
| 当前 TDR 静态字段 | 当前恢复 GEO/ZONE 文件，与归档清单及前后哈希核对 | 2,526 个名称/结构头交叉核对通过，导出 25 个选定结构与实际宏/selector | 不启动游戏、不读取凭据、不写客户端；不等于全部字段 wire 格式已验证 |
| 当前 DLL 离线序列化 08/09 | 独立有界 x86 Job；当前 DLL 与当前 TDR，未调用网络 Init | 实收请求精确往返；空/单条响应独立 body/full pack/unpack/repack、host 全字节、长度、哨兵及清理通过 | 01–07 为工具环境/ABI 失败历史；不证明游戏已消费响应 |
| GEO 请求及单条研究目录 | `tools/run_tests.py --tooling --pattern test_native_geo.py` | 17/17 通过，含独立 DLL Ping/Zone golden、计数/字符串/截断/端口边界 | 编码测试与实际 18/19 消费分别记录，不创建原生房间 |
| 最终静态与依赖检查 | Django check、makemigrations --check、compileall、pip check、git diff --check | 全部通过 | 不替代原生运行或全量历史回归 |
| 中文文档字符串检查 | Python AST 检查各报告明确列出的本轮文件 | GEO 两文件 32 个对象无缺失；扩展工具最新全批范围由父批次报告刷新 | 不声称全项目注释整改完成 |

后端证据：[业务 8 项](../../evidence/aliyun-email-mysql-tests.json)、[邮箱回归 13 项](../../evidence/aliyun-email-regression.json)、[SMTP/并发/备份 4 项](../../evidence/aliyun-email-transport-backup-tests.json)。首次两项测试夹具失败已保留在 `evidence/failures/aliyun-email-attempt-01.json/.txt`，修正夹具字段和环境后原场景通过；不隐藏初次失败，不将历史全量回归算作本轮重新执行。

邮箱批次专项证据：[31 项回归](../../evidence/aliyun-email-final-mysql-tests.json)、[旧 v1 两账号 getter 交接](../../evidence/aliyun-email-final-native-bridge.json)、[注册固定交错修复后 7 项](../../evidence/email-register-interleaving-fixed-mysql-tests.json)。[固定交错修复前失败](../../evidence/failures/email-register-interleaving-before-fix.json)已保留，修复后回归原场景。该阶段去重为 32 个测试 ID；其中原生 getter 的旧语义解释已更正，并由后续 v2 原场景回归补证，不覆盖旧报告。

本机新增证据：[v2 默认 DLL 自测](../../evidence/aliyun-native-v2-default-ipc.json)、[v2 工具 3 项](../../evidence/aliyun-native-v2-tooling-tests.json)、[v2 MySQL 桥接测试](../../evidence/aliyun-native-v2-mysql-tests.json)、[v2 双账号桥接明细](../../evidence/aliyun-native-v2-mysql-bridge.json)、[配置守卫 14 项](../../evidence/native-config-guard-fault-tests.json)、[Geo/Zone 配置四字段往返](../../evidence/aliyun-native-network-config-roundtrip.json)、[早期 transport 边界 8 项](../../evidence/aliyun-native-transport-boundary-tests.json)、[异步 SYNACK 修复](../../evidence/aliyun-native-transport-synack-v2-tests.json)、[换钥/业务帧边界](../../evidence/aliyun-native-keychange-application-final-tests.json)、[当前 GEO/ZONE 元数据](../../evidence/aliyun-current-geo-zone-metadata.json)。v2 桥接与 32 项中的原生测试 ID 重复；其余工具专项已经整合进最新完整工具套件，不与完整套件相加计数。

独立工具最新证据：[完整工具测试](../../evidence/aliyun-email-and-native-final-tooling-tests.json)；[早期 23 项](../../evidence/aliyun-email-tooling-tests.json)保留为历史。邮件批次收尾时 Django check、makemigrations --check、compileall、pip check、git diff --check 均通过。[邮件批次中文文档字符串检查](../../evidence/aliyun-email-chinese-comments.json)仅证明其中列出的文件范围，[GEO 当前两文件中文注释检查](../../evidence/aliyun-native-geo-directory-comments.json)单列本轮两个文件；后续 Windows 工具最终范围另行刷新，不扩展为全项目。

GEO 响应新增证据：[当前 DLL 离线验证](../../evidence/aliyun-native-tdr-offline.json)、[早期固定 Ping 回归 8 项](../../evidence/aliyun-native-geo-response-builder-tests.json)、[当前 Ping/Zone 回归 17 项](../../evidence/aliyun-native-geo-zone-response-tests.json)、[当前 Zone 登录静态合同](2026-10-15-native-zone-login-contract.md)。这些测试已进入后续完整 runner；独立专项与完整报告按测试 ID 去重，不能简单相加。

真实环境证据：[SMTP 认证](../../evidence/aliyun-smtp-auth.json)、[发布](../../evidence/aliyun-email-dev-deploy.json)、[邮件闭环](../../evidence/aliyun-email-live.json)、[重启](../../evidence/aliyun-email-restart.json)、[当前库备份](../../evidence/aliyun-before-current-v2-backup.json)、[PvE 库备份](../../evidence/aliyun-before-pve-v2-backup.json)。原生独立自测：[默认布局](../../evidence/aliyun-native-default-ipc.json)、[回环服务器布局](../../evidence/aliyun-native-server-ipc.json)。

迁移后 36 表备份证据：[最终当前库备份](../../evidence/aliyun-before-current-final-backup.json)、[最终 PvE 库备份](../../evidence/aliyun-before-pve-final-backup.json)。两者均为实际恢复对账，不只验证 SQL 文件生成。

最新发布证据：[release 061412](../../evidence/aliyun-email-final-dev-deploy.json)、[最终实际核验](../../evidence/aliyun-email-final-dev-verification.json)。核验工具首次使用了错误能力字段名 `email_verification`，把已正确返回的 `email_ownership_verification=CONFIGURED` 派生为 false；保留[字段名修正记录](../../evidence/failures/aliyun-final-verification-field-name.json)，修正工具后重新核验通过，服务端没有因此重设邮件配置或追加邮件。

原生停点证据：[当前构建静态及自有进程代码分析](../../evidence/aliyun-native-loader-static.json)、[受限环境 D3D9](../../evidence/aliyun-native-d3d-caps-sandbox.json)、[非沙箱 D3D9](../../evidence/aliyun-native-d3d-caps-unsandboxed.json)、[早期 Sleep 调用点分析](../../evidence/aliyun-native-loop-static.json)、[第 10 次帧时间控制确认](../../evidence/aliyun-native-frame-pacing-static.json)、[getter 形状合同](../../evidence/aliyun-native-getlogininfo-contract.json)、[游戏身份与端点消费语义](../../evidence/aliyun-native-login-consumption-static.json)。旧 getter 形状报告不足以证明业务值，后续消费证据已经更正 v1 语义；原生登录仍未通过。

[公开启动汇总](../../evidence/aliyun-native-launch-summary.json)已覆盖第 04–23 次共二十次运行，按白名单保存结果、四个原件关键文件基线哈希及三份工作副本配置恢复检查；本批未重新哈希整个原件目录。`transport_handshake_verified=true` 的范围固定为 `INITIAL_OWNED_SESSION_AUTH_CHALLENGE_KEYCHANGE_APPLICATION_ONLY`；`research_zone_login_response_consumed=true` 仅说明固定研究回复触发初始化；`reconnect_verified`、`native_launch_ready`、`role_verified`、`lobby_verified` 仍为 false。原生所有流量均被限制为回环尚未证明；早期 TCP 状态有外部 80 端口连接，未捕获或归因其负载。私人原始证据继续保留，公开材料不含身份凭据。

### 真实邮件补测的中断记录

第 1 封完成注册。第 2 封密码重置已提交，但在验收脚本最后读取完成前过早重启 DEV，导致读取中断，脚本也未及时保存新生成的测试密码。这是验收脚本检查点与操作顺序问题，不能将该次最后读取当作通过。

已修正为外部变更前保存待用密码和快照、服务成功后立即保存状态。为补测，只对本工具创建的测试账号使用既有 Owner 正常 MFA 和带审计的 `user.reset` 准备已知临时密码，随后第 3 封仍走真实邮件码自助恢复，完整验证后才重启服务。公开报告保留 `recovery_preparation_admin_reset=true`；它不是用户自助恢复的替代路径。三封授权已经用完，后续自动测试不得继续真实发信。

## 接手与回退

- 本机先设置私有数据库及邮件环境，再向前应用 migration 0006。DEV 可运行 `python -m tools.deploy_dev --source ../DEV环境服务器.txt --env-file runtime/user-mysql.env --mail-env-file runtime/aliyun-mail.env`，详细发布/回退步骤见 [DEV 手册](../DEV_DEPLOYMENT.md)。不得把任何秘密直接追加到命令行。
- 运行中的最新数据库优先保留；异常时先停写、取得并验证新的备份，核对旧 release 对当前迁移兼容性后再切换应用版本。不能用本批迁移前 SQL 覆盖新注册、改密、撤销或账本写入。旧 release 和受保护旧邮件配置保留供检查，不自动退回旧安全状态。
- SMTP 配置回退只恢复明确核对过的专用配置文件并重启服务；若密码已由云侧另行轮换，恢复本地旧文件不能恢复云密码。公开材料不记录地址凭据、用户邮箱、验证码或测试密码。
- 原生探针一次只运行一份，临时 XML/可选网络配置通过守卫记录并逐项确认恢复摘要，所有本次 Job 子进程需确认结束。中断恢复使用相同工作副本和输出目录加载既有计划，遇外部编辑保留并人工核对，不强制覆盖；详见[配置守卫交接](2026-10-15-native-config-guard.md)。不改 `../nz`，不执行未核验的原 TCLS 启动器。
- 下一步按原生实际停点继续处理，只有原客户端的认证、角色和大厅获得请求响应及画面证据后才能推进地图/战斗验收。自动化、单端、双端结果继续分别记录。

上游通知顺序及当前结构差异另见[对照交接](2026-10-15-upstream-zone-notifications.md)：当前 PlayerInfo 在 Experience 后已有新增字段，不能复制 PH 的短结构；其 FF05 后 A005/A006 的顺序只是研究参考，最低必需通知集仍未证。
