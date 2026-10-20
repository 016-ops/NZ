# Web 平台发布手册（2026-10-20）

本轮交付 Web 门户、管理员端、后台维护任务及可选监控采集器的发布准备。用户已要求暂停部署；当前未执行远程迁移、服务切换或数据库写入。以下步骤供后续明确恢复部署时使用。原 Windows 游戏节点和桌面 Launcher 不由 Web 发布脚本启动。

## 发布物

运行 `python tools/package_web.py` 生成 `outputs/portal-v2-2026-10-20/nz-web-platform-2026-10-20.tar.gz`、同名 `.sha256` 和 `.manifest.json`；首轮2026-10-19归档单独保留。归档内的 `RELEASE.json` 记录基线提交、逐文件摘要以及 `WORKING_TREE` 源码状态。工作树发布物不能被描述为某个提交的原样构建。

归档包含 Django 源码/迁移、固定兼容目录、已构建 React、服务模板、监控采集器与本手册。不包含数据库、运行目录、密钥、SMTP配置、游戏客户端、原生游戏服务端、桌面可执行程序或虚拟环境。四份 Excel 设计书单独交付。

## 目标环境和配置

确定目标系统后使用对应 Python 3.12+ 和 MySQL 驱动编译依赖安装 `requirements.txt`。前端已在归档内构建，目标机无需 Node。下列步骤以具备 systemd、Nginx 的 Linux 目标为例；不应直接执行旧 `tools/dev_install.sh`，该脚本管理历史 DEV 双环境。

1. 保留现有业务库；向用户指定的 MySQL 库先做逻辑备份。发布前记录旧服务目录、当前迁移及用户/资产总数。
2. 新发布解压至 `/opt/nz-web/releases/<release-id>`，核对外部 SHA256 与 `RELEASE.json` 的每个文件；解压仅接受普通文件/目录，拒绝绝对路径、`..` 和链接。
3. 创建专用运行用户、`/var/lib/nz-web` 可写目录；在新目录创建 `.venv` 并安装固定依赖。受保护文件放 `/etc/nz-web/`，只允许 root 与服务组读取。
4. `web.env` 配置 `NZ_DB_HOST`、`NZ_DB_PORT`、`NZ_DB_NAME`、`NZ_DB_USER`、`NZ_DB_PASSWORD`、私有随机 `NZ_SECRET_KEY`（至少32字符），并设置 `NZ_PUBLIC_ORIGIN=https://实际域名`、`NZ_MEDIA_ROOT=/var/lib/nz-web/media`。媒体目录由运行用户持有，公共封面与私图分别保存，不能放在只读release目录。已有环境必须保留原密钥以读取既有房间口令密文；新库使用独立随机值，公网配置缺失/默认密钥会拒绝启动。不得把真实凭据写入命令参数、归档或日志。`NZ_MAIL_ENV_FILE` 指向受保护`mail.env`，其中设置`NZ_MAIL_PROVIDER=aliyun-smtp`、允许的阿里云区域`NZ_MAIL_HOST`、`NZ_MAIL_PORT=465`、`NZ_MAIL_USERNAME/PASSWORD/FROM`；允许主机见`revival/mailing.py`。仅支持验证证书的TLS465。邮件是管理员登录与Owner高风险操作的硬前置，未配置或发送失败就拒绝认证，没有TOTP或免码后备。固定邮箱实际收件须单独验收。
5. 先停止旧Web与维护进程，用服务用户和相同 `NZ_ENV_FILE`、`NZ_PROFILE=current` 执行 `manage.py check`、`manage.py migrate --noinput`。本批包含 `0022_campaigns`、`0023_server_monitoring`、`0024_email_admin_auth`、`0025_feedback_and_campaign_presentation`；0024只切换管理员认证并撤销旧管理员会话/TOTP恢复码，不改变任何玩家状态，既有待审仍需管理员显式批准，不重发旧资产。0025增加反馈、消息、私图、公共封面、活动展示五张表，旧活动ID回填为稳定地址。完整空库初始化仍应保留最初迁移对不可变历史触发器的权限要求。
6. 复制 `deploy/nz-web.service.example`、`nz-web-maintain.service.example` 为实际 unit，按目标修改路径。迁移成功后原子切换 `/opt/nz-web/current` 到新目录，再启动 Web 与维护服务。只监听 `127.0.0.1:18765`。
7. `deploy/nginx-web.conf.example` 中填实际域名与证书路径，校验 Nginx 配置后启用 HTTPS 反代。只在精确截图上传与活动封面路径允许6m正文，普通请求仍64k；应用继续限制单图5MiB。不得将`private_feedback`静态映射到公网。`NZ_PUBLIC_ORIGIN` 必须精确匹配浏览器来源，包含非默认端口时也必须一致；不配置通配 Origin。仅本机Nginx反代时设置 `NZ_TRUST_LOOPBACK_PROXY=1`：它覆盖 `X-Real-IP`，服务仅在本机连接且精确公网Host时接受该头，登录/邮件限流才能按实际客户端区分；默认忽略转发头。
8. 新库首次建立 Owner 用 `manage.py bootstrap_owner <login>` 交互输入密码；随后通过原密码与固定管理员邮箱邮件码登录；不再生成TOTP/恢复码。已有Owner保留原密码，升级后重新申请邮件码登录，不重建或覆盖账号。

