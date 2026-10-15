# 本地启动与管理手册

当前架构为 React + TypeScript + shadcn/ui、Django 5.2、MySQL。真实游戏客户端接入仍未完成；本地业务验证不能代替原客户端兼容验收。

玩家入口采用邮箱和独立密码，新用户先取得邮件验证码，使用邀请注册并等待批准；管理员切到“管理员”页签，继续使用账号、密码与 MFA。旧玩家由用户管理员/Owner 在“设置玩家邮箱”中迁移，保留原 game_id、角色与资产，撤销旧会话和旧用户名登录。页面提供“找回密码”和“验证已有邮箱”；所有权校验与账号审批是独立步骤。

独立交接诊断（可使用 DEV SSH 隧道，邮箱/密码只在终端交互输入）：

```powershell
.venv/Scripts/python.exe -m tools.email_launcher --server http://127.0.0.1:18765 --workspace runtime/native-recovery/coherent-2020/nz --handshake-only
```

Windows 可追加 `--proxy-self-test`，用系统自带 32 位 Windows PowerShell 调用当前已核验 TenProxy，在独立进程读取本地 ID、短期令牌和会话标识。它不运行 TGame；诊断结束注销玩家会话。`--handshake-only` 不可省略，当前仅有受控目录及 Zone 请求研究，完整登录/大厅仍未实现。详细边界见 [邮箱交接记录](delivery/2026-10-15-independent-email-login.md)。

当前本机工具使用 IPC v2：本地 game_id 写入游戏实际使用的 tag2；旧 v1 自测的 LocalGameId 标签指向 tag1，现已证实那是 ServiceId。旧报告保留为布局读取历史，勿据此判断真实账号已正确进入游戏。v2 默认 DLL 自测、相关工具回归及两个独立 MySQL 账号到 DLL 的桥接回归已通过；第 13 次捕获 60 字节 AUTH，第 15 次通过 challenge，第 16 次换钥后收到首个 9 字节 GEO PingList，第 17 次以真实 DEV 邮箱身份复现。随后 18/19 已消费特定单条 GEO 目录，20/21 真实邮箱身份已到 Zone A000；第 22/23 次 A001 研究响应已消费并收到 A204/FF05，角色与大厅仍未完成，诊断工具不因此开放面向玩家的完整启动入口。

## 邮件服务与邮箱入口

本机读取独立私有文件 `runtime/aliyun-mail.env`，可用 `NZ_MAIL_ENV_FILE` 指定其他位置；不应把 SMTP 凭据写入数据库配置或源码。文件包含以下变量，具体秘密通过受保护的本地编辑方式填写：

| 变量 | 用途 |
|---|---|
| `NZ_MAIL_PROVIDER` | `aliyun-smtp` |
| `NZ_MAIL_HOST` / `NZ_MAIL_PORT` | 与阿里云区域匹配的允许端点；当前为 `smtpdm.aliyun.com` / `465` |
| `NZ_MAIL_FROM` / `NZ_MAIL_USERNAME` | 已核验的阿里云发信地址 / SMTP 用户名 |
| `NZ_MAIL_PASSWORD` | 该发信地址的 SMTP 专用密码，不使用主账号密码或 AccessKey |
| `NZ_MAIL_SENDER_NAME` | 发件显示名称，默认 NZ Revival |
| `NZ_EMAIL_VERIFICATION_REQUIRED` | 正常运行保持 `1`，也是缺省值；旧自动化夹具的关闭设置不得用于真实注册环境 |

