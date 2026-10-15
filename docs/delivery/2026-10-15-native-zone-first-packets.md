# 当前 Zone 首包、登录请求与固定研究响应

## 目标与批次边界

2026-10-15，继续 `2026-10-15-native-zone-login-contract.md` 后续工作。旧静态报告原文保留；本文件更新当前已证与未完成项。

原始静态批次没有执行 DLL 或游戏。之后主任务的 boot19/20 实际启动了工作副本，采集第二条 TCP 的通知和登录请求；本批另执行了受限离线 DLL 请求往返，并接入其他协作检查完成的当前 DLL 响应 golden。不能再以“没有执行 DLL”描述全部后续工作。没有发送邮件、查询/修改玩家数据库或改动原始客户端。

修改范围：`adapters/cn_current/zone.py`、`tests/test_native_zone.py`；受限探针新增 `--verify-zone-login` 的 `tools/native_tdr_probe.py/.ps1`；`tests/test_native_tdr_probe.py` 的模式隔离测试；本交接与公开证据。后续 `--verify-zone-login-response` 由协作静态验证任务维护。未改 `native_email_boot.py` 或 GEO 实现，主任务负责连接状态及启动验证。

## 已完成

### 实际 Zone 请求及当前 DLL 互证

| 项目 | 当前证据 | 结果 |
| --- | --- | --- |
| `C2ZN_NtfNetBarKey` | boot19 第二 TCP 完成 AUTH/SYN/SYNACK/CHGSKEY 后的 `0x1250`，11 字节 | 当前 `proto_c2zn` 的选择器和 2 字段吻合；当前 DLL probe11 解包/重包完全一致 |
| `C2ZN_ReqLogin` | boot20 的真实 DEV 邮箱身份，经完整 GEO 后到 Zone；`0xA000`，26 字节 | 当前 DLL `native-tdr-zone-login-01`：26 → host 1,025,331 → 26；四个 DWORD 和一个 U16 逐字段验证一致 |
| `ZN2C_ResLogin` 研究 golden | 当前 DLL `native-tdr-login-response-01` 完整包、独立正文及 host 互证 | `0xA001`，41 字节/正文 33；body 与整包回解、再打包一致。这里只证明离线线格式，实机消费由主任务另行记录 |

固定组件 SHA-256 `e28a7da8e6668feb65f5573819714738bce7210b79c49dfe287a63f2be3da228`；当前 Zone TDR SHA-256 `c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba`。离线工具不调用网络 Init，不启动游戏，使用专用 x86 进程、Job 及受限 Guard；实际运行结束清理确认通过。

输入/输出摘要：

- NetBarKey 11 字节：`411481f88d63ea7ac96ed7db0d264d023d53243acd31f01e0149fb6af7b469ca`。
- ReqLogin 26 字节：`4c00864169617cf28adfedffc566c8e6867471d1836e6546a44548679b106a38`。
- A001 研究 golden 41 字节：`71c6d9b507ea51733717213d306cc2d587960275fae747474683b89515aafc2c`。

### 严格解析 API

`parse_zone_header(payload)` 只检查完整 8 字节头、`0x3243`、声明长度与总包最多 64 KiB，未知命令返回 `body_validation=UNVALIDATED`，不能当作业务接受或登录成功。

`parse_netbar_key_notification(payload)` 只接受 `0x1250`，`KeyLen` 为 2 字节计数，`KeyData` 数组最多 512 字节，正文必须恰为 `2+KeyLen`。当前实际计数 1。解析结果只返回长度与固定元信息，不输出密钥、密钥摘要或原始包体；允许语法上的零长和 512 上限，不把单次观测固化为只能 1 字节。

`parse_login_request(payload)` 只接受 `0xA000` 且正文恰 18 字节，读取 4 个 BE32 和 1 个 BE16。保留 `SubChannelId=0xFFFFFFFF` 的原始值，不转成业务含义未知的 `-1`；`CurrentZoneHostIp` 同时保留 wire 字节和 DWORD，当前 wire 为 `0100007F`，不按普通 IPv4 线序重写。CRC、频道和平台都只是请求值，不用于推断身份。请求没有邮箱、密码或 Uin，账号仍来自可信连接认证。

