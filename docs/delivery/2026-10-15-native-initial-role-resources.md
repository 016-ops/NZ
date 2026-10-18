# 当前客户端初始角色资源只读核对

日期：2026-10-15。范围：当前恢复副本 `runtime/native-recovery/coherent-2020/nz`、旧提取目录的文件索引和当前 Zone TDR 元数据。目标是找到可继续核验的初始角色候选；本批不修改客户端、协议实现、业务目录或玩家资产，不运行 DLL 或游戏。

## 已完成

公开证据：`evidence/aliyun-current-initial-role-resources.json`。定点解码 7 个配置文件，全部与旧 `../evidence/private/client-catalog/json/source_files.json` 的明文 SHA-256 一致；当前原始文件均匹配恢复归档清单。旧文件与当前文件的密文不同，不能用旧密文摘要替代当前校验。

`TGame/Config/DefaultItemLibrary.ini` 的 `[UTGame.TGFPSItemLibrary]` 中有以下候选。行号指解码文本，不是密文文件偏移。

| 行 | ItemName | nItemID | Location | ResID1 | ResID2 | nItemIconID |
|---|---|---:|---:|---|---:|---:|
| 15 | GhostArmy | 100014 | 10 | `0x70000000` | 65536 | 3000088 |
| 16 | Variance | 100016 | 11 | `0x70200000` | 0 | 3000019 |

两条均为 `bDefaultItem=true`、`bHidden=false`、`ConsumeType=0`。这些字段只证明当前表中的定义，不证明应当首登赠送。物品 104192、104193、104194 同时带 `bDefaultItem=true` 与 `ConsumeType=1`，报告保留了这三条反例。

当前 `TGame/TDR/proto_c2zn.tdr`（SHA-256 `c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba`）提供了位置名称：`EPropLocationType_Role1=10`（宏描述符 `0x254`）、`EPropLocationType_Role2=11`（`0x264`）。另有 `ECamp_Rebel=0`（`0xBA84`）和 `ECamp_Regular=1`（`0xBA94`）。这些声明没有直接连起 Role1/Role2 与 Regular/Rebel 的索引规则。

`TGame/Config/DefaultTGResManager.ini` 的 `[UTGame.TGResManager]` 有明确家族路径：

| 行 | 配置键 | 当前字段 |
|---|---|---|
| 13 | ArrayFamilyPathMirror | FamilyType_Army，FamilyString=Army，FamilyID=0，FamilyPath=FamilyArmy |
| 15 | ArrayFamilyPathMirror | FamilyType_Variance，FamilyString=Variance，FamilyID=2，FamilyPath=FamilyVariance |
| 75 | ArrayCharacterMirror | CharacterSubType=FamilyType_Army，CharacterName=FamilyArmy |
| 77 | ArrayCharacterMirror | CharacterSubType=FamilyType_Variance，CharacterName=FamilyVariance |

物品的 ResID1 高位与这两个 FamilyID 数字吻合，但原生位运算和查表消费代码尚未证明这条连接；不能据此编写角色编码规则。

定点读取并核对了以下 12 个当前包，共 16,788,413 字节；全部与当前 `archive-manifest.json` 中的长度、SHA-256 一致，逐文件摘要见 JSON：

- `TGame/CookedPC/Avatar/CharacterItem/FamilyArmy.upk`、`FamilyVariance.upk`。
- `TGame/CookedPC/Avatar/FamilyArmy/` 下的 `Arms/FamilyArmy_Arms_002_F.upk`、`Arms/FamilyArmy_Arms_002_F_SF.upk`、`Body/FamilyArmy_Body_001.upk`、`torsor/FamilyArmy_Torsor_002_F.upk`、`torsor/FamilyArmy_Torsor_002_F_SF.upk`。
- `TGame/CookedPC/Avatar/FamilyVariance/` 下的 `Arms/FamilyVariance_Arms_001_F.upk`、`Arms/FamilyVariance_Arms_001_F_SF.upk`、`Body/FamilyVariance_Body_001.upk`、`torsor/FamilyVariance_Torsor_001_F.upk`、`torsor/FamilyVariance_Torsor_001_F_SF.upk`。

两个 CharacterItem 包的当前首部均为 `FFA8273E`；旧 catalog 对包的记录是 `PACKAGE_UNDECODED`。本轮未解包，不将文件名视为内部骨骼、材质或动画对象已经验证的证据。

## 未实现与当前阻塞

本轮得到两个可继续研究的候选，完整可加载 starter 数仍为 **0**。不能生成全零 PlayerInfo 来代替以下工作：

1. `C2ZN_ReqChangeRole` 的 `RegularRoleIndex`、`RebelRoleIndex` 各 2 字节；100014/100016 超过无符号 16 位范围，不能直接把物品 ID 填入索引。需要当前消费者确认索引、家族、物品及资源 ID 的换算。
2. `PlayerInfo.CurRoleGID`、`PlayerInfo.RoleType` 与 `PropInfo.GID` 都有 8 字节字段，但相同宽度和名称不证明外键、拥有关系或位集规则。仍需当前代码和实际消息核验。
3. 需要证明 Role1/Role2 与 Regular/Rebel 的对应，以及新独立账号应拥有和默认选择的外观规则。
4. 这 12 个包的存在和摘要没有证明其对象路径及依赖闭包完整。仍需定点解包或原生加载证明网格、材质、动画及附属资源可用。

上述是原生角色投影的具体证据缺口，未阻断其他独立工作。本批没有启用任何提取 ID、授予角色、修改余额或向客户端发送角色内容。

## 验证记录

使用仓库 `.venv/Scripts/python.exe`，复用 `tools.client_catalog.decode_resource/parse_ini/fields` 读取 7 个选定配置；仅从 `source_files.json` 对比对应明文摘要。按当前归档索引筛选两个家族的 12 个包后逐文件计算摘要，没有扫描其他二进制文件。复用 `runtime/inspect_current_geo_metadata.py` 的固定指纹、字段边界及名称交叉校验，选择上述 4 个宏；角色字段来源于已保存的当前 `aliyun-current-zone-login-contract.json`。

结果：配置明文一致 **7/7**；当前配置归档摘要一致 **7/7**；当前包归档摘要一致 **12/12**。本轮只新增证据与文档，没有新增业务代码，因此没有运行 MySQL、邮件、工具单测或游戏验收；中文注释审计不涉及新的代码模块。单端角色/大厅与双端验收均未通过本报告宣称完成。

## 接手与恢复

下一步优先定位 `RegularRoleIndex/RebelRoleIndex` 与 `CurRoleGID/RoleType` 的当前原生消费代码，再对两个候选做有界依赖加载核验。证据中的候选值不应直接写入业务 catalog 或玩家档案。

没有运行配置变更或数据库迁移，无需恢复客户端和数据；新增报告可独立保留。原始客户端与恢复工作副本均未写入。
