# 新会话接续：独立邮箱、原生大厅、单区服与 M0–M6

> 最新续建（北京时间2026-10-16）：优先商城/仓库，其后猎场/塔防；新增史诗/自选/精良碎片和宝物工坊。七钱包已补满，75实见NZ/GP/MP均2147483647。轻量换装由约6秒降至幂等1.24–1.39秒，76实机角色切换1.65–1.83秒。78背包槽12漏接导致断线，现已修复并发布`20261015T170739-9f43165cad44`；180工具/11 MySQL回归通过。新增根目录“启动在线测试.cmd”，新手动会话已进入仓库并由用户测试，进程保留到退出，无自动计时。76/77/78旧会话已清理；77更新检查卡住根因仍未明。商城实际交易、容量提示、四类抽奖、M5/M6与在线可玩交付尚未完成。先读[最新商城、角色、背包和启动交接](2026-10-16-inventory-shop-role-wallet.md)，再读[M0–M6台账末尾](2026-10-15-m0-m6.md)。


## 2026-10-15 续建范围更新：M0–M6

用户已要求实施 M0–M6 续建计划，并允许明确标注差异的可玩兼容版。最新要求是**暂缓离线版本，本次必须交付在线可玩版本**；此处未宣称在线交付已经完成。
最新实际进展、当前昵称转换停点、验证与未完成清单见 [M0–M6 本批交接](2026-10-15-m0-m6.md)，
猎场逐图候选与 M6-T01–T12 见 [M6 台账](M6.md)。下文保留上一会话交接原文；其中 boot25、下一轮26、113项测试是上一会话的历史状态，后续接续以本批交接为准。

## 交接时的明确结论

日期：2026-10-15，北京时间。用户要求暂停新增实现、整理交接，并在新会话继续原任务。

**真实邮件注册/找回密码和 DEV 控制面已验证；当前原客户端已消费单区服目录及 A001 登录研究响应，并进入后续资料请求。角色、大厅、原生房间与完整 M5 尚未完成。**

本文件是新会话的第一入口。其他交付文档包含不同阶段的历史记录；旧的“尚无原生请求”“未发送 GEO/A001”“最新 72/97/104 项”等不能覆盖此处最新状态。原有失败证据均保留，不因后续修复而删除。

## 新会话先做什么

1. 阅读本文件、仓库 `AGENTS.md` 和 `.agents/skills/nz-development/SKILL.md`，再读下面的未完成清单。新增代码保持详细中文说明，每批更新 `docs/delivery/`。
2. 在 `C:\Users\ZJH\NZ\2020逆战\repository` 先检查 Git 与现存进程，不重启或覆盖已运行的 DEV，不删除运行目录。
3. 优先解决当前 A005 玩家资料的中文昵称消费编码：下一最小自有进程代码采样是 **当前 TGame RVA `0x91F0`，长度 `0x200`**。该目标已由实际 A005 消费者调用定位；不能猜 UTF-8、ACP 或 GBK，也不能改写现有中文昵称来回避。
4. 接着验证当前 A005/A006、角色与大厅初始化顺序及最小有效资料，逐步接入真实账号、角色和资产。已存在的空资产资料编码器只是离线夹具，尚未接入原生发送分支。
5. 房间服务器 SSH 已可用，继续核实当前构建的原生服务端入口、最小依赖与单实例运行条件，再部署真正房间节点。

可直接复制到新会话：

```text
继续 NZ 原任务。请先阅读 C:\Users\ZJH\NZ\2020逆战\repository\docs\delivery\2026-10-15-session-handoff.md、AGENTS.md 和 nz-development 技能，核对未完成清单后继续实现。保留当前提交及未提交改动、MySQL 数据、原客户端和私人运行材料。当前停点是 boot25 已实时核验邮箱身份并通过 GEO→Zone→A001→A205；A005 尚未发送给游戏，中文昵称转换 RVA 0x91F0/0x200 待验证。先打通角色和大厅，再推进原生房间、M0–M4 实机关卡及完整 M5。只提供一个可选区服和一个房间节点，仍保留同节点双房隔离和双端验收。不要追加真实测试邮件，不要把离线夹具或合成测试当作实机完成。
```

## 工作区、Git 与保留要求

