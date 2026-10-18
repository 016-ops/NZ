# React + Tauri 桌面 Launcher

从工作区根目录双击 `启动Launcher.cmd`，或直接打开 `repository/launcher/bin/NZLauncher.exe`。这是 React + Tauri 原生桌面窗口，使用已经注册、验证邮箱并创建角色的账号登录，再点击“启动游戏”。普通玩家与GM使用同一入口。当前电脑默认“兼容显示”已完成真实大厅、仓库、好友面板和商城页面交互验证；仍是DEV版本，全部业务及战斗验收尚未完成。“仓库已同步”仅表示协议阶段，不自动证明画面就绪。

## 启动与退出

- 保留Launcher窗口。页面会显示连接、账号验证、客户端检查、仓库读取、进程启动和区服连接阶段。
- 从游戏内正常退出后，等待Launcher显示“游戏已退出，清理完成”。也可点击“停止本次启动 / 游戏”结束本次启动器创建的游戏进程。
- 点击“关闭Launcher”或窗口右上角关闭均先完成清理。游戏退出会同时注销本次账号，下次启动重新登录。
- 重复双击入口会重新打开当前Launcher，不会重复启动游戏。不要直接双击TGame或GameLoader。
- 游戏进程启动后，三分钟内未完成区服与首次仓库初始化，会结束本轮自有进程并进入配置/凭据清理，显示`CLIENT_STARTUP_TIMEOUT`。仓库同步后不再受此启动期限限制；画面仍需实际确认。
- 原生TCP首个认证帧使用30秒累计等待预算，身份及挑战验证保持严格。第25轮曾在首帧前超时；第26轮正常启动并完成重登验证，但首帧仅等待0.031秒，不能据此断言该等待调整已根治偶发启动失败。失败编号和恢复证据继续保留。
- “游戏显示模式”默认“兼容显示（当前电脑推荐）”，使用本项目帧显示桥，标准显示仍可手动选择。兼容显示有CPU回读开销，长期性能、全屏切换和战斗仍待验收。此前失败的D3D9Ex模式已从界面移除，保留诊断API与失败证据。

## 失败处理

“检查并恢复”只处理已有恢复计划，活跃旧进程和被外部修改的配置会明确报错，不会强行覆盖。注销因网络失败未能确认时，保持Launcher运行，网络恢复后再次点击此按钮。

启动记录会给出 `launcher-日期时间` 诊断编号。详细记录保存在 `repository/evidence/private/<编号>/`；阶段摘要为 `launcher-summary.json`，配置计划为 `native-config-guard/plan.json`，凭据撤销证明为 `session-cleanup.json`。不要公开私人目录。启动器自身未能打开时查看 `repository/runtime/native-launcher-error.log`。

## 当前依赖与边界

需Windows、WebView2、本仓库`.venv`、已构建`admin/dist`、受控客户端副本以及可用的DEV SSH配置。窗口读取127.0.0.1:18769页面；业务服务通过本次专属随机回环端口，转发至已登记DEV的18765。不会复用不明来源的本机业务端口，页面不能修改服务器或执行文件路径。已回读DEV实际使用用户提供的远程MySQL 8.0.30-txsql，4323项库存和GID保持。这是当前机器可运行入口，尚不是第二端安装包，不应将私人SSH配置分发给其他玩家。

密码和业务凭据只保存于本轮进程内存。Tauri通过继承管道取得随机本机操作能力，不写能力文件、不把它放进命令行；WebView使用临时会话，不允许Rust通用命令调用。旧浏览器诊断模式保留DPAPI能力文件机制，日常入口已切到Tauri。

父窗口消失时子服务收到管道EOF，停止自有游戏并恢复配置、注销凭据。若整个进程树被强制终止或断电，Windows Job结束自有游戏，下一次启动检查配置恢复；已兑换的来源/游戏凭据停止续期后最多180秒失效，并非即时撤销。正常运行每30秒续期，单次登录最多12小时；服务端封禁、注销及安全版本变化仍立即拒绝续期。仅登录未点击启动时尚未进入短租约，正常关闭仍注销来源会话。

已通过16项Launcher控制层测试、含显示桥的206项工具回归；此前18项MySQL登录/租约回归、Rust地址边界测试、真实DEV三分钟失联到期及真实游戏父管道EOF清理保持原证据。Tauri界面、协议业务、用户观察及原生大厅分别记录；全目录业务和多人实机仍未全部完成。最新显示与恢复结果见[显示桥交接](delivery/2026-10-16-frame-bridge-experiment.md)。

## 构建

在仓库根目录运行 `./tools/build_launcher.ps1`。脚本先编译x86显示桥并校验固定摘要，打包至`launcher/bin/components/d3d9-frame-copy.dll`，再构建React、执行Rust测试，编译并复制至`launcher/bin/NZLauncher.exe`。显示桥用`/Brepro`可复现构建；源码或编译工具变化造成摘要变化时必须重新验收并显式更新版本，不能跳过校验。x86编译会恢复环境，避免污染Tauri x64链接。默认工具目录`runtime/tools/launcher-build`，使用隔离Cargo/Rustup、Rust 1.99.1 MSVC、Microsoft C++ Build Tools及Windows SDK；不修改全局PATH。`Cargo.lock`固定Tauri 2.12.4等实际依赖。首次准备依赖后可用`-SkipFrontend`跳过前端重建。安装包、签名及第二台机器所需受限网关尚未交付。

当前默认组件为本项目`tools/native_d3d9_flip_bridge.cpp`，摘要由`tools/native_frame_bridge.py`固定。系统D3D9保留原渲染和Present调用，将当前回缓冲复制到同一游戏窗口的D3D11 Flip交换链；不创建子窗口、不改变窗口过程、不接管游戏消息。仅往受控副本添加单个d3d9.dll，已有同名文件拒绝覆盖，退出按持久恢复计划核对摘要并移除；日志仅含HRESULT/帧数，固定写入本轮私人目录。原始客户端和系统DLL不改动。

历史D3D9Ex诊断组件来自[DxWrapper官方v1.9.8700.25](https://github.com/elishacloud/dxwrapper/releases/tag/v1.9.8700.25)，摘要在`tools/native_graphics_compat.py`固定，许可证保留于`launcher/licenses/dxwrapper.txt`。它在本机游戏初始化失败，不是当前默认组件；`prepare_graphics_compat`仅保留供旧诊断路径使用。

启动时按`cn2020-renewal-offer-restore-v1`在受控工作副本临时恢复192个旧续费商品入口。`DefaultCommodityLibrary.ini`需完整匹配固定摘要，只改目标行的`bShowCommodity/bGoodsAdded`；价格、期限和销售日期保持。变体纳入同一配置恢复计划，原件不改，未知摘要停止启动。此配置需要对应DEV服务端规则，实机结果见维修续费交接文档。
