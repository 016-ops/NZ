# Launcher图形诊断、恢复与全目录操作核查

**后续更新：**[直接窗口显示桥与Launcher集成](2026-10-16-frame-bridge-experiment.md)已恢复当前构建单端大厅交互，并成为当前电脑默认显示模式。以下保留早期失败历史；矩阵当前投影范围也已在后续03版更正为4323，旧接口2466。

日期：2026-10-16。接续[React + Tauri交接](2026-10-16-react-tauri-launcher.md)、[原功能台账](2026-10-16-native-features-development.md)。本批不更改玩家资产或数据库结构，原有4323项权益和GID沿用。以下明确区分独立图形程序、真实游戏和用户ToDesk观察。

## 已实现

- `tools/native_d3d_present.cpp`和`tools/native_d3d11_present.cpp`建立独立x86诊断；仅绘制自身窗口，分别核对设备创建、颜色回读、Present成功及遮挡状态。`tools/build_graphics_diagnostics.ps1`可重建。D3D11的`--observe`保留十分钟，避免用户没赶上自动关闭。
- React + Tauri统一入口保持`启动Launcher.cmd`。新增标准/试验显示模式，标准默认；“图形接口存在”与“实际画面可见”分开显示。试验模式未恢复游戏，页面明确提示这一事实。
- 固定官方DxWrapper v1.9.8700.25包，压缩包SHA256 `4680618ac5563d2c4042e344402eae76a55192634a3746e8fd672789089067e1`，各成员另行校验；许可证完整保留。仅往工作副本添加d3d9.dll、dxwrapper.dll、dxwrapper.ini和许可证，预检全部目标，已有文件拒绝覆盖。
- `NativeConfigGuard`向前兼容v1恢复计划，v2记录原来不存在的文件；持久登记先于写入，失败后可重启恢复。删除使用同一Windows独占写/删除保护句柄校验内容，防止检查后文件被替换。修改过的文件保留并报冲突。
- 游戏初始化三分钟超时：转交进程残留不能让Launcher永久显示启动中；超时结束本轮Job、恢复配置、撤销来源/游戏凭据，再显示`CLIENT_STARTUP_TIMEOUT`。首次仓库同步后不限制游戏时长。
- `tools/native_capability_matrix.py`产生全部4323项逐物品核查表，含427个隐藏项、操作信号、原生请求启用状态、规则、持久表、配方/皮肤/吞噬关系、商城关联和实测状态。当前有效证据为`evidence/2026-10-16-inventory-capability-matrix-02.json`；01版的抽奖表名说明错误，02改为实际native_receipts，旧证据保留。

## 实际图形结果

| 对照 | API/回读 | 实际画面 |
|---|---|---|
| 系统D3D9 DISCARD | Create/Clear/readback/Present成功 | 空白；用户确认白色或桌面残影 |
| 系统D3D9On12 DISCARD | On12接口验证、颜色回读、Present成功 | 仍白色 |
| D3D11硬件 DISCARD | 回读正确、3876次Present成功 | GDI蓝条正常，GPU区域灰色 |
| D3D11 WARP DISCARD | 回读正确；0次Present成功，3497次DXGI_STATUS_OCCLUDED | GDI蓝条正常，GPU区域灰色；不能写为Present成功 |
| D3D11 FLIP_DISCARD | 回读及呈现成功 | 截图蓝绿正常；重开十分钟观察后，用户明确确认ToDesk中能看到蓝绿 |
| D3D9Ex FLIPEX | 回读及3863次Present成功 | 截图绿色正常，未单独取得用户确认 |
| 自有D3D9程序加载DxWrapper强制FlipEx | 回读及7025次Present成功 | 截图绿色正常；JSON的system-d3d9只是API选择，实际加载了本地wrapper |

证据均在`evidence/2026-10-16-d3d*.json/png`。最新用户确认窗口为`d3d11-flip-observe-02.json`和`d3d11-flip-user-confirmed.png`，8046次Present成功，零失败。上一轮窗口关闭导致用户没看到，不计入用户通过证据。**目前只证实呈现方式相关，尚未证明驱动、ToDesk或系统状态中的哪一项是原始触发原因。**

## 真实游戏失败及修正

