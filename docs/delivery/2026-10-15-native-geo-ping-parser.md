# 当前 GEO PingREQ 最小解析器

日期：2026-10-15。范围：`adapters/cn_current/geo.py`、`tests/test_native_geo.py`。只解析当前已证请求，不运行游戏、访问数据库、发送邮件或业务响应。

## 已实现

- `parse_ping_request(payload: bytes)` 只接受精确9字节：大端 uint16 Magic=0x8202、Cmd=0x1001、HeadLen=8、BodyLen=1，随后一个 IsForceLogin 字节。
- 返回 profile、消息名、四个头字段和原始 `is_force_login` 整数；不将未知非零值解释为布尔或强制登录授权。请求 profile 为 `nz-current-geo-ping-request-v1`。
- 拒绝其他命令、magic、大小端、声明长度、截断、尾随帧或错误输入类型。`GeoProtocolError` 仅包含固定错误码，不回显业务字节。
- 已证依据为 `evidence/aliyun-current-geo-zone-metadata.json` 的当前 GEO 字段/命令，以及父任务两次真实客户端首包公开摘要。9字节夹具 `820210010008000100` 的 SHA256 为 `62c85c113786fadf47bfcb66180e9ba50eb1a443f051fbb02d2681618cda5324`，与这两次摘要一致。
- 模块、类、函数和关键约束均含中文说明。

## 未实现

没有 PingList 响应、目录/区服响应、角色协议或大厅协议编码；响应字段、RankInfo、字符串和数组线格式等待当前 DLL pack/unpack 的独立证据。没有从 PH 结构直接套用响应布局。

## 当前阻塞

无解析器专项阻塞。真实响应路径仍需当前构建序列化合同及实机验证，不能把首包解析成功当成大厅完成。

## 验证记录

本机 Windows `.venv`，执行 `.venv/Scripts/python.exe -m unittest tests.test_native_geo -v`；**5/5 通过、0 跳过、0 失败**。另用同一 unittest 集和项目 `tools.run_tests.Result` 保存 `evidence/aliyun-native-geo-ping-parser-tests.json/.txt`。

五项覆盖真实首包摘要/字段、所有截断长度及尾随数据/类型、未知magic/命令/小端、声明头体长度不符，以及单字节边界值原样保留。测试没有网络发送或真实客户端调用。

## 接手与恢复

调用方应在已认证的当前 transport 业务解密后，把精确业务 bytes 传给本解析器；解析返回值只说明该消息符合已证请求形状，不授予账号权限。未知消息应停止响应并保留私人证据。

没有客户端、数据库或部署变化，无相应回滚操作。
