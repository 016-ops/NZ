# 门户与 Launcher 前端验收

2026-10-19（America/Los_Angeles）。全部写操作仅针对 `http://127.0.0.1:8875` 的新建隔离 QA 数据库。真实凭据、TOTP、会话与节点令牌不包含在本目录中。

具体结果见 `qa-results.json`。管理员发放/回收、封禁/解封、商城上下架和改价、地图策略、活动草稿新增/修改，以及玩家签到和普通奖励均通过真实页面操作。独立只读 API 回读进一步确认浏览器改价已落库。监控截图包含修复采集器后的真实 CPU、内存、带宽和磁盘数据；没有将缺失指标补成零。

活动发布最终确认受到自动审批拒绝，原因是用户要求本期活动保留草稿。未重试或绕过该限制，新增 QA 活动仍为草稿。玩家领奖使用的是测试准备阶段已存在的已发布隔离 fixture。浏览器发布、暂停、结束、封榜不作为已验收项。

`launcher-*-layout-only.png` 仅为 Mac 浏览器下的界面预览。没有本机启动能力，因此明确显示受限入口并禁用登录和启动；它不证明 Windows 安装包或原生游戏可用。

最终前端检查：`npm --prefix admin run typecheck`、`npm --prefix admin test`（5/5）、`npm --prefix admin run build` 均通过。最后修正活动显示时区为显式 `Asia/Shanghai`，并增加北京/洛杉矶跨日回归；部分先前截图的活动时间来自修正前的浏览器本地时区，不能据此更改服务端定义。新增源码及涉及旧模块含中文职责、状态、权限、幂等、快照和异常注释；未声称完成其他存量模块整改。

## 原创图片来源

使用内置 `image_gen`，未使用 CLI、第三方素材或 API Key。最终资产为 `admin/public/assets/expedition-hero.png`（约 2.2 MiB），供门户、玩家中心及 Launcher 共用。生成文件已复制到仓库，不依赖 `.codex` 缓存路径。

最终提示词：

> Use case: stylized-concept
> Asset type: original wide hero illustration for a Chinese co-op FPS launcher and private community portal.
> Primary request: premium cinematic game key art, an original futuristic expedition soldier in weathered gunmetal armor, seen from a low three-quarter angle on the RIGHT HALF of frame, holding a restrained modern sci-fi rifle pointed down. Vast ruined metropolitan avenue and a distant colossal mechanical gate, pale cold blue shafts of morning light and a few warm gold embers. The LEFT HALF should be dark atmospheric negative space for separate HTML headlines. Composition feels like a carefully art-directed AAA cooperative PvE campaign, mature and optimistic, not horror, no gore. Ultra-wide landscape composition, rich material detail, crisp focal soldier, softer distant architecture, cool slate and cobalt palette with restrained brass highlights.
> Constraints: no text, no letters, no wordmarks, no logos, no watermark, no existing game characters or recognizable weapon skins, no UI, no frame or card borders. Fully original artwork. Return a landscape image suitable for web.
