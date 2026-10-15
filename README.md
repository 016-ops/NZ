# NZ Revival 本地开发与测试交付

目标是用户当前提供的 `nz/` 客户端。2026-10-13 用户确认使用当前样本，并确认本轮先交付本地开发与测试成果。最初的 CN206 大纲保留为历史规划，当前样本的准确发行版本仍待确认。

已实现独立账号、邀请审核、管理员密码加 MFA、制裁与会话撤销、房间控制状态、资产账本、购买/消耗/装备、合成结算和受保护的本地管理页面。**这些是本地业务实现；当前游戏客户端登录、真实多人战斗和游戏内资产同步尚未接入。**

默认环境拒绝未知地图、货币、物品和游戏协议。合成数据必须显式选择 `--test-profile`，使用单独数据库，页面持续显示 `TEST_PROFILE`。

## 阶段记录

| 阶段 | 本轮交付记录 | 当前结论 |
|---|---|---|
| M0 | [基线与内容盘点](docs/delivery/M0.md) | 文件盘点、版本证据、源码固定与工具完成；原始性/隔离快照未验收 |
| M1 | [上游与兼容研究](docs/delivery/M1.md) | 上游源码研究和单测、候选符号、适配拒绝边界；真实认证/实例阻塞 |
| M2 | [账号与管理后台](docs/delivery/M2.md) | 本地账号、角色、权限、制裁和管理页面已实现；游戏首登未接入 |
| M3 | [房间与实例契约](docs/delivery/M3.md) | 本地房间状态、分配/租约/清理和测试节点契约；真实战斗未实现 |
| M4 | [资产与合成结算](docs/delivery/M4.md) | 测试目录下事务/幂等/账本/装备/购买/结算已实现；真实目录/协议未接入 |
| M0–M5 续建 | [逐项清单、测试与阻塞](docs/delivery/2026-10-15-m0-m5.md) | 管理、房间、持久批次及独立合成 PvE 已补齐；完整实机阶段未完成 |
| Win32 恢复 | [归档恢复与启动图后退出](docs/delivery/2026-10-15-native-client-recovery.md) | 完整归档 CRC 通过，截断 DLL 已定位；可到启动图，登录/大厅未接通，Win64 未恢复 |
| 独立邮箱 | [邮箱认证与原生交接自测](docs/delivery/2026-10-15-independent-email-login.md) | 邮箱注册/登录、旧账号迁移、一次票据与 Win32 接口自测已实现；完整游戏大厅未接通 |
| 邮件验证 | [阿里云邮件与原生启动续建](docs/delivery/2026-10-15-aliyun-email-and-native-launch.md) | DEV 邮件闭环通过；真实邮箱身份已消费单条 GEO 目录并进入 Zone A000；A001 研究响应已消费；角色/资产/大厅待完成 |
| 单服务器 | [Windows 房间目标准备](docs/delivery/2026-10-15-single-room-server.md) | SSH 与专属工作目录通过；当前原生服务端入口、最小资源闭包及房间部署未验证 |
| M5 | [生存、保卫、围剿边界](docs/delivery/M5.md) | 三种合成规则已实现；逐图原生玩法和双端 T01–T12 均未通过 |
| 技术栈迁移 | [Django/MySQL/React 与 UI 交接](docs/delivery/2026-10-14-stack-refactor.md) | 本机 65 项回归通过，独立 MySQL、只读导入、测试管理员与新版工作台已实现 |

[启动与管理手册](docs/LOCAL_RUNBOOK.md) · [实际接口](docs/API.md) · [架构决策](docs/DECISIONS.md) · [本轮 MySQL 自动化证据](evidence/stack-refactor-tests.json)

## 当前客户端资产目录

玩法、模式、地图、物品、武器、功能和商城的只读提取工具，以及 JSON、Markdown 和独立 MySQL 目录 SQL，见 [资产目录交付说明](docs/CLIENT_CATALOG.md)。完整产物在仓库同级 `../evidence/private/client-catalog/`。2026-10-15 经用户单独授权，已在用户 MySQL 的独立 catalog 库导入 19 张表并验证重复导入；[实际证据](evidence/m0-m5-catalog-mysql.json)。这些 ID、价格、规则尚未提升为玩家业务目录，游戏内效果仍未验证。

## 本地启动

