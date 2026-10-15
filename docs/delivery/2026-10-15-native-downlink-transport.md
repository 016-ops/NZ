# 当前研究下行业务封装

日期：2026-10-15。范围：`adapters/cn_current/transport.py`、`tests/test_native_transport.py`。依据父任务对当前 `ProtocalHandler.dll` 活动接收路径 RVA 0x64750 的核对：该路径不读取 head+3、不剥离序号，AES3 解密正文后直接入业务队列。本批只实现封装边界，不构造或解释 GEO 内容，不修改启动探针。

## 已实现

- 新增 `DOWNLINK_PROFILE=nz-current-application-downlink-research-v1` 和 `make_application_response(application: bytes, key: bytes)`。
- 仅接受非空业务 bytes、精确16字节 key；预先计算自定义 padding6..21及密文长度，总帧超过65536字节时在加密前拒绝。
- 下行固定 cmd0/EncHeadLen0/HeadLen12，BodyLen为全部AES3密文长度；明文是调用方完整业务字节、随机填充、`tsf4g` 和一字节padding长度，使用当前已证 IV 00..0F。没有添加上行的4字节序号。
- EncHeadLen0 是无加密序号头的显式研究选择。独立下行 profile 保留其与已证上行 EncHeadLen4 的差异，旧认证接收器和上行解码器范围不放宽。
- 新增API、测试及关键约束含中文说明。

## 未实现

没有生成任何 GEO 响应字段、消息ID或业务成功结果，没有角色、区服、大厅业务规则。实际下行帧须由调用方在认证/challenge/换钥通过后，使用当前构建已验证的业务序列化结果生成。

本批没有向真实游戏发送下行，工具测试不能代替当前客户端业务响应验收。

## 当前阻塞

无封装器专项阻塞。真实GEO响应仍等待当前DLL pack/unpack合同及父任务实机验证。

## 验证记录

本机 Windows `.venv`、已有锁定 cryptography，运行：

```powershell
.venv/Scripts/python.exe -m tools.run_tests --tooling --pattern test_native_transport.py --output evidence/aliyun-native-downlink-transport-tests.json
```

**23/23 通过、0 跳过、0 失败、0.063 秒**。证据 `evidence/aliyun-native-downlink-transport-tests.json/.txt`。包含之前19项和新增4项，不与历史测试数量相加。

新增测试通过独立密码库解密验证完整业务无序号；覆盖全部padding6..21及随机字节数量、最大业务65514字节生成65532字节帧、65515字节起超限拒绝，以及空业务、非bytes、key类型/长度边界。上行解码器仍拒绝该下行帧，避免错误剥除业务前4字节。

## 接手与恢复

调用方负责连接状态、已核验的业务序列化内容和当前会话key，本API只返回待发送bytes，不执行网络发送。未知消息不得凭此封装能力构造响应。模块不改客户端文件、数据库或外部服务，无相应回滚操作。
