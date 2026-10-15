# 原生客户端恢复与启动图后退出排查

## 基本信息

- 日期：2026-10-15（北京时间）。本批处理 Win32 `0xC000007B`、Win64 全零及“文件已损坏或丢失”；原客户端登录、大厅和战斗仍未验收。
- 涉及 `tools/native_image_audit.py`、`native_recovery.py`、`archive_client_workspace.py`、`native_job.py`、`native_probe.py`、`tests/test_native_images.py`、测试入口及 CI 依赖。
- 遵循仓库 nz-development 规范；本批无数据库或 DEV 部署变更。

## 已实现功能

找到了上级原有的 `C:\Users\ZJH\NZ\2020逆战.rar`。此前“现有材料不能恢复”的判断范围过窄，现已更正：该归档可恢复一个独立的 **2020 Win32** 候选客户端，不能恢复归档中不存在的 Win64。

归档大小 18,336,656,429 字节，SHA256 `a7a25276d4afcf9361cb80d02f8f63b1866f1db4439223c726ff51a799eeeb03`；含 8,982 个文件，解出 30,299,626,336 字节，**逐文件 CRC 全部一致**，同时生成 SHA256。CRC 证明与所持归档一致，不能单独证明厂商来源或运行兼容。

完整恢复副本：`runtime/native-recovery/coherent-2020/nz`。首次清单为其上级 `archive-manifest.json`，SHA256 `3ead163a832764a8d0f085c3fd52da0c46fd6f9ffcaa2ece53b05cb51d086e9c`。运行会生成保护组件和启动器缓存；首次清单是解包基线，不表示运行后整个目录仍等于归档。

| 文件 | 旧目录字节数 | 归档字节数 | 实际对照 |
|---|---:|---:|---|
| Binaries/Win32/TGameBase.dll | 34,440,256 | 35,847,832 | 旧文件为归档前缀，缺 1,407,576 字节；两个 PE 节的磁盘数据越界 |
| Binaries/Win32/GameLoader.exe | 4,495,936 | 5,574,736 | 旧文件为归档前缀，缺 1,078,800 字节 |
| Binaries/Win32/WebBrowserControl.dll | 322,048 | 2,573,672 | 旧文件为归档前缀，缺 2,251,624 字节 |
| Binaries/Win32/TGame.exe | 18,161,216 | 17,959,576 | 归档文件为旧文件前缀，旧文件另附 201,640 字节；不猜测附加内容用途 |

四个归档文件的 Windows Authenticode 检查均为 Valid，旧 TGame.exe/TGameBase.dll 为 NotSigned。旧 DLL 的非执行镜像映射返回 WinError 193，恢复文件映射成功；只恢复该 DLL 后原先立即发生的 `0xC000007B` 不再出现。见 [镜像报告](../../evidence/native-repair-image-audit.json)。这不是将所有 `0xC000007B` 都归因于同一 DLL，也不代表大厅通过。

旧 Win64/TGame.exe 共 61,762,088 字节且全部为零，仍不是 PE；本批不伪造入口、不把 x86 改名当作 x64。归档不含 Win64，后续可复测路线为 Win32。

归档成员与旧目录按大小、缺失和全零快速对照，共 6,002 个成员存在差异。这不等于 6,002 个文件全部损坏：部分可能是版本混合，且未核对旧目录全部同大小文件的 CRC。两处 version.ini 均标识 2020201024，仍不足以排除混合内容风险，因此后续使用完整副本。

工具已实现：PE 节边界和架构检查；仅固定四路径的可验证覆盖恢复；新目录解包、路径/链接/ADS/设备名/重复项检查、空间预留及全量 CRC/SHA256；标准完整 argv0 的客户端模式；Windows Job 跟踪与清理整棵派生进程树，并保存可取得的子进程退出码。父入口退出 0 不再被当成整个游戏正常。Job 无 UI/内存/CPU 限额，仍属于探针条件，不能替代正常交互验收。

## 未实现内容

1. 原客户端独立账号登录、角色及大厅交接；现有 Django `/api/command` 和 DEV 网页不会自动变成此版本客户端认证/区服协议。
2. 当前样本真实消息帧、版本校验、启动器交接材料、认证请求响应与实例就绪；不猜测或沿用 PH 消息号、补丁地址。
3. 原生地图、出生、战斗、资产投影、可信结算、M5 全图及双端验收。
4. Win64 入口恢复；本次只有归档 Win32 候选。

## 当前阻塞

用户随后确认没有另一份启动器，明确要求移除腾讯 QQ 认证、改用自建邮箱登录。后续不再要求取得腾讯原启动器作为前提：改为独立邮箱身份和自建启动交接，见 [邮箱登录交接](2026-10-15-independent-email-login.md)。下列 HashMismatch 与 TCLS 失败保留为原路径历史证据；新路线的原生大厅适配仍须单独实现，不能因邮箱 API 通过而解除。

后续已抓到明确日志：`TenproxyLog/20261015_122710.log`（GB18030）显示共享内存打开和映射成功，随后两次报 **“TCLS版本0不兼容Proxy32”**，接着 **`[CTclsProxy::GetLoginInfo]GetLoginInfo Fail`**。对应 PID 14468 的退出码为 `0x00000999`；这是观察到的厂商进程退出码，未将它擅自解释为 Win32/NTSTATUS 错误。直接 TGame 入口没有取得有效 TCLS 登录交接信息；尚未证明登录版本字段为何为 0，也没有通过修改共享内存伪造身份。