专用解析结果统一标记 `VALIDATED_STRUCTURE_ONLY`。没有 `authorized/login_success/character_ready/lobby_ready` 等成功推断；所有错误采用固定错误码，不回显输入内容。

### 显式研究 A001 响应

当前运行进程的代码采样定位 `HandleMessage_Response_Login`，已证明 `Result=0x8100` 进入成功初始化分支；`0x01A0/0x01A7` 落入 unknown，而非可替换的成功码。`ServerTime=0` 明确跳过时间校准。该结论来自代码分支，不能单独当作本次研究响应已在实机消费成功。

`build_research_login_response(*, profile)` 必须显式传入 `cn2020-zone-login-success-research-v1`，只输出以下固定内容：`Result=0x8100`，主/子频道均 1，ServerTime、双币、FreePropCount、IsNewbie、AreaId 均 0。它不接受账号或资产参数，不访问数据库，不授予道具、不扣减余额。当前 DLL 确认 FreePropCount=0 时省略数组，使正文为 33 字节。

`login_response_compatibility()` 每次返回独立说明，明确标注研究值、跳过时间校准、角色与钱包/库存投影未完成、大厅未验收。调用方修改说明不会改变后续编码。零币和零免费道具只属于隔离研究响应，不是当前真实钱包的投影。

### FF05 的当前静态识别

当前 `ID_C2ZN_NTF_ANTIBOTCLIVER=0xFF05`，宏记录文件偏移 `0x15AA4`，索引 5529。包体联合选择器 `NtfAntibotCliVer` 描述在 `0x23F834`；结构 `C2ZN_NtfAntibotCliVer` 头在 `0x181294`，只有 `CliVer`（type8，4 字节，offset0），字段描述在 `0x18134C`。

完整当前宏/结构名称索引没有同名 ZN2C 响应。相邻 `0xFF01/0xFF02` 为 `ZN2C_NtfAntibotMsg` / `C2ZN_NtfAntibotMsg` 双向通知，字段分别为 4 字节的 Type/GameId/RoomId/Uid、2 字节 BufferLen 和最多 32,768 字节 Buffer；不能据此伪造版本 ACK。`0xFF10/0xFF11` 是心跳响应/通知，并非 FF05 回包。初版 `aliyun-current-zone-first-packets.json` 将 FF05 保留为尚未抓到的静态字段；随后 boot22 实包更新记录在独立 `aliyun-current-zone-post-login-metadata.json`，没有覆盖初版历史。未实现 FF05 响应。

### 随后 boot22：实际消费 A001 后的请求

主任务已实际发出同一固定 A001，之后接收到 `0xA204`（8 字节、正文 0）、`0xFF05`（12 字节、正文 4），以及 5 条受限 `0xFF02`。本批只读取并校验 A204 和 FF05 两个明确文件的头、长度及摘要，没有读取或解码 FF02 正文。达到 8 条采集上限后，探针主动关闭连接；随后重连的首帧不是 AUTH，产生 `UNSUPPORTED_COMMAND`。这是采集策略与重连协议尚未覆盖的边界，不能归为原先 A001 登录失败。

当前 `ID_C2ZN_REQ_GET_PLAYERDEFINEINFO=0xA204`（宏 `0x13D54`），对应 `ID_ZN2C_RES_GET_PLAYERDEFINEINFO=0xA205`（`0x13D64`）。核对全部 1,904 个结构名和 1,175 个包体分支，**没有** `C2ZN_ReqGetPlayerDefineInfo` 结构或 A204 包体分支，与实际空正文吻合；不得给 native pack 虚构一个空结构。

A205 的 `ZN2C_ResGetPlayerDefineInfo` 在 `0x1EA40C`，有 2 字节 `Result` 和 `Info`。`PlayerDefineInfo` 在 `0xDB1B8`，含 `DataLen`（4 字节）与 `Data`（最多 3200 字节），计数字段双向引用。Data 内部格式、版本、合法空值与 Result 消费规则仍未知；没有构造或发送 A205。关联的 A202/A203 是设置同一 Info 的请求/响应，不能代替读取响应。

