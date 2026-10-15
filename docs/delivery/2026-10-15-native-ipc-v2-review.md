# v2 原生 IPC 独立账号复验

日期：2026-10-15。本批为 v2 IPC 修改的独立审阅及专项验证。父任务实现 adapter/proxy 字段修改和实际启动探针；本批调整 `tests/test_email_native.py`、`tests/test_email_tools.py`，并补充 `adapters/cn_current/email_ipc.py` 路径字节边界，没有运行 TGame、发送邮件或修改客户端。

## 已实现

- 对照当前构建消费代码证据审阅 `adapters/cn_current/email_ipc.py`、`tools/native_email_proxy.ps1`：tag2 对应独立 game_id（输出 +0x10）；tag1 对应研究 ServiceId（输出 +4）；服务器 sub2/sub3 对应主地址/端口（输出 +0x1480/+0x1580）。修正后的编码与原生 getter 校验位置一致，未发现本次字段交换修复的实质问题。
- 原生 MySQL/HTTP 专项现在分别覆盖默认空服务列表和回环服务列表：两个不同账号经过独立登录交接，实际 32 位 TenProxy 读取各自 ID、令牌及会话，第二账号核对真实 DLL 输出的服务器主端点和保留研究端点。临时端口仅在测试中绑定，未运行游戏或发送协议包。
- 工具测试新增嵌套服务器偏移表边界、主端点 sub2/sub3 位置及回环端口范围检查；sub4/sub5 仍明确为未知业务语义的研究值。
- 审阅发现并修复原有路径边界：保留 `len(client_path)<=250` 字符限制，新增含终止符的 UTF16 编码字节数不超过已证 tag19 上限 0x208（520 字节），直接复用校验过的编码结果。129 个非 BMP 字符加一个 ASCII 字符恰为 520 字节可接受；130 个非 BMP 字符和 251 个 ASCII 字符均拒绝。
- 修改涉及函数与关键分支已补中文说明。`git diff --check` 通过。

## 未实现

- 本批没有验证实际游戏角色、大厅、战斗或双端。真实启动探针由父任务记录；原生 getter 成功不能代替原生服务端握手和游戏验收。
- ServiceId=1 的业务有效性、服务器 sub4/sub5 等字段语义仍未验证。

## 当前阻塞

无专项测试阻塞。游戏大厅未通的协议阻塞由父任务继续分析；本批测试不将该状态描述为已完成。

## 验证记录

真实数据库环境通过 `NZ_ENV_FILE=runtime/user-mysql.env` 加载，凭据未输出；测试使用 Django 独立测试库，真实 MySQL **8.0.30-txsql**。`NZ_MAIL_ENV_FILE` 指向不存在的测试文件，且既有夹具关闭验证码发信路径；没有 SMTP 调用。

```powershell
$env:NZ_ENV_FILE='runtime/user-mysql.env'
$env:NZ_MAIL_ENV_FILE='runtime/nonexistent-test-mail.env'
$env:NZ_NATIVE_TEST_REPORT='evidence/aliyun-native-v2-mysql-bridge.json'
.venv/Scripts/python.exe -m tools.run_tests --labels tests.test_email_native --output evidence/aliyun-native-v2-mysql-tests.json
.venv/Scripts/python.exe -m tools.run_tests --tooling --pattern test_email_tools.py --output evidence/aliyun-native-v2-endpoint-tooling-tests.json
.venv/Scripts/python.exe -m tools.run_tests --tooling --pattern test_email_tools.py --output evidence/aliyun-native-v2-utf16-tooling-tests.json
```

- MySQL 专项 **1/1 通过、0 跳过、19.484 秒**。该测试内部实际验证两个独立账号；默认 `nz-email-ipc-research-v2` 与回环 `nz-email-ipc-loopback-research-v2` 的 `ipc_read_verified`、`ServerRecordVerified` 均为 true，进程树清理通过，源会话已撤销。
- 工具专项 **4/4 通过、0 跳过、0.516 秒**。
- UTF16 边界修复后的最终工具专项 **5/5 通过、0 跳过、0.516 秒**，证据 `evidence/aliyun-native-v2-utf16-tooling-tests.json/.txt`。包含前四项，不能把 4 与 5 相加统计。此修复未改变真实工作路径编码，按范围仅重跑工具集，没有重复 MySQL 专项。
- 桥接结果 `evidence/aliyun-native-v2-mysql-bridge.json`；测试报告 `evidence/aliyun-native-v2-mysql-tests.json/.txt` 与 `evidence/aliyun-native-v2-endpoint-tooling-tests.json/.txt`。以上使用新文件，未覆盖历史证据。

## 接手与恢复

测试专用原生桥报告环境变量的实际名称为 `NZ_NATIVE_TEST_REPORT`。重跑时指定新的报告路径，避免覆盖历史结果。Django 测试会建立并清理专用测试库；没有当前业务库迁移、部署或数据回退操作。客户端文件未改动，无客户端恢复步骤。
