# DEV 控制面部署与 SSH 隧道

2026-10-15 设置增量发布 **20261015T111056-a6ef9c556708**：新增0007原生设置表及游戏会话读写接口，MySQL CAS/事务/恢复验收通过。四服务、两库完整性、各六个保护触发器和发布前后既有账号角色/资产视图一致。此为控制面增量，原生大厅、房间和在线可玩交付仍未完成；离线暂缓。见 [M0–M6交接](delivery/2026-10-15-m0-m6.md)、[发布](../evidence/m0-m6-native-settings-dev-deploy.json)与[核验](../evidence/m0-m6-native-settings-dev-verification.json)。

2026-10-15 最新发布 **20261015T061412-1cfd594907ca** 已部署邮箱恢复/补验幂等、注册并发重放修复、前端请求期间锁定和随机内部别名。四个服务 active、两环境前端与资源、匿名权限拒绝、邮箱能力、两库完整性及各六个保护触发器通过；仅监听回环 18765/18767，重登后 game_id、角色、verified 状态和资产视图保持。本次发布验证追加邮件 **0 封**，原生大厅仍未通过。见[最终发布报告](../evidence/aliyun-email-final-dev-deploy.json)、[最终运行核验](../evidence/aliyun-email-final-dev-verification.json)及[本批交接](delivery/2026-10-15-aliyun-email-and-native-launch.md)。

此前发布 **20261015T054145-873d60549d7d** 加入阿里云 SMTP、migration 0006、验证码注册、找回密码与旧邮箱验证，并完成三封真实邮件的闭环验收。原报告 `evidence/aliyun-email-dev-deploy.json`、`evidence/aliyun-email-live.json`、`evidence/aliyun-email-restart.json` 保留。下述更早记录继续保留，不把任一控制面发布当作原生游戏通过。

2026-10-15 后续发布 **20261015T050503-a7827976c028** 已取代下述初次发布。独立邮箱入口、migration 0005、两环境实际注册/审批/登录及角色重启保持、DEV 令牌到本机 Win32 组件读取通过；四服务 active、两库完整性及六个历史触发器各自保留。详见 [邮箱交接](delivery/2026-10-15-independent-email-login.md) 和 `evidence/email-login-dev-*.json`。仍未接通完整游戏大厅或游戏节点。下列初次部署记录保留，不覆盖历史报告。

这是 Django/React 控制面的 DEV 环境，原客户端游戏节点尚未运行。用户于 2026-10-15 提供 `../DEV环境服务器.txt` 并明确选择 SSH 隧道访问。地址、系统账号和密码不进入文档；数据库使用本机忽略的 `runtime/user-mysql.env`。

后续 Windows 本机研究工具新增配置恢复守卫、网络取样和 IPC v2 语义修正，第 13 次捕获 60 字节 AUTH，第 15 次通过 challenge，第 16 次换钥后收到 9 字节 GEO PingList，第 17 次以真实 DEV 邮箱身份复现。18/19 已消费单条回环 GEO Ping/Zone 目录；20/21 的真实邮箱身份进入第二条已认证 Zone 连接并发出 A000。后几次握手和业务包接收仍在本机受控研究监听器中完成；这些本机工具变化未改变本节 DEV 邮箱 release。DEV 的 `native_launch_ready` 继续为 false，第 22/23 次本机 A001 研究响应已消费，角色或大厅服务未通过。Windows 房间目标已完成 SSH 盘点和专属工作目录准备，但没有安装原生节点，见[单服务器交接](delivery/2026-10-15-single-room-server.md)。

## 环境与访问

实际发布 `20261015T033015-409cbc699d98` 已成功安装并完成验证：四个服务 active，两个控制台的前端资源、正常 Owner MFA、匿名/跨来源拒绝通过；重启四个服务后提交记录保持，探针邀请已撤销。两库实际 MySQL 8.0.30-txsql 完整性正常，各有六个历史保护触发器。证据为 `evidence/m0-m5-dev-deploy.json`、`evidence/m0-m5-dev-verification.json`；无凭据页面截图为 `evidence/m0-m5-dev-console.jpg`。

CPython 源码 SHA256：fb85a13414b028c49ba18bbd523c2d055a30b56b18b92ce454ea2c51edc656c4，本机下载与服务器实际构建来源摘要一致。没有将编译过程中的警告或未运行的 CPython 全套测试宣称为通过。

目标服务器为 Ubuntu 22.04.5 LTS，系统 Python 3.10.12 保留。项目需要 Python 3.12，因此从 python.org 的固定 CPython 3.12.12 源码构建到 `/opt/nz-revival/shared/python312`；Django 等依赖使用仓库 `requirements.txt`。首次 GitHub 托管 Python 下载超时，未作为成功发布。构建产物与源码摘要、最终发布状态以 `evidence/m0-m5-dev-verification.json` 为准。

| 用途 | 服务器监听 / 隧道本机端口 | 配置 |
|---|---|---|
| 当前客户端控制台 | 127.0.0.1:18765 | current / cn-current-unverified，真实接入仍 BLOCKED |
| 合成 PvE 控制台 | 127.0.0.1:18767 | synthetic + test-pve-v1，独立 PvE 库 |
| MySQL | 用户提供的已有实例 | 当前/普通合成/PvE/可销毁测试/catalog 分库 |

