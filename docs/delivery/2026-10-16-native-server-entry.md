# 当前构建原生服务器入口实验

## 范围与状态

日期：2026-10-16。继续原生房间、地图、战斗和结算任务。此记录补充 10 月 15 日的单房间节点交接，**尚未交付可玩原生房间**。本轮只运行本机受控工作副本，没有部署远端房间节点，没有改变已有账号、资产或数据库。

## 已验证

第 31 轮 Launcher 运行 `launcher-20261016-223733-942256` 使用当前地图参数 `server SV-Maya_3_Main?game=PVEGame.TGSVGame -unattended -nosplash`。主 EXE SHA256 为 `b81bb4e1256ad1fca1ecbbf93b35979d96c36ca16f8bbfc8d126e9a452df51f8`。

当前启动代码 RVA `FC3B25` 将模式最终设为 client=1/server=0；当前崩溃报告分类代码 RVA `13590A` 独立证明 VA `02549AD0` / `02549AD4` 分别为 client/server 字段。未借用其他客户端的全局地址。

自动审批曾拒绝未获明确授权的进程代码修改。已向用户说明仅修改本轮自有进程两条模式赋值、崩溃可能性、90 秒结束和清理。用户随后明确回复“授权限时实验”，相同范围重试获准。实验在映像指纹、Job 所有权、固定基址、可执行页面归属和完整 12 字节锚点全部匹配后交换两条赋值的源寄存器；不修改磁盘 EXE。写入后核对完整内容、刷新指令缓存并恢复页面权限。研究实现位于忽略目录 `runtime/native_server_mode_experiment_51.py`，尚未纳入产品启动流程。

实际游戏子进程约第 15 秒应用变更，最终模式持续为 client=0/server=1；第 20 秒观察到 UDP 9989，结束前同时观察到 UDP 7777。进程运行到 90 秒限时，由本轮 Job 主动结束，退出码 `0xE0000001` 不是自然崩溃证据。没有 Geo/Zone 登录请求。配置恢复、自有进程清理、Launcher 来源及游戏凭据撤销均通过。

## 未实现与当前阻塞

监听端口与模式标记仅证明入口进展。尚未证明 DS 管理器握手、目标地图/世界加载、玩家加入、敌人/Boss、胜败奖励、返厅和下一局，不能设置生产节点 ready。没有引擎日志或实机地图画面可佐证本轮地图就绪。继续核验当前 DS TDR、实际连接、启动参数和资源闭包；这是待完成研究，不把“未实现”伪装为已验证。

远端 Windows 节点仍需重新测量可用资源并建立最小资源闭包后部署。双端、跨网、双房隔离与十局仍未验收。用户的完整可玩版本及测试账号交付目标保持未完成。

## 验证及恢复

- 第 31 轮清理报告：`evidence/2026-10-16-launcher-features-ui-31.json`。
- 固定代码锚点、模式与页面权限结果：`evidence/private/server-mode-51.json`。
- UDP/线程/退出与配置恢复原始记录：`evidence/private/launcher-20261016-223733-942256/`。
- 无原客户端写入，所有临时配置由既有持久恢复守卫恢复。没有数据库迁移或补偿需要；研究脚本不作为产品发布文件。

研究脚本中文职责与关键校验注释已核对。下一步只在相同已授权的自有进程和限时范围内核验 DS 管理器连接；任何新的代码位置或更大范围修改均不视为本次授权内容。

## 第 32–34 轮追加核验

三轮均沿用相同的两条赋值、指纹、Job 和 90 秒边界，配置与凭据清理全部通过。

- 第 32 轮将 `DefaultTGNet.ini` 中 `UTGame.TGDsDsmNetHandler` 的 Host/Port 暂时指向本轮回环 TCP 采集端，其他 DS 字段保持原样。没有收到连接或字节，没有发送猜测应答；`evidence/private/ds-capture-52/report.json` 保留空采集结果。computer-use 观察到 100×100 的空白小窗口，截图 `evidence/2026-10-16-server-mode-window-32.png` 不能作为地图画面通过。
- 第 33 轮通过当前进程命令行只读查询，逐项确认 `server`、`SV-Maya_3_Main`、`?game=PVEGame.TGSVGame`、`-ABSLOG=`、`-unattended`、`-nosplash` 保留在实际游戏子进程中。报告只保存布尔比较，不保存账号/认证字段或完整命令行。排除了参数在 GameLoader 转交时丢失这一假设。
- 当前 `proto_ds2dsm.tdr` SHA256 `506b3365bbffde4f59edb84991538cd107ffd3c87fecbe6cd64274f828658471` 与 `proto_zn2ds.tdr` SHA256 `ca5265726751e3474d6e895287add740b8647d063cfc58223e1fd39a9abd9594` 均是元数据格式 V8；Geo/Zone 为 V11。已经按 V8 的真实内嵌名称、24/23 个结构、偏移/尺寸表逐项互证，没有强行沿用 V11 解析器。
- 固定当前 `ProtocalHandler.dll` 的离线 x86 Job 加载 DS TDR 失败，返回 `0x82010459`，DLL 自身错误文本为“生成元数据库文件的tdr工具的核心数据构建版本和tdr库的核心数据构构建版本不一致”。保护页/哨兵、虚表槽和自有进程清理均通过，**协议夹具未通过**。失败证据在 `evidence/private/ds-tdr-53/`，错误文本专项复核在 `evidence/private/ds-tdr-54/`。
- 第 34 轮只读核验当前构建对象列表和名称解析调用链，继续定位实际地图/DS对象。没有写对象字段或任意状态。尚未证明原生 DS 初始化实际使用这份旧 TDR，因此版本不一致目前是确证的离线不兼容问题，不能直接宣称它就是未建连根因。

