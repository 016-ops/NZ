# 活动独立页面与意见反馈后端交接

## 目标与范围

- 日期：2026-10-20（America/Los_Angeles）。
- 新增 `revival/feedback.py`、`media.py`、`activitypresentation.py`、`feedback_models.py`、迁移 `0025_feedback_and_campaign_presentation`；扩展 `campaigns.py` 和测试。
- HTTP路由/中间件/本机Launcher桥与前端由父任务集成；本批只读审阅HTTP接入，并以Django Client验证真实路由/中间件，不读取服务器配置或运行秘密，不连接远端。

## 已实现

- 反馈归属认证玩家，正文纯文本；Owner/users处理、auditor只读、resources拒绝。创建/回复/状态更新复用Service事务、审计、幂等；回复/状态带 `expected_revision`。四态为 OPEN、IN_PROGRESS、RESOLVED、CLOSED；RESOLVED收到回复重开OPEN，CLOSED只读。
- 每工单最多3张截图，每张输入/输出均不超过5MiB。Pillow真解码PNG/JPEG/WebP，拒绝动画、超过1600万像素或8192边长的图片；统一WebP重编码并去除元数据。私图每次读都查会话与归属，不能通过公共媒体地址读取。
- 正文先提交，附件单独上传。`upload_key`按工单/上传者隔离，同图同键返回原图片，不同原图同键409；事务回滚清理新文件，提交应答未知先回读引用，防止误删已经提交的图片。
- 活动展示保存独立slug、标题、摘要、主题、封面、区块及CAS修订，不更改冻结奖励。旧活动迁移回填 `slug=id`；发布后slug锁定，草稿只对活动管理只读角色可见。公共封面与私图分表分目录。
- 0025只新增五表，依赖 `0024_email_admin_auth`。应用回退保留新表，不反向迁移删除反馈或媒体数据。

## 接口合同

通用命令继续要求 `request_id/idempotency_key/reason`：

| action | 业务字段 | 返回 |
| --- | --- | --- |
| feedback.create | title、body | ticket_id、revision=1、state=OPEN |
| feedback.reply | ticket_id、expected_revision、body | ticket_id、message_id、revision、state |
| feedback.status | ticket_id、expected_revision、state | ticket_id、revision、state |
| campaign.presentation.save | campaign_id、expected_revision、presentation | 更新后的presentation |

展示字段：`presentation={slug,title,summary,theme,cover_id,sections}`。theme为default/tactical/anniversary；sections最多12项，每项 `{key,kind,title,body?}`，kind为text/rewards/leaderboards，正文只作纯文本。封面只可引用public_media，不能填写任意外链或私图ID。

| view | query | 返回 |
| --- | --- | --- |
| feedback | offset、limit、state（均可选） | 本人 `{tickets,total,offset,limit}` |
| feedback-admin | 同上 | 管理列表同结构 |
| feedback-detail | ticket_id | ticket平铺，另含messages和attachments |
| campaign-detail | slug（或campaign_id） | 单个活动与presentation；玩家有本人进度，游客无进度 |
| campaigns / campaign-admin | 沿用 | 每个活动追加presentation |

ticket为 `{id,account_id,title,state,revision,created_at,updated_at,message_count,attachment_count}`；message为 `{id,body,author_kind,created_at}`。分页默认offset0/limit25，上限100；HTTP字符串需严格转换为整数。

| HTTP集成入口 | Python hook | 返回 |
| --- | --- | --- |
| POST /api/feedback/{ticket_id}/attachments，multipart(file,upload_key) | media.upload_feedback(service,token,ticket_id,uploaded,upload_key) | `{id,url,mime,width,height,bytes,created_at,ticket_revision}` |
| GET /api/feedback/attachments/{id}，Bearer必需 | media.read_feedback_attachment(service,token,id) | `(payload_bytes,mime)` |
| POST /api/admin/media，multipart(file,upload_key) | media.upload_public_cover(service,token,uploaded,upload_key) | `{id,url,mime,width,height,bytes}` |
| GET /api/media/{id} | media.read_public_cover(service,id) | `(payload_bytes,mime)` |

- 成功附件会增加工单revision，前端上传后回读详情；读私图使用认证fetch/blob或受限本机代理，不能让无认证img直接读取。
- `GET /api/public/campaign-detail?slug=...` 为游客独立活动详情；仅公开已发布内容，不返回本人进度。`POST /api/player/view` 的读取信封为 `{view,query}` → `{data:...}`，反馈分页支持URLSearchParams产生的整数文本，布尔值拒绝。
- 私图响应应为 `Cache-Control: private,no-store` 与 `X-Content-Type-Options: nosniff`；multipart额度只提升上述两个上传路径，其余JSON正文限制保持。
- 文件根通过 `NZ_MEDIA_ROOT` 配置为独立可写目录（例如 `/var/lib/nz-web/media`），不能由Nginx静态映射私图目录。应用默认仅本机 `runtime/media`；运行用户需要写权限。

## 未实现内容