| 项目 | 当前实际状态 |
|---|---|
| 根目录 | `C:\Users\ZJH\NZ\2020逆战` |
| 代码仓库 | `C:\Users\ZJH\NZ\2020逆战\repository` |
| Git 分支/HEAD | `main`，`e42705a44a2e7b0d8b2511172747fcd92d652521`；标题 `feat: add email authentication, operations and native client research` |
| 本地远端跟踪引用 | `origin/main` 与 HEAD 相同；本次交接未重新 fetch，因此不声称实时远端状态已检查 |
| 远端 | `https://github.com/016-ops/NZ.git` |
| 当前未提交改动 | 后续 Zone 资料编码器、fresh 邮箱校验、原生/TDR 工具及测试、证据与文档；本次交接没有提交、推送、清理或重置 |
| 锁定技术栈 | Django 5.2、MySQL、React、TypeScript、shadcn/ui；Python 使用仓库 `.venv/Scripts/python.exe` |
| 原始客户端 | 上级 `nz/`，保留不写。不能运行签名异常的原 `TCLS/Client.exe` 或把其他区域补丁套进当前样本 |
| 实际研究工作副本 | `runtime/native-recovery/coherent-2020/nz`；不要切回旧的混合/损坏副本 |

原始更新过程从 `c3bf828` 快进到 `2e61ac1`，现已存在后续 `e42705a`。旧文档中的“本轮尚未提交”属于当时记录，不能据此撤销现有提交。新会话如需同步远端，先保护当前工作区；禁止 reset/clean/强制覆盖或回退数据库。

## 已授权范围与私人材料

- 用户已授权使用根目录提供的 MySQL、DEV 服务器及测试房间服务器；客户端派生路径/哈希/已解码配置可导入独立 catalog 库，不能混入玩家业务资产目录。
- DEV 通过 SSH 隧道访问，服务监听回环地址。房间服务器已开 SSH；无需重复询问管理方式、索取密码或要求用户再次开通端口。
- 用户要求只有一个可选区服入口、一个房间节点；同节点可有多个互相隔离房间，双端与双房隔离仍需验收，见 ADR-012。
- 邮件采用阿里云 SMTP 专用配置；**真实验收已使用完获准的 3 封邮件**。继续登录既有测试账号不会发邮件；不得自动补发第 4 封。
- 私人连接文件只在本机读取：上级 `mysql配置.txt`、`DEV环境服务器.txt`、`房间服务器配置.txt`；仓库 `.env.local`、`runtime/user-mysql.env`、`runtime/aliyun-mail.env` 等。不要打印文件正文、把秘密放进命令行/文档或上传到 Git。
- `runtime/mail-acceptance.json` 保存既有验证账号、重置后的密码及验收检查点。保留该文件；不覆盖、不输出。不要用另建共享固定账号替代独立身份。
- `runtime/`、`evidence/private/`、`upstream/`、`.venv/`、`admin/dist/` 均在忽略范围。新会话仍需本机目录；公共 Git 文档不会携带客户端、私钥、邮箱凭据或原始协议包。
- 当前原生窗口没有可用自动 UI 控制接口。代码/协议/日志可继续分析；窗口、角色显示和大厅画面需真实可用 UI 工具或用户观察，不得通过进程内存读取窗口文本绕过工具限制。

## 最新验证状态

