# 当前首包与 challenge 边界模块

日期：2026-10-15。范围：`adapters/cn_current/transport.py`、`tests/test_native_transport.py` 的认证边界。字段合同由父任务对当前构建的静态研究提供；本批实现和测试该有界合同，不运行客户端、不连接数据库、不发送邮件。本文包含后续已证异步 SYNACK 修正；独立换钥/业务取证 API 另见 `2026-10-15-native-keychange-application.md`。

## 已实现

- `receive_frame(stream, max_bytes=65536)` 读取单个 TCP 帧，支持分片，恰好消费声明总长度，保留后续帧。固定头为 12 字节：magic=0x55、version=0x0e、1 字节 command，HeadLen/BodyLen 为大端 uint32。只接受 AUTH=3、SYN=8、SYNACK=9，总长度上限 64KiB。默认 EncHeadLen=0/BodyLen=0；仅额外允许已证异步 SYNACK 的 EncHeadLen=4/HeadLen=45/BodyLen=16。
- `validate_auth(frame, expected_id, expected_token)` 只核对本次预先确定的独立身份。AUTH 为 EncMethod=3、ServiceID=0、AuthType=3、Uin 四个大端 uint32，随后单字节 token 长度和 token；以常量时间比较 token，严格拒绝未知标量、其他账号、截断、尾随数据及不符长度。返回值只含非秘密结构摘要，无原始令牌、密文或明文。
- `make_challenge(key, challenge)` 按当前合同生成 45 字节 compact SYN：AES128-CBC，IV 为 0..15，16 字节 challenge + 10 字节随机填充 + `tsf4g` + 0x10，32 字节密文前置单字节长度。
- `verify_challenge_reply(frame, key, challenge)` 接受 45 字节 compact 或 61 字节异步 SYNACK，校验 challenge 解密尾标及填充长度，并常量时间核对 16 字节 challenge。异步形式额外正文仍使用 bootstrap key/IV，要求解密后为 4 字节 BE 序号、6 字节随机填充、`tsf4g`、0x0c；序号起点随机，不猜 0/1，不输出实际序号。格式错误抛出固定错误码 `TransportError(ValueError)`，合法结构中的密文/尾标/challenge 不符返回 false。
- 涉及模块、类、函数和关键边界均含中文说明。独立子审阅未发现实质问题；该审阅只读，未重复执行测试。

## 未实现

- 认证接收器不处理 CHGSKEY 或业务帧；后续显式换钥和单向业务取证另有独立 API。没有角色/大厅/资产业务响应，也不将 challenge 校验称为完整原生登录成功。
- 本模块没有提供通用多账号服务、会话分配、重放记录或业务状态机；调用方必须提供本次唯一预期身份并为每连接生成新的随机 challenge。
- 当前已证字段/加密格式的实机消费证据与启动探针由父任务归档。本批工具测试不能代替实机、单端游戏或双端验收。

## 当前阻塞

无本批模块测试阻塞。下一步由父任务接入有界本机探针，采集实际 SYNACK 结果并继续协议研究。

## 验证记录

Windows、本机 `.venv`，`cryptography==50.0.2` 来自既有锁定开发依赖。直接运行：

```powershell
.venv/Scripts/python.exe -m unittest tests.test_native_transport -v
```

**8/8 通过、0 跳过、0 失败**。随后以同一 unittest 测试集与项目既有 `tools.run_tests.Result` 保存报告 `evidence/aliyun-native-transport-boundary-tests.json/.txt`，实际 0.125 秒。

在父任务实机第 14 次探针发现 SYNACK 被旧零正文约束拒绝后，当前构建静态证据补齐了异步发送路径。认证研究 profile 升为 `nz-current-auth-challenge-research-v2`，新增三项异步 SYNACK 测试，`evidence/aliyun-native-transport-synack-v2-tests.json/.txt` 为 **11/11 通过、0 跳过、0.047 秒**。8 项包含在 11 项中，不相加统计。

覆盖真实 socket 分片、连续帧不超读、截断 EOF、超时、非法长度/上限/正文/头标志/未知命令、正确及错误 AUTH 身份与令牌、当前 60 字节 AUTH 形状、标量与令牌边界、独立解密核对 SYN 明文、合法 SYNACK、错 key/challenge/footer/填充长度/密文、加密输入长度错误。

## 接手与恢复

`receive_frame` 的 `max_bytes` 为仅关键字参数；socket 必须由调用方预先设置超时，生命周期和关闭也由调用方负责。处理顺序为接收 AUTH → `validate_auth` → 新随机 challenge → 发送 SYN → 接收 SYNACK → `verify_challenge_reply`，本模块没有发送函数或后续业务状态迁移。

仅通过验证时才能记录该边界通过；异常统一按失败证据处理，不将输入原文写入公开报告。模块不修改客户端和配置，无客户端回滚操作。
