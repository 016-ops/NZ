# 上游 Zone 登录通知顺序与当前样本差异

日期：2026-10-15。只读固定提交 `armangido/af-emulator@4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495`，与当前恢复样本 Zone 元数据比较；没有运行上游代码、游戏或网络，没有更改业务目录、玩家资产或客户端。证据为 [比较报告](../../evidence/aliyun-upstream-zone-notification-comparison.json)；当前结构来源见 [Zone 静态合同](2026-10-15-native-zone-login-contract.md)。

## 已核对

上游 `server/assaultfire_server_v143b.py:10580` 附近正常登录处理先发送 A001，再等待其 PH 客户端 FF05 就绪。之后依次发送：A005 玩家资料（CurRoleGID 为 0）、A006 每包最多五个道具、完整道具之后再次 A005（设当前角色 GID）、钱包/经验同步、持久流水、ZoneHints，以及存在时的旧房间恢复。首次昵称为空时则使用上游实验得到的 0x0401，并故意不发送资料。源码中这些 PH 实测说明只作为参考，不能证明当前样本的顺序、结果码或最小必需通知集。

| 位置或结构 | 当前与上游差异 | 实施影响 |
|---|---|---|
| `_v48_parse_zn_login` | 上游只读前 12 字节；当前 A000 体 18 字节，多 CurrentZoneHostIp 与 LoginPlatform | 使用当前严格解析器，账号由已认证连接绑定 |
| `_v48_build_zn_login_response` | PH 空免费道具响应体 28 字节；当前还有 IsNewbie、AreaId | 当前空列表候选体为 33 字节，须当前 DLL 独立打包及游戏消费验证 |
| `_v48_player_info` | PH 在 Experience 后接两个 datetime；当前先有 EvolutionPoint、EvolutionGameTime | 直接照搬从这里开始错位；不能靠尾部补零修复 |
| 当前 PlayerInfo | 78 个字段，含 PlayerGeniusFlagDb 的 1,024 字节嵌套标志及 CreditScore 等 | 用当前字段顺序与类型重新构建、取得当前 DLL golden |
| `_v127_build_playerprops_chunk` / 当前 NtfPlayerProps | 当前元数据独立证实 Result、IsLastPkg、PropCount、Props[5]，PropInfo 14 字段、layout 70 | 形状相近仍不证明相同业务位置、期限或资源值 |
| 上游默认道具 | 角色/背包/武器/角色部件有特定 GID、OwnerGID、Location、期限及推送顺序 | 当前资源 ID、拥有关系与默认值必须独立核对，不导入 PH 默认资产 |

上游资料初次 CurRoleGID=0、完整道具后再次发资料，是其代码记录的角色引用修复路线。可借鉴“先完成被引用对象再发布当前选择”的依赖思路，但当前客户端是否需要同样两次通知、是否主动发角色操作请求，尚未证明。

## 未实现与下一步

本轮没有实现 A005/A006 builder，也没有证实 FF05、ZoneHints、钱包、经验、流水中哪些是当前大厅最小必需集合。当前角色资源映射、昵称编码与字节限制、道具实例 GID/OwnerGID、Location、永久期限及默认武器都未完成。现有 Django 角色记录不能等同已拥有可显示的原生角色资源。

应先验证当前 A001 的消费分支，观察下一个实际请求及状态；再用当前 DLL 单独建立 A005、A006 golden，每次只增加有证据的通知并记录客户端推进。账号来源继续绑定真实自有会话；未知值放入显式版本化研究 profile，不能冒充持久资产或原版规则。完整登录与角色/大厅的最新实机状态由[主交付](2026-10-15-aliyun-email-and-native-launch.md)记录，本参考不替代实机结论。

## 验证与恢复

执行 `runtime/inspect_upstream_zone_sequence.py`：静态 AST 定位八个相关函数，记录固定源码 SHA256、调度行号、当前六类结构字段名及差异；未 import/执行上游模块，无数据库写入、客户端修改、运行时部署或回滚步骤。未证明的业务规则继续保留。