- 本批反馈、媒体、活动展示领域功能无未实现内容。浏览器与Windows本机桥的端到端上传由父任务验证；本批已覆盖HTTP和领域层，不把Django Client结果称作浏览器/Windows实机验收。
- 未进行真实SMTP投递、远端部署或重新生成发行包；本批不读取运行秘密，不连接远端。

## 当前阻塞

- 无。HTTP审阅发现的公开详情路由、重复upload_key、过大文本表单及损坏boundary均已由父任务修复并实际回归；Nginx模板已仅对精确上传路径增加6m传输额度。
- 运维注意：结果未知而数据库也无法回读时，保留无法直接访问的潜在孤立文件；后续离线按数据库引用对账清理，不能直接删已提交图片。这是保护已提交文件的失败处理策略，不表示附件上传依赖外部协调。

## 验证记录

- Pillow==13.0.0已从官方PyPI安装。图片纯内存3项通过，覆盖允许格式、元数据清理、伪文件/截断/动画/字节与像素限制；使用显式内存Django设置，无项目环境文件或数据库连接。
- 新模型/业务/迁移/测试 `py_compile` 通过；当前全模型最小Django配置 `makemigrations --check --dry-run` 无模型差异（dummy backend仅用于静态迁移比较，不宣称数据库行为验证）。
- 独立 `nz_fix_feedback_tests` MySQL专项首轮35/35通过、0失败、0错误、0跳过，44.776秒；服务器实际 `SELECT VERSION()` 为26.7.0。使用明确空环境文件和独立测试库，不复用业务库或auth测试库。证据：`evidence/2026-10-20-feedback-presentation-tests.json`/`.txt`。
- 首轮包括反馈13项、活动19项、图片3项：CAS/重开/关闭、角色降权后的幂等拒绝、四连接同时上传总限、事务/审计回滚与提交应答丢失、真实multipart/私图GET与安全头、命名空间隔离、文件额度及公开slug。
- HTTP增强定向复验2/2通过，0失败/错误/跳过，4.969秒；证据 `evidence/2026-10-20-feedback-http-boundaries-tests.json`/`.txt`。涵盖分页整数文本/bool拒绝、匿名不解码、重复字段、多文件、无效图、128KiB表单文本413、损坏boundary400及不落盘。初次定向曾实际复现大文本字段500，修复后重跑并保存最终结果；这2项属于原35项中的增强复验，不当作额外两个唯一用例重复计数。
- 中文注释覆盖新模块、类、方法、权限、CAS、幂等及失败清理边界；本批文件 `git diff --check` 通过。
- `docs/API.md`只追加末尾“反馈、私有截图与活动独立页面”完整合同；auth前部由并行后端任务更新，没有整文件覆盖。HTTP审阅只读父任务负责的views/middleware/routes，修复均由父任务写入。

## 接手与回退

确认Pillow固定依赖和 `NZ_MEDIA_ROOT`；先备份业务库，再向前执行0024/0025。HTTP上传必须先走认证和明确的multipart白名单，媒体秘密不进入命令参数或日志。

回退应用时保留新表、图片目录和当前业务库；不反向执行删除这些表的迁移，不将私图目录转换成公共静态目录。

## 第二轮跨端审阅与公共封面修正

- 只读审阅确认发行`PublicApiClient`缺反馈媒体方法、Launcher补验邮箱携带多余password字段、公共封面相对地址被错误请求至本机服务三处集成差异；前两处由父任务/前端任务修复，本批负责最后一处。
- 新增固定`campaign-cover-image`本机操作，只接受小写32位hex媒体ID；Controller仍要求随机能力和登录。`Client/PublicApiClient.public_cover_image`只GET固定公共媒体路径，不发送玩家Bearer，复用已有TLS、无跳转、5MiB和WebP响应校验，不增加外部URL输入。
- 新增共享`CampaignCover`：Web保留同源src；Launcher仅将严格`/api/media/{id}`交给`RuntimeApi.readCampaignCover`，回环base64在解码前后均检查预算和MIME，转换Blob；组件切换/卸载撤销URL，迟到响应不覆盖新活动。
- 修改范围：`tools/email_launcher.py`、`launcher_runtime.py`、`native_launcher.py`的少量媒体桥；`admin/src/lib/platform.ts`、`campaign-cover.ts`、新`components/campaign-cover.tsx`、`platform-pages.tsx`两处图片调用、`native-launcher.tsx`对应方法。没有更改奖励、认证、私图角色或普通JSON额度。
- 真实回环HTTP+实际PublicApiClient+Controller/endpoint新增5项与相邻工具回归共22/22通过，0跳过，2.532秒；证据`evidence/2026-10-20-campaign-cover-bridge-tests.json`。包括远端无Bearer、控制器能力/登录/来源门禁、固定路径拒绝、错误MIME、5MiB响应超限、302不跟随、HTTPS系统证书工厂。
- 前端全套15/15通过（5个文件），本批新增3项固定URL及base64解码边界；`npm run build`类型检查与生产构建通过。保留既有Vite大chunk提示，未宣称本批完成全站拆包优化。
- 本批仍未进行Windows冻结包重新构建、WebView实机、远端发布或真实SMTP投递；回环网关用于传输合同验证，不是游戏/正式服务器验收。