服务使用 SMTP_SSL，校验证书及主机名，只允许 TLS 465，不自动重试投递。阿里云端点依据[官方文档](https://help.aliyun.com/zh/direct-mail/smtp-endpoints)。修改配置后重启业务进程；未经 SMTP 实际认证/收信验收的配置不能仅凭 `/api/launcher/status` 中 configured 标志判断可用。

应用 migration `0006_email_verification` 后启动服务。玩家填写邮箱及有效邀请，申请六位码，在十分钟内完成注册；申请码不消耗邀请，成功注册才消耗一次。找回密码需新的 reset 用途码，密码更新后旧 Web 和游戏会话失效，仍需满足原审核及制裁条件。每邮箱发码冷却 60 秒、每小时最多五次；失败投递也计额度。完整合同见 [API](API.md#邮件验证码合同)。

页面请求期间会锁定收件字段和入口切换；网络响应丢失后，保留相同表单内容重试可以读回已提交的注册、改密或补验结果，不再次消耗验证码。恢复/补验的外部调用方需发送完整幂等元数据才能获得此行为。旧账号改绑释放的邮箱可注册新的独立账号，原账号 ID、game_id、角色和资产继续归原账号。

上述邮箱修复已包含在 DEV `20261015T061412-1cfd594907ca`；最终部署核验保留了原测试账号角色与资产，未追加发信。实际版本与恢复入口见 [DEV 手册](DEV_DEPLOYMENT.md)。

原生启动的最新图形诊断发现：同一 x86 进程架构、Session 1、RTX 4070 SUPER 和驱动下，受限执行环境的 Direct3D9 能力读取失败，非沙箱环境可返回 Shader 3.0；第 08 次非沙箱研究启动已出现用户确认的加载画面，但未进入大厅。因此复现这一路径应使用已核验工作副本和既有有界工具，在具备正常图形访问的执行环境观察；不凭错误提示修改显卡驱动或客户端文件。详情和证据见[本批原生研究](delivery/2026-10-15-aliyun-email-and-native-launch.md#原生启动研究)。

第 09 次正常用户环境的 180 秒研究身份启动发生在图形门禁实现前，当时另行执行了只读 D3D9 对照，游戏仍只到加载画面。`tools/native_email_boot.py` 现已集成同一执行环境的只读 x86 D3D9 前置检查；不满足条件时返回 `D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT`，不创建游戏子进程。第 10 次 60 秒运行实际通过新门禁，进程清理、配置恢复和采样线程恢复均通过，但仍无大厅或受控回环请求。

第 10 次定点代码采样已确认先前的 Sleep 属于帧时间/最大帧率控制，其等待条件由经过时间和帧间隔计算。此结果未证明持续渲染或成功大厅，后续应核对实际代理身份读取和选中端点，不通过改动帧等待逻辑绕过停点。

原生研究可选 Geo/Zone 变体仅改 `DefaultTGNet.ini` 中两个 Host 与两个 Port，保留认证/TDR 等其他字段。第 11 次单独改变这四行仍未收到请求；第 12 次已验证三份临时配置的持久恢复计划及 30 次 TCP 状态采样；随后修正 IPC v2 才在第 13 次取得首包。配置守卫的中断恢复、外部修改保留和故障处理见[独立交接](delivery/2026-10-15-native-config-guard.md)。一次只运行一份探针，不能强制覆盖摘要已变化的配置。

第 14 次 challenge 失败报告只保存了 TransportError，不能推断客户端没有回包；修正异步 SYNACK 封装后，第 15 次实际往返为 AUTH 60 → SYN 45 → SYNACK 61 字节。第 16/17 次进一步换钥并解出 GEO 首包，当时未回复；后续 18/19 的特定 Ping/Zone 回复已被消费。协议研究以[当前恢复样本 TDR 字段](../evidence/aliyun-current-geo-zone-metadata.json)与当前 DLL/实包对照：当前 ServerArray 上限为 192，ResPingList 带额外 RankInfo，不能套用 PH 的旧结构；当前 DLL 已独立确认本次 Ping/Zone 字符串及数组 golden；通用输入、其他业务结构仍需分别核验。

真实邮件验收记录见[本批交接](delivery/2026-10-15-aliyun-email-and-native-launch.md)。专用验收工具保留私人状态和测试账号，仅在有明确收件人及发送额度授权时使用；既有三封授权已用完。自动化测试替换 SMTP 投递，不发真实邮件。更换 NZ_SECRET_KEY 会使尚未消费的验证码失效，不能在排错时随意轮换。

## 环境与独立数据库

需要 Python 3.12–3.14、Node 22.12+ 和 MySQL 客户端/服务器。Windows 脚本默认使用本机 `MySQL Server 9.0/bin`，其他安装目录通过 `-MySQLBin` 指定。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./tools/mysql-local.ps1 -Action Init
```

初始化只创建 `runtime/mysql` 独立实例，监听 `127.0.0.1:3307`，不修改系统服务。数据目录已存在或 `.env.local` 已存在时拒绝覆盖。首次生成的数据库凭据保存在忽略的 `.env.local`；勿公开、提交或截图。独立本地实例关闭二进制日志，使限定库权限的用户可以创建保护触发器。

环境变量：`NZ_DB_HOST`、`NZ_DB_PORT`、`NZ_DB_USER`、`NZ_DB_PASSWORD`、`NZ_DB_NAME`、`NZ_SYNTHETIC_DB_NAME`、`NZ_PVE_DB_NAME`、`NZ_TEST_DB_NAME`、`NZ_SECRET_KEY`。本地实例停止管理另外使用 `NZ_MYSQL_ROOT_PASSWORD`。MySQL 工具不在 PATH 时设置 `NZ_MYSQL_BIN`。四种数据库名称必须不同。`NZ_ENV_FILE` 可选择另一个私有 dotenv 文件；显式路径不存在时拒绝启动。

默认当前库 `nz_current`、合成库 `nz_synthetic`，自动化测试使用可销毁的 `nz_unittest`。`NZ_PROFILE=current|synthetic` 在 Django 初始化前决定库和规则；数据库内部 profile 再次校验，禁止混用。

```powershell
./tools/mysql-local.ps1 -Action Start
./tools/mysql-local.ps1 -Action Status
./tools/mysql-local.ps1 -Action Stop
```

## 初始化 Owner 与启动

```powershell
.venv/Scripts/python.exe -m revival migrate
.venv/Scripts/python.exe -m revival bootstrap-owner owner
./tools/start-local.ps1
```

Owner 初始化交互输入 12–128 字符独立密码，MFA URI 与八个恢复码只显示一次，首次完成后拒绝覆盖。验证器丢失时执行 `manage.py recover_admin_mfa <账号>`，交互输入密码和未用恢复码；它会更换 MFA、替换全部恢复码并撤销旧会话。没有恢复码时由另一位 Owner 再认证重置；不提供跳过认证的后门。旧管理员可由 Owner 使用管理页面生成恢复码。

启动脚本启动独立数据库、安装锁定前端依赖、构建、应用迁移，再启动维护进程和前台 WSGI 服务。已有构建可使用 `-SkipBuild`；Ctrl+C 停服务并清理本次维护进程，MySQL 可显式 Stop。服务默认 `http://127.0.0.1:8765`，只允许回环监听。

手动启动时，分别运行 `manage.py serve` 与 `manage.py maintain`；维护每 5 秒清理过期会话和租约、执行持久发放子任务并补偿超时续局，不依赖有人访问页面。`maintain --once` 适合一次性检查。

## 合成业务闭环与前端开发

用户明确要求的测试管理员可在合成模式执行 `manage.py create_test_admin <账号>` 创建；命令生成强密码及可重复的六位固定测试码，凭据只写 `runtime/test-admin.json`，固定码摘要与账号 ID/数据库名写忽略的 `.env.local`。服务重启后加载。此例外只对指定 TEST_PROFILE 账号生效，其他管理员和当前客户端模式仍要求正常 TOTP；密码、停用、安全版本、会话期限和权限检查继续执行。拒绝覆盖既有配置。本文不记录任何测试凭据。

另一个用户指定的免登录验证码账号通过 `manage.py create_demo_admin <账号>` 创建，密码交互输入，也支持 `--password-stdin`；只允许合成模式，凭据存 `runtime/demo-admin.json`，仅账号 ID/库名写环境配置。`NZ_PASSWORD_ONLY_TEST_ADMIN_ID`、`NZ_PASSWORD_ONLY_TEST_ADMIN_DATABASE` 控制单账号例外。该账号登录可以留空验证码，高风险操作仍按原再认证规则执行。固定码账号与免码账号可以并存，各自的配置不相互覆盖。

```powershell
.venv/Scripts/python.exe -m revival --test-profile migrate
.venv/Scripts/python.exe -m revival --test-profile bootstrap-owner owner
./tools/start-local.ps1 -TestProfile -Port 8766
```

页面持续显示 TEST_PROFILE。管理员创建邀请，玩家受邀注册，管理员批准，玩家登录创建角色；资源中心先预览再确认发放 GP/NZ、test-token 或 test-rifle。购买步枪由服务端扣 500 测试 GP；房间仅验证席位、准备与分配状态，未接入真实地图实例。

账号与角色、币种钱包、库存实例保持独立。用户管理支持制裁、停用、激活、重置和会话撤销；多个封禁叠加检查。高风险发放、批量和补偿继续使用原 API 的密码/MFA 再认证字段，页面保留普通单人操作范围。

前端开发：在 `admin` 执行 `npm ci`、`npm run dev`，默认通过本地代理访问 8765；合成服务 8766 时设置 `NZ_API_TARGET=http://127.0.0.1:8766`。浏览器会话仅存内存，刷新需重新登录。失败重试沿用幂等键和冻结请求体，成功后清空重试槽。

## SQLite 显式迁移

先关闭旧服务写入；保留源文件及 WAL，不删除或手工搬移文件。目标需执行 Django migrations，且业务表为空。迁移不会自动寻找旧文件。

```powershell
.venv/Scripts/python.exe -m revival import-sqlite <旧库绝对路径> --dry-run
.venv/Scripts/python.exe -m revival import-sqlite <旧库绝对路径>
```

合成旧库给命令添加 `--test-profile`。源 SQLite 仅以只读连接打开，核对 schema v1、完整性、外键、字段和 profile；完整导入位于一个 MySQL 事务内，并逐行对账。失败回滚；重复导入及非空目标拒绝。原账号 ID、game_id、密码/MFA、安全版本、期限、会话、审计、资产流水和幂等结果保留，内部新增主键不参与业务对账。

`--db` 不再接受 SQLite 路径，会明确报错并提示环境配置与显式导入方式。

## 备份、检查和回退

```powershell
.venv/Scripts/python.exe -m revival backup runtime/backups/current-new-001.sql
.venv/Scripts/python.exe -m revival check
```

备份锁定业务写入，使用 `mysqldump --single-transaction` 导出到新文件，在随机 `nz_backup_*` 临时库实际恢复并逐表对账，最后移除临时库。执行账号需要相应数据库创建/删除及触发器权限；已有目标拒绝覆盖。备份包含敏感账号资料，仅保存在受保护的忽略目录。校验失败不会报告成功。

2026-10-15 邮件批次最终备份已覆盖 migration 0006 之后的 36 张业务表，当前库和 PvE 库均实际恢复对账成功，包含 email_challenges。公开结果见[当前库](../evidence/aliyun-before-current-final-backup.json)和[PvE 库](../evidence/aliyun-before-pve-final-backup.json)；SQL 仍只保存在受保护私有目录。备份验证通过不授权覆盖运行库中新发生的写入。

不提供覆盖运行库的自动恢复命令。回退前先停写并保留 MySQL 最新数据、审计和 outbox；迁移后已有新写入时，不能直接回到旧 SQLite 快照，必须先对账，避免丢失流水或复活撤销会话。

没有真实客户端 transport，当前客户端 outbox 保持 PENDING。合成节点现有受认证的 HTTP 就绪、事件、待办与确认接口；凭据只能由 Owner 再认证为一份分配生成，不接受玩家 token。指派给节点的事件需所有目标节点逐一确认才标记送达，通用 sender 不能代替这些确认。确认重试不会重复发资产；这些能力未开放到当前客户端模式。

## 持久任务与合成 PvE

Owner 在“管理员”管理角色、状态、密码、MFA 和恢复码，最后一位启用 Owner 不能停用或降权。高风险操作需当前密码和新的 TOTP，密码/MFA/角色变化会撤销旧会话。

“批量任务”先固定 1–500 个唯一账号 ID 和资产变化、预览，再认证后入队。维护进程逐目标提交；失败只重试失败 ID，成功目标不重发。操作者停用、角色或安全版本变化后，未完成项拒绝执行。人工复核只追加结案决定，额外补偿需另立有依据的资产操作。

```powershell
$env:NZ_ENV_FILE = "$((Get-Location).Path)/runtime/user-mysql.env"
.venv/Scripts/python.exe -m revival --pve-test-profile migrate
.venv/Scripts/python.exe -m revival --pve-test-profile bootstrap-owner owner
./tools/start-local.ps1 -PveTestProfile -EnvironmentFile runtime/user-mysql.env -Port 8767
```

`test-pve-v1` 是单独数据库的测试规则，含三张合成地图、二维直线路径、Boss 半血狂暴、目标/载具/机关、整局奖励和限时续局。它不读取或执行原生游戏脚本，不提供游戏画面。当前客户端库不接受这些地图或节点事件。

工作副本位于忽略的 `runtime/client-workspace`；原件为同级 `../nz`。使用 `python -m tools.client_workspace verify|restore --source ../nz --workspace runtime/client-workspace --manifest ../evidence/private/client-catalog/json/source_files.json` 验证或恢复。恢复只替换基线文件并保留额外日志；拒绝目录联接、硬链接及来源摘要失配。独立副本恢复不等于 Windows 快照/驱动/系统配置恢复。

## 验证

```powershell
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
.venv/Scripts/python.exe tools/run_tests.py
cd admin
npm run typecheck
npm run build
npm test
```

测试结果写入 `evidence/stack-refactor-tests.json` 和文本日志；数据库测试实际使用 MySQL，SQLite 仅用于导入源夹具。CI 的 Ubuntu 使用 MySQL 8.4，Windows 执行独立工具与前端检查；是否实际运行以 Actions 结果为准。