| 层次 | 已验证 | 尚未证明 |
|---|---|---|
| 真实邮件与业务账号 | 注册验证码、邮箱所有权、邀请/批准、独立账号/角色；改密、旧会话撤销、重登及服务重启后的身份/资产一致性 | 原客户端首登昵称与可加载角色闭环 |
| DEV 控制面 | 最近已发布记录 `20261015T061412-1cfd594907ca`，69 文件；0006 迁移；阿里云已配置；保留备份与恢复对账 | 本机最新 Windows 研究工具未重新部署成常驻游戏服务 |
| 交接只读健康 | `127.0.0.1:18765`=`cn-current-unverified`、`:18767`=`test-pve-v1` 均 health=ok、邮件 CONFIGURED、native_launch_ready=false | 健康接口不代表原生房间就绪 |
| 最新独立工具 | **113/113，0 跳过、0 失败**，`aliyun-email-and-native-final-tooling-tests.json/.txt` | 不代替 MySQL、单端或双端 |
| 已有 MySQL 证据 | MySQL `8.0.30-txsql`；邮件批次 31 项及固定交错补测去重共 32 个测试 ID（30 个实际 MySQL、2 个 SMTP mock），v2 桥接重测与原 ID 重叠 | 文档交接收尾没有重新执行所有历史业务/MySQL测试；报告不能相加冒充新覆盖 |
| 中文说明 | Python AST 检查列出的 **37 文件 / 450 个模块、类、函数说明，无缺失**；compileall、diff check 通过 | 不是全仓注释整改声明；PS/C# 说明按改动人工核对 |
| 原件与配置 | 公开汇总覆盖 boot04–25，共 22 次；原件四个关键文件匹配基线、最新三份配置恢复、自有 Job/捕获线程结束均通过 | 本批没有重新哈希整棵原客户端目录 |
| 单端原生 | 已验证独立邮箱身份到当前 transport、GEO、Zone 登录成功初始化分支及后续资料请求 | 玩家资料显示、角色、稳定大厅、原生地图、战斗、结算、下一局 |
| 双端/多人 | 没有通过本批声明完成 | 共同敌人、共同阶段、断线资格、双房隔离、连续10局、人数上限等全部仍需实机 |

交接收尾未发现 TGame/GameLoader 遗留进程。现有 Python 服务/隧道应保留，不能按进程名批量结束。

### 原生探针递进与精确停点

私人目录为 `evidence/private/email-native-boot-NN/`，每轮新目录，不覆盖既有证据。当前最后一轮为 **25**，下一轮应使用新的 `26` 目录。

| 轮次 | 实际结果 |
|---|---|
| 13–15 | 修正 v2 IPC；当前 AUTH→SYN→61B 异步 SYNACK 验证通过。14 的狭窄读取失败不应被解释为客户端拒绝 |
| 16/17 | 换钥后实收9B当前 GEO PingList；17使用真实已验证 DEV 邮箱身份 |
| 18 | 消费当前 DLL 验证的52B PingList响应，回40B ZoneList请求 |
| 19 | 消费仅含一个回环入口的70B ZoneList响应；第二连接独立认证、挑战、换钥，首包11B `1250` 是 NetBarKey 通知 |
| 20/21 | 真实 DEV 邮箱身份在该通知后发送26B `A000`，不是带邮箱密码的登录包；身份来自已认证连接 |
| 22/23 | 发送41B `A001` / `Result=0x8100`，实收 `A204` 和 `FF05`，证明当前成功初始化分支执行。22在8帧上限主动关闭诱发未实现IDENT重连；23保持连接到清理后不再出现该干扰 |
| 24 | 发送14B显式空自定义资料 `A205` 后，实收116B `A202` 写回请求；内容仍不透明，未持久化或回复 `A203` |
| 25 | 立即读取 `/api/views/me` 核对当前email_verified及本机验收账号/游戏ID与redeem身份三方一致，再重复上述流程。两条连接，无协议错误，配置恢复/线程/进程清理通过；0封追加邮件 |

`--serve-zone-login` 只在 `--dev-email-state`、完整当前采集前置条件满足时允许；fresh 检查失败也会注销来源会话。失败前的纯mock复现与修复后11项专项保留在 `evidence/failures/native-dev-fresh-identity-before-fix.*` 和 `aliyun-native-dev-fresh-identity-tests.*`。

当前换钥/下行路径基于本样本代码及实际消费，不套用 PH。已知独立 profile：

- IPC：`nz-email-ipc-research-v2` / `nz-email-ipc-loopback-research-v2`；PID映射容量必须 `0x80000`。
- Transport：`nz-current-auth-challenge-research-v2`；换钥与应用下行分别版本化。
- GEO：`nz-current-geo-ping-loopback-research-v1`、`nz-current-geo-zone-loopback-research-v1`。
- A001：`cn2020-zone-login-success-research-v1`；8100正常分支已由当前代码及实收FF05双证。主/子频道1、时间0、币值/赠品/新手/区域0均为披露的研究值，没有真实资产投影。
- A205：`cn2020-player-define-empty-research-v1`；8100/长度0在当前消费代码跳过版本内容并回调。未清空真实玩家设置，未构造非空格式。
- A005：`cn2020-player-info-empty-assets-research-v1` **仅离线编码器**。两组固定测试ID/ASCII昵称的完整host、正文、全包往返与Python字节编码一致；**没有发送到游戏、没有 CLI `--serve-player-info`，没有角色/钱包/库存投影**。

