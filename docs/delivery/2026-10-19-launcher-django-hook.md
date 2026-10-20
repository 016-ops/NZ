# Launcher Django 冻结依赖补充

## 本轮目标与范围

- 日期：2026-10-19（America/Los_Angeles）。
- 范围：只补 `tools/launcher_hooks/hook-django.py` 的 Django 公共动态模块及 `tests/test_launcher_hook.py` 的隔离回归；不收集项目 settings、数据库、SSH 或环境文件。
- 初始背景：旧 77813a0 Windows CI 已通过 Rust/PyInstaller 构建，冻结启动失败，后续安全诊断为 `INVALIDTEMPLATELIBRARY`。当前 37b7598 的 Launcher CI 39375681700 已成功，冻结 bootstrap/close 烟测与归档独立核验均通过；原冻结启动阻塞已解除。

## 已实现功能

- 明确收集 `django.templatetags.i18n`。Django 5.2.18 的 `django.views.debug` 在模块导入时构造 `DEBUG_ENGINE`，通过 `libraries` 字符串动态加载该模块，原 hook 已包含 `views.debug` 却未明确列入其 i18n 依赖。
- 回归读取 hook 和已安装 Django 的 AST，验证上游动态库声明被覆盖；测试不执行 PyInstaller hook。
- 验证 `views/templates/**` 命中公共异常模板目录，含 `technical_500.html`、`technical_500.txt`、`technical_404.html` 和 `default_urlconf.html`。
- 隔离内存子进程使用与 Launcher 相同的空应用/空中间件设置构造 WSGI。默认数据库配置为 dummy，但该启动流程未实际导入 dummy backend，因此本轮没有基于猜测扩大收集。

## 未实现内容

- Windows 开发包尚未连接实际 API、未进行干净 Windows 安装或游戏实玩；默认回环18765仍为占位网关，正式配置与这些验收独立跟进。
- MySQL 综合 CI 39375957208 最终已成功：MySQL8.4.11，579项、565通过、0失败/错误、582.514秒；14项测试内 Skip 与6个缺原件 suite Skip另列。最终部署书据此更新；另三本设计书与既有 Web 包没有变化。

## 当前阻塞

- 本轮 hook 冻结启动阻塞：无，37b7598 的 Windows 构建及 bootstrap/close 烟测已通过。
- Web 远程部署仍等待用户提供服务器配置；Windows 实际 API、干净安装和游戏实玩未验收，不将 CI 产包成功视为这些验收完成。

## 验证记录

| 验证 | 结果 | 限制 |
| --- | --- | --- |
| `.venv/bin/python -m unittest tests.test_launcher_hook -v` | 3/3 通过，Django 5.2.18 / Python 3.14，本机内存验证 | 不代表 Windows 冻结成功 |
| `.venv/bin/python -m py_compile tools/launcher_hooks/hook-django.py tests/test_launcher_hook.py` | 通过 | 仅语法检查 |
| `git diff --check` | 通过 | 不涉及业务数据库 |
| Windows Launcher CI 39375681700（37b7598） | Rust/PyInstaller、冻结 bootstrap/close 烟测成功；754个payload及manifest独立核验通过 | 不包含实际 API 登录、干净 Windows 安装或游戏实玩 |
| 综合 CI 39375957208（6f12054） | MySQL job118474030677及Windows job118474030815均success；freshartifact12243727463确认MySQL579项/565通过/0失败错误，582.514秒 | MySQL14项测试内Skip、Windows1项测试内skip及各6个缺原件suite单列；不据此宣称所有Windows-only场景已验 |
| 中文注释审查 | hook 解释动态导入原因；测试类与方法说明职责、隔离边界和预期 | 不宣称完成全项目注释整改 |

## 接手说明

- 父任务已统一提交并推送此 hook 与测试，Windows 冻结烟测已成功；无需数据库迁移或新增配置。后续检查实际 API 与干净安装。
- 失败时继续依赖严格白名单诊断，不把原始 stderr、用户路径或启动能力写入 CI 日志。
- 仅增加一个公共 hidden-import；回退可撤销该项及独立测试，不修改 Web 包或业务数据。

