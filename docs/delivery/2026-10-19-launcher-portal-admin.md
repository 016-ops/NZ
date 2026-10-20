# 本轮开发交接：Launcher 与 Web 运营平台

## 基本信息

- 日期：2026-10-20（北京时间）；本地开发批次文件日期为2026-10-19。
- 用户范围：Launcher布局与已实现功能集成、管理员用户/仓库/商城/地图管理、开服活动、分组排行榜、Excel设计书及服务器监控。
- 最新部署约束：**本次只部署Web，用户稍后提供Web服务器配置**；不自动使用旧DEV或操作远端Windows。
- 分支：`fix/launcher-portal-admin`，从实际最新 `origin/main` 的 `57ed0beffa0adcc91a704883d9a0bbe41f73b4e5` 建立。远程无master，已按确认使用main。原 `feature/portal-activity-ui` 分支保留，未混入其0007迁移。
- 技术栈：React + TypeScript + shadcn/ui、Django、MySQL；桌面沿用原React + Tauri + 受控Python服务。使用nz-development、frontend-design、imagegen及Spreadsheets流程；DreadHunger本地仓库布局作为结构参考，原创美术见 `admin/public/assets/expedition-hero.png`。
- 主要模块：`admin/src/portal-app.tsx`、`components/platform-pages.tsx`、`components/operations-center.tsx`、`platform.css`；`revival/portal_operations.py`、`campaigns.py`、`monitoring.py`；`tools/native_launcher.py`、`launcher_runtime.py`、`package_launcher.py`、`package_web.py`；迁移0022/0023；四本Excel。

## 已实现功能

### 门户、Launcher与用户运营

1. 公共门户 `/`、上手指南 `/guide`、活动 `/activities`、先锋榜 `/leaderboards`、玩家中心 `/player`、管理员 `/admin` 与原完整工作台 `/console`。深色金色主题、原创远征主视觉；入口懒加载，构建不再出现500k块警告。
2. Launcher沿用左导航、顶栏、主内容、固定启动区结构，集成原邮箱登录、状态/日志、显示设置/启动和清理，并新增仓库、商城、活动、榜单页。远程Bearer留在Python服务，renderer只能访问固定白名单接口，不能指定远程URL/命令。
3. 管理员沿用密码+MFA；按owner/users/resources/auditor权限显示入口。用户搜索、资料/仓库中文名称、发放预览、按准确实例/版本回收、封禁/解封、撤销会话。装备、皮肤和吞噬依赖保护保留；过期物品可由管理员清理。军衔字段明确为空及 `PENDING_NATIVE_SOURCE`，不伪造等级。
4. 玩家库存读取真实本人实例，按revision锁定游标分页；商城分页，服务端报价/购买；购买与资产账本/事件事务一致，丢响应沿既有幂等合同恢复。

### 商城、地图策略、活动及榜单

1. 已有商品上下架、排序、价格与可表达期限覆盖；全商城修订CAS发布，不修改冻结目录；报价含商城修订，发布后拒绝旧报价。原生购买报价同样服从策略，原生客户端静态展示资源未全部重打。
2. Owner在55张固定房间目录内配置地图/模式/难度。创建、加入、改图和开始均校验最新白名单，拒绝给出中文警告；空保存表示全关闭，不能意外恢复默认。
3. 默认首发七图：猎场大都会113、黑暗复活节164、丛林魅影191；塔防沙漠神殿141、蔷薇庄园140、空间站168、联盟大厦96。同名160变体默认关闭，Owner可后来启用。开放不代表原生地图已可玩。
4. 活动支持草稿、修订编辑、发布、暂停、结束、封榜；五类奖励：累计签到、Launcher登录天数、游玩秒数、成功通关次数、榜奖。规则发布冻结，时间按Asia/Shanghai显示；自然日累计跨月不清零。正式活动不预设开服时间或擅自发布奖励。
5. 普通奖励玩家点击领取，唯一资格回执与资产同事务防重复；普通Web登录不累计Launcher登录。专用Launcher登录渠道不是二进制远程证明。
6. 排名按**地图×难度×实际战斗人数**分区：猎场1–8人、塔防1–4人，耗时毫秒升序；同成员组合保留最好成绩，平局稳定排序。观战不计、中途增员失格；猎场任务成功，塔防击杀boss且成功结算才能入榜。
7. Owner封榜冻结排名与获奖名单，每人每活动只取最高优先级一档；维护器逐人自动发奖，失败目标退避重试，成功者不重发。榜奖没有手动领取按钮。公开榜单只返回昵称，不返回账户ID。

### 监控与发布物