### 当前 A005 / 设置 / 角色研究结论

1. A005 为 `ZN2C_NtfPlayerInfo`：当前正文host1412B、PlayerInfo1410B/78字段；包含 PH 布局没有的字段与1024B天赋结构。不能复制上游短包或把邻近PingList的 `8B00` 当作A005结果语义。
2. 当前A005真入口 `14F4BA0`，先要求本地玩家对象 `this+280` 非空；已采入口未看到外层Result比较。昵称 `正文+0xE` → `call 0x91F0`。当前真实验收昵称非ASCII、UTF-8长度18B；编码未证，禁止静默截断、替换或改名。
3. 尾部 `14E74F0` 开始跳受保护控制流，不能用线性反汇编声称已找到大厅完成门禁；旧候选 `14F42C0` 已否定为 DevLogin，并保留纠错历史。
4. A204空请求无正文联合体分支；A205数据上限3200B。A202保存请求为 `DataLen+Data`；A203只有Result，完整原生消费者原值转交委托，没有自身成功值比较。持久化与完成规则未实现。
5. `100014/100016` 角色候选配置与 Role1/Role2、Family路径已核对；7份配置和12个相关包（16,788,413B）摘要一致。**完整可加载starter仍为0**：16位Regular/RebelRoleIndex不能直接写六位物品ID；GID/RoleType关系、阵营对应及UPK内部对象/依赖仍待验证。不能把 `bDefaultItem=true` 直接解释成首登赠送。

## M0–M5 未完成与解除清单

| 阶段 | 当前已实现的范围 | 下一解除条件 |
|---|---|---|
| M0 | 独立副本、恢复守卫、归档恢复、目录提取及独立catalog导入，Win32启动损坏已修复 | 当前资源依赖闭包、缺失/全零路径逐项处置；Windows隔离/系统恢复证据；Win64入口仍全零且归档无可恢复版本 |
| M1 | 当前认证/换钥/测速/唯一服务器目录与部分Zone请求响应 | 玩家资料/首登/角色/大厅完整回放与实机证据；原生地图加载、出生、网络运行 |
| M2 | 真邮件注册/找回、账号/业务角色持久化、管理生命周期、Owner/MFA保护、独立票据及fresh邮箱验证 | 原客户端昵称/角色、重登一致；真实登录/游戏动作中的撤销/封禁传播与节点确认 |
| M3 | 控制面频道/房间/过滤/密码/邀请/难度，合成节点票据/租约/就绪/清理 | 当前原生服务端入口、实例分配与握手、单端实际战斗，随后双端共同对象及同节点双房隔离 |
| M4 | 独立提取目录、事务账本、预览确认、持久批次/失败重试、人工复核 | 已核验资源定义提升业务目录；游戏中显示/装备效果、可信原生结算、重登和重启一致性；当前币种映射仍空 |
| M5 | `test-pve-v1` 的生存/保卫/围剿合成规则、事件去重与恢复 | 冻结实际地图难度矩阵；首选祭坛MapId47 / SV-Maya_3_Main依赖及原生加载；真实敌人/导航/伤害/波次/Boss/胜败/复活/退出/崩溃/奖励/下一局；逐图、生存扩展、保卫设施/载具、围剿阶段/续局；M5-T01–T12双端与10局 |

合成测试中的三图、简化导航、奖励和人数不能当作当前游戏原规则。248张逻辑图中缺失/未知条目继续留在分母。多人阶段在必要资源、隔离恢复和双端关卡通过前不能宣布完成。

## 单房间服务器与 DEV

房间服务器已通过SSH认证和hostkey固定：Windows Server 2025 Datacenter build26100/x64，4核/4线程，约4GiB内存（盘点时空闲约1.57GiB），C盘约39.9GiB（准备后空闲约23.76GiB）。`python/python3` 是 WindowsApps alias，不算已安装Python。

已创建 `C:\NZResearch`、4个无凭据文件及5个空目录，远端SHA读回/只读预检通过；没有安装运行时、上传客户端、创建/启动游戏服务、修改防火墙或登记原生ready。当前完整恢复归档28.22GiB比空闲空间大，但完整客户端也不是已证的最小房间包；必须先核实当前服务端入口和闭包，不能随意复制EXE就宣告部署完成。

