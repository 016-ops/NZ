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

[启动与管理手册](docs/LOCAL_RUNBOOK.md) · [实际接口](docs/API.md) · [架构决策](docs/DECISIONS.md) · [自动化证据](evidence/automated-tests.json)

## 本地启动

需要 Python 3.12+，自有服务无第三方 Python 依赖。在仓库根目录执行：

```powershell
python -m revival --db runtime/current.db bootstrap-owner owner
python -m revival --db runtime/current.db serve
```

初始化时交互输入独立密码，把只显示一次的 MFA URI 加入验证器。密码和 MFA 资料不要放入命令行参数、Issue 或提交记录。访问 [本地控制台](http://127.0.0.1:8765)，选择管理员身份，以密码和动态验证码登录。

测试业务闭环使用另一个数据库：

```powershell
python -m revival --db runtime/test.db --test-profile bootstrap-owner owner
python -m revival --db runtime/test.db --test-profile serve --port 8766
```

服务只允许监听回环地址。数据库、MFA 资料和备份由当前操作系统用户保护。本轮没有对公网部署、Linux 实机、容量或原客户端兼容作出通过结论。

## 验证

```powershell
python tools/run_tests.py
node --check admin/app.js
python -m revival --db runtime/current.db check
```

测试使用临时数据库，包含真实 HTTP 请求、并发邀请/座位/资产操作、事务回滚、权限拒绝、过期/重启、合成节点结算、防重复到账、备份完整性。CI 配置提供 Windows/Linux 回归；其是否实际执行以 GitHub Actions 结果为准。

## 样本与上游

客户端、DLL、地图、私人清单、数据库和凭据均不进入仓库。扫描工具只读客户端，输出必须在客户端目录之外。

```powershell
python tools/client_inventory.py scan ./nz ./evidence/private/client-manifest.csv
./tools/version_evidence.ps1
python tools/inspect_metalib.py ./nz/TGame/TDR ./evidence/private/metalib-symbols.json
```

上游固定到 [armangido/af-emulator / 4bbabab](https://github.com/armangido/af-emulator/tree/4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495)，只作为 PH 参考。上游启动器包含客户端写入和系统配置操作，本轮没有运行它。

```powershell
./tools/fetch-upstream.ps1
python tools/verify_upstream.py
python tools/run_tests.py --upstream --output evidence/upstream-tests.json
```

上游抽选 35 项测试有 1 项静态文本检查失败，完整记录已保留。[上游许可证](third_party/af-emulator/LICENSE)只涉及其原创代码，客户端资源另属第三方。本项目自有服务未复制 PH 的消息号、密钥或二进制补丁。