## 历史：后续设计书状态草稿（当时最终 CI 待确认）

- 更新 `tools/design_release_status.json`，记录父任务确认的最新推送 `6f12054997e32258e807980265dad04a4afe914e`。Windows 工具/前端采用较新实证：39375957208 的 job 118474030815 成功，artifact 12243705393；246项、245通过、0失败/错误、2.748秒，1项测试内 skip 与缺原件6套件另列，不计为实际通过；前端成功。
- Launcher 39375681700 后续已成功，开发包构建与冻结 bootstrap/close 烟测通过，artifact 12243650799 已上传；未连接实际 API、未干净 Windows 安装、未游戏实玩。默认回环18765仍是占位网关。artifact wrapper 为25,777,855字节、SHA256 `d92596490ed233ea21c7102c221449430f2a787d70ee9bf464623e7b71bb25c4`，不可用作内部发行 zip 摘要；父任务正在独立核对内部 zip/manifest。
- MySQL 39375957208 尚在执行；旧 77813a0 Launcher 的 `INVALIDTEMPLATELIBRARY` 仅作历史诊断。没有将未完成运行写为成功。
- 生成器已分别保留 Launcher 构建、Windows 工具/前端和综合回归状态；本机演示仅列账号名、8875入口与正常 Owner MFA。秘密不写入设计书输入。
- 本次只更新生成源并完成 JSON/Node 语法与 `git diff --check`，尚未重新导出任何工作簿。最终等待 CI 结论后仅导出部署书并审查3张影响页；另三本和 SHA256 `7d61de07bf52581318fa797a56f3c627827f22de2337acc9b507dd345c1f7cb9` 的 Web 包保持不变。

## 历史：后续部署书预导出（当时 MySQL 仍待确认）

- 根据父任务指示，已选择性导出部署书的最新已知状态；MySQL明确显示“运行中”，未标记通过。部署总览、打包文件清单、交付与阻塞三张改变页已完整渲染并目视核查，未见截断。
- 内部 `NZLauncher-DEV.zip` 已由父任务独立核验：25,974,212字节，SHA256 `139c84de2b034d33be4f5079be6821d582112c02d4552b375733f02494102993`，754个payload另加manifest。设计书分别列内部zip与GitHub artifact包装层，避免摘要混淆。
- 部署书7张表的冻结、筛选及公式错误扫描通过；本次文件39,671字节，SHA256 `82543914ffb9abde57382c48d7ecbe1a5ee6a9d5ed6ab9a733f19d26e2f3779d`，仍为等待最终MySQL结论的预导出版。
- 另三本和Web tar的前后SHA逐一相同，另三本验证记录原样保留。对照记录见 `evidence/2026-10-19-design-book-update-verification.json`，后续最终版本另行补充。未提交、未推送、未远程部署。

## 最终部署书增量导出

- 6f12054 的综合 CI 39375957208 两个 job 均成功。MySQL freshartifact12243727463：MySQL8.4.11，579项、565通过、0失败/错误，582.514秒；14项测试内 Skip 与6个缺原件 suite Skip单列。Windows：246项、245通过、0失败/错误，2.748秒；1项测试内 skip 与6个缺原件 suite Skip单列。不据此推断所有 Windows-only 场景已验证。
- 仅重新导出 `部署手册及打包清单.xlsx`，重渲染部署总览和交付与阻塞两页，目视核查通过。其余5页保留已审查预览；完整检查7张表的冻结5行、7个筛选和缓存错误均通过，Artifact Tool公式错误扫描0项。
- 最终部署书39,631字节，SHA256 `87311a55c6f8853ccd5a7919376c3a4b16d557c945ba584a657fd25ab392f12a`。另三本与Web tar的前后SHA再次确认一致，原验证记录保持。更新后的对照证据及工作簿验证摘要已保存。
- Git/CI来源使用交接路径，不硬编码易偏移行号；本机demo来源动态查找“独立测试账号”标题。没有读取或记录账号密码、MFA种子或恢复码。
- 仍未远程部署，Windows实际API/干净安装/实玩未验收。由父任务统一提交与推送，本子任务没有提交或推送。
