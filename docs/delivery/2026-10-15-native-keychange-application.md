# 显式研究换钥与单向业务取证

日期：2026-10-15。范围：`adapters/cn_current/transport.py`、`tests/test_native_transport.py`。依据 `evidence/aliyun-native-post-syn-static.json` 的当前 `ProtocalHandler.dll` 合同（SHA256 `e28a7da8e6668feb65f5573819714738bce7210b79c49dfe287a63f2be3da228`），新增独立研究 API。本批不修改启动探针、不运行游戏、不访问数据库或邮件。

## 已实现

- 新增独立 `APPLICATION_PROFILE=nz-current-keychange-application-research-v1`。既有 `TRANSPORT_PROFILE=nz-current-auth-challenge-research-v2` 和认证 `receive_frame` 范围不变，继续拒绝 cmd0/cmd1 入站。
- `make_key_change(old_key, new_key)`：只接收精确 16 字节 key。生成 48 字节 CHGSKEY，cmd1/EncHeadLen0/HeadLen48/BodyLen0；扩展为 BE16 Type0、BE16 密文长度32、32字节 AES3 密文。旧 bootstrap key、固定 IV 00..0F 加密新 key + 10 随机字节 + `tsf4g` + 0x10。Type0 是当前解析器忽略字段的明确研究选择，不作生产语义保证。
- `receive_application_frame(stream, max_bytes=65536)`：独立只接收 cmd0/EncHeadLen4/HeadLen12、BodyLen 至少16且是16倍数、总帧不超过64KiB。兼容 TCP 分片，只消费一帧，截断和不符格式拒绝。
- `decode_application(frame, new_key)`：再次独立核验完整边界，使用新 key 解密；核验 footer、padding6..21、当前 canonical padding 公式、去padding后至少4字节，移除恰好4字节 BE 序号后只返回业务 bytes。该返回值供调用方私人存证，不输出序号或自动解释业务。
- 所有异常只使用固定错误码，不包含 key、令牌、明文或密文；新增函数和关键约束有中文说明。

## 未实现

- 模块没有通用会话或连接状态机。调用方必须先验证本次预期 AUTH 身份、再通过 challenge，随后生成新随机 key 并显式调用换钥 API；只有发送该换钥帧之后的帧使用新 key。
- 未实施未知消息 ID、TDR 业务规则或角色/大厅/资产业务响应，也没有 IDENT 分配、重连身份和业务重放控制。
- 本批没有实机发送或验证 CHGSKEY。实际接受、业务首包和后续大厅结果由父任务启动探针独立记录，不能由本工具测试推定。

## 当前阻塞

无模块专项阻塞。实际游戏业务协议仍需父任务采包及当前构建证据确定；这不妨碍本模块作为单向取证边界使用。

## 验证记录

本机 Windows `.venv`、已有锁定 `cryptography==50.0.2`，真实临时 socket 与公开固定加密夹具，不访问 MySQL。

```powershell
.venv/Scripts/python.exe -m tools.run_tests --tooling --pattern test_native_transport.py --output evidence/aliyun-native-keychange-application-final-tests.json
```

最终 **19/19 通过、0 跳过、0 失败、0.047 秒**。证据：`evidence/aliyun-native-keychange-application-final-tests.json/.txt`。保留早一轮 `evidence/aliyun-native-keychange-application-tests.json/.txt`；两轮都是同19项，不相加。

19 项包括既有11项认证/challenge测试与新增8项：CHGSKEY 精确格式和独立解密、key边界、业务分片/不超读/截断、头及总长度门禁、旧认证接口拒绝新命令、变长padding与最大65532字节合法帧、错key/footer/padding/密文，以及解码入口完整帧/密钥边界。空业务体可返回空bytes，不能据此宣称收到业务登录。

## 接手与恢复

调用方负责顺序、socket超时/绝对截止时间、连接关闭，以及新 key 在内存中的生命周期。原始帧和解密 bytes 只进入私有证据目录，公开结果只应包含是否验证通过、长度和摘要；未知业务不得自动回包。

模块没有写客户端配置、数据库或外部服务，故无相应回滚；删除本次生成的私有研究材料需按父任务证据保留规则处理。先前认证 profile 和报告保留，不能用新19项结果覆盖旧失败证据。
