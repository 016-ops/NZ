# 本地启动与管理手册

当前架构为 React + TypeScript + shadcn/ui、Django 5.2、MySQL。真实游戏客户端接入仍未完成；本地业务验证不能代替原客户端兼容验收。

## 环境与独立数据库

需要 Python 3.12–3.14、Node 22.12+ 和 MySQL 客户端/服务器。Windows 脚本默认使用本机 `MySQL Server 9.0/bin`，其他安装目录通过 `-MySQLBin` 指定。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./tools/mysql-local.ps1 -Action Init
```

初始化只创建 `runtime/mysql` 独立实例，监听 `127.0.0.1:3307`，不修改系统服务。数据目录已存在或 `.env.local` 已存在时拒绝覆盖。首次生成的数据库凭据保存在忽略的 `.env.local`；勿公开、提交或截图。独立本地实例关闭二进制日志，使限定库权限的用户可以创建保护触发器。

环境变量：`NZ_DB_HOST`、`NZ_DB_PORT`、`NZ_DB_USER`、`NZ_DB_PASSWORD`、`NZ_DB_NAME`、`NZ_SYNTHETIC_DB_NAME`、`NZ_TEST_DB_NAME`、`NZ_SECRET_KEY`。本地实例停止管理另外使用 `NZ_MYSQL_ROOT_PASSWORD`。MySQL 工具不在 PATH 时设置 `NZ_MYSQL_BIN`。三种数据库名称必须不同。

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

Owner 初始化交互输入 12–128 字符独立密码，MFA URI 只显示一次，首次完成后拒绝覆盖。当前尚无 MFA 丢失恢复流程。

启动脚本启动独立数据库、安装锁定前端依赖、构建、应用迁移，再启动维护进程和前台 WSGI 服务。已有构建可使用 `-SkipBuild`；Ctrl+C 停服务并清理本次维护进程，MySQL 可显式 Stop。服务默认 `http://127.0.0.1:8765`，只允许回环监听。

手动启动时，分别运行 `manage.py serve` 与 `manage.py maintain`；维护每 5 秒清理过期会话和租约，不依赖有人访问页面。`maintain --once` 适合一次性检查。

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

不提供覆盖运行库的自动恢复命令。回退前先停写并保留 MySQL 最新数据、审计和 outbox；迁移后已有新写入时，不能直接回到旧 SQLite 快照，必须先对账，避免丢失流水或复活撤销会话。

没有真实客户端 transport，outbox 保持 PENDING。sender 按 event_key 幂等且明确返回 True 才会标记送达；重试通知不会重复发资产。合成节点注入仅存在 Python harness，HTTP 不开放管理注入入口。

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