1. 独立节点登记/停用/阈值版本控制；令牌只首次显示，数据库仅摘要，审计与幂等结果不保存令牌。Agent每15秒采集CPU、内存、磁盘、网卡累计计数和显式服务状态，独立Bearer认证，拒绝重定向。
2. 序号/载荷去重、防回退；首次或重启网络速率未知，计数器复位不产生负速率。采样和收到时间都新鲜才ONLINE，60秒后STALE并清空实时值。短窗口/分钟/小时加权趋势；原始7天、分钟30天，维护器全局清理包括离线节点。
3. Web服务默认回环。公网必须设置精确 `NZ_PUBLIC_ORIGIN` 和至少32字符私有 `NZ_SECRET_KEY`；已有环境保留旧key。可显式信任本机Nginx覆盖的 `X-Real-IP` 用于登录/邮件限流，其他peer/Host或默认配置忽略转发头。
4. `tools/package_web.py` 输出白名单tar、逐文件摘要和SHA256，拒绝链接及私密材料。配套独立current Web/maintenance systemd与Nginx模板；不使用旧双profile安装脚本。详情见 `docs/WEB_DEPLOYMENT.md`。
5. Windows自包含开发包脚本/PowerShell入口/CI：Tauri窗口+PyInstaller onedir服务+公开目录+React，状态在LocalAppData，无开发仓库/系统Python依赖。当前包不含游戏客户端，启动明确 `PACKAGE_NATIVE_CLIENT_UNAVAILABLE`；不能把平台开发包称为完整游戏发行包。
6. 四本Excel：`表设计.xlsx`、`游戏服务端接口设计.xlsx`、`Web端设计书.xlsx`、`部署手册及打包清单.xlsx`。从当前模型/路由/原生协议提取；最终产物在 `outputs/01a15734-c809-7ff2-8313-f617805a4441/`，预览和提取中间件留本机。部署书以实际tar摘要为准。

### 相关基础修复

- 两份历史固定JSON的摘要基于CRLF，Git跨平台检出为LF时目录校验失败；只在能匹配原固定SHA时恢复换行，不接受其他内容变更，不改规则指纹。
- 备份恢复原生驱动JSON文本与ORM对象比较不一致；按模型decoder严格解码并支持Date编码，保持逐表内容/摘要比对，不跳过JSON数据。
- 本机系统HTTP代理未绕过127.0.0.1导致Agent断连；回环采样改为直连，公网HTTPS保留运维代理设置。
- HTTP测试关闭通过Waitress事件线程执行，修复Mac select与跨线程关闭fd的清理竞争；没有隐藏运行异常。

## 未实现内容

1. **远端Web部署与域名/证书/SMTP/实际主机监控验收**：代码、包和模板已准备，待目标配置后执行备份、迁移、切换和外部验收。
2. **Windows归档及干净机器验收**：已提供固定构建和服务bootstrap smoke步骤；本机为Mac，不把布局预览或计划输出当Windows执行证据。是否实际产包以CI/Windows报告为准，签名/升级/安装卸载未验收。
3. **可信原生结果、真实游玩时长/通关数、正式榜单和榜奖**：合成合同已验证，正式profile的 `/api/runtime/results` 明确拒绝，等待原生世界/多人/结算独立验收。禁止向正式榜灌入模拟成绩。
4. **真实军衔等级**：没有已核实持久字段/更新来源，页面显示待接入，不能从研究占位等级或资产推算。
5. **浏览器活动“发布确认”点击**未完成：自动审批将隔离QA发布误判为违背首期草稿要求，已取消且未重试。发布/领取合同已由真实MySQL自动化覆盖；玩家签到/领取使用事先存在的隔离已发布活动。

## 当前阻塞

| 阻塞 | 证据 / 影响 | 解除条件与下一步 |
|---|---|---|
| Web目标配置尚未提供 | 用户最新说明只部署Web并稍后提供服务器配置；未执行任何远程部署 | 提供地址/系统/目录/域名与受保护配置位置，继续按Web手册部署；不在聊天传密钥 |
| 无本机Windows构建/原生运行环境 | Mac只完成Launcher布局预览；未证明Windows可执行包和实际游戏启动 | 独立Windows构建/CI及干净Windows验收；本次不部署远端Windows |
| 原生可信战斗/结算源尚未通过 | 正式成绩入口 `COMPATIBILITY_BLOCKED`；沿用主分支节点世界/入局未完成结论 | 原生真实开局、boss/任务成功、稳定成员和结算事件映射后再开启正式累计与榜单 |
| 军衔原生字段缺失 | `level=null` 与 `PENDING_NATIVE_SOURCE` | 核实持久等级字段和变更路径，绑定真实游戏读回 |
| 自动审批拒绝QA发布点击 | 明确理由为首期仅草稿；已停止操作 | 该可选浏览器验收不影响自动化发布合同，正式发布需配置具体活动后执行 |

## 验证记录

测试用项目独立MySQL `127.0.0.1:3307`（实际版本26.7.0），专用可销毁库 `nz_fix_platform_tests`；数据库测试不是SQLite。UI用另建 `nz_fix_portal_demo`，所有账号/道具为隔离夹具，无真实玩家数据。凭据仅在忽略的runtime私有文件，未入库或归档。