## 未实现与当前阻塞

- A001 已实际触发后续 A204/FF05 请求；A205 自定义玩家信息的内部格式、消费规则和响应仍未完成。此前等待服务端响应期间的有界 15 秒采集超时，不作为客户端崩溃或登录协议失败的证据。
- 新用户首登/建昵称页：已知代码有 `0x0401/0x040B` 分支，但完整 UI 行为、正确选择及后续通知尚未验证。
- 当前完整玩家资料通知顺序、最低必需集合、昵称编码/容量、角色索引与外观资源、真实钱包与库存投影仍未完成。原静态交接记录的 Django 映射缺口继续有效。
- FF05 等后续消息只识别当前结构，不猜测原版业务规则或防护响应。
- 大厅、进入地图、战斗、单端完整 PvE、双端及连续局验收仍未完成。

已证的 Zone 请求捕获、请求 DLL 往返、A001 离线序列化与成功码代码分支，从最新未完成清单移除；这些结果不等于角色/大厅或完整 M0–M5 已完成。

## 验证记录

1. `.venv/Scripts/python.exe tools/native_tdr_probe.py --output evidence/private/native-tdr-zone-login-01 --verify-zone-login`：通过，进程退出 0，未超时，Job 清理确认。首次拟使用的 `native-tdr-probe-12` 目录已存在，工具在启动前拒绝；旧目录未覆盖，改用新的独立输出目录。
2. Zone 解析专项首次 13/13；新增 A001 固定编码后，`tools/run_tests.py --tooling --pattern test_native_zone.py --output evidence/aliyun-native-zone-login-response-tests.json` 为 **16/16，0 跳过**。覆盖真实独立摘要、计数0/512/超限、未知命令隔离、类型/端序/声明长度、截断/尾随、64 KiB 边界、敏感内容不输出、请求值保留与显式 profile。
3. `tools/run_tests.py --tooling --pattern test_native_tdr_probe.py --output evidence/aliyun-native-zone-probe-boundary-final-tests.json` 为 **6/6，0 跳过**。通知、登录请求、登录响应三模式两两互斥，均不能混入 GEO 响应；任意 DLL 选项和公开输出目录在原生进程前拒绝。
4. 中文模块、类、方法说明及关键边界注释已补齐；Python AST 审计更新在 `evidence/aliyun-email-chinese-comments.json`。未运行 MySQL 或前端检查，本批没有数据库/前端变更。

## 证据与接手

- `evidence/aliyun-current-zone-first-packets.json`：当前 10 结构、已捕获两请求、FF05 静态字段；不含通知正文或主机指针。
- `evidence/aliyun-current-zone-post-login-metadata.json`：另 10 个相关结构、实际 A204/FF05 头、A204 无正文结构证据及 A205 未验证项；未读取 FF02 正文。
- `evidence/aliyun-native-zone-login-tdr-offline.json`：本批 26 字节请求的当前 DLL 实际验证。
- `evidence/aliyun-native-zone-tdr-offline.json`：协作任务完成的 11 字节通知当前 DLL 实际验证。
- `evidence/aliyun-native-zone-login-response-offline.json`：当前 DLL 41 字节 A001 独立 golden 和兼容差异。
- `evidence/aliyun-native-zone-login-consumer-static.json`：当前成功码与时间分支代码证据。
- `evidence/aliyun-native-zone-parser-tests.json`、`aliyun-native-zone-login-response-tests.json`、`aliyun-native-zone-probe-boundary-final-tests.json`：保留首轮与后续测试结果，不覆盖历史失败/成功记录。

部署没有变化；研究响应由调用方明确开关启用，外层继续做身份、连接状态和受限生命周期校验。停止探针即停止研究服务，不需要数据库回滚。客户端配置恢复由主任务的 `NativeConfigGuard` 独立验证，不能用本模块测试代替恢复验收。