当前技术栈为 React + TypeScript + shadcn/ui、Django 5.2 和 MySQL。需要 Python 3.12–3.14、Node 22.12+ 及 MySQL。Windows 可通过 `-MySQLBin` 指定 MySQL 程序；本轮独立实例使用 8.4.6，不修改系统服务。在仓库根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./tools/mysql-local.ps1 -Action Init
.venv/Scripts/python.exe -m revival migrate
.venv/Scripts/python.exe -m revival bootstrap-owner owner
./tools/start-local.ps1
```

初始化时交互输入独立密码，把只显示一次的 MFA URI 加入验证器。密码和 MFA 资料不要放入命令行参数、Issue 或提交记录。访问 [本地控制台](http://127.0.0.1:8765)，选择管理员身份，以密码和动态验证码登录。

玩家新注册默认要求邮箱验证码，并继续保留邀请和人工审批。阿里云 SMTP 配置单独保存在忽略的 `runtime/aliyun-mail.env`；配置与迁移步骤见[邮件服务配置](docs/LOCAL_RUNBOOK.md#邮件服务与邮箱入口)。页面提供验证码注册、找回密码与旧邮箱验证，邮件验证码不能解除账号停用或封禁。

最新 DEV 发布 `20261015T061412-1cfd594907ca` 已包含邮箱幂等与并发修复，四服务、两库和账号重登保持核验通过；本次核验没有追加邮件。原客户端本机研究已修正 IPC v1 的身份/端点字段错位，第 18/19 次消费单条 GEO Ping/Zone 研究目录并独立认证第二条连接，第 20/21 次真实 DEV 邮箱身份已发出 26 字节 Zone A000；第 22/23 次已消费 41 字节 A001 研究响应并返回 A204/FF05，角色和大厅仍未通过。Windows 研究工具的后续变化未改变 DEV 邮箱发布；M5 仍未通过，详见[最新批次交接](docs/delivery/2026-10-15-aliyun-email-and-native-launch.md)。

测试业务闭环使用另一个数据库：

```powershell
.venv/Scripts/python.exe -m revival --test-profile migrate
.venv/Scripts/python.exe -m revival --test-profile bootstrap-owner owner
./tools/start-local.ps1 -TestProfile -Port 8766
```

合成 PvE 使用第三个独立业务库，显式执行 `python -m revival --pve-test-profile migrate`、`python -m revival --pve-test-profile bootstrap-owner owner`，再运行 `./tools/start-local.ps1 -PveTestProfile -Port 8767`。选择用户提供的数据库时加 `-EnvironmentFile runtime/user-mysql.env`。生存、保卫、围剿均为 `test-pve-v1` 测试规则，不能代表原客户端已可游玩。DEV 使用 [SSH 隧道部署手册](docs/DEV_DEPLOYMENT.md)。

服务只允许监听回环地址。数据库与凭据位于忽略的 `runtime/mysql` 和 `.env.local`，通过环境变量配置。当前库、合成库、可销毁测试库分离；旧 `--db` 文件参数明确拒绝。SQLite v1 可通过 `import-sqlite <源路径> --dry-run` 和显式导入命令迁移到空 MySQL 业务库，原文件保留。详见启动手册。本轮没有对公网部署、Linux 实机、容量或原客户端兼容作出通过结论。

## 验证

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-catalog.txt
.venv/Scripts/python.exe tools/run_tests.py
.venv/Scripts/python.exe -m revival check
cd admin
npm run typecheck
npm run build
npm test
```

业务测试使用真实 MySQL 测试库，包含 HTTP 请求、并发邀请/座位/资产、事务回滚、权限拒绝、过期/重启、合成结算、防重复到账、备份恢复和 SQLite 导入对账。前端测试验证幂等重试。CI 的 Ubuntu 使用 MySQL 8.4，Windows 执行独立工具与前端构建；是否实际运行以 GitHub Actions 结果为准。

## 样本与上游

客户端、DLL、地图、私人清单、数据库和凭据均不进入仓库。扫描工具只读客户端，输出必须在客户端目录之外。

```powershell
python tools/client_inventory.py scan ../nz ./evidence/private/client-manifest.csv
./tools/version_evidence.ps1
python tools/inspect_metalib.py ../nz/TGame/TDR ./evidence/private/metalib-symbols.json
```

上游固定到 [armangido/af-emulator / 4bbabab](https://github.com/armangido/af-emulator/tree/4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495)，只作为 PH 参考。上游启动器包含客户端写入和系统配置操作，本轮没有运行它。

```powershell
./tools/fetch-upstream.ps1
python tools/verify_upstream.py
python tools/run_tests.py --upstream --output evidence/upstream-tests.json
```

上游抽选 35 项测试有 1 项静态文本检查失败，完整记录已保留。[上游许可证](third_party/af-emulator/LICENSE)只涉及其原创代码，客户端资源另属第三方。本项目自有服务未复制 PH 的消息号、密钥或二进制补丁。
