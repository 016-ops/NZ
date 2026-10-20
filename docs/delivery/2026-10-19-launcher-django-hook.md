# Launcher Django 冻结依赖补充

## 本轮目标与范围

- 日期：2026-10-19（America/Los_Angeles）。
- 范围：只补 `tools/launcher_hooks/hook-django.py` 的 Django 公共动态模块及 `tests/test_launcher_hook.py` 的隔离回归；不收集项目 settings、数据库、SSH 或环境文件。
- 相关背景：Windows CI 已通过 Rust/PyInstaller 构建阶段，冻结服务启动 smoke 失败；实际失败模块仍等待安全诊断。此次分析不能替代该 CI 证据。

## 已实现功能

- 明确收集 `django.templatetags.i18n`。Django 5.2.18 的 `django.views.debug` 在模块导入时构造 `DEBUG_ENGINE`，通过 `libraries` 字符串动态加载该模块，原 hook 已包含 `views.debug` 却未明确列入其 i18n 依赖。
- 回归读取 hook 和已安装 Django 的 AST，验证上游动态库声明被覆盖；测试不执行 PyInstaller hook。
- 验证 `views/templates/**` 命中公共异常模板目录，含 `technical_500.html`、`technical_500.txt`、`technical_404.html` 和 `default_urlconf.html`。
- 隔离内存子进程使用与 Launcher 相同的空应用/空中间件设置构造 WSGI。默认数据库配置为 dummy，但该启动流程未实际导入 dummy backend，因此本轮没有基于猜测扩大收集。

## 未实现内容

- 尚未完成这次补丁对应的 Windows 冻结构建与启动 smoke；由父任务统一提交并重跑。
- 未重新导出 Excel，未更改另三本设计书或既有 Web 发布包。

## 当前阻塞

- Windows CI 当前仍在运行，尚无当前失败模块的安全诊断。影响范围仅为冻结服务启动验证；解除条件是收到该轮固定错误码/缺失模块名与重跑结果。

## 验证记录

| 验证 | 结果 | 限制 |
| --- | --- | --- |
| `.venv/bin/python -m unittest tests.test_launcher_hook -v` | 3/3 通过，Django 5.2.18 / Python 3.14，本机内存验证 | 不代表 Windows 冻结成功 |
| `.venv/bin/python -m py_compile tools/launcher_hooks/hook-django.py tests/test_launcher_hook.py` | 通过 | 仅语法检查 |
| `git diff --check` | 通过 | 不涉及业务数据库 |
| 中文注释审查 | hook 解释动态导入原因；测试类与方法说明职责、隔离边界和预期 | 不宣称完成全项目注释整改 |

## 接手说明

- 父任务统一提交此 hook 与测试，再等待 Windows 构建烟测；无需数据库迁移或新增配置。
- 失败时继续依赖严格白名单诊断，不把原始 stderr、用户路径或启动能力写入 CI 日志。
- 仅增加一个公共 hidden-import；回退可撤销该项及独立测试，不修改 Web 包或业务数据。