公开报告中的 `native_server_mode_verified=false` 是原启动探针尚无模式认定接口的保守默认值；实验单独记录的两字段实值为 client=0/server=1。不得直接把该默认字段改成地图/节点 ready。后续需把模式、世界、网络、租约和玩家加入分别形成可检验的条件。

## 第 35–38 轮与异常清理修复

第 35–37 轮确认当前名称表、对象表和公共对象元数据布局，均从当前函数指令推导而非移用上游地址。第 36 轮在 35/70 秒两次读到 440,576 个对象、177,806 个名称；完整枚举仅导出世界、目标地图及网络类的标识。存在真实运行对象 `Transient.TGOnlineSubsystem_0.TGOnlineDS_0`、`TGDsDsmNetHandler_0`、`TGDsDsaNetHandler_0`，但世界一直是 `UTFrontEnd.TheWorld`，仅存在 `PVEGame.Default__TGSVGame` 类默认对象，没有玛雅地图中的 TGSVGame 实例。第 37 轮去掉参数首位的 `server` 后同样仍为前端世界。两轮均不能记作目标地图加载成功。

当前 DS 类还存在必须单独核验的实现差异：其虚表若干入口指向固定返回/空函数，DSM 的包容量函数返回 `0x17A08`，与归档旧 TDR 的 `0xFA08` 不同。只修改 TDR 版本头或借用其他地区协议显然不足以建立当前服务端。后续继续核验实际调用；尚未修改这些函数或虚表。

第 38 轮尝试原生 `EXEC` 文件加载固定地图时，研究采样脚本把单元素 tuple 写成整数，25 秒后抛出 TypeError。Windows Job 关闭已触发进程退出，但最初配置恢复抢在兼容 DLL 解除占用之前，报告保留 `cleanup_verified=false` / `configuration_restored=false`。来源及游戏凭据撤销成功。等待结束后，按相同持久计划复查并恢复全部配置，另存 `configuration-retry.json`，未改写原失败报告。

据此修复 `tools/native_job.py`：关闭 Job 时使用基础枚举取得自有后代句柄，终止后限时等待 Job 清空和进程退出信号，再释放句柄；诊断子类的 `pids` 异常不能再次打断清理。终止/查询失败仍关闭 Job 兜底并记录未验证状态。`tools/native_email_boot.py` 的异常收尾读取该独立清理结果，随后才恢复配置，保留原运行错误。没有数据库或资产修改。

真实 Windows 子进程回归 `test_native_job` 2 项通过（无跳过），覆盖文件被占用时观察器抛错及父进程先退出的后代清理；配置恢复 18 项、Launcher 16 项通过。证据分别为 `native-job-cleanup-60`、`native-config-cleanup-60`、`native-launcher-cleanup-60`。新增清理函数与测试中文注释已核对。研究 tuple 已修正，原生 EXEC 实验和清理重跑继续进行。

## 第 39–41 轮：默认配置成功加载实际地图

第 39 轮 EXEC 重跑已确认异常清理修复生效，配置、进程、来源及游戏凭据清理全部通过，但仍处于 UTFrontEnd。当前代码的 EXEC 路径要求 GEngine 中存在本地玩家；服务器模式没有本地玩家，因而不会执行该命令文件，不能用它判定目标地图加载失败。

第 40 轮在启动前因 DefaultEngine 的 UTF-16 编码假设不匹配而拒绝修改，未启动游戏且清理通过。随后按原编码解码、重编码往返核验，并仅替换 [URL] 的 Map、LocalMap 两行。

第 41 轮 `launcher-20261016-231921-492352` 在第 35 秒和第 70 秒均完整枚举 463,508 个对象、200,791 个名称，读到 `SV-Maya_3_Main.TheWorld`、`SV-Maya_3_Main.TheWorld.PersistentLevel.TGSVGame_0` 以及导航、美术、脚本等流式子世界。这是当前构建实际目标世界和游戏实例加载证据，覆盖前面“始终只有前端世界”的限制；**尚不证明网络握手、玩家加入或完整战斗可玩**。此轮全部退出清理通过，原配置摘要已恢复。没有添加新的进程代码修改位置。

原始元数据记录在 `evidence/private/server-code-62/objects-35.json` 和 `objects-70.json`。继续核验网络驱动类别及 listen 配置；暂不将生产节点标为 ready。

## 第42、44、46轮：玩法与网络驱动同实例核验

第42轮在默认地图URL同时加入 listen 与 game=PVEGame.TGSVGame 时，加载了玛雅世界和 TcpNetDriver，但玩法复制实例退回 UTGameReplicationInfo，不能视作正确生存玩法。

第44轮只加入 listen，保留地图自身的玩法选择，35秒和70秒均读到实际 TGSVGame_0、TGSVGameReplicationInfo_0、TcpNetDriver_0、玛雅世界及子关卡脚本的开始/结束节点。它覆盖前轮玩法错误，尚未证明玩家控制器、角色、敌人或战斗就绪。原始证据为 evidence/private/server-code-71/objects-35.json、objects-70.json。第46轮沿用同配置，附加只读查找直连模式函数引用；固定字符串引用未命中，不据此猜测或修改函数。两轮始终只保留原授权的12字节模式赋值变化，90秒后清理，配置、进程及凭据恢复全部通过。

TGGame 原脚本表明网络入场需要真实 DS 玩家资料，或当前构建原生直连模式；下一步核验当前函数和受支持参数。未修改认证判断、虚表、玩家资料或其他代码位置。线上房间和战斗完整闭环继续保持未完成。
