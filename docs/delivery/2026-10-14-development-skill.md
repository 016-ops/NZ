# 本轮开发交接：项目开发 skill

## 基本信息

- 日期：2026-10-14（北京时间）
- 目标：将用户指定技术栈和每次修改后的交接要求固化为项目内开发 skill。
- 范围：新增开发规范、项目入口与交接模板。

## 已实现功能

| 文件 | 实现内容 |
|---|---|
| `.agents/skills/nz-development/SKILL.md` | 定义 React + TypeScript + shadcn/ui、Python + Django、MySQL 的开发要求；规范技术栈差异处理及每批次交接 |
| `.agents/skills/nz-development/assets/delivery-template.md` | 提供基本信息、已实现、未实现、阻塞、验证、接手与回退六部分模板 |
| `AGENTS.md` | 为后续项目开发提供 skill 阅读入口，并要求修改交付前写交接记录 |
| `docs/delivery/2026-10-14-development-skill.md` | 记录本次规范交付及现有系统与目标栈的差异 |

本 skill 可通过 `$nz-development` 引用；项目入口规则也明确要求开发时阅读该文件。新会话是否出现在技能列表中，以实际项目技能发现结果为准；当前会话未重新加载技能目录。

## 未实现内容

本轮 skill 编写需求已实现。业务系统技术栈迁移尚未执行，属于后续任务：

- `admin/index.html`、`admin/app.js`、`admin/style.css` 仍为原生网页，需要后续迁移至 React + TypeScript + shadcn/ui。
- `revival/http.py` 仍使用 `http.server` 与 `ThreadingHTTPServer`，需要后续迁移至 Django。
- `revival/db.py` 仍使用 SQLite，需后续设计 Django 模型、MySQL 配置、迁移和既有数据兼容方案。
- 现有 README 和启动文档仍描述当前可运行实现；业务迁移后需同步更新。

## 当前阻塞

skill 文件编写无阻塞。内置 `quick_validate.py` 在系统及内置 Python 环境均因缺少 PyYAML 报 `ModuleNotFoundError: No module named 'yaml'`，完整校验器验证受阻。已改用文件结构和内容检查；若需补跑完整校验器，应先在具备 PyYAML 的工具环境运行。

旧技术栈差异不阻塞本次规范交付，也不代表业务迁移已经完成；后续迁移需单独执行与验收。

## 验证记录

| 验证项 | 环境及命令/步骤 | 实际结果 | 未验证范围 |
|---|---|---|---|
| 内置 skill 校验器 | Python 执行 `quick_validate.py .agents/skills/nz-development` | 未完成：缺少 PyYAML；系统与内置 Python 均复现 | 完整 YAML 解析及校验器规则 |
| 文件结构及内容 | 读取四个新增文件；检查 frontmatter、命名、模板和入口链接及章节 | 通过；技术栈和每批次交接要求已覆盖，引用目标存在 | 新会话中的实际技能发现与调用 |
| 修改范围及空白 | `git status --short`、`git diff --check`，另检查未跟踪新增文件的行尾空白 | 通过；仅新增本次四个文件，无业务代码改动 | 业务运行测试不适用，本次仅修改规范文档 |

## 接手与回退

- 后续开发先阅读 `AGENTS.md` 和 skill，再按任务范围实现目标技术栈，交付前填写交接模板。
- 本次没有新增业务依赖、数据库配置或迁移文件，也没有修改业务代码和启动流程。
- 回退仅涉及本次新增的四个规范/交接文件，无业务数据影响。

## 追加交接：详细中文注释要求

- 日期：2026-10-14（北京时间）。
- 已实现：在 `.agents/skills/nz-development/SKILL.md` 新增“中文代码注释”规范，并同步技能描述。要求所有项目代码有详细中文注释，明确模块职责、函数/组件输入输出、关键业务逻辑和测试场景的注释内容，以及同步维护和交付检查要求。
- 未实现：本轮仅追加 skill 规则，未执行全项目存量代码注释整改；存量代码是否全部符合要求尚未检查。
- 当前阻塞：规则更新无阻塞。完整 skill 校验器仍有前述 PyYAML 依赖限制。
- 验证：读取确认新增规范及 frontmatter 结构，检查本轮两个文档的行尾空白与交接内容；通过。本轮未修改业务代码，业务运行测试不适用。
- 接手与回退：后续代码修改必须同步维护中文注释并记录检查结果；回退本次追加仅需移除新增注释章节、恢复技能描述和移除本追加交接段落。
