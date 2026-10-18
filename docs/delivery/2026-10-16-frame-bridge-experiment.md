# D3D9最终帧显示桥试验

日期：2026-10-16。接续[Launcher图形诊断](2026-10-16-launcher-graphics-diagnosis.md)。本批仍服务于React + Tauri统一启动与原生功能验收；没有修改资产、钱包、历史流水或正式签到配置。

## 已完成和依据

- 用户确认独立D3D11 Flip对照能看到蓝色和绿色。此确认只属于独立程序。
- 校验官方DXVK v3.1.1包：18,041,512字节，SHA256 `40565b4a724aadc4433fa4e010b4b23916d9b1f1baeee64e17186db94f54e608`。仅按固定成员名读取x32/d3d9.dll，未安装到系统或游戏。成员7,856,142字节，SHA256 `265888c31ca78dffa290c39cb7e50bfb02762590e41927906e46fb32f01497fa`。
- DXVK独立程序的立即呈现和FIFO均回读绿色、Present成功，但computer-use实际截图仍白色，故未进入游戏。`d3d9-dxvk-present-02/03`及`d3d9-dxvk-fifo-04`分别为7219、4774、7227次成功；01轮启动隐藏，不计画面证据。JSON中的system-d3d9仅表示调用API，不能据它认定实际加载系统库。
- `tools/native_d3d9_flip_bridge.cpp`实现实验帧桥：系统D3D9仍负责游戏渲染，将自己的回缓冲经CPU回读上传至D3D11 Flip。没有更改游戏EXE、认证接口或系统图形库。`generate_d3d9_forwarders.py`按Windows SDK公开接口生成17/119/10个方法的显式代理，普通方法转发原参数和结果。
- 第一版试图替换单个对象虚表，在自有诊断中以`0xc0000005`退出，未加载游戏。已改成独立代理对象；失败01/02证据为空文件，不能当成功记录。
- 显式代理版独立诊断1459帧回读及Present通过，computer-use实际绿色。子窗口版游戏运行`launcher-20261016-150359-744007`完成4323库存同步，**用户明确看到完整大厅**，但同时报告窗口未响应及“圣光裁决（分期版）还剩0天，请注意及时还款”。computer-use两次激活超时；桥日志有2400帧成功后停滞。没有宣布大厅交互通过。
- 补齐子窗口消息派发的下一轮`launcher-20261016-151022-408311`仍出现激活超时，日志最后1200帧。两轮均通过自有Job清理、配置恢复、来源与游戏凭据撤销，原始传输文件均0；见`launcher-graphics-frame-bridge-06/07.json`和`game-frame-bridge-user-06.json`。
- 直接在原窗口创建Flip交换链的独立对照1692帧成功、截图绿色，省去跨线程子窗口。对应原生游戏复测单独记录，不能由独立结果推断游戏成功。

## 直接窗口修复与Launcher集成（本批最新）

- 08轮`launcher-20261016-151620-851369`实际激活游戏后，computer-use连续打开大厅、仓库分类、好友面板和商城，画面持续更新，未重现子窗口版的激活超时。该轮仍是诊断注入，不能单独证明产品配置生效。结束后的Job、配置、来源及游戏凭据恢复全部通过，见`launcher-graphics-frame-bridge-08.json`。
- 移除失败的子窗口分支及环境开关，只在游戏自身HWND建立D3D11 Flip交换链；不创建/销毁窗口、不更改窗口过程、不接管消息循环。保留系统D3D9渲染、原始Present调用及真实返回值，Reset前释放自有D3D11资源，避免持有D3D9默认池对象阻塞Reset。
- `native_frame_bridge.py`固定组件摘要；`build_frame_bridge.ps1`使用`/Brepro`，重复编译三次摘要一致，打包到`launcher/bin/components/d3d9-frame-copy.dll`。配置守卫只向工作副本添加单个DLL，目标已存在则拒绝覆盖，退出核对并移除；日志路径仅传给自有子进程，内容只有HRESULT/帧数。
- Launcher新增默认`frame-copy`（兼容显示），保留`system`。失败的旧`d3d9ex-flip`不再出现在页面，仍保留诊断API兼容。React提供缺失、损坏和冲突的中文错误。x86显示桥构建保存/恢复INCLUDE和LIB，不污染Tauri x64构建。
- 09轮`launcher-20261016-153058-006275`通过**正式Launcher API、无诊断替换**登录并完成4323库存同步，computer-use实际看到完整大厅及“圣光裁决（分期版）剩余0天”提示。关闭叉未关闭提示，但点击“确定”立即关闭，随后仓库和商城连续切换正常；本轮没有窗口未响应。提示本身仍未修复，不能把画面修复当作还款语义完成。
- 游戏内退出按钮打开旧活动页，活动内容加载官方登录窗口，未操作认证表单，也未完成游戏内正常退出验收。最终通过Launcher父管道EOF清理自有进程、组件、配置及凭据，全部通过。自动报告中的`lobby_verified=false`是脚本不推断视觉结果；实际视觉证据单列`frame-bridge-ui-observations.json`和`game-product-*.png`。
- 已打开新版未登录Tauri窗口；界面默认兼容显示及限制说明实际可见，截图`tauri-launcher-frame-copy.png`。正常产品没有自动读取验收密码，登录/启动测试经私有脚本调用同一服务API，与Tauri界面验收分开。