1. `launcher-20261016-140506-558862`：v1配置错误地用逗号列进程名，上游按整行匹配，实际未启用预期选项；游戏完成4323库存同步仍黑屏。不得把这轮当作有效FlipEx对照。清理通过，graphics-live-01。
2. `launcher-20261016-140935-052669`：v2逐行白名单生效，初始化阶段未完成区服握手；原测试断言失败后清理通过，graphics-live-02。
3. `launcher-20261016-141414-542486`：开启本轮诊断日志，发现`D3D9Viewport.cpp:330 / D3DERR_INVALIDCALL`。日志确认FlipEx设备已创建；不能仅凭窗口消失认定认证或数据库故障。graphics-live-03。
4. `launcher-20261016-142347-782974`：v3将WrapperMode改为AUTO，消除错误模式提示；图形调用错误仍存在。实际三分钟后自动退出，ERROR/CLIENT_STARTUP_TIMEOUT，配置、四个新增文件、Job和两种凭据清理均通过，原始传输文件0。graphics-live-04。
5. `launcher-20261016-143024-339169`：标准模式再次完成登录和4323库存同步，computer-use尝试Alt+Enter后窗口和黑屏状态未改变，未宣称已成功切换全屏。父管道关闭后进程、配置、凭据均清理；graphics-standard-05及game-standard-alt-enter.png。

未修改游戏EXE或系统图形库；没有更新驱动、重启ToDesk或更改全局显示设置。原生呈现参数与FlipEx约束的适配仍待查清，不将独立诊断成功替代游戏通过。

## 验证记录

- 恢复守卫17项、Launcher16项、完整工具203/203，0跳过；`graphics-overlay-guard-tests-02`、`launcher-tests-07`、`native-feature-tooling-all-07`。
- React TypeScript/Vite构建通过：`index-BJ3bR9yk.js`；Rust release边界测试1/1及重建通过。
- 新EXE SHA256 `67cdca8c6a34e2a231da7b18e087e7e3a6449d246b596a95a407fd481ff2789b`。
- 新增模块、关键恢复/限时分支及测试均有中文说明；未宣称全仓注释整改。`git diff --check`通过，仅已有CRLF提示。
- 新版Tauri真实显示、标准默认和“兼容模式（试验）”文字已用computer-use核验，未操作登录表单；截图`tauri-launcher-graphics.png`。8个Python文件105个说明对象中文docstring检查通过；公开792文件已知28项秘密扫描零命中，范围和排除项见`launcher-graphics-comments`/`launcher-graphics-public-secret-scan`。
- DEV发布`20261016T063647-3c9631c0298b`，104个白名单文件，前端HTML/JS及业务源码摘要回读一致；四服务active、两健康端点正常、迁移至0016、4323库存/4323绑定、正式签到启用数0。证据`launcher-dev-deploy-04`/`launcher-dev-readback-04`。
- 本批无MySQL业务逻辑和迁移变更，未用203项纯工具测试替代数据库回归。此前0016、18项MySQL及DEV180秒租约验证见前一交接。
- 全目录静态核查：4323项、427隐藏项；当前登录投影覆盖2466项，装备入口明确开放1100项。127项有升级标志或配方关联，72种主体有皮肤关系，6种主体有吞噬关系。仍有大量未实现/未实测项，不能因矩阵存在就宣称功能全覆盖。

## 未实现与阻塞

- 游戏标准模式空白和FlipEx初始化错误仍未关闭，影响所有原生UI及局内验证。解除条件：真实游戏稳定绘制并完成原场景重测。
- 原计划的全部使用/升级/分解/兑换/重生、隐藏槽、商城细则、战队资金/扩容/徽章等仍是开发待办，不归咎于图形阻塞；矩阵给出逐物品起点。
- 原生服务端入口、资源闭包和第二客户端条件继续未满足；战斗、地图难度、跨网络、双房隔离和十局验收未通过。
- 正式签到保持停用；此批没有更改奖励配置。完整计划尚未交付。

## 接手和恢复

标准启动用React + Tauri入口，图形试验不要设为默认。构建命令及组件校验见[启动手册](../native-launcher.md)。故障先查私人运行目录和持久恢复计划，不能用旧数据库或旧库存快照覆盖新写入。此批没有Git提交、推送或合并。

参考：[微软D3D9交换效果](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3dswapeffect)、[DxWrapper官方发布](https://github.com/elishacloud/dxwrapper/releases/tag/v1.9.8700.25)、[官方配置说明](https://github.com/elishacloud/dxwrapper/wiki/Configuration)。实际行为以本机证据为准。
