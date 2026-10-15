# 单区服入口与单房间节点交接

## 目标与范围

日期：2026-10-15。用户新增约束为一个可用服务器，并提供根目录 `房间服务器配置.txt`。本轮先只读检查其管理入口，随后建立专属研究目录；凭据通过既有 `tools.dev_remote`/`read_credentials` 读取，不打印原文件、地址、账号或密码，不修改远端系统配置、不重启既有服务。

部署决定为一个可选区服入口、一个测试房间节点。同一节点仍须支持多个互相隔离的房间，原定双房隔离和双端实机验收继续保留。该决定已记录到 [ADR-012](../DECISIONS.md#adr-0122026-10-15-单区服入口与单房间节点)，没有更改现有用户、资产或房间数据。

最新状态：用户完成 OpenSSH 准备后，SSH 实际凭据认证和只读系统盘点已通过，管理接入阻塞解除。下述先前拒绝/超时保留为历史；原生房间节点尚未部署或验收。

已创建独立 `C:\NZResearch`，只包含所有权标记、说明、安装计划、只读预检脚本及五个空目录。没有安装 Python/游戏运行库，没有上传客户端或大资源，没有新增服务、游戏监听或防火墙规则。

## 已实现

- 读取用户指定目标配置，使用项目已有解析器及 SSH 主机密钥固定流程。配置没有显式端口，本次 SSH 使用默认 22。
- 首次受限环境连接失败后，在获准的正常网络执行环境进行同一只读连接，明确得到 `ConnectionRefusedError` / `WinError 10061`。不是自动审批拒绝，不把失败解释为凭据错误。
- 对同一目标的 22、3389、5985、5986 四个常见管理端口做有限 TCP 连接检查，不发送凭据。22 拒绝；其他三项均在四秒超时内没有建立连接。没有扩大扫描端口或访问其他服务器。
- 用户随后确认已放通 3389，只复查一轮相同四端口：3389 已建立 TCP 连接，22 仍拒绝、5985/5986 仍超时。没有使用 RDP UI、发送登录凭据或执行远端部署；端口可达不能等同凭据登录成功。
- 用户本人完成 OpenSSH 设置后，使用原有配置通过 SSH 实际认证，并首次固定主机密钥；后续只读检查沿用并核对固定记录，未覆盖变化密钥。首次长命令盘点退出码为 1，修正为固定短入口通过标准输入读取脚本后，原场景退出码 0；该工具失败已保留，不当作服务器业务故障。
- 实际读取到 Windows Server 2025 Datacenter、版本 `10.0.26100`、x64；AMD EPYC 7K62 虚拟分配 4 核/4 线程；总内存约 4 GiB，当时空闲约 1.57 GiB；C 盘约 39.90 GiB，当时剩余约 23.72 GiB。这些是盘点瞬时值，不是游戏容量结论。
- `sshd` 为 Running/Automatic；远端本机监听中包含 TCP 22/3389/5985 等系统服务。只记录端口、范围与进程名称，没有读取命令行；本机存在 5985 监听不等于外部防火墙已放通。
- 相关进程筛选未发现 NZ/TGame/Python 游戏或项目服务；五个固定项目候选目录未发现现有项目。检查没有递归用户目录，不能据此断言全磁盘不存在其他安装。`python`/`python3` 进一步确认为 WindowsApps 占位入口，没有执行它们，不能记作 Python 运行时已经安装。
- 公开报告仅含状态、端口及错误类型，原始配置和私有主机记录不提交到仓库。
- 新建目录前拒绝接管已存在目录，创建后检查非 reparse point；四个文件均以 CreateNew 写入并逐文件核验哈希。空目录为 `incoming`、`releases`、`runtime`、`evidence`、`client-workspaces`。远端实际运行无凭据的只读 `preflight.ps1` 成功，明确输出 `native_node_ready=false`；仓库对应脚本为 `tools/room_workspace_preflight.ps1`。

## 最小部署包核对与后续安装计划

当前仓库 `revival/nodes.py` 的 enroll、ready 和节点认证路径，及 `revival/rooms.py:test_host_ready`，都要求合成 test_profile；当前客户端 profile 继续返回 `COMPATIBILITY_BLOCKED`。`tools/deploy_dev.py` / `dev_install.sh` 部署 Linux Django 控制面；`native_email_boot.py` 是有界客户端研究工具，均不能作为 Windows 房间守护进程。没有用占位进程登记“原生节点已就绪”。

[当前组件清单](../../evidence/single-room-current-component-inventory.json)只读核对当前恢复归档的 8,982 个条目，并对 133 个选定实物重算哈希，均与清单一致。归档中没有名为 AFDEV、Dedicated、GameServer 或 DS 的可执行文件；这一命名检查不能证明 TGame 内不存在尚未识别的服务端模式。

| 当前候选材料 | 实际大小 | 已证范围与缺口 |
|---|---:|---|
| `Binaries/Win32/TGame.exe` / `TGameBase.dll` | 17,959,576 / 35,847,832 字节 | PE x86，当前客户端路径已研究；没有确证 dedicated-server 入口 |
| `Binaries/Win32/GameLoader.exe` | 5,574,736 字节 | 客户端进程交接，不等于房间启动器 |
| `Binaries/Win32/ProtocalHandler.dll` | 1,224,777 字节 | 当前客户端 transport/TDR 已验证部分路径；其他同名 DLL 指纹不同，不得替换混用 |
| `TGame/TDR/proto_ds2dsm.tdr` / `proto_zn2ds.tdr` | 50,132 / 45,908 字节 | 有 DS 相关协议元数据，尚未解析并证实实际服务端入口/握手 |
| `DefaultEngineG4WLiveDedicatedServer.ini` | 388 字节 | 可解码的通用 Engine/OnlineSubsystemLive 配置；不是可执行服务端或当前启动证明 |
| 当前 `.u` 脚本包 | 110 个，共 376,736,172 字节 | 文件存在且哈希一致；所需脚本子集与运行依赖闭包未确定 |
| `TGame/CookedPC/Maps/Maya_3/SV-Maya_3_Main.udk` | 163,105 字节 | 当前 DefaultGame 关联 MapId 47；主图存在，子图/模型/脚本完整闭包及原生加载未通过 |

归档总计 30,299,626,336 字节（约 28.22 GiB），其中 Win32 481,355,699 字节、Config 1,384,040 字节、TDR 6,385,954 字节、CookedPC 29,107,004,373 字节。远端预检时约 23.76 GiB 空闲；若要求部署全归档则放不下，但**全客户端并不是已经证实的最小房间包**。不能只按主 EXE 和主地图的大小决定能否部署；依赖闭包、临时文件、备份及运行内存预算都待验证。

上游固定 `af-emulator` 的 PH 1.0.0.24 采用预约、首个有效 UDP 后延迟启动、独立房间进程、原生就绪检查和最后玩家退出清理，可以借鉴生命周期设计。其 `TGame_AFDEV.exe` 来自用户 PH 客户端的版本特定派生副本，上游不携带游戏二进制/地图；固定 native patch、消息值和游戏类不能移植为当前样本证明。参考源码路径及范围在组件报告中列出，本轮没有执行上游工具。

后续依次完成：当前构建服务端入口和启动参数证据；选定地图及资源依赖闭包/指纹；独立 Win32 运行库、Python 是否必要及具体版本；单实例资源预算和 Job 清理；节点身份绑定实例、对局、epoch、租约与原生就绪事件；单端后双端、同节点双房隔离。上述入口未确定前，工作目录保持准备状态，不安装伪房间服务。

## 未实现与阻塞

SSH 管理登录与系统盘点已经验证。尚未安装或启动原生房间实例，未验证当前 Win32 组件、图形/运行库需求、数据库接入、游戏监听和容量；当前目标不能标为“可玩服务器”。本机研究 ZoneList 的一个回环入口也不等于该远端节点已提供游戏服务。

当前工具没有 RDP 桌面控制能力，不能通过模拟其他控制方式绕过工具边界。用户已本人完成 Windows OpenSSH 准备，代理继续使用已有凭据文件，没有再次索取密码，也没有触发自动审批拒绝。接下来核对当前 Win32 组件的运行条件、磁盘空间和所需独立运行时，再按单节点拓扑部署；管理入口通过不替代原生地图/实例及双端验收。

## 实际验证

| 项目 | 结果 | 证据 |
|---|---|---|
| 首次 SSH 尝试 | 未连接，`NoValidConnectionsError` | [首次失败记录](../../evidence/failures/single-room-server-attempt-01.json) |
| 正常网络环境 SSH 重试 | 22 拒绝，10061；无远端执行 | [脱敏盘点结果](../../evidence/single-room-server-inspection.json) |
| 有限管理端口检查 | 22 拒绝；3389/5985/5986 超时；无协议认证 | [TCP 检查](../../evidence/single-room-server-management-connectivity.json) |
| 用户放通 3389 后的一轮检查 | 3389 TCP 可达；22 仍拒绝；5985/5986 仍超时，未登录 | [复查记录](../../evidence/single-room-server-management-connectivity-rdp-open.json) |
| 用户启用 OpenSSH 后实际盘点 | SSH 认证、主机密钥固定、Windows/资源/服务/监听/候选目录只读检查通过 | [Windows 盘点](../../evidence/single-room-server-windows-inspection.json) |
| 首次远端脚本失败 | SSH 已认证，长命令退出 1；改为标准输入后原场景通过 | [工具失败历史](../../evidence/failures/single-room-server-windows-inspection-attempt-01.json) |
| Python 命令来源 | 两个命令均为 WindowsApps 占位入口；没有运行 Python | [只读命令发现](../../evidence/single-room-server-python-discovery.json) |
| 专属工作目录准备 | 四个无凭据文件逐项哈希一致、五个空目录、只读预检成功 | [准备报告](../../evidence/single-room-server-workspace-preparation.json) |
| 当前组件候选核对 | 133 个选定文件哈希一致；未证明原生服务端入口或最小依赖闭包 | [组件清单](../../evidence/single-room-current-component-inventory.json) |
| 远端系统变更 | 0 次系统配置修改，0 次服务重启，0 次客户端/运行库安装 | 同上；独立工作目录创建另列 |

本轮忽略目录中的盘点与准备脚本执行固定命令并记录脱敏状态；新增公开 `room_workspace_preflight.ps1` 包含中文职责与边界说明，PowerShell 语法解析及远端实际只读运行通过。没有修改业务代码、运行远端客户端或进行容量测试。

## 接手与恢复

以下保留用户已完成的 OpenSSH 接入准备步骤，供恢复和接手参考，不要求再次安装。Microsoft 要求受支持 Windows 与管理员环境；安装功能会创建 SSH 防火墙规则，具体步骤参照[官方 OpenSSH 首次使用说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)。这些设置由用户通过服务器端管理员环境完成，本轮代理没有代为执行：

```powershell
Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service sshd -StartupType Automatic
Get-NetFirewallRule -Name OpenSSH-Server-In-TCP
```

若接手时功能已安装，应跳过安装；若现有防火墙规则停用，应核对后启用该规则。服务器云安全组同时允许所需管理来源访问 TCP 22；自定义端口需同步更新配置文件。当前 `sshd` 运行及 SSH 认证已有实际证据，详细安装过程仍属于用户操作，未假定其他命令均由代理执行。

后续复用既有凭据读取方式，不将密码放入 argv、环境打印或公开报告。主机密钥已记录在忽略的 `runtime/room-server-known-hosts`，后续必须拒绝变更；不能把首次 TOFU 记录称为第三方认证。准备目录尚未安装服务，无须回退系统配置。若需要移除本次准备内容，先核验绝对路径、所有权标记、四个文件哈希及五个子目录仍为空；如已有后续数据则保留并人工核对，不递归覆盖或删除未知内容。
