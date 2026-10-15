# 当前样本 Zone 登录、昵称与角色静态合同

> 本文保留最初静态批次的历史边界。其后实际捕获、当前 DLL 往返和研究响应进展，见 [Zone 首包与登录响应后续交接](2026-10-15-native-zone-first-packets.md)。下文“未实现”和“阻塞”是最初静态批次时的状态；最新剩余项以后续交接为准，不把后续验证混写成最初已完成。

## 本轮目标与范围

2026-10-15。为已认证 GEO 连接之后的单服务器 Zone 接入整理当前样本证据，补齐登录、昵称、角色、玩家资料与资产通知依赖；只读客户端、静态元数据及项目源码。没有执行 DLL、游戏、数据库命令或网络请求，没有读取凭据，没有改业务实现或客户端文件。

使用 `runtime/inspect_current_zone_contract.py` 在独立命名空间复用 `runtime/inspect_current_geo_metadata.py` 的固定指纹和边界校验，生成新证据 `evidence/aliyun-current-zone-login-contract.json`。原 `aliyun-current-geo-zone-metadata.json` 保留。

当前文件 `TGame/TDR/proto_c2zn.tdr` 为 3,024,238 字节，SHA-256：`c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba`，与当前恢复归档清单一致。本轮完整检查 1,904 个命名结构头和字段边界，选择导出 31 个结构、195 个字段、183 个相关宏。

## 已实现：可复查的结构证据

本轮实现的是静态证据整理，不是 Zone 服务。表中的字节大小和位置属于元数据 layout，不能直接当作抓包线序、可变长度字符串线长或全包实际长度。当前 GEO pack/unpack 的成功不等于 Zone 编码已经验证。

### 消息入口

`PROTO_MAGIC_C2ZN=0x3243`。`C2ZNPkgHead` 的 `Magic/Cmd/HeadLen/BodyLen` 四项均为 2 字节类型槽，头部 layout 为 8。包体联合体共有 1,175 个分支，本轮仅选取相关入口；没有假设所有消息都必需。

| 消息 | 当前命令 | 字段与直接依赖 |
| --- | --- | --- |
| `C2ZN_ReqLogin` | `0xA000` | `SubChannelId`、`PreferenceCrcValue`、`SystemSettingCrcValue`、`CurrentZoneHostIp` 各 4 字节；`LoginPlatform` 2 字节；合计 layout 18 |
| `ZN2C_ResLogin` | `0xA001` | `Result`、主/子频道、`ServerTime`、`TGamePoint`、`GoldPoint`、`FreePropCount`、`FreeProps`、`IsNewbie`、`AreaId` |
| `C2ZN_ReqCreateAccount` | `0xA002` | `Nickname` 字符容量 32；`LoginPlatform`、`CreatePlatform` 各 2 字节 |
| `ZN2C_ResCreateAccount` | `0xA003` | 仅 `Result`，2 字节类型槽 |
| `C2ZN_ReqHeartbeat` | `0xA004` | 仅 `Padding`，1 字节；周期及断线阈值未知 |
| `ZN2C_NtfPlayerInfo` | `0xA005` | `Result` + `PlayerInfo`，玩家资料与登录响应是独立消息 |
| `ZN2C_NtfPlayerProps` | `0xA006` | `Result` + `IsLastPkg` + `PropCount` + `Props[最多5]` |
| `ZN2C_NtfPlayerRoleType` | `0xA00D` | `RoleType`，8 字节；未确认是否位集或具体取值语义 |
| `C2ZN_ReqZoneChannelList` / `ZN2C_ResZoneChannelList` | `0xA132/0xA133` | 请求 `MainChannelId`；响应 `ZoneChannelListInfo` |
| `C2ZN_ReqCheckNickname` / `ZN2C_ResCheckNickname` | `0xA144/0xA145` | 请求 `Nickname` 容量 32；响应 `Result` |
| `C2ZN_ReqChangeRole` / `ZN2C_ResChangeRole` | `0xA146/0xA147` | 请求 `RegularRoleIndex`、`RebelRoleIndex` 各 2 字节；响应 `Result` |
| `ZN2C_NtfKickOff` | `0xA1FF` | `Reason`、`FreezeLeft`、`ReasonLen`、`AntibotPunishReason[最多256]` |
| `C2ZN_ReqChangeRoleName` / `ZN2C_ResChangeRoleName` | `0xF301/0xF302` | 请求 `NewNickName` 容量 32；响应 `Result` + `NewNickName` |
| `ZN2C_NtfZoneLoginRefuse` | `0xFF12` | 仅 `Reason`，4 字节；这是另一个独立通知 |