现有 `revival/nodes.py` 在非test_profile仍 `COMPATIBILITY_BLOCKED`。`deploy_dev.py/dev_install.sh` 是Linux Django控制面部署，`native_email_boot.py` 是本机限时客户端研究，均不是当前Windows原生房间守护进程。

继续保留当前 MySQL 用户、审计、制裁与账本；新结构用Django migrations。已发布到0006，并有36表备份/恢复对账。恢复必须保留新写入，不能用旧数据库覆盖。DEV此批Windows研究更新尚未重新发布；只有实际必要的应用发布才操作既有服务。

## 关键文件与验证命令

优先阅读：

- [原生启动公开汇总](../../evidence/aliyun-native-launch-summary.json)、[本次只读健康](../../evidence/2026-10-15-session-handoff-health.json)。
- [邮件与启动主交付](2026-10-15-aliyun-email-and-native-launch.md)、[当前原生线协议接续](2026-10-15-native-zone-login-wire.md)、[首批Zone包](2026-10-15-native-zone-first-packets.md)。
- [A005消费代码](../../evidence/aliyun-native-playerinfo-consumer-static.json)、[A005离线夹具](../../evidence/aliyun-native-player-info-serialization.json)、[A202/A203合同](../../evidence/aliyun-current-player-define-write-contract.json)。
- [角色资源](2026-10-15-native-initial-role-resources.md)、[单房间服务器](2026-10-15-single-room-server.md)、[上游顺序及差异](2026-10-15-upstream-zone-notifications.md)。
- [原M0–M5交付](2026-10-15-m0-m5.md)、[M5清单](M5.md)、[DEV部署](../DEV_DEPLOYMENT.md)、[恢复守卫](2026-10-15-native-config-guard.md)。

代码入口：`tools/native_email_boot.py`（fresh身份与有界流程）、`adapters/cn_current/{email_ipc,transport,geo,zone}.py`、`tools/native_tdr_probe.py/.ps1`（固定DLL/元数据的隔离x86离线验证）、`tools/native_login_probe.py` / `native_thread_probe.py`（仅自有已核验进程固定代码采样）。正式业务入口仍为Django模块，不能把研究脚本当作已完成产品启动器。

以下为安全的只读/独立工具检查；本次已执行通过，无需在没有改动时反复重跑：

```powershell
Set-Location -LiteralPath 'C:\Users\ZJH\NZ\2020逆战\repository'
git status --short --branch
git log -1 --oneline
.venv/Scripts/python.exe tools/run_tests.py --tooling --output evidence/aliyun-email-and-native-final-tooling-tests.json
git diff --check
```

真实探针必须在普通用户图形环境运行；受限环境此前D3D9失败，不能误归为代码失败。恢复副本路径必须固定，输出必须是新的 `evidence/private/` 子目录，最长180秒，一次一份自有Job。当前已验证25的选项是 `--sample-threads --discover-login-references --redirect-game-network --verify-native-challenge --capture-application --serve-geo-ping --serve-geo-zone --capture-zone-sequence --serve-zone-login --serve-player-define --dev-email-state`；**新会话先补入91F0固定代码采样，再开始下一轮，不需原样重复25。**

离线A005夹具选项是 `--verify-player-info-research-response`，已执行于私人目录 `native-tdr-player-info-01`；只使用固定合成身份与ASCII昵称，不读取真实账号。此模式没有动态角色/资产输入或网络发送能力。

## 收尾、风险与恢复

本次交接保留全部未提交改动，没有发邮件、发布新release、迁移数据库、重启远端服务或新开实机探针。已完成现有代码113项独立回归、中文说明检查、语法检查、当前健康读取和最新公开汇总。已知秘密扫描的实际结果另见 `evidence/aliyun-email-public-secret-scan.json`，只代表其列出的已知来源/可交付文件，不声称未知秘密绝对不存在。

后续如中断，先核对对应私人 `report.json` 与 `native-config-guard/plan.json`；只有变体摘要仍匹配时才恢复，遇外部编辑保留现场。禁止按名称结束所有Python、删除整个runtime或恢复旧MySQL快照。资料夹具和当前资源候选不得自动提升为生产角色或游戏资产规则。
