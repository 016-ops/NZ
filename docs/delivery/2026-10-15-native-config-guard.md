# 原生研究配置恢复守卫

日期：2026-10-15。范围：`tools/native_config_guard.py`、`tests/test_native_config_guard.py`。父任务负责 `tools/native_email_boot.py` 的调用整合及真实客户端验证；本批不运行游戏、不访问数据库、不发送邮件。

## 已实现

- `NativeConfigGuard(root, output)` 在工作副本之外的既有证据目录创建独占 `native-config-guard/`。`apply(relative, variant_bytes)` 先保存并同步原件、核对 SHA256，再原子持久化 `PREPARED` 计划，最后安装变体。
- 每次配置写入都先在目标同目录写完整临时文件并同步，再复核预期摘要后执行 `os.replace`；变体摘要由准备写入的字节计算，不采纳写入后出现的其他内容作为合法变体。
- `restore()` 逐文件独立处理、恢复后复核 SHA256，并返回 `configuration_restored`、`configurations` 的状态/错误、`plan_errors`。某个配置或计划写失败不会跳过其他配置；现有内容既不匹配原件也不匹配预期变体时保留现有内容。
- `NativeConfigGuard.from_plan(root, output).restore()` 支持调用方中断后的重新加载。恢复计划绑定工作副本根；拒绝路径穿越、重复目标、Windows ADS/非规范路径、硬链接和重解析点，备份文件名限定在该输出目录内。
- 新增文件、类、函数和关键分支均有中文说明，测试说明实际故障与预期行为。

## 未实现

- 本批未运行真实客户端，不能用工具测试宣布启动、大厅或多人验收通过；真实客户端整合由父任务记录。
- 未提供多个独立进程同时编辑同一配置的操作系统级比较并交换；调用方须保持单一启动探针独占工作副本。摘要在替换前复核并在替换后验证，但检查与系统替换之间仍存在极短的并发窗口。
- 已同步文件内容并使用同目录原子替换；未进行真实断电/磁盘控制器故障测试，不作硬件级持久性保证。

## 当前阻塞

无。本批测试可独立运行；原生大厅与协议能力的剩余阻塞归父任务交接。

## 验证记录

Windows、本机锁定 `.venv`，不初始化 Django/MySQL。命令：

```powershell
.venv/Scripts/python.exe -m tools.run_tests --tooling --pattern test_native_config_guard.py --output evidence/native-config-guard-fault-tests.json
```

最终 **14/14 通过、0 跳过、0 失败**，0.500 秒。证据：`evidence/native-config-guard-fault-tests.json` 和同名 `.txt`。

覆盖原件/计划先于变体写入、从磁盘计划恢复、计划写失败、原子替换失败、变体同步失败、安装后记录失败、逐项恢复独立性、备份损坏、目标缺失、外部编辑保留、恢复后摘要核验、计划路径逃逸、真实硬链接、真实 Windows 目录联接、恢复中计划持久化失败。

首次 `evidence/native-config-guard-tests.json` 保留 11 项中 1 项因普通用户符号链接权限跳过的记录；第二次改用临时目录内 Windows 目录联接，`evidence/native-config-guard-final-tests.json` 为 11/11；最终增加三项故障边界成为 14/14。以上轮次有重复，不相加统计。

## 接手与恢复

调用方先校验已登记的独立客户端工作副本，再初始化守卫；`output` 须已存在、位于工作副本之外且本次没有 `native-config-guard/`。每个 `apply` 只接收已核验的相对配置路径与完整变体字节。无论进程或证据采集是否失败，都应在独立 `finally` 中调用 `restore()` 并保存其完整返回值。

若调用方被终止，先确认该工作副本的客户端已退出且无其他配置写入者，然后用相同 `root/output` 调用 `from_plan(...).restore()`。遇到 `EXTERNAL_CHANGE_PRESERVED` 或 `RESTORE_ERROR` 时检查逐项结果，不要强制覆盖；备份和计划保留在本次证据目录。`configuration_restored=true` 只表示配置字节核验通过，不能代替客户端功能验收。
