# Launcher与Web第二轮开发交接

## 基本信息

- 日期：2026-10-20（用户时区America/Los_Angeles）。
- 分支：`fix/launcher-portal-admin`；本轮保持不部署，只操作本机隔离演示库与自动化测试库。
- 范围：邮箱注册、邮箱管理员认证、平台商城关闭、展示目录/图片、二次元主视觉与景深、统一弹窗、独立活动专题、反馈工单/私图及四份Excel。
- 详细模块交接见同目录`2026-10-20-email-auth-registration.md`、`2026-10-20-feedback-presentation.md`、`2026-10-20-frontend-account-activities-feedback.md`、`2026-10-20-portal-v2-excel.md`。

## 已实现功能

1. Web与Launcher共用邮箱验证码注册、密码确认和昵称，创建ACTIVE账号与初始角色共用事务。仅旧ACTIVE且当前邮箱已验证的无角色账号允许补建；旧待审/停用/封禁不被迁移、登录、找回或补验隐式激活。
2. 玩家商城导航、旧console购买表单及Web/Launcher商城读、报价、购买接口全部关闭，关闭门禁先于幂等回执；管理员运营与原生游戏商城独立保留。
3. 按用户最新意见沿用深色蓝灰、暖金。ImageGen根据已查看的艾米、芭比原图生成双人主视觉，背景/按钮/卡片加入有界景深和transform/opacity动效；失焦、后台、减少动画时停用。未采用粉白试稿。
4. 4323项资源展示目录独立于冻结游戏目录。已有四项核实图鉴配图，主武器/其他装备分别使用明确标注的AI分类示意；中文名称与错误文案集中解析，未知名称不猜测。
5. 全局反馈Provider持续保存提交结果。签到/领取弹窗包含重复签到、已领取/未达条件原因及奖励图文；提交中防重复，提交成功后的刷新失败不误报写入失败。
6. 管理员每次密码登录均向固定邮箱发送六位单次码；Owner重要操作另取绑定会话、动作、内容和幂等标识的独立邮件码。五分钟/六十秒重发/五错锁定；旧TOTP、恢复码、固定码和免码入口退役，0024撤销旧管理员会话。
7. “活动”中心与独立slug页面支持刷新/历史导航、三种模板预置封面、自定义公共封面、结构化纯文本区块。展示CAS与已发布奖励定义分开，草稿不公开、发布后slug固定。
8. 登录玩家可提交文字、最多三张5MiB截图、查看状态和回复；Owner/users处理，auditor只读，resources拒绝。截图真实解码后去元数据重编码，私有存储逐次鉴权；失败明确提示文字已提交、图片可重试。关闭工单只读。
9. Launcher新增固定账号代理、角色初始化、反馈multipart/私图二进制与公共封面Blob桥；研究客户端与真实发行PublicApiClient均实现，业务Bearer留在Python，不使用任意URL代理。
10. 四份Excel已更新至`outputs/portal-v2-2026-10-20/`；MySQL迁移当前模型74表469字段。部署模板补精确上传路由限额、媒体私有目录及邮箱认证前置；旧归档与旧四本书保留。

## 未实现内容

- 精确原道具图片仍缺4319项，按本轮约定先用有标记的分类示意；逐项清单`docs/assets/item-image-missing.csv`，不宣称原图齐备。
- 真实SMTP邮箱收件尚未验证；代码和模拟投递测试不能替代实际收件。
- 真实军衔、可信原生游玩时长/结算/正式榜单仍沿用首轮原生来源阻塞，不向正式榜灌入合成成绩。

## 当前阻塞

| 项目 | 证据/影响 | 解除条件 |
|---|---|---|
| SMTP未配置 | 本机没有受保护阿里云SMTP配置；管理员新登录/Owner确认/新用户邮箱注册实际发信保持关闭 | 提供受保护配置文件路径，完成固定邮箱真实收件验收；不在聊天发送密码/授权码 |
| 部署暂停 | 用户明确先修改、预览；没有远端数据库写入、服务切换或实际部署 | 用户后续明确恢复部署 |
| 原生实玩与干净Windows环境 | 便携平台包不含完整游戏客户端，可信结算与军衔来源未验 | 原Windows/原生环境专项验收 |

## 验证记录