在仓库根目录运行：

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m tools.dev_tunnel --source ../DEV环境服务器.txt
```

然后访问 [DEV 当前控制台](http://127.0.0.1:18765) 或 [DEV 合成 PvE](http://127.0.0.1:18767)。隧道必须保持运行；Ctrl+C 只关闭本机隧道，服务器服务继续运行。两个端口使用相同的本机/远端编号以保留 Host/Origin 校验，不能任意替换为其他本机端口。

首个 SSH 主机公钥记录在忽略的 `runtime/dev-known-hosts`，后续变更拒绝；这是首次连接信任记录，不是独立第三方指纹认证。实际指纹见 `evidence/m0-m5-dev-inspection.json`。没有开放公网管理 HTTP 端口、改变防火墙或放宽 Host/Origin。

当前库、PvE 库分别建立 `dev-owner`，随机独立密码、MFA URI 和恢复码仅在本机 `runtime/dev-owner-current.json`、`runtime/dev-owner-pve.json`。账号使用正常 TOTP，无固定码/免码例外。通过受保护的本地方式查看并加入验证器，勿把文件放进 Git/聊天/公开日志。本轮临时 UI 夹具 qa-owner 已由新 Owner 正常再认证停用并撤销会话，既有账号密码未重置。

## 发布入口与目录

```powershell
# 先完成 MySQL 回归及 admin 下的 npm run build、npm test。
.venv/Scripts/python.exe -m tools.deploy_dev --source ../DEV环境服务器.txt --env-file runtime/user-mysql.env

# 首次安装或有意更新邮件配置时显式附加；不传该参数会保留服务器现有 mail.env。
.venv/Scripts/python.exe -m tools.deploy_dev --source ../DEV环境服务器.txt --env-file runtime/user-mysql.env --mail-env-file runtime/aliyun-mail.env
```

`deploy_dev.py` 仅打包 revival/nzproject/adapters/config、Vite 构建产物和运行入口/依赖。客户端、私人提取目录/SQL、账号库、node_modules 和 Windows MySQL 不上传。归档 SHA256 记录在公开发布报告；环境文件单独经 SSH/SFTP 上传，写凭据前权限设为 0600，服务器持久文件为 root:nz-revival/0640。

`dev_install.sh` 仅接受受控发布 ID 和摘要。已存在项目目录或同名服务无管理标记时拒绝接管。新增系统包用于 Python/MySQL 驱动编译和备份客户端，不安装 MySQL 服务；独立应用用户 nz-revival 不可交互登录。每次发布使用新目录 `/opt/nz-revival/releases/<release_id>`，配置在 `/opt/nz-revival/shared/mysql.env`，运行链接为 `/opt/nz-revival/current`。

四个 systemd 服务：`nz-dev-current`、`nz-dev-current-maintain`、`nz-dev-pve`、`nz-dev-pve-maintain`。后台维护负责期限、租约、批次和超时退款。服务以 nz-revival 用户运行，启用 NoNewPrivileges、PrivateTmp、ProtectHome 和只读系统约束；只给 shared 写权限，日志进 journal。前端在本机构建后与后端同源发布。

邮件配置以允许变量单独上传，临时文件先设 0600，不进入发布归档；持久路径 `/opt/nz-revival/shared/mail.env` 为 root:nz-revival/0640，四个服务通过 NZ_MAIL_ENV_FILE 指向它。替换前保留受保护的 `mail-before-<release_id>.env`，缺少新的上传文件时保留现有配置。不要把环境文件打印到 journal、终端记录或发布报告。服务器出站需能连接对应 SMTP 465；管理页面仍只接受 SSH 隧道回环访问。

## 验证与恢复

安装程序退出 0 只是安装结果，最终还必须检查四个服务 active、两个健康/profile 接口、首页静态资源、未登录权限拒绝、实际 MySQL/迁移/六个历史触发器，以及服务重启后的恢复。记录见 `evidence/m0-m5-dev-verification.json`；原客户端单端/双端结果仍是未通过/未执行。

远端检查示例（通过已有 SSH 登录后执行）：

```bash
sudo systemctl status nz-dev-current nz-dev-pve nz-dev-current-maintain nz-dev-pve-maintain
sudo journalctl -u nz-dev-current -n 50 --no-pager
curl --fail http://127.0.0.1:18765/health
curl --fail http://127.0.0.1:18767/health
```

不在日志命令中输出环境文件。发布前后保留当前 MySQL 数据、审计、制裁和账本，迁移只向前执行。迁移失败不切换运行链接；首次失败发布目录保留用于排错。成功切换前记录旧链接到 shared/previous-release，旧发布目录保留。

本批最终当前库/PvE 库备份均在临时 MySQL 库恢复并逐表核对 migration 0006 之后的 36 张业务表，结果见 `evidence/aliyun-before-current-final-backup.json` 与 `evidence/aliyun-before-pve-final-backup.json`。这是比早期 35 表迁移前备份更新的恢复验证，仍不得用任意旧快照覆盖其后产生的安全和业务写入。

若新版本异常，先暂停写入并保留最新数据库，核对旧代码兼容当前迁移后再切换到明确检查过的旧 release 链接并重启这四个服务；不得恢复旧数据库覆盖新写入。需要完全停 DEV 时停止这四个服务即可，数据库及发布目录保留。实际控制面重启验证不代表 Windows 游戏实例崩溃恢复。

当前未验证公网 HTTPS、容量或数据库 TLS；实际 MySQL Ssl_cipher 为空的情况保留在总交付阻塞表。用户已选择 SSH 隧道管理访问，SSH 加密不等同数据库链路已启用 TLS。