| 验证项 | 命令 / 环境 | 实际结果 | 未验证范围 |
|---|---|---|---|
| 核心运营/活动/监控/原生相关回归 | `tools/run_tests.py --labels tests.test_portal_operations tests.test_campaigns tests.test_monitoring tests.test_portal_http tests.test_native_rooms tests.test_native_features tests.test_email_native` | 79项：78通过、1跳过；`evidence/2026-10-19-platform-final-tests.json` | 跳过项为真实Windows双客户端，不能视为实机通过 |
| 扩展回归 | control_plane/operations/native_inventory/room_access/email_verification/http/node_http/backup_encoding | 79/79通过，0跳过；`evidence/2026-10-19-platform-regression-final-tests.json` | 第一轮曾有旧断言及备份缺陷，已修并重跑；清理线程竞争另项最终验证 |
| HTTP最终复验 | http/node_http/portal_http/proxy_address | 13/13通过，无线程关闭异常；`evidence/2026-10-19-http-final-tests.json` | 公网真实Nginx未部署 |
| 备份定向 | Deployment + backup_encoding | 4/4通过；`evidence/2026-10-19-backup-profile-fix-tests.json` | 真实线上数据备份待目标配置 |
| 工具与发布安全 | launcher_runtime/portal_proxy/frozen_config/native_launcher/package_web/public_settings/proxy_address/monitor_agent | 40/40通过；`evidence/2026-10-19-launcher-tooling-tests.json` | 不连接业务库，不代表Windows实机 |
| 前端 | `npm --prefix admin run typecheck`、`npm --prefix admin test`、`npm --prefix admin run build` | 类型通过、5/5测试、Vite构建通过 | 尚无Windows WebView2实机和完整移动端矩阵 |
| Django/迁移 | `manage.py check`、`makemigrations --check --dry-run`；隔离demo迁移0001–0023 | 系统检查通过、无模型漂移、全迁移通过 | 目标生产MySQL版本与权限待确认 |
| 已登录UI | Safari + 8875隔离Web | MFA、发放/回收、封禁/解封后登录、商城上下架、白名单保存、草稿新建编辑、签到/领奖到账、1–8/1–4分组均实际操作 | 发布确认点击被自动审批拒绝；详见UI记录 |
| 监控真实采集 | 修复后的本机Agent向隔离8875服务上报 | ONLINE、CPU/内存、带宽差值、真实多点趋势、磁盘已实见；截图 `evidence/portal-ui/admin-monitoring-live.png` | 未配置服务名，无服务健康采集；远端采集器未安装 |
| Excel | Artifact Tool导出、逐表渲染、公式错误扫描 | 四本/26个worksheet已渲染；摘要 `design-workbooks-verification.json` | 不把预览当原生游戏验收 |
| 代码和注释 | 逐模块审查、`git diff --check` | 本批新增模块、关键事务/幂等/权限/恢复路径及测试有中文说明，空白检查通过 | 既有全仓中文注释未专项整改 |

测试过程注意：一次子任务未带专用库参数，重建了默认可销毁 `nz_unittest` 并因创建触发器权限不足停止；已确认与current/synthetic/pve业务库不同，没有改业务库或MySQL全局参数。随后统一显式专用库与项目隔离root测试身份；失败事实见 `evidence/failures/2026-10-19-backup-profile-fix-attempt-01.json`。不要使用该初次失败状态宣称通过。

## 接手与回退

- **Web发布**：按 `docs/WEB_DEPLOYMENT.md`；备份实际业务库，安装固定依赖，向前迁移，原子切换新release，仅current Web+maintain，精确HTTPS来源。`NZ_SECRET_KEY`沿用已有受保护配置，不能任意换key导致房间密文不可读。
- **维护任务**：必须常驻 `manage.py maintain` 才会逐人发放榜奖、重试和清理过期监控。待发不等于已到账。
- **迁移**：0022新增活动五表，0023新增监控三表；不覆盖玩家旧资产/规则/流水，不采用旧功能分支的冲突迁移。
- **Windows构建**：`python -m tools.package_launcher --api-base-url <公开HTTPS或已准备隧道>`；独立Windows执行。Mac可 `--plan` 审查，不能标记Windows通过。首次CI默认公开配置为回环18765，未配置真实玩家网关。
- **配置/数据**：SMTP/数据库/Owner MFA/监控令牌仅外部受保护文件；包没有游戏原件、SSH配置、运行库或测试凭据。隔离UI文件不用于生产发布。
- **应用回退**：停新maintain，切回旧release重启；新增表保留，不删除发奖/审计历史。业务数据恢复另行对账，不能以旧备份覆盖发布后新增资产。
- **下一步**：收到Web目标配置后完成真实部署和线上只读/专用账号验收；原生游戏接口、军衔和完整Windows分发另按阻塞逐项协调。