核对真正的 `TCLS/Client.exe` 后发现：其 manifest 同样要求管理员权限，**Authenticode 为 HashMismatch**。该文件在旧目录与归档中 SHA256 都是 `37baa8b4c908b98bcf12fb44fdaef688096f2e645ee5ef81c4f50ac8e0f0b264`，因此不是本次解包造成的不匹配。CRC 全通过不能把这一启动器称为未修改原版；本批没有执行它或把它提权。TCLSClient.dll、SSOPlatform.dll、SSOCommon.dll 及上述四个 Win32 恢复文件签名均为 Valid。

| 项目 | 证据与影响 | 解除条件 |
|---|---|---|
| 启动图后未进登录界面 | 用户确认看到逆战图片/进度条，无账号框；标准客户端入口一次约 25.5 秒自行退出，早于 60 秒清理期限 | 查明子进程退出原因、验证正常上下文并取得实际登录请求 |
| 启动权限 | TGame.exe manifest 为 requireAdministrator；当前工具令牌不是管理员，RunAsInvoker 仅为普通权限诊断 | 正常管理员启动须由用户处理 Windows UAC；不能直接把普通权限失败当成资源损坏 |
| TCLS 启动器与交接 | Client.exe 签名 HashMismatch；直接 TGame 的代理日志为版本 0 / GetLoginInfo Fail | 取得可核验的同版本启动入口，或先审查当前修改来源并在可恢复环境验证；不能靠直接 TGame 绕过启动认证 |
| 原生协议 | 当前版本登录/区服/大厅无真实回放，尚未连接业务账号 | 取得可复现消息及会话契约，再接独立业务适配 |
| 系统隔离与双端 | 隔离恢复及双端实机仍未准备完成 | 按原交付关卡执行，启动图不能代替 |

本批没有主动安装、关闭或绕过保护驱动，没有更改系统兼容设置或运行 PH 补丁启动器。游戏自身在工作副本生成 ACE-DRV32.dll、缓存等文件，完整清单保留在私人报告。没有管理员启动通过结论。

## 验证记录

| 验证项 | 环境及步骤 | 实际结果/边界 |
|---|---|---|
| 完整归档 | Windows tar.exe + rarfile 4.2；逐成员 CRC/SHA256 | 8,982 文件通过，约 296.7 秒；不等于运行通过 |
| 非执行映射 | 旧 DLL / 归档 DLL / 全零 Win64 | 193 / 成功 / 193；不执行 DLL 初始化 |
| 地图探针 | 完整副本、历史 argv0、普通权限、Job 观察 300 秒 | 父入口退出 0，观察到 6 个 PID；末尾仅 GameLoader 存活，限时清理通过；无地图/出生/网络证据 |
| 普通客户端 | 完整路径、无 server 参数、普通权限、最多 60 秒 | 首次约 25.515 秒全组自行退出，未触发超时，清理通过；用户看到启动图，无账号框 |
| 带退出码复测 | 相同普通入口，实际记录子进程退出 | 60.063 秒到期仅 GameLoader 残留并清理；自然退出码 0、3、2、2457，清理退出码 0xE0000001 单独区分，查询错误为 0；抓到上述 TCLS 日志 |
| 工具回归 | `python -m tools.run_tests --tooling --output evidence/native-repair-tooling-tests.json` | 20 项通过，无跳过；含真实 Windows 父退出/子存活/退出码/清理测试 |
| 失败保留 | 退出码记录的两次初始回归 | 失败报告在 evidence/failures/native-repair-exit-tracking-attempt-01/02；修复父登记时机及 Job 移除 PID 早于句柄触发的竞态后通过 |
| 代码和依赖 | 本批 Python AST 中文 docstring、pip check、git diff --check | 通过；未声明全仓库注释整改完成 |
| MySQL、前端、DEV | 本批无业务、迁移或部署修改 | 此前 MySQL 102 项及前端/DEV结果不计作本批新实机证据 |

私人报告：`evidence/private/native-repair/coherent-2020-*`；公开 [恢复摘要](../../evidence/native-repair-summary.json)。对应时间内未查到 TGame/GameLoader 的 Application Error/WER 崩溃条目，不能据此证明正常退出。

## 接手与回退

- 首选目录：`C:\Users\ZJH\NZ\2020逆战\repository\runtime\native-recovery\coherent-2020\nz`。不要用旧混合目录再次覆盖此树，不将运行缓存标记为归档文件。
- 普通权限诊断（在 repository 执行，输出目录必须不存在）：`.venv/Scripts/python.exe -m tools.native_probe --workspace runtime/native-recovery/coherent-2020/nz --architecture Win32 --mode client --as-invoker --visible --seconds 60 --output evidence/private/native-repair/NEW-RUN`。这不代表正常管理员启动方案。
- 原 `nz/` 和 RAR 未写入。覆盖仅在旧工作副本，旧字节及 APPLIED 凭据位于 `runtime/native-recovery/backups/` 与 `archive-four-files-receipt.json`。撤销覆盖前逐条核对备份摘要，只恢复对应目标并撤销覆盖登记；这会回到已知截断的旧基线，不是推荐启动方式。
- 完整副本独立，不需覆盖原目录回退；保留归档、首次清单与失败证据。为腾出空间，对旧工作副本 180 个全零文件做无损 NTFS 压缩并逐一核对 SHA256，内容未变。清理前核对路径和备份，不删除原件。
- 数据库迁移：无。DEV 服务和用户 MySQL 未改动。
- 下一步：确认正常启动上下文及启动器交接，取得当前版本认证/目录消息，完成原客户端登录，再推进原生 M3–M5。
