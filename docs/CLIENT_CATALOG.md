# 当前客户端资产目录与 MySQL SQL

本工具仅读取用户提供的当前客户端，输出内容定义目录。现有账号、玩家库存、资产账本和 SQLite 后台没有迁移；没有启动客户端、修改配置或接入游戏协议。

## 提取与复验

在 `repository/` 根目录运行，Python 3.12+。解码依赖独立记录在 `requirements-catalog.txt`；本次使用预装的 cryptography 50.0.2。首次提取在独立环境安装该依赖，现有控制面服务仍无第三方依赖。

```powershell
python -m pip install -r requirements-catalog.txt
python -m tools.client_catalog --client-root ../nz --output ../evidence/private/client-catalog
python -m tools.client_catalog_verify --output ../evidence/private/client-catalog
python -m unittest tests.test_client_catalog -v
```

已经生成过的非空输出目录不会被覆盖。重新扫描时使用新的输出目录，例如 `client-catalog-next`。快照 ID 来自所有客户端文件的路径、大小、SHA256，以及提取程序和外部补充的摘要；解析逻辑改变也会产生新目录快照，客户端本体摘要单独保存。目录内部 ID 与原始客户端 ID 分开。完整运行包含两次全文件哈希与文件集合核对，约 97 GiB 样本需要时间。

如只需改进分类或解析，允许对已双重校验的历史提取产物重新生成，不重读客户端二进制：

```powershell
python -m tools.client_catalog --rebuild-from ../evidence/private/client-catalog --output ../evidence/private/client-catalog-next
```

重建校验原交付中的文件与原始配置摘要，记录原始快照与客户端校验时间。它代表重建已捕获内容，不代表重新验证当前磁盘上的客户端。

若解码器改进后需要重试此前失败的文本，可在重建命令附加 `--refresh-failed-text-from ../nz`。只读这些失败文本，逐个核对原始 SHA256 后解码；内容改变立即拒绝，重试成功的文件会写入来源记录。

若从另一个位置调用，显式指定真实客户端目录和客户端之外的输出目录。源码中的资源解码格式参考固定上游 `af-emulator` 的 MIT 工具，仅使用只读 AES/zlib 解码；没有调用其修复、翻译安装或重编码功能。许可证见 `third_party/af-emulator/LICENSE`。

## 产物入口

默认完整产物在仓库上一级的 `evidence/private/client-catalog/`：

- `md/README.md`：总览、分类入口、数量和限制。
- `json/`：完整文件台账、原始配置、条目、物品、武器、模式、地图、功能、商城、证据和缺口。
- `md/`：对应的全量中文 Markdown，按每页 250 条分页；各页 JSON 详情与结构化文件一致。
- `sql/001_schema.sql`：19 张 `nzcat_` 前缀目录表。
- `sql/002_seed.sql`：全部目录数据，按快照隔离并支持同快照重复导入。
- `sql/003_verify.sql`：预期行数、孤立引用、重复 ID、缺口分组检查。
- `IMPORT_MYSQL.md`：MySQL 5.7.8+ 与 8 系列的导入、重复导入及查询说明。
- `summary.json`、`verification.json`、`delivery-manifest.json`：覆盖、静态校验及交付文件摘要。

客户端原始文件和完整私人目录产物受已有忽略规则保护，不自动提交；工具、测试、字段与操作说明可以进入仓库。

## 内容解释

武器/物品常用字段独立整理，并保留其原始嵌套字段。武器类与物品内部名称匹配是一种客户端证据，不将 ID 范围猜测写成官方分类。配置节和语言节包含玩家功能以及底层支持类，目录明确记录类型。

模式包括明确的 `GameModeSettings` 数字 ID、`DefaultMapPrefixes` 类与前缀，以及可关联的类规则。地图区分逻辑地图和物理资源；主图、子关卡和旧文件不会简单相加宣称为可选地图数量。类继承、全局默认值和服务器限制没有证据时不推断。

商城价格、期限、礼包和显示状态按客户端原值记录。未知期限单位、负值、货币含义和礼包数量不自行解释；客户端列表存在不代表当前在售。隐藏、测试、移除指令和重复定义均保留，最终生效状态需另行验证。

所有配置原始行、重复键、数组、嵌套字段、继承路径和未知语法仍可在原始记录中查询。二进制定义与包未取得可靠解析时标记 `BINARY_DEFINITION_UNDECODED` / `PACKAGE_UNDECODED`，全零文件与解码失败另外登记。

功能条目保存字段与 `record_id` 索引，完整值在原始配置表中保存一次；避免整份物品库或商城库反复嵌入功能 JSON，并确保生成的每条 SQL 小于 4 MiB。服务器 `max_allowed_packet` 应至少为 4 MiB；本轮未修改任何 MySQL 配置。

文本末尾的零填充记录字节数后单独处理；旧式西文 CP1252 编码记录为可追踪的回退解释。不会用替换字符吞掉无法解码的字节。

只解码 `TGame/` 与 `Engine/Config/` 的内容文本。平台组件、启动器、日志和账号缓存只登记文件摘要，不复制它们可能含有的私人内容；所有文件仍进入覆盖台账。

## 外部补充与验证范围

外部补充规则和已访问来源见 `config/client-catalog-external.json`。仅关联本地线索对应条目，不创建历史全集，不覆盖本地名称或数值。官网当前活动和访问失败的历史链接不作为当前样本资产的验证。历史类名对应属于人工审查的推断，会记录匹配规则和版本未核验状态。

静态验证逐条检查 JSON/MD/SQL 的字段与数量、所有 SQL 主键、外键引用和交付文件摘要。基础测试覆盖中文编码、损坏密文、嵌套配置、重复键、特殊字符、未解析物品引用、客户端改动检测和产物篡改。

**2026-10-15 实际导入已验证。** 经用户单独授权，`tools/catalog_mysql_import.py` 在独立 catalog 库导入 19 表，执行生成的校验 SQL，并重复导入确认数量不变、引用孤立为零；[实际报告](../evidence/m0-m5-catalog-mysql.json)。玩家业务库未被此导入器写入；派生 ID、价格及规则未提升为运行目录。目录导入不能代表原客户端功能、多人战斗或游戏内资产同步已通过。首次校验的字符串分号拆分错误及修复见 [本轮交接](delivery/2026-10-15-m0-m5.md)。
