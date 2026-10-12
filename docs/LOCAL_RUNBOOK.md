# 本地启动与管理手册

本轮交付仅在本机 Windows / Python 3.12.14 执行，真实游戏客户端、隔离快照、跨家庭网络和 Linux 实机未测试。

## 1. 目录与环境

- `revival/`：自有领域、数据库、HTTP 服务；没有导入上游模块或游戏 DLL。
- `adapters/cn_current/`：原客户端证据门槛。未知协议默认拒绝。
- `admin/`：实际管理页面与玩家测试页面，无构建依赖。
- `config/cn-current.json`：当前样本研究状态；禁止直接把 capability 开关改成“已验收”。
- `config/test-profile.json`：合成目录；修改规则必须改变版本并重新回归。
- `runtime/current.db` 与 `runtime/test.db`：必须独立，服务在启动时核对 profile，不接受混用。
- `evidence/private/`：本地指纹/索引，不进入 Git。

自有服务不需要 pip install。此台机器可使用以下已实际验证的解释器，其他机器使用自己的 Python 3.12+：

```powershell
$nzPython = 'C:\Users\ZJH\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $nzPython tools/run_tests.py
```

## 2. 初始化 Owner 与启动

```powershell
python -m revival --db runtime/current.db bootstrap-owner owner
python -m revival --db runtime/current.db serve --port 8765
```

命令交互读取 12–128 字符独立密码，确认后输出一次 `otpauth` URI。将它存入个人验证器并妥善保护备份，勿公开或截入共享日志。第一次 bootstrap 之后不能再次覆盖 Owner。当前版本尚无 MFA 丢失恢复流程；需要恢复时保持服务关闭并单独评审，不直接关闭 MFA。

访问 `http://127.0.0.1:8765`，选择管理员，用独立密码与当前动态码登录。页面令牌仅存在内存，刷新页面需要重新登录。TOTP 验证拒绝重复使用同一时间步，刚登录后若进行高风险 API 操作，需要下一枚新动态码。

可以用 `tools/start-local.ps1 -PythonPath <解释器绝对路径>` 启动。此脚本保持前台日志，不修改 hosts、防火墙、客户端、驱动或官方服务配置。Ctrl+C 停止。

## 3. 测试目录下体验业务闭环

```powershell
python -m revival --db runtime/test.db --test-profile bootstrap-owner owner
python -m revival --db runtime/test.db --test-profile serve --port 8766
```

页面持续标记 TEST_PROFILE。Owner 生成邀请、保存一次性令牌；另一个浏览器页面用受邀注册建立独立玩家；Owner 在用户管理批准；玩家登录并创建本地昵称。注册、审批、角色和钱包不会共用一个存档。

Owner 到资源中心选择目标，填写测试币种 `GP` 或 `NZ` 和增量，先预览后确认。`test-token` 是可消耗测试物品；`test-rifle` 是独占主武器。玩家购买测试步枪的服务端价格为 500 测试 GP。它们没有已确认的游戏内 ID。

大额发放、批量、补偿使用 API 的 MFA 再认证字段，详见 `docs/API.md`；第一版页面仅开放普通单人预览/发放，未开放全部高风险表单。测试房间可验证创建/席位/准备/取消/迁移，申请开始只分配 STARTING，30 秒没有节点就超时；不会加载真实地图。

## 4. 制裁与审计

用户管理支持临时/永久封禁、停用、激活、重置和踢会话。撤销制裁须输入指定 sanction_id；仍有其他有效封禁时不能重连。禁言/特定模式封禁/原客户端踢人尚未接入。

所有命令要求 request_id、idempotency_key 和原因。后台角色权限在服务端检查。查询不返回密码哈希、MFA 密钥和可复用 token。高风险成功变更与审计同事务；审计写入故障会回滚资产。

## 5. outbox 与合成 host

没有当前客户端 transport，因此资产/安全事件保持 PENDING。这是如实显示的未同步状态，不能手工把它改成真实游戏同步成功。`deliver_outbox(service, sender)` 要求 sender 按事件键幂等并明确返回 True，成功后才标 DELIVERED；失败只重试通知。

`test_host_ready` 仅供 Python 自动化 harness，未暴露 HTTP 管理入口。合成节点获得独立凭据并绑定 epoch/lease，玩家拿不到这些凭据。测试结算不证明真实通关、碰撞或掉率。

## 6. 备份与恢复

```powershell
python -m revival --db runtime/current.db backup runtime/backups/current-new-001.db
python -m revival --db runtime/current.db check
```

备份用 SQLite backup API，不直接复制正在写入的数据库，目标必须是新文件并检查 integrity。回滚时先停止服务，保留当前写入与审计，针对备份在**新的数据库路径**进行核对。不要覆盖运行数据库，也不恢复旧制裁/会话来消除安全版本。当前版本没有一键恢复到生产的命令，完整恢复演练待隔离环境。

## 7. 复现上游研究

```powershell
./tools/fetch-upstream.ps1
python tools/verify_upstream.py
python tools/run_tests.py --upstream --output evidence/upstream-tests.json
```

脚本只获取固定源码。不要执行上游一键启动器，也不以 PH 补丁地址/消息号替换当前国服。抽选上游测试中 1 个静态源码文本检查失败已保留；重跑结果可能新增研究事实，应单独记录，不能改成当前客户端通过。