## 发布验收

逐项核验 `/health`、门户、管理员原密码+固定邮箱邮件码、旧待审不能通过登录/找回密码开通、ACTIVE已验证邮箱补角色、真实账号只读库存、管理员商城发布与平台商城读/报价/购买关闭、白名单保存/冲突提示、活动草稿/发布/普通奖励幂等、监控 ONLINE/STALE。管理操作使用显式测试账号，不能随机删除玩家道具。外部来源、未登录管理接口和错误监控令牌应被拒绝。

新增验收包括绑定业务内容的独立Owner验证码、活动独立地址/草稿404/发布后slug锁、反馈私图越权与关闭终态、图片重复上传/部分失败重试。Web与Launcher必须分别验证公共封面与私图传输；Mac布局预览不等同Windows WebView2或原生游戏验收。

榜奖由 `maintain` 对封榜快照逐人发放，失败目标单独重试；维护进程未运行时不能把 PENDING 当作发奖完成。正式游戏成绩入口仍拒绝未经可信原生结算证明的数据，本批不安排真实榜奖发放验收。活动时间/奖励尚未配置为正式开服活动，发布后由管理员设置。

## 服务器监控

Owner 在管理员监控页登记节点，仅首次响应显示 `node_token`。在被监控主机安装 `requirements-monitoring.txt`；受保护环境文件设置 `NZ_MONITOR_TOKEN`。按 `deploy/monitor-agent.service` 修改独立采集器路径与 HTTPS 门户地址。令牌不得放进 URL 或命令行。

CPU、内存、磁盘、网络累计计数和配置的系统服务状态每15秒采集。首次采样/重启/计数器复位的带宽未知；超过60秒显示 STALE 并清空实时值。原始7天、分钟聚合30天；维护任务负责离线节点的清理。带宽告警需要 Owner 填实际链路 Mbps，不能把网卡计数推断为运营商套餐。

## 回退

保留旧 release、数据库备份、媒体目录和原配置。0024已清除旧TOTP秘密并撤销旧会话，不能直接切回依赖TOTP的首轮发行版；应回退到保留本轮邮箱认证合同的兼容代码。保留0022至0025新增表与上传文件，不逆向删除活动/发奖/监控/反馈历史，不恢复旧管理员会话。涉及业务数据恢复必须先核对回退后新增的资产流水，不能用旧备份覆盖在线新增数据。凭据不随应用回退变更。