组件版本`cn2020-frame-copy-v1-lobby-verified`，DLL SHA256 `12592afcafdb0e76581a48ce142f3b152fcda55288ce1156855c639a393fd051`；Launcher EXE SHA256 `61996ab253036eb85f01af05735a6806ad703ba9df2470b8636d0f6523226f65`。React产物`index-BSy3I4Il.js`。这些是本机开发产物，未发行签名。

## 目录核查修正

当前分页库存桥会投影所有有效持有类型；仅旧的非分页接口限制ConsumeType 0/2。前一核查表02版错误地把2466写成当前范围，03版修正为当前4323、旧接口2466，历史证据保留。有效矩阵：`evidence/2026-10-16-inventory-capability-matrix-03.json`，SHA256 `94bbe54481fbf26e0f064e3232a702607848597f62917ef9e0d791492b68b032`。这不证明全部物品期限、数量、还款及操作语义已验证。

分期道具101539原配置含`ConsumeType=1`、`bCanRepay=True`及外部`RepayUrl`；全目录共有7个还款标记物品。目前没有服务器贷款订单的证据，不能据客户端提示创造欠款、扣钱包或删除原权益。当前永久时间投影对这类客户端逻辑是否正确仍待核查。

## 未完成与阻塞

- 显示桥有同步CPU回读开销；未做性能、长期稳定、全屏切换、全部接口身份、资源GetDevice返回路径或战斗验收。Direct3DCreate9Ex仍原样转发，不属于当前桥覆盖路径。
- 当前构建直接窗口方式已通过单端大厅交互；子窗口方式失败证据保留。标准D3D9在同一ToDesk链路空白的系统根因没有完全定位，当前为可撤销的兼容解决方案。
- 游戏内退出活动页及“分期剩余0天”仍待修复；使用Launcher停止可以完成本轮已验证的清理。未创建贷款、扣款或删除既有权益来消除提示。
- 还款提示及原计划中的未实现功能保持待办，不能以修改文字或隐藏物品替代业务完成。
- 既有原生资源闭包、第二客户端和多人地图验收边界沿用原台账。

## 验证和接手

`./tools/build_launcher.ps1`编译/校验/打包显示桥、构建React、执行Rust测试并构建Tauri。单独`build_frame_bridge.ps1`仅生成隔离组件，带`-Package`才核验摘要并复制到Launcher组件目录；均不自动安装到游戏。运行时NativeConfigGuard管理临时DLL。失败不回滚数据库，不覆盖原客户端。

- 完整工具回归206/206、0跳过；最终Launcher控制层16/16；`launcher-frame-bridge-tooling-02.json`、`launcher-frame-bridge-controller.json`。新用例覆盖组件篡改/冲突/持久恢复、互斥模式、默认模式传递和超时清理。
- React TypeScript/Vite构建、Rust release测试1/1及构建通过。首次沙箱Vite无法读取父目录，普通桌面上下文重建通过；未降低系统安全配置。构建证据`frame-bridge-build.json`。
- 新增C++、生成器、构建脚本、前端分支及测试有中文说明；7个Python模块AST审计无缺失，公开838文件/已知28秘密零命中。见`frame-bridge-comments.json`、`frame-bridge-public-secret-scan.json`。不宣称全仓注释合格或未知秘密不存在。
- DEV发布`20261016T073811-79f7d6180a5f`，104个白名单文件；安装退出0，前端和业务摘要回读一致，四服务active，迁移0001–0016，库存4323/GID4323、正式签到启用0。见`launcher-dev-deploy-05.json`、`launcher-dev-readback-05.json`。回读脚本首次直接运行缺模块路径、尚未连接，改为`python -m runtime.verify_launcher_deploy_05`通过。
- 本批无MySQL业务代码或结构变化，不将工具回归当作MySQL事务测试；`git diff --check`通过，仅既有CRLF提示。没有提交或推送Git。

本节证据文件统一位于`evidence/`并以`2026-10-16-`开头。08/09没有执行资产交易；单端大厅交互不等于好友送达、购物支付、抽奖或战斗通过。恢复采用组件移除及兼容代码向前修复，保留数据库新写入。

原会话只读协助对照见[成功基线记录](2026-10-16-lobby-baseline-assistance.md)。成功时没有额外图形层；模块差集不是根因证据，不据此补装系统DLL。

参考：[DXVK官方发布](https://github.com/doitsujin/dxvk/releases/tag/v3.1.1)、[该版本官方配置](https://github.com/doitsujin/dxvk/blob/v3.1.1/dxvk.conf)。