登录请求没有邮箱、密码或账号 ID 字段。后续服务应以已验证的连接身份绑定业务账号，不能从请求中的频道、IP 或平台推断身份。该判断来自完整的当前 `ReqLogin` 五字段描述，不是 PH 响应模板。

### 登录、玩家资料与道具依赖

- `ZN2C_ResLogin` 结构头文件偏移 `0x1DEAB0`，10 字段。`FreePropCount` 与 `FreeProps` 相互引用，数组上限 256；`PropInfo` 每项 layout 70。这里的免费道具列表不能替代 `NtfPlayerProps` 的每包最多 5 项库存投影。
- `PlayerInfo` 结构头 `0x24C18`，78 字段，两个大小槽分别为 1379/1410。主要包括 `Uin`、`VerifyKey`、`NickName`、四种点数、经验与等级、`CurRoleGID`、`RoleType`、战队与成就、时间、封禁信息、活动/VIP/信用字段。它还嵌套 `PlayerGeniusFlagDb` → 8 个 `PlayerGeniusFlagGroup` → 每组 128 字节。元数据没有证明这些字段全零时客户端可以进入大厅。
- `PropInfo` 头 `0x2AA7C`，14 字段：`Uin`、`GID`、`ItemId`、`ResFlag`、`StackNum`、`ObtainTime`、`Validity`、`Durability`、`DurabilityMax`、`OwnerGID`、`Location`、`AvailbilityHour`、`UseHour`、`GainType`。字段名中的拼写按当前样本保留。期限单位、永久值、资源标志和位置枚举仍未知。
- `ZoneChannelListInfo` 有 `IsFirstPkg`、`IsLastPkg`、`ZoneCount`、`Zones[最多5]`。`ZoneChannelInfo` 包含主频道 ID、IPv4、容量 128 的 Domain、Port、`Channels[最多16]` 和 `FalseChnls[最多5]`。`ChannelInfo` 有 ID、容量 32 的 Name、PlayerCapacity、总人数及四个人数分类。容量均为 schema 上限，不能当作多人验收人数或实际开放频道。
- `PlayerPreference` 的 CRC、模式参数和地图索引/标志可由元数据关联；`PlayerSystemSettingData` 有两个 1 字节字段。但 CRC 算法、协商流程、零值行为和配置下发顺序未确认，不能默认忽略请求的两个 CRC。

### 成功、错误与平台枚举

以下数值均从当前 TDR 宏记录读取，文件偏移在 JSON 中保留。它们证明声明存在，尚未证明每个响应具体使用哪个宏。

| 当前宏 | 数值 | 使用限制 |
| --- | --- | --- |
| `ZONE_ERR_SUCC` | `0x8100` | 不能把所有结果都按通用 0 成功；也不能仅凭名称确认它就是 `ResLogin` 成功分支 |
| `ZONE_ERR_FAIL` | `0x0100` | 通用 Zone 失败声明 |
| `ZONE_FAIL_BADNICKNAME` / `NICKNAMEEXIST` | `0x0101/0x0103` | 可作为昵称校验结果的候选，仍需消费路径确认 |
| `ZONE_FAIL_NOACCOUNTEXIST` | `0x0104` | 与首登行为有关的候选之一 |
| `ZONE_FAIL_INVALID_ROLE` | `0x01A5` | 无效角色声明；合法索引及拥有条件未知 |
| `ZONE_LOGIN_NORMAL` / `ZONE_LOGIN_SWITCH_ZONE` / `ZONE_LOGIN_NEWACCOUNT` | `0x01A0/0x01A1/0x01A7` | 正常/切区/新账号声明；当前客户端分支尚未定位 |
| `DLH_FAIL_PLAYERNOTEXIST` / `DLH_FAIL_WAITCREATEACCOUNT` | `0x0401/0x040B` | 与 Zone 宏分属另一类；不得直接混用为首登响应 |
| `ZONE_LOGIN_REFUSE_ZONE_FULL` / `LIMIT_BY_PRIVATE_MATCH` | `1/2` | 对应独立拒绝通知的候选原因 |
| `ELoginPlatform_None/ShunWang/ShunWang2/YunGengXin` | `0/1/2/3` | 当前可见登录平台声明；没有“邮箱平台”枚举，不能自行追加数值 |

还存在 `NAME_SUCCESS=1` 等名字服务宏，这不等于 `ResCheckNickname.Result` 使用 1。必须由当前消费代码或当前端真实请求/响应确认其路由。当前静态检查未确认邮箱新用户需要 `0x0104`、`0x01A7`、`0x040B` 中哪一个首登分支。

## Django 映射与缺口