| 验证 | 环境/证据 | 实际结果与限制 |
|---|---|---|
| 完整回归 | 本机隔离MySQL，`evidence/2026-10-20-full-mysql-tests.json` | 619项，605通过，14测试skip及6缺原件suite skip，零fail/error；此轮加载时尚未包含最后“错密码预算”增量 |
| 最后认证增量 | `evidence/2026-10-20-admin-password-budget-final-tests.json` | 14项全部通过，包含同挑战五次错密码及其他login不消耗挑战 |
| 最终同源MySQL CI | `138c66e`，[run 39402348358](https://github.com/016-ops/NZ/actions/runs/39402348358)，`evidence/2026-10-20-final-ci-verification.json` | MySQL 8.4.11；620执行、606通过、14测试skip及6缺原件suite skip；零fail/error，703.111秒，含最后错密码预算回归 |
| 最终同源Windows CI | 同一源码/同一测试run，job 118559893054 | 254执行、253通过、1测试skip及6缺原件suite skip；零fail/error。MySQL中12项Windows-only skip逐ID均在本次Windows通过集合中，差集为空；其他跳过项仍未验 |
| 反馈/活动/图片 | `evidence/2026-10-20-feedback-presentation-tests.json`及HTTP边界复验 | 35项通过；其中2项增强断言复验通过，不重复计为37项 |
| Launcher桥 | `evidence/2026-10-20-launcher-v2-tests.json` | 53项工具通过，无数据库或Windows实机声明 |
| 公共封面桥 | `evidence/2026-10-20-campaign-cover-bridge-tests.json` | 22项相邻工具通过，包含真实PublicClient回环HTTP；与前组有重叠 |
| 前端 | `npm run build`、`npm test`、专项JSON | 最终6文件21项通过，生产构建1721模块通过；补齐注册/找回确认密码，不一致不调用API、确认值不进入后端或本机桥。完整目录展示JSON有体积提示，图片只按可见卡片懒加载 |
| 文档 | `outputs/portal-v2-2026-10-20/design-workbooks-verification.json` | 四份Excel最终导出，依次8/11/9/12页；模型计数、公式/冻结/筛选校验通过，历史四本SHA未变；同源CI、归档和未验限制已写入最终验收页 |
| 中文注释 | 本轮新增模块docstring/JSDoc与权限、事务、幂等、图片/令牌边界检查 | 仅本轮改动模块整改，不宣称全项目存量注释完整 |

早期失败/中断记录单独保留于`2026-10-20-full-mysql-attempts.json`；不把局部中断exit0误计为全量通过。最终验收以`138c66e`两条同源CI为准，不能将跳过项计作通过。

## 接手与回退

- 本机Web预览`http://127.0.0.1:8876/`，Launcher布局预览`http://127.0.0.1:5174/launcher`。Mac无本机能力时允许看注册表单，提交/启动不绕过验证。
- 本轮仅迁移原任务隔离演示库`nz_fix_portal_demo`，不触碰用户提供的目标服务器配置。测试账号凭据仍留在忽略的0600文件内，管理员登录说明改为实际邮箱码。
- 后续部署必须先准备邮件与`NZ_MEDIA_ROOT`；0024已清旧认证秘密，不能直接回退到依赖TOTP的旧版。保留新增表、媒体和已撤销会话状态，详见`docs/WEB_DEPLOYMENT.md`。
- 运行源码基线：`138c66e4677fc6475626bcede083f920ba7f15e4`。交付文件提交与运行源码基线分开记录，不把文档提交误写为安装包源码。

## 本机页面验收

- 使用本机隔离演示库实际登录测试玩家，检查仓库中文名称与分类示意、重复签到结果弹窗和独立活动页。没有向正式榜单写入合成成绩。
- 实际创建测试反馈并上传一张生成的分类示意图，反馈详情显示待处理、消息和鉴权图片。该工单明确标注“本机隔离验收”，不含真实玩家问题或个人截图。
- 实际输入测试管理员账号和现有密码申请登录码，页面明确显示“管理员邮件服务尚未配置，无法完成认证”，验证码和登录按钮保持关闭；没有用旧TOTP、恢复码或模拟码绕过。截图前清空密码。
- Web与Launcher注册均可见邮箱、昵称、新密码、确认密码、邮箱验证码。Launcher在Mac无本机能力时只展示表单，提交、登录及游戏启动不能绕过固定桥验证。
- 官网与Launcher在390×844视口读取`scrollWidth=clientWidth=390`，未发生横向溢出；桌面布局另行核对。临时视口覆盖已恢复。减少动态效果与失焦停用通过源码边界核对，未冒称操作系统减少动态效果端到端实测。
- 截图目录：`evidence/portal-ui-v2/`；主要文件为`web-home-dark.png`、`web-home-mobile-dark.png`、`launcher-home-dark.png`、`launcher-register-dark.png`、`web-register-dark.png`、`checkin-result-dark.png`、`inventory-dark.png`、`activity-detail-dark.png`、`feedback-private-image-dark.png`与`admin-email-unconfigured-dark.png`。

## 本轮归档

- Web：`outputs/portal-v2-2026-10-20/nz-web-platform-2026-10-20.tar.gz`，4,241,785字节，178个业务文件及`RELEASE.json`。SHA256：`9bec06bc99bcd6a086c70b9903a31eb3a7e7361133eb29321fb7fd432581b67a`。
- 逐文件摘要、普通文件/相对路径、清单全集、私密路径排除均已核验，见`evidence/2026-10-20-web-package-verification.json`。包标记`WORKING_TREE`说明打包器读取冻结提交的工作目录，不表示部署通过。
- Windows：`outputs/portal-v2-2026-10-20/NZLauncher-DEV.zip`，26,934,988字节，761个payload及内嵌清单。SHA256：`640f97b8e43ed11e4da71a58353bc4cbae20b3d8671ddd690cb0aa8d1de14628`。运行源码同为`138c66e4677fc6475626bcede083f920ba7f15e4`。
- Windows流水线[39402348645](https://github.com/016-ops/NZ/actions/runs/39402348645)成功，逐文件大小/摘要、路径与私密边界、清单全集和最终拷贝均通过；启动/退出冒烟耗时2.272秒，API登录未执行。对应脱敏证据`evidence/2026-10-20-launcher-package-verification.json`。GitHub artifact外层ZIP摘要单列，不能当作内层交付ZIP摘要。
- Windows外置清单保留CI原始CRLF字节；`.gitattributes`对该文件禁用文本换行转换，避免Git检出后的摘要与内嵌清单不一致。
- Launcher包不含完整游戏客户端，API地址为本机占位配置；SMTP真实收件、原生登录/实玩与干净Windows机器验收独立保留未验状态。