| 目标 | 当前来源 | 可以复用的部分及仍需补齐项 |
| --- | --- | --- |
| 连接账号 → `PlayerInfo.Uin` / `PropInfo.Uin` | `Account.game_id`；`revival/launcher.py:identity` 返回已校验身份 | 已有独立整数 ID，能在认证后绑定，不使用邮箱或历史 login 代替 Uin。64 位 native 字段的具体线序仍需 Zone pack 验证 |
| 首登是否已有业务角色 | `Character` 一账号一条；`identity.character` | 可以区分不存在/已创建；客户端哪个结果进入建角页和哪些后续通知必需，仍未知 |
| 昵称创建与唯一性 | `revival/service.py:character.create`；`Character.nickname` 唯一约束 | 可复用事务、NFC、幂等与唯一约束。当前 web 允许 24 字符、48 UTF-8 字节；native 容量只有 32，编码及 NUL 规则未证实，需显式兼容规则并验证已有昵称，不得静默截断或重命名 |
| `TGamePoint` / `GoldPoint` | `Wallet(currency,balance,version)` | 余额与账本可复用，但 `config/cn-current.json` 的 currency_mappings 为空；`NZ/GP` 仅为 test-only-v1 的合成币种，不能依据名称擅自映射 |
| `PropInfo` | `Inventory`、`Equipment`、已隔离的提取目录 | 有归属、数量、期限和装备槽位；缺少已核验 native ItemId、数值 GID/OwnerGID、资源标志、耐久、Location、获得时间/方式、期限单位映射。业务实例 ID 是字符串，不能随意转整数当 GID |
| 角色外观/阵营 | `Character` 当前只有昵称、状态和 profile | 缺少 Regular/RebelRoleIndex、CurRoleGID、RoleType、拥有/可选角色与资源映射；“建业务角色完成”不能当作“已有可加载游戏外观” |
| 时间、制裁 | `Account.created_at/last_login`、`Sanction`、安全版本和会话撤销 | 来源可复用；ServerTime、PlayerInfo 时间类型的单位/日历编码、冻结与封禁展示字段仍需确认。授权必须继续实时复核，不能仅显示封禁字段 |
| 主/子频道及人数 | 房间 channel 字符串、目录配置 | 现有 Channel 没有原生数值映射；需版本化单服主/子频道及容量配置，和 GEO 宣告端点保持一致，不从用户输入直接开放任意频道 |
| 经验、等级、VerifyKey、功能位/VIP/信用与其他资料 | 当前 Character 没有对应持久字段 | 缺少模型/规则；不能把全零假数据宣称为真实账号资料。最小兼容 profile 必须逐项写明差异并经当前客户端验证 |

主要源码核对：`revival/models.py` 的 Account/Character/Wallet/Inventory/Equipment；`revival/service.py` 的 `character.create` 和 `_user_view`；`revival/launcher.py` 的 `source_session`、`identity`；`config/cn-current.json` 和 `config/test-profile.json`。没有查询真实玩家行或泄露账号凭据。

## 未实现

Zone transport 实例与登录请求接收；Zone 当前 DLL pack/unpack golden；昵称线编码和字节边界；新账号/已有账号分支；完整玩家通知顺序与最低必需集；角色资源映射；真实钱包/库存投影；大厅显示、单端完整闭环、双端验收。本轮没有新增这些能力开关。

## 当前阻塞与解除条件

1. 目前结构来自当前文件，缺少当前 Zone 应用包与消费分支实证。先完成 GEO 定向到受控 Zone 端点并捕获真实 `ReqLogin`，随后用相同当前 DLL 对最小 Zone 样本 pack/unpack 严格回环。
2. `ResLogin` 的正常/新账号结果码仍有多个候选。需定位当前消费者或者以隔离、可恢复试验验证，禁止套用 PH 固定响应或猜测 0 成功。
3. 原生角色、道具和币种尚无核验映射；现有数据库持久化不能补出缺失规则。需要当前资源定义、已验证 ID 和最小兼容规则版本，不能直接启动合成目录值。
4. 昵称容量与现有 web 规则存在明确差异。确认当前字符串编码、长度前缀与 NUL 后，补边界检查和老昵称处理；不得覆盖原昵称数据。

## 验证与接手

执行 `.venv/Scripts/python.exe runtime/inspect_current_zone_contract.py` 成功：31 结构 / 195 字段 / 183 宏，指纹与归档一致，读取后重新核对源哈希。只解析有限文件及索引引用；未做全树二进制扫描。

不需要迁移、重启或回滚；新增证据和本交接文件不改变运行行为。临时研究脚本位于被忽略的 `runtime/`，公共 JSON 保留基础解析器路径及摘要、全部字段文件偏移和保留边界，便于接手复查。中文说明已检查；本轮未修改业务代码，未声称完成全仓注释整改。
