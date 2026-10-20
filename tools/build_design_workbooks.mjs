/**
 * 用内置 artifact-tool 生成四份中文工程设计书。
 * 先运行 python -m tools.extract_design_sources；不访问业务数据库、不读取环境凭据。
 * 数据来自当前源码注册表，导出后逐张渲染并记录检查结果，便于后续改动重新生成。
 * 仅发布状态变化时：--workbooks=部署手册及打包清单 --render-only=部署总览,打包文件清单,交付与阻塞。
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const outputDir = path.join(root, 'outputs/01a15734-c809-7ff2-8313-f617805a4441')
const packages = process.env.NZ_ARTIFACT_NODE_MODULES || '/Users/zjh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
await fs.mkdir(outputDir, { recursive: true })
const moduleLink = path.join(outputDir, 'node_modules')
try { await fs.symlink(packages, moduleLink, 'dir') } catch (error) { if (error.code !== 'EEXIST') throw error }
const resolveArtifact = createRequire(path.join(outputDir, 'artifact-authoring.mjs'))
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(resolveArtifact.resolve('@oai/artifact-tool')).href)
const sources = JSON.parse(await fs.readFile(path.join(outputDir, 'design-sources.json'), 'utf8'))
// 发布记录与业务源码快照分开维护；仅更新部署状态时无需重提模型或重写其他三本。
const releaseStatus = JSON.parse(await fs.readFile(path.join(root, 'tools/design_release_status.json'), 'utf8'))
const selectedArgument = process.argv.find(value => value.startsWith('--workbooks='))
const selectedBooks = selectedArgument ? new Set(selectedArgument.slice('--workbooks='.length).split(',')) : null
const shouldBuild = name => !selectedBooks || selectedBooks.has(name)
const verificationPath = path.join(outputDir, 'design-workbooks-verification.json')
let previousVerification = {}
if (selectedBooks) {
  try { previousVerification = JSON.parse(await fs.readFile(verificationPath, 'utf8')) } catch (error) { if (error.code !== 'ENOENT') throw error }
}
let webPackage = null
const webArchive = path.join(outputDir, 'nz-web-platform-2026-10-19.tar.gz')
try {
  const bytes = await fs.readFile(webArchive)
  const manifestBytes = await fs.readFile(webArchive + '.manifest.json')
  webPackage = { file: path.basename(webArchive), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
    manifest: JSON.parse(manifestBytes.toString('utf8')), manifestBytes: manifestBytes.length,
    manifestSha256: createHash('sha256').update(manifestBytes).digest('hex') }
} catch (error) { if (error.code !== 'ENOENT') throw error }
const FONT = 'Arial'
const NAVY = '#16283F'
const INK = '#223349'
const FAINT = '#EDF2F7'
const verification = { ...previousVerification, asOf: sources.as_of, baselineCommit: sources.baseline_commit,
  workbooks: selectedBooks ? [...(previousVerification.workbooks || [])] : [] }
verification.releaseStatus = releaseStatus
verification.incrementalRun = selectedBooks ? { workbooks: [...selectedBooks], preservedOtherFiles: true } : null
// 最终仅复核改变内容的页；未改变页保留此前已经逐张审查的预览，不重复耗时渲染。
const renderArgument = process.argv.find(value => value.startsWith('--render-only='))
const renderOnly = renderArgument ? new Set(renderArgument.slice('--render-only='.length).split(',')) : null
verification.renderMode = renderOnly ? { changedSheets: [...renderOnly] } : 'all'
let tableCount = 0

/** 将列序号转换为Excel字母；仅针对当前已知有限列数，不访问工作簿内部实现。 */
function col(index) {
  let result = ''
  for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) result = String.fromCharCode(65 + ((value - 1) % 26)) + result
  return result
}

/** 将复杂源值序列化为可检索文字；数字和布尔值保持真正单元格类型。 */
function cell(value) {
  if (typeof value === 'string' && value.startsWith('=')) return "'" + value
  return value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : value
}

/** 读取代码片段的真实行号，生成可交接的源码定位；未匹配时保留文件路径而不编造行号。 */
async function source(file, text = '') {
  try {
    const lines = (await fs.readFile(path.join(root, file), 'utf8')).split('\n')
    const index = text ? lines.findIndex(value => value.includes(text)) : 0
    return index < 0 ? file : `${file}:${index + 1}`
  } catch { return `${file}（待文件生成）` }
}

/** 表格统一设置可筛选标题、冻结标识列和换行，不以缩小字体强行压缩工程说明。 */
function addTable(book, name, title, headers, rows, widths, context = '代码设计与实现说明，部署及实机验收状态单独记录') {
  const sheet = book.worksheets.add(name)
  sheet.showGridLines = false
  const lastCol = col(headers.length - 1)
  const lastRow = Math.max(6, rows.length + 5)
  sheet.getRange(`A1:${lastCol}${lastRow}`).format.font = { name: FONT, size: 10, color: INK }
  sheet.getRange(`A1:${lastCol}${lastRow}`).format.verticalAlignment = 'center'
  sheet.getRange('A2').values = [[title]]
  sheet.getRange('A2').format.font = { name: FONT, size: 16, bold: true, color: NAVY }
  sheet.getRange(`A2:${lastCol}2`).format.rowHeight = 30
  sheet.getRange('A3').values = [[context]]
  sheet.getRange(`A3:${lastCol}3`).format.font = { name: FONT, size: 10, italic: true, color: '#617185' }
  sheet.getRange(`A3:${lastCol}3`).format.rowHeight = 27
  const matrix = [headers, ...(rows.length ? rows : [headers.map((_, i) => i === 0 ? '暂无记录' : '')])].map(row => row.map(cell))
  sheet.getRange(`A5:${lastCol}${lastRow}`).values = matrix
  const table = sheet.tables.add(`A5:${lastCol}${lastRow}`, true, `DesignTable${++tableCount}`)
  table.style = 'TableStyleMedium2'
  table.showFilterButton = true
  sheet.getRange(`A5:${lastCol}5`).format = { fill: NAVY, font: { name: FONT, size: 10, bold: true, color: '#FFFFFF' },
    rowHeight: 30, horizontalAlignment: 'center', verticalAlignment: 'center', wrapText: true }
  sheet.getRange(`A6:${lastCol}${lastRow}`).format.wrapText = true
  sheet.getRange(`A6:${lastCol}${lastRow}`).format.horizontalAlignment = 'left'
  headers.forEach((_, index) => { sheet.getRange(`${col(index)}5:${col(index)}${lastRow}`).format.columnWidth = widths[index] || 28 })
  rows.forEach((row, index) => {
    // 中文近似双字宽估算换行，避免说明和长标识在固定高度里被截断。
    const lines = Math.max(...row.map((value, i) => {
      const content = String(cell(value)); const weighted = content.replace(/[^\x00-\xff]/g, 'aa').length
      return Math.ceil(weighted / Math.max(8, (widths[i] || 28) - 2))
    }), 1)
    sheet.getRange(`A${index + 6}:${lastCol}${index + 6}`).format.rowHeight = Math.min(150, Math.max(27, lines * 14 + 8))
  })
  sheet.freezePanes.freezeRows(5)
  if (headers.length > 5) sheet.freezePanes.freezeColumns(2)
  return { sheet, name, rows: rows.length, lastCol, lastRow }
}

/** 输出前进行公式重算、错误扫描与每张表可视渲染，验证记录与工作簿一并保留。 */
async function finish(book, basename, sheets) {
  book.recalculate()
  const check = await book.inspect({ kind: 'table', range: `'${sheets[0].name}'!A5:D12`, include: 'values,formulas', tableMaxRows: 8, tableMaxCols: 4, maxChars: 1800 })
  const errors = await book.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!', options: { useRegex: true, maxResults: 100 }, maxChars: 2400 })
  const rendered = []
  const previewDir = path.join(outputDir, 'previews')
  await fs.mkdir(previewDir, { recursive: true })
  for (const sheet of sheets) {
    const target = path.join(previewDir, `${basename}-${sheet.name}.png`)
    let refreshed = !renderOnly || renderOnly.has(sheet.name)
    if (!refreshed) {
      try { await fs.access(target) } catch { refreshed = true }
    }
    if (refreshed) {
      // 发布状态和实际归档摘要位于清单后半段，局部更新时完整呈现这三张说明页。
      const completePage = ['部署总览', '打包文件清单', '交付与阻塞'].includes(sheet.name)
      const renderRows = completePage ? sheet.lastRow : Math.min(sheet.lastRow, 13)
      const blob = await book.render({ sheetName: sheet.name, range: `A1:${sheet.lastCol}${renderRows}`, scale: 1, format: 'png' })
      await fs.writeFile(target, new Uint8Array(await blob.arrayBuffer()))
    }
    rendered.push({ sheet: sheet.name, rows: sheet.rows, preview: path.relative(root, target),
      renderedThisRun: refreshed, verification: refreshed ? '本轮已渲染，需审查' : '未改内容，保留此前已审查预览' })
  }
  const target = path.join(outputDir, `${basename}.xlsx`)
  const xlsx = await SpreadsheetFile.exportXlsx(book)
  await xlsx.save(target)
  const relativeTarget = path.relative(root, target)
  const entry = { file: relativeTarget, sheets: rendered, inspection: check.ndjson, errorScan: errors.ndjson }
  const existing = verification.workbooks.findIndex(value => value.file === relativeTarget)
  if (existing >= 0) verification.workbooks[existing] = entry
  else verification.workbooks.push(entry)
  // 新导出的文件必须重新检查，其他未改工作簿的摘要/冻结/筛选验证原样保留。
  if (verification.export_checks) verification.export_checks = verification.export_checks.filter(value => value.file !== relativeTarget)
  console.log(JSON.stringify({ file: target, sheets: sheets.length, rows: sheets.reduce((sum, sheet) => sum + sheet.rows, 0) }))
}

// 表设计：完整模型、字段、关系和约束采用注册表自动提取，避免手抄遗漏新活动或监控表。
if (shouldBuild('表设计')) {
  tableCount = 0
  const book = Workbook.create(); const sheets = []
  const overview = [
    ['文档版本', '本批开发设计 v1', '2026-10-19 America/Los_Angeles', '本地实现提交及设计书源码快照；部署状态单独记录'],
    ['源码基线', sources.baseline_commit, '当前分支 fix/launcher-portal-admin', 'Git HEAD；不代表已部署版本'],
    ['数据库', 'MySQL / Django ORM', '迁移仅向前', '现有数据、GID、钱包、账本和安全状态保持'],
    ['模型覆盖', '全量 revival 应用模型', '表数见公式', '含既有原生业务、活动5表和监控3表'],
    ['字段覆盖', '具体字段逐行列出', '字段数见公式', '字段类型由Django MySQL映射推导，非运行库DDL回读'],
    ['新增活动', 'campaigns / campaign_days / campaign_claims / campaign_results / campaign_board_freezes', 'migration 0022_campaigns', '签到、Launcher登录、结算进度和封榜隔离'],
    ['新增监控', 'monitor_nodes / monitor_samples / monitor_minutes', 'migration 0023（以当前迁移清单为准）', '采样凭据独立于玩家、管理员和游戏节点'],
    ['军衔等级', '待可信原生字段接入', '不得按研究常量伪造', '用户视图必须显示真实持久字段或待接入'],
    ['资产安全', '统一 mutate 与不可改写账本', '事务与幂等', '领取/自动发奖失败不能留半份资产'],
    ['正式成绩', '原生可信结果入口默认关闭', '合成 test_only 分区', '真实战斗、多人和完整胜负单独验收'],
  ]
  sheets.push(addTable(book, '设计概览', 'NZ 数据库表设计', ['项目', '设计', '状态或版本', '说明'], overview, [20, 66, 38, 65]))
  sheets.push(addTable(book, '表目录', '全部持久化模型', ['物理表', 'Django模型', '职责', '字段数', '显式约束数', '显式索引数', '源码位置'], sources.tables.map(r => [r.table, r.model, r.description, r.field_count, r.constraint_count, r.index_count, r.source]), [35, 32, 76, 11, 14, 14, 43]))
  sheets.push(addTable(book, '字段定义', '全量字段与默认值', ['物理表', '数据库列', '模型字段', 'MySQL映射类型', '可空', '主键', '唯一', '默认值', '排序规则', '源码位置'], sources.fields.map(r => [r.table, r.column, r.field, r.sql_type, r.nullable ? '是' : '否', r.primary_key ? '是' : '否', r.unique ? '是' : '否', r.default, r.collation, r.source]), [34, 28, 26, 29, 8, 8, 8, 31, 31, 43]))
  sheets.push(addTable(book, '约束与索引', '显式约束和查询索引', ['物理表', '类型', '名称', '定义', '源码位置'], sources.constraints.map(r => [r.table, r.kind, r.name, r.definition, r.source]), [35, 25, 38, 100, 43]))
  sheets.push(addTable(book, '外键关系', '表间关系与删除策略', ['来源表', '来源列', '目标表', '目标列', '删除策略', '可空', '源码位置'], sources.relations.map(r => [r.table, r.column, r.target_table, r.target_column, r.on_delete, r.nullable ? '是' : '否', r.source]), [34, 28, 34, 25, 23, 9, 44]))
  sheets.push(addTable(book, '迁移与数据保护', '迁移顺序与数据保护', ['序号', '迁移文件', '执行策略', '来源'], sources.migrations.map((name, i) => [i + 1, name, '按Django依赖向前执行；不覆盖旧数据库', `revival/migrations/${name}:1`]), [10, 62, 70, 76]))
  sheets[0].sheet.getRange('C9').formulas = [[`=COUNTA('表目录'!A6:A${sources.tables.length + 5})`]]
  sheets[0].sheet.getRange('C10').formulas = [[`=COUNTA('字段定义'!A6:A${sources.fields.length + 5})`]]
  await finish(book, '表设计', sheets)
}

const routeDescriptions = {
  '/health': ['GET', '控制面健康与profile', '匿名', '无', '{status,profile,retail_compatible}', '不代表真实地图或多人可玩'],
  '/api/status': ['GET', '实际能力状态', '匿名', '无', 'profile/retail/independent_login', '保留原生完整对局未验收状态'],
  '/api/login': ['POST', '管理员或兼容账号登录', '匿名；admin需MFA', 'kind/login/password/otp_code', '内存Bearer会话', '不得把MFA秘密写入日志'],
  '/api/player/login': ['POST', '玩家邮箱Web登录', '匿名', 'email/password', '玩家会话', '不会累计Launcher登录日'],
  '/api/launcher/login': ['POST', '统一Launcher玩家登录', '匿名；独立玩家身份', 'email/password', '玩家会话', '由真实入口记录Launcher日累计'],
  '/api/player/view': ['POST', '桌面受限玩家视图', '玩家Bearer', 'view/query', '{data:视图结果}', '只允许me/库存/商城/活动/榜单'],
  '/api/player/register': ['POST', '独立邮箱注册', '邮箱验证码及邀请', 'email/password/invitation/challenge_id/code/幂等元数据', '账号标识/待审核状态', '禁止login字段绕过邮箱入口'],
  '/api/player/email-code': ['POST', '请求邮箱验证码', '同源公开；持久限流', 'email/purpose；register另需invitation', 'challenge_id/expires_in/retry_after', '验证码仅通过邮件投递'],
  '/api/player/password-reset': ['POST', '验证码找回密码', 'reset验证码', 'email/password/challenge_id/code/幂等元数据', '改密结果', '撤销旧会话，不恢复封禁或审核'],
  '/api/player/email-verify': ['POST', '旧邮箱补验', 'verify验证码', 'email/challenge_id/code/幂等元数据', '验证结果', '不改变角色和资产'],
  '/api/launcher/status': ['GET', 'Launcher协议与能力', '匿名', '无', '协议/邮箱能力/原生就绪标记', '能力声明不代替游戏画面或战斗验收'],
  '/api/launcher/tickets': ['POST', '60秒一次启动票据', '玩家Bearer', 'protocol/client_sha256/verifier_sha256/幂等元数据', 'ticket/launch_id/expires_at', '秘密不进入通用重放结果'],
  '/api/launcher/redeem': ['POST', '兑换独立游戏身份', '一次票据及持有证明', 'protocol/client_sha256/ticket/verifier/session_lease', 'game_token/launch_id/expires_at', '票据只消费一次'],
  '/api/game/session': ['POST', '查询游戏身份', '游戏Bearer', 'protocol/client_sha256', '本地身份/角色/期限', '游戏身份不能冒充管理员或节点'],
  '/api/game/session/renew': ['POST', '续期180秒游戏租约', '游戏Bearer', 'protocol/client_sha256/session_lease', 'expires_at/lease_max_expires_at', '每30秒续期，单次最多12小时'],
  '/api/game/settings/get': ['POST', '读取原生设置', '游戏Bearer', '版本化游戏范围', '设置快照/修订', '与本人游戏身份绑定'],
  '/api/game/settings/set': ['POST', '保存原生设置', '游戏Bearer', '版本化范围/expected_revision/设置', '新修订', 'CAS冲突拒绝覆盖'],
  '/api/game/inventory/get': ['POST', '读取原生库存投影', '游戏Bearer', '版本化范围/游标', '库存/GID/期限/装备', '服务端资产不等同全部游戏效果已验收'],
  '/api/game/inventory/equip': ['POST', '原生装备操作', '游戏Bearer', '版本化范围/实例/槽位/修订', '装备及投影修订', '按归属和期限检查'],
  '/api/game/command': ['POST', '原生业务命令', '游戏Bearer', 'feature_version/action/业务字段/幂等元数据', '领域业务回执', '原生消息桥映射；不接受任意字段写入'],
  '/api/game/view': ['POST', '原生业务视图', '游戏Bearer', 'feature_version/view/查询范围', '{data:视图结果}', '只返回授权范围'],
  '/api/command': ['POST', 'Web命令统一入口', '玩家或管理员Bearer', 'action/request_id/idempotency_key/reason/业务字段', '命令结果', '权限、幂等、审计与业务事务统一'],
  '/api/asset-preview': ['POST', '资产变更预览', 'asset.preview权限', 'target_account_id/changes/catalog_version', 'before/after', '保存点回滚，不实际发放'],
  '/api/runtime/results': ['POST', '受信对局结果入库', '单局节点Bearer', '见可信结果合同', '{result_id,valid,rank_eligible,reason,test_only}', '正式默认关闭，仅test_profile合成验收'],
  '/api/monitoring/samples': ['POST', '独立主机采样上报', '监控专用Bearer', '见监控采样合同', 'accepted/duplicate', '不能复用玩家、管理员或单局节点凭据'],
  '/api/public/portal': ['GET', '公开门户地图与能力', '匿名', '无', 'maps/play_policy_revision/能力状态', '不返回服务器地址或管理信息'],
  '/api/public/campaigns': ['GET', '公开活动规则', '匿名', '无', '已发布活动数组', '无个人进度和领取消费信息'],
  '/api/public/leaderboards': ['GET', '公开分榜', '匿名', 'campaign_id/map_id/difficulty/player_count', 'entries/finalized/test_only', '公开结果移除账号ID，只显示昵称'],
  '/api/views/<str:view>': ['GET', '认证读模型', '按view授权', '查询字段（数字严格转换）', '视图数据', '名单/资产/监控不允许匿名'],
  '/api/logout': ['POST', '撤销当前会话', '当前Bearer', '{}', 'logged_out', '释放席位；真实节点断线仍需送达证据'],
}

// 游戏服务端设计：把HTTP控制面合同和原生消息元数据分别列出，未知DS字段保持未验收。
if (shouldBuild('游戏服务端接口设计')) {
  tableCount = 6
  const book = Workbook.create(); const sheets = []
  sheets.push(addTable(book, '接口概览', 'NZ 游戏服务端接口设计', ['接口层', '用途', '认证边界', '当前状态', '来源'], [
    ['HTTP控制面', '账号、资产、运营、活动、榜单、监控', '玩家/管理员/游戏/节点/监控令牌互不通用', '本批实现；Web上线待环境', 'revival/views.py:1'],
    ['本机Launcher', '受限本机API、启动/停止/恢复与玩家功能代理', '随机本机操作能力，经父子管道传输', '现有Tauri路径复用', 'tools/native_launcher.py:1'],
    ['原生Zone', '已固定构建的登录、资料、仓库、社交和频道房间', '当前会话transport/challenge/换钥', '单端部分功能实机通过', 'adapters/cn_current/zone.py:1'],
    ['原生业务消息', '386个已固定命令与2082个结构字段（数量以表为准）', '固定元数据摘要及严格编解码', '结构确认不等于全部实机通过', 'adapters/cn_current/features.py:1'],
    ['原生DS', '目标世界、玩家入场、自然胜负与结算', '实例/epoch/租约待原生接通', '未通过；禁止伪造READY', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['可信成绩', '成功通关、Boss、时长和冻结人数', '单局节点独立Bearer', '正式关闭；合成独立分榜', 'revival/campaigns.py:1'],
  ], [25, 58, 65, 49, 68]))
  const rows = sources.routes.filter(r => r.path.startsWith('/api/') || r.path === '/health').map(r => {
    const meta = routeDescriptions[r.path] || (r.path.startsWith('/api/node/') ? ['POST', '合成节点契约', '单局节点Bearer', 'match_id/epoch及已定义事件字段', '合成节点回执', '真实原生节点未接通'] : ['POST', '兼容注册入口', '同源公开/邮箱验证', '沿用邮箱注册合同', '注册结果', '不得绕过邮箱要求'])
    return [meta[0], r.path, ...meta.slice(1), r.source]
  })
  sheets.push(addTable(book, 'HTTP路由', 'HTTP 路由与授权', ['方法', '路径', '用途', '认证', '请求字段', '响应概要', '边界', '来源'], rows, [9, 35, 27, 32, 61, 53, 59, 41]))
  sheets.push(addTable(book, '命令权限', 'Web 命令权限矩阵', ['action', '权限标识', '身份原则', '事务和幂等', '来源'], sources.commands.map(r => [r.action, r.permission, r.permission === 'player' ? '仅本人玩家会话' : r.permission === '*' ? 'Owner专属' : '按Service权限矩阵', '统一request_id/idempotency_key/reason；成功重试不重复提交', r.source]), [40, 28, 36, 76, 42]))
  const contracts = [
    ['活动保存', 'campaign.save', 'campaign_id? / expected_revision(修改草稿)', 'string / int', '草稿保存使用CAS；发布快照不可覆盖', 'revival/campaigns.py'],
    ['活动保存', 'campaign.save', 'title / definition', 'string / object', 'definition含starts_at/ends_at/timezone/rewards', 'revival/campaigns.py'],
    ['活动奖励', 'definition.rewards[]', 'key/title/type/threshold/changes', 'object', '普通类型CHECKIN_DAYS/LAUNCHER_LOGIN_DAYS/PLAYTIME_SECONDS/CLEAR_COUNT；正数道具', 'revival/campaigns.py'],
    ['榜奖规则', 'definition.rewards[]', 'type=LEADERBOARD/priority/rank_from/rank_to', 'object', '可限制map_ids/difficulties/player_counts；每人每期最高priority一档', 'revival/campaigns.py'],
    ['活动生命周期', 'campaign.publish/pause/end', 'campaign_id', 'string', 'resources可运营；auditor只读', 'revival/campaigns.py'],
    ['普通奖励', 'campaign.checkin/claim', 'campaign_id / reward_key(领取)', 'string', '签到只累计，普通奖励本人点击领取；同事务账本防重复', 'revival/campaigns.py'],
    ['排行榜封榜', 'leaderboard.finalize', 'campaign_id', 'string', 'Owner封榜，冻结排名与最高档；维护器逐目标自动发奖并重试失败', 'revival/campaigns.py'],
    ['奖励状态', 'campaigns / campaign-admin', 'delivery / delivery_status / payouts', 'CLAIM|AUTOMATIC / 状态 / 计数', '普通奖励点击领；榜奖自动发放；后台显示pending/failed/succeeded', 'revival/campaigns.py'],
    ['受信结果', '/api/runtime/results', 'campaign_id/match_id/instance_id/epoch/event_id', 'string', '绑定活动及已就绪单局；相同局换内容拒绝', 'revival/campaigns.py'],
    ['受信结果', '/api/runtime/results', 'map_id / difficulty / participant_ids', 'int / int / string[]', '须与冻结名单和地图难度一致；观战不计，增员失格', 'revival/campaigns.py'],
    ['受信结果', '/api/runtime/results', 'duration_ms / settled_at', 'int / Unix秒', '毫秒正整数；时长不能超过实例生命周期；结算时刻不在未来', 'revival/campaigns.py'],
    ['受信结果', '/api/runtime/results', 'outcome / boss_killed / settled', 'VICTORY|FAILURE / bool / bool', '仅成功入榜；塔防还须Boss已击杀且结算完成', 'revival/campaigns.py'],
    ['监控采样', '/api/monitoring/samples', 'node_id/boot_id/sequence/measured_at', 'string/string/int/Unix秒', '启动标识+序号去重；不同内容冲突；只接近实时采样', 'revival/monitoring.py'],
    ['监控采样', '/api/monitoring/samples', 'cpu_percent/memory_total/memory_used', 'number/int bytes/int bytes', 'CPU 0至100；used不得大于total；拒绝NaN与bool', 'revival/monitoring.py'],
    ['监控采样', '/api/monitoring/samples', 'rx_bytes/tx_bytes/disks/services', 'int bytes/int bytes/array/object', '累计字节作差；重启和回退不得产生负带宽；服务UP/DOWN/UNKNOWN', 'revival/monitoring.py'],
    ['商城运营', 'shop.offer.save', 'commodity_id/expected_revision/enabled/sort_order/prices/period_hours', '版本化目录/报价', '修改已知商品；原生和Web共用覆盖；变更使旧报价失效', 'revival/portal_operations.py'],
    ['商城购买', 'player.shop.quote / player.shop.purchase', '目录版本/购物车；提交另需报价摘要', '服务端报价与原子交易', '旧报价、币种余额、期限和完整礼包子项由当前规则校验', 'revival/portal_operations.py'],
    ['地图开放', 'play-policy.save', 'expected_revision / maps[{map_id,enabled,submodes}]', 'CAS版本化规则', '创建/修改/入房/开始共用校验；禁用同名变体', 'revival/portal_operations.py'],
    ['删除库存', 'asset.remove', 'target_account_id/instance_id/expected_version/quantity', '本人归属目标与CAS', '服务端保护装备树/皮肤/吞噬关系；删除仍记录不可改写账本', 'revival/portal_operations.py'],
  ]
  for (const row of contracts) row[row.length - 1] = await source(row[row.length - 1])
  sheets.push(addTable(book, '新增请求合同', '本批运营与运行时合同', ['业务', '入口', '字段', '类型', '验证和行为', '源码位置'], contracts, [23, 36, 61, 31, 95, 43]))
  sheets.push(addTable(book, '既有请求合同', '既有 API 字段和操作合同', ['原文章节', '入口或动作', '字段和行为', '授权或补充', '来源'], sources.api_contracts.map(r => [r.section, r.entry, r.contract, r.permission, r.source]), [35, 59, 112, 88, 28], '从维护中的docs/API.md表格提取；本批新增合同见前页，实际权限以当前Service为准'))
  sheets.push(addTable(book, '原生消息目录', '固定构建原生消息目录', ['消息号（十六/十进制）', '结构名称', '验证状态', '元数据来源'], sources.native_commands.map(r => [`${r.command} (${parseInt(r.command, 16)})`, r.schema, r.status, r.source]), [27, 74, 69, 66], '结构取自当前固定 v13；A11B统计联合体未完成，不填空结算'))
  sheets.push(addTable(book, '原生结构字段', '当前 TDR 结构字段', ['结构', '字段', 'TDR类型', 'host偏移', 'host声明大小', '容量', '嵌套结构', '计数引用', '来源'], sources.native_fields.map(r => [r.schema, r.name, r.tdr_type, r.host_offset, r.host_size, r.capacity, r.nested, r.reference, r.source]), [57, 39, 12, 13, 17, 12, 59, 31, 65], 'host偏移/声明大小仅为x86主机布局，不能当作动态wire字节偏移或线上包长度'))
  sheets.push(addTable(book, '验收与错误', '验收边界与故障处理', ['场景', '预期处理', '状态', '依据'], [
    ['来源或游戏会话过期/封禁', '拒绝续期和后续业务；Launcher停止本轮自有游戏并清理', '已有租约/清理机制', 'docs/native-launcher.md:1'],
    ['重复请求或重复领取', '相同内容返回原结果；不同内容IDEMPOTENCY_CONFLICT/RESULT_CONFLICT', '本批自动化覆盖', 'tests/test_campaigns.py:1'],
    ['地图/模式被禁用', 'Web和原生均返回警告，不能继续创建/配置/开始', '本批实现', 'revival/portal_operations.py:1'],
    ['塔防未击杀Boss或未结算', '不得入榜或累计成功通关数', '合成合同验证；正式来源禁用', 'revival/campaigns.py:1'],
    ['A11A / A116 / A11B', '成功开局/加入/结算仍须真实节点证明', '未通过', 'docs/delivery/2026-10-18-native-channel-rooms.md:1'],
    ['原生世界和多人', '纹理加载、世界、网络、角色入场、自然胜负、返厅下一局依次验收', '完整流程0项通过', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['公网客户端分发', 'HTTPS受限玩家网关与干净Windows安装包另行验收', '本次只交付开发包', 'docs/native-launcher.md:1'],
  ], [34, 96, 45, 75]))
  await finish(book, '游戏服务端接口设计', sheets)
}

// Web设计书：页面、权限、流程和七图规则直接对应当前实现，区分门户体验和游戏验收。
if (shouldBuild('Web端设计书')) {
  tableCount = 14
  const book = Workbook.create(); const sheets = []
  sheets.push(addTable(book, '页面架构', 'NZ Web 端设计书', ['路由', '页面', '用户', '主要内容和操作', '数据入口', '源码位置'], [
    ['/', '官网首页', '所有访客', '首屏视觉、玩法与开放地图、加入指南', '/api/public/portal', await source('admin/src/portal-app.tsx', 'function PortalHome')],
    ['/guide', '上手指南', '所有访客', '邮箱注册、角色准备、Windows开发版Launcher与限制说明', '静态指南与实际能力', await source('admin/src/portal-app.tsx', 'function Guide')],
    ['/player', '玩家中心', '已认证玩家', '角色资料、仓库、商城、活动、排行榜', '/api/views/me 及本人视图', await source('admin/src/portal-app.tsx', 'function PlayerWorkspace')],
    ['/admin', '管理中心', '各角色管理员', '用户、库存、制裁、商城、开放地图、活动、监控', '/api/views/* 与 /api/command', await source('admin/src/components/operations-center.tsx')],
    ['/activities', '开服活动', '访客与玩家', '活动奖励、北京时间累计进度、签到与普通奖励领取', '公开或本人campaigns', await source('admin/src/components/platform-pages.tsx', 'CampaignsPage')],
    ['/leaderboards', '先锋榜', '所有访客', '活动/地图/难度/实际人数筛选，毫秒通关时间', '公开或认证leaderboards', await source('admin/src/components/platform-pages.tsx', 'LeaderboardsPage')],
    ['/launcher', 'Tauri专用页面', '本机启动器', 'DreadHunger参考布局、启动/恢复、本人仓库/商城/活动', '受限本机 /launcher/api/*', await source('admin/src/components/native-launcher.tsx')],
    ['/console', '原控制台', '现有管理用户', '保留兼容管理工作流', '原有视图和命令', await source('admin/src/main.tsx')],
  ], [23, 23, 26, 77, 51, 61]))
  const roleRows = await Promise.all(Object.entries(sources.permissions).map(async ([role, permissions]) => [role, permissions.join(', '), role === 'owner' ? '全部管理，地图政策、最终封榜和监控节点配置' : role === 'resources' ? '资产、商城和活动运营' : role === 'users' ? '账号审核、会话撤销、封禁解封' : '管理只读审计', role === 'auditor' ? '不得提交写操作' : '所有写操作按服务端权限与事务验证', await source('revival/service.py', `'${role}':`)]))
  sheets.push(addTable(book, '角色与权限', '服务端权限与页面入口', ['角色', '服务端权限', '页面行为', '限制', '来源'], roleRows, [20, 94, 58, 63, 40]))
  sheets.push(addTable(book, '业务流程', '用户操作与服务端结果', ['流程', '操作步骤', '服务端检查', '成功结果', '失败处理', '来源'], [
    ['用户管理', '搜索账号、查看级别/仓库、发道具或删除实例', '用户/资源权限分离；目录、归属、数量、期限与装备关系', '账本、库存与同步修订同事务更新', '权限/库存容量/实例变化则拒绝，不留半份资产', 'revival/portal_operations.py:1'],
    ['封禁/解封', '选择玩家、输入原因与期限、提交', '制裁权限、账号状态和安全版本', '撤销会话、释放房间并发撤销事件；解封保留历史', '不宣称数据库封禁等于原生节点已断开', 'revival/service.py:1'],
    ['商城运营', '筛选商品、编辑上架状态/售价/排序/期限、保存', 'expected_revision CAS；使用实际目录商品和币种', '追加运营规则并使旧报价失效', '并发配置变化提示刷新；不覆盖他人新规则', 'revival/portal_operations.py:1'],
    ['玩家购买', '查看商品、取得报价、确认购买', '最新运营修订、价格、余额、限制及目录版本', '钱包扣减、道具发放、历史回执一同提交', '商品下架或旧报价拒绝，未知结果重试同键', 'revival/portal_operations.py:1'],
    ['开放地图', 'Owner选择地图/难度，保存配置', '固定目录、CAS和实际地图容量规则', 'Web和原生建房/入房/配置/开始共用白名单', '禁用地图弹出警告，已开始快照不任意改写', 'revival/portal_operations.py:1'],
    ['活动发布', '建立草稿、核对时间/时区/奖励、发布', 'resources或Owner；只发正数量已知道具', '发布不可变活动定义；默认不发布任何奖励', '已发布定义不可编辑，修改建立新活动', 'revival/campaigns.py:1'],
    ['签到/普通奖励', '玩家点击签到，达到阈值后点击领取', '北京时间自然日、活动期、本人资格和领取唯一键', '跨月累计不重置；成功领取一次到账', '资产失败回滚，可用原请求重试', 'revival/campaigns.py:1'],
    ['榜单与榜奖', '选择图/难度/人数；Owner结束后封榜', '仅成功成绩；塔防Boss和结算；同成员组合保留最佳', '冻结榜单，每人每期最高priority一档由维护器自动发放', '失败目标保留待重试；不让玩家手工领取榜奖', 'revival/campaigns.py:1'],
    ['主机监控', 'Owner登记节点，Agent采集，管理页看趋势', '独立采样凭据、boot/sequence及有限数值', 'CPU/内存/磁盘/带宽/服务状态，最后采样时间', '首样本/失联/重启/计数器回退显示明确状态', 'revival/monitoring.py:1'],
  ], [22, 63, 82, 84, 88, 43]))
  const mapData = JSON.parse(await fs.readFile(path.join(root, 'config/cn2020-native-room-compat-v2.json'), 'utf8'))
  const launchMaps = [113, 164, 191, 141, 140, 168, 96]
  sheets.push(addTable(book, '七图与排行规则', '首发七图和分榜', ['MapId', '地图', 'ModeId', '模式', '排行人数', '难度代码', '入榜条件', '实际游戏状态', '来源'], launchMaps.map(id => {
    const value = mapData.maps[String(id)]; const hunt = id === 113 || id === 164 || id === 191
    return [id, value?.name || '待目录确认', value?.mode_id || '', hunt ? '僵尸猎场' : '塔防战', hunt ? '1至8人，8个独立榜' : '1至4人，4个独立榜', (value?.submodes || []).join(', '), hunt ? '任务成功并完成可信结算' : 'Boss已击杀且完成成功结算', 'native_loaded/combat_verified尚未通过', 'config/cn2020-native-room-compat-v2.json:1']
  }), [13, 28, 13, 20, 38, 34, 72, 63, 69]))
  sheets.push(addTable(book, '交互与状态', '交互、数据和隐私状态', ['模块', '状态或场景', '用户反馈', '实现要求', '来源'], [
    ['认证', '未登录/已过期/已封禁', '显示专用登录入口或服务端拒绝信息', '会话仅在内存；退出清空当前数据', 'admin/src/portal-app.tsx:1'],
    ['表格', '加载/空数据/错误', '真实加载提示、空态和可重试错误', '不以空数组掩盖请求失败', 'admin/src/lib/platform.ts:1'],
    ['写请求', '等待/成功/未知结果', '锁定重复提交；成功回读；未知提交同键重试', '请求体冻结，不在重试时换业务内容', 'admin/src/lib/api.ts:1'],
    ['等级', '可信字段尚未接入', '显示待接入', '禁止使用研究常量或按通关次数伪造军衔', 'admin/src/portal-app.tsx:1'],
    ['资产', '仅DB提交/同步待确认', '区分发放成功和游戏内效果', '同一账本保留GID、期限、装备与同步修订', 'revival/assets.py:1'],
    ['榜单', '正式无可信结果', '显示空榜与条件说明', '不注入演示成绩，不向用户宣称玩法通过', 'revival/campaigns.py:1'],
    ['公开页面', '游客阅读', '只显示公告规则、地图、昵称和成绩', '隐藏账号ID、邮箱、服务器地址和管理数据', 'revival/views.py:1'],
    ['视觉', '桌面/移动/减少动画', '海军蓝底、冰冷蓝光和克制金色，参考DreadHunger布局', '图像仅装饰；表单/焦点/对比度可用；不冒充真实截图', 'admin/src/platform.css:1'],
  ], [22, 40, 73, 87, 50]))
  sheets.push(addTable(book, '验收清单', 'Web 与 Launcher 验收清单', ['编号', '场景', '通过条件', '本次状态', '依据'], [
    ['WEB-01', '注册/验证/登录', '真实MySQL账号状态一致，邮箱和MFA按入口校验', '统一自动化结果另附交接', 'tests/test_http.py:1'],
    ['WEB-02', '库存发放/删除', '归属、期限、装备关系、账本、GID和权限均正确', '本批回归覆盖', 'tests/test_portal_operations.py:1'],
    ['WEB-03', '商城运营/购买', '下架/改价令旧报价失效，购买事务可重试', '本批回归覆盖', 'tests/test_portal_operations.py:1'],
    ['WEB-04', '七图白名单', 'Web和原生创建/配置/开始拒绝禁用地图及同名变体', '本批回归覆盖', 'tests/test_portal_operations.py:1'],
    ['WEB-05', '签到/累计奖励', '跨月按活动期累计、每日去重、并发只发一次、失败可重试', '本批回归覆盖', 'tests/test_campaigns.py:1'],
    ['WEB-06', '分榜/封榜发奖', '成功、Boss、人数、难度、同队最佳、最高档和失败重试正确', '合成验证，正式来源禁用', 'tests/test_campaigns.py:1'],
    ['WEB-07', '服务器监控', '首样本/重启/回退/失联状态正确，趋势与权限隔离', '本批回归覆盖；远端Agent未部署', 'tests/test_monitoring.py:1'],
    ['WEB-08', '构建与页面', 'TypeScript/Vite、前端回归及实际视窗检查', '以本批交接实际结果为准', 'admin/package.json:1'],
    ['GAME-01', '原生多人完整胜负', '真实角色入图、战斗、自然胜负、结算奖励、返厅下一局', '未通过，另行验收', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['REL-01', 'Web部署', '用户提供环境后执行发布、迁移、HTTPS和在线冒烟', '等待环境，未部署', '本次用户指示'],
  ], [16, 35, 99, 61, 69]))
  await finish(book, 'Web端设计书', sheets)
}

// 部署手册：本次只部署Web，环境待用户提供；Windows继续开发包，不自动使用历史DEV。
if (shouldBuild('部署手册及打包清单')) {
  tableCount = 20
  const book = Workbook.create(); const sheets = []
  sheets.push(addTable(book, '部署总览', 'NZ 部署手册及打包清单', ['项目', '本次选择', '当前状态', '执行要求', '来源'], [
    ['部署对象', '仅current Web前后端、maintenance及可选监控Agent', '待用户提供环境', '不安装synthetic/PvE服务；不得自动覆盖原DEV或远端Windows', 'docs/WEB_DEPLOYMENT.md:1'],
    ['Web技术栈', 'React/TypeScript/shadcn + Django + MySQL', '代码已具备', '使用锁定依赖与向前迁移', 'pyproject.toml:1'],
    ['Launcher', '保留React+Tauri，新增自包含开发包脚本', releaseStatus.launcher.package_status, '玩家包用HTTPS网关或已存在的本机隧道，不附私人SSH配置', 'tools/package_launcher.py:1'],
    ['新数据库迁移', sources.migrations.filter(name => name.startsWith('0022') || name.startsWith('0023')).join(', '), '新增活动与监控表', '发布前备份；不恢复旧库覆盖新写入', 'revival/migrations/'],
    ['正式游戏节点', '不在本次Web部署范围', '指定节点原生世界/入局/可信结算未通过', '不得标记READY或声称可玩完成', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['版本识别', webPackage?.manifest.source_commit || sources.baseline_commit, '本地已提交实现；包manifest标记WORKING_TREE', '不将工作树归档描述为某个提交原样构建', 'git rev-parse HEAD / RELEASE.json'],
    ['发布产物SHA', webPackage?.sha256 || '待实际打包后读取', webPackage ? '已从实际归档计算SHA256' : '尚未生成归档', '发布前核对归档字节与清单，不等于已部署', 'tools/package_web.py:1'],
    ['远端与CI', releaseStatus.git.summary, releaseStatus.ci.summary, '当前未进行远端Web或Windows部署；CI构建不等于安装或游戏验收', releaseStatus.evidence],
  ], [25, 87, 66, 88, 66]))
  sheets.push(addTable(book, '环境配置', '待确认环境与最小配置', ['配置项目', '需要填写的值', '要求', '用途', '来源'], [
    ['目标主机和系统', '待用户提供', '确认操作系统、CPU、内存、磁盘及独立目录', '本手册Linux示例使用systemd/Nginx，其他系统单独适配', 'docs/WEB_DEPLOYMENT.md:1'],
    ['公网域名与TLS', '待用户提供', '有效HTTPS证书；可信Host/Origin明确白名单', '公众Web和玩家受限API', 'revival/middleware.py:1'],
    ['NZ_PUBLIC_ORIGIN', 'https://目标公网域名', '必须是单一精确HTTPS来源，和Nginx server_name一致', '由配置扩展可信Host/Origin；不放宽为任意来源', 'nzproject/settings.py:30'],
    ['MySQL连接', '在受保护环境文件中填写', '主机/端口/库名/独立用户/密码；配置数据库TLS要求', '不要写入Git、Excel或普通日志', 'nzproject/settings.py:1'],
    ['NZ_PROFILE', 'current', '正式库与synthetic测试库分离', '正式成绩入口保持关闭', 'nzproject/settings.py:1'],
    ['NZ_SECRET_KEY', '私有随机至少32字符；已有环境保留旧值', '公网启用时缺省、默认或短key拒绝启动；房间口令AES-GCM依赖旧key', '新库独立生成；密钥不进入Excel、归档、命令参数或日志', 'nzproject/settings.py:1'],
    ['NZ_TRUST_LOOPBACK_PROXY', '仅同机Nginx反代时设置1；缺省关闭', '只在回环peer且精确公网Host下校验信任X-Real-IP', '按真实客户端执行登录/邮件限流；不信任任意转发头', 'revival/middleware.py:1'],
    ['邮件配置', '受保护SMTP环境文件', '固定TLS端点、465、发件身份和持久发送额度', '邮箱验证、密码恢复', 'revival/mailing.py:1'],
    ['管理员', '通过受控bootstrap交互创建', 'Owner正常MFA；不启用合成固定码例外', '发布后的安全管理入口', 'revival/management/commands/bootstrap_owner.py:1'],
    ['监控节点', 'Owner登记后安全配置node_token', '凭据只供Agent，不分发给玩家', 'CPU/内存/磁盘/网卡采样', 'revival/monitoring.py:1'],
    ['Windows构建', '单独Windows构建机', 'Python3.14、Node22、Rust MSVC、WebView2；安装requirements-launcher-build.txt', '本次开发包，不远端部署', 'tools/package_launcher.py:1'],
  ], [30, 54, 92, 77, 61]))
  sheets.push(addTable(book, '发布与回退步骤', 'Web 发布与回退流程', ['步骤', '操作', '检查或命令', '完成条件', '失败处理', '来源'], [
    [1, '确认环境与目录所有权', '核对系统、资源、现有服务、目标目录和端口', '只接管用户指定目录；默认/opt/nz-web/releases/<release-id>', '不直接执行旧tools/dev_install.sh，不安装历史双profile', 'docs/WEB_DEPLOYMENT.md:1'],
    [2, '完成测试和前端构建', '.venv/bin/python -m tools.run_tests --labels 相关模块；npm --prefix admin run build', '真实MySQL回归、TypeScript/Vite成功', '不使用SQLite或合成游戏证据代替', 'tools/run_tests.py:1'],
    [3, '构建并核验Web发布归档', 'python tools/package_web.py；核对外部SHA256及RELEASE.json逐文件摘要', '仅普通文件/目录；解压拒绝绝对路径、..和链接', '缺产物或摘要不一致则不发布', 'tools/package_web.py:1'],
    [4, '备份并演练恢复', '记录旧目录、迁移和资产总数；受保护MySQL逻辑备份，在隔离库核对恢复', '保留目标现有账号、资产、MFA和配置', '不覆盖正在写入的当前库，不用旧快照抹掉新增流水', 'docs/WEB_DEPLOYMENT.md:1'],
    [5, '配置运行用户与环境', '专用服务用户；/etc/nz-web/web.env及mail.env受保护；/var/lib/nz-web可写', 'MySQL连接正确，current独立；新密钥至少32字符，已有密钥保持', '不得把凭据写入包、Excel、命令参数或日志', 'docs/WEB_DEPLOYMENT.md:1'],
    [6, '新release安装依赖和迁移', '新建.venv；pip install -r requirements.txt；同NZ_ENV_FILE、NZ_PROFILE=current执行check/migrate --noinput', '0022/0023向前迁移；初始空库保留历史触发器权限要求', '失败保留发布目录，旧链接不切换，不删旧表', 'docs/WEB_DEPLOYMENT.md:1'],
    [7, '配置仅current Web与维护服务', '使用deploy/nz-web.service.example及nz-web-maintain.service.example；切换/opt/nz-web/current', '仅Web回环127.0.0.1:18765与同库maintain；不启动PvE服务', '迁移成功后才切换；维护未运行时榜奖PENDING不能称已到账', 'deploy/nz-web.service.example:1'],
    [8, '配置HTTPS反代', '使用deploy/nginx-web.conf.example；NZ_PUBLIC_ORIGIN精确匹配；同机Nginx才启用NZ_TRUST_LOOPBACK_PROXY=1', '证书/Host/Origin正确；回环peer+精确公网Host下X-Real-IP限流', '不配置通配Origin；不开放数据库直连给公众', 'docs/WEB_DEPLOYMENT.md:1'],
    [9, '在线冒烟与可选监控', '检查健康/页面/管理员MFA/本人库存/商城旧报价/白名单/活动/监控ONLINE和STALE', '实际MySQL与原账号/GID/钱包保持；Agent需独立节点Bearer', '原生可信结算仍禁用，不安排真实游戏榜奖验收', 'docs/WEB_DEPLOYMENT.md:1'],
    [10, '回退与完成交付', '先停新maintain、切回兼容旧release并重启；保留0022/0023表、当前库与原密钥', '记录commit、包SHA、迁移、在线证据和未验收项', '业务数据恢复先核对新增流水，不倒退覆盖在线写入', 'docs/WEB_DEPLOYMENT.md:1'],
  ], [9, 36, 116, 83, 91, 64]))
  const packageRows = [
    ['Web归档', 'revival/', 'Django领域代码和全部migrations', '必需', '不包含账号库、缓存、私有证据', 'revival/'],
    ['Web归档', 'nzproject/', '配置/URL/WSGI入口', '必需', '环境值单独注入，不在归档写凭据', 'nzproject/'],
    ['Web归档', 'adapters/ 与 config/', '原生固定元数据、规则和目录', '必需', '完整保留固定摘要与版本校验', 'adapters/cn_current/features.py:1'],
    ['Web归档', 'admin/dist/', '构建后的HTML/CSS/JS/美术资源', '必需', '构建前检查TypeScript，运行时同源提供', 'admin/package.json:1'],
    ['Web归档', 'manage.py / requirements.txt / pyproject.toml', '运行入口与锁定Python依赖', '必需', '使用发布环境独立venv', 'pyproject.toml:1'],
    ['Web归档', 'deploy/nz-web.service.example / nz-web-maintain.service.example', '仅current Web与同库maintain模板', '按目标环境替换路径并安装', '不包含旧tools/dev_install.sh；不安装双profile或PvE服务', 'deploy/'],
    ['Web归档', 'deploy/nginx-web.conf.example / monitor-agent.service', 'HTTPS反代与可选独立监控Agent样例', '按目标环境替换配置', '证书/域名/环境文件单独提供，未自动安装服务', 'deploy/'],
    ['Web归档', 'docs/WEB_DEPLOYMENT.md', '本次仅Web发布、门禁、校验、维护与回退手册', '必需', '四份Excel单独交付；不用历史DEV双环境安装步骤', 'docs/WEB_DEPLOYMENT.md:1'],
    ['Web归档产物', webPackage?.file || 'nz-web-platform-2026-10-19.tar.gz（待生成）', webPackage ? `${webPackage.bytes} bytes；SHA256 ${webPackage.sha256}` : '实际文件生成前不填写摘要', '单独manifest.json与.sha256', '交付包已生成不代表目标Web环境已部署', 'tools/package_web.py:1'],
    ['文档包', '四份xlsx、交接Markdown、运行说明', '设计与部署交接', '必需', '不附私人配置或原始传输包', 'outputs/01a15734-c809-7ff2-8313-f617805a4441/'],
    ['监控包', 'tools/monitor_agent.py及独立依赖说明', '采样与重试，默认15秒', '按部署节点选择', 'NZ_MONITOR_TOKEN通过环境注入，不放入玩家安装包', 'tools/monitor_agent.py:1'],
    ['Launcher开发包', 'NZLauncher.exe / launcher-config.json', 'Tauri窗口与公开API设置', releaseStatus.launcher.file_status, '配置不允许包含私人SSH/数据库秘密；实际CI产物状态见交付与阻塞页', 'tools/package_launcher.py:1'],
    ['Launcher开发包', 'service/NZLauncherService.exe 与 _internal/', 'PyInstaller onedir服务及Python依赖、admin/dist、config', '自包含开发包需要', '冻结服务先执行无账号bootstrap/close烟测，不代表登录/游戏验收', 'tools/package_launcher.py:1'],
    ['Launcher开发包', 'package-manifest.json / licenses / 使用说明.txt', '文件字节/摘要、依赖许可、使用边界', releaseStatus.launcher.file_status, 'manifest内native_start_available=false，PACKAGE_NATIVE_CLIENT_UNAVAILABLE', 'tools/package_launcher.py:1'],
    ['Launcher实际归档', releaseStatus.launcher.inner_zip.file,
      `${releaseStatus.launcher.inner_zip.bytes} bytes；SHA256 ${releaseStatus.launcher.inner_zip.sha256}`,
      'CI产物已独立核验', `${releaseStatus.launcher.inner_zip.payload_files}个payload另加manifest；${releaseStatus.launcher.inner_zip.verification}`,
      releaseStatus.launcher.inner_zip.evidence],
    ['GitHub下载包装层', releaseStatus.launcher.inner_zip.artifact_url,
      `${releaseStatus.ci.windows.artifact_wrapper.bytes} bytes；SHA256 ${releaseStatus.ci.windows.artifact_wrapper.sha256}`,
      'artifact wrapper单独摘要', '此摘要属于下载包装层，不能代替NZLauncher-DEV.zip内部发行归档摘要',
      releaseStatus.launcher.inner_zip.evidence],
    ['原生研究工作区', 'launcher/bin/components/d3d9-frame-copy.dll', '已有本机原生研究路径的固定摘要x86显示桥', '非本次自包含门户包必要组件', '自包含包未带原生客户端，不能声明可以完整游戏启动', 'tools/native_frame_bridge.py:1'],
    ['Launcher构建入口', 'python -m tools.package_launcher --api-base-url https://公开API', '生成NZLauncher-DEV目录及同级zip', 'Windows执行；--plan可静态审查', '已存在输出拒绝覆盖；归档SHA只在实际生成后报告', 'tools/package_launcher.py:1'],
    ['排除', 'runtime/、evidence/private/、.env*、*.pem、*.key', '凭据、缓存及私人证据', '必须排除', '不把排除目录作为游戏安装包依赖分发', '.gitignore:1'],
    ['排除', '原始游戏客户端、数据库、node_modules、.git', '第三方大体积数据与源码管理内部信息', '必须排除', '游戏内容分发与授权单独安排', '.gitignore:1'],
  ]
  sheets.push(addTable(book, '打包文件清单', '文件用途与发行边界', ['包类型', '路径或文件', '用途', '是否需要', '注意事项', '来源'], packageRows, [26, 83, 67, 31, 102, 72]))
  if (webPackage) {
    const manifestRows = webPackage.manifest.files.map((file, index) => [index + 1, file.path, file.bytes,
      file.sha256, file.path.startsWith('admin/dist/') ? '已构建Web资源' : file.path.startsWith('deploy/') ? '部署样例模板' : '运行源码、固定规则或依赖说明'])
    manifestRows.push([manifestRows.length + 1, 'RELEASE.json', webPackage.manifestBytes,
      webPackage.manifestSha256, '归档内逐文件manifest，与外部.manifest.json字节一致'])
    sheets.push(addTable(book, '实际归档文件', '实际 Web 归档逐文件清单', ['序号', '包内相对路径', '字节', 'SHA256', '用途'], manifestRows, [10, 106, 17, 74, 78],
      `${webPackage.file}；${webPackage.bytes} bytes；来源 .tar.gz.manifest.json，正式部署尚未执行`))
  }
  sheets.push(addTable(book, '监控与运维', '主机监控和运维规则', ['项目', '配置或口径', '显示与行为', '数据保留', '来源'], [
    ['采样周期', 'Agent默认15秒，可配置5至60秒', '有采样时间与最近上报时间，不能当作游戏在线人数', '原始7天', 'tools/monitor_agent.py:1'],
    ['CPU', '有限数值0至100%', '默认告警阈值85%，Owner可配置', '分钟聚合30天', 'revival/monitoring.py:1'],
    ['内存', 'total/used均为bytes', 'used/total百分比，默认阈值90%', '同上', 'revival/monitoring.py:1'],
    ['磁盘', '最多16个盘total/used', '默认阈值90%，保留盘名和最新测量', '同上', 'revival/monitoring.py:1'],
    ['带宽', '相邻同boot累计rx/tx字节差除时间', '未知链路Mbps时只展示速率，不捏造利用率', '原始与分钟聚合', 'revival/monitoring.py:1'],
    ['首样本与重启', 'SAMPLING / COUNTER_RESET', '缺测不填零，重启不产生负速率', '最近状态', 'revival/monitoring.py:1'],
    ['失联', '超过60秒无有效新样本', '显示STALE，不显示健康零值', '原始清理后仍保留节点登记', 'revival/monitoring.py:1'],
    ['长窗口与清理', '24小时以内分钟；超过24小时按小时聚合', '每小时清理全局过期原始/分钟数据，离线节点同样清理', '原始7天、聚合30天', 'revival/monitoring.py:1'],
    ['服务', 'UP/DOWN/UNKNOWN', '只检查明确配置的服务名，不采集命令行或账号秘密', '最新样本', 'revival/monitoring.py:1'],
    ['资产与活动', '后台maintain及事务账本', '榜奖失败目标可重试，成功目标不重复发放', '历史审计不删除', 'revival/campaigns.py:1'],
  ], [26, 72, 97, 39, 47]))
  sheets.push(addTable(book, '交付与阻塞', '发布前状态与真实游戏边界', ['事项', '当前结果', '阻塞或下一步', '验收证据要求', '来源'], [
    ['Web部署', '本次未部署；仅current Web+maintain已备模板', '等待用户提供服务器配置', '发布包摘要、两项服务、HTTPS、MySQL迁移和在线冒烟', 'docs/WEB_DEPLOYMENT.md:1'],
    ['Windows开发包', releaseStatus.launcher.package_status, releaseStatus.launcher.next_step, '安装/卸载/升级/退出恢复及无秘密分发；CI不代替实际安装验收', 'docs/native-launcher.md:1'],
    ['游戏房间节点', '世界/入局/可信结算未通过', '纹理加载与初始化停点继续研究', '实际世界、网络和玩家进入，不能仅靠资源哈希', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['正式榜单与游玩奖励', '正式受信来源保持关闭', '原生结果和多人数据独立验收', '真实开始/结算时刻、人数、Boss与成功状态', 'revival/campaigns.py:1'],
    ['军衔等级', '待可信持久字段', '不得用研究等级1绕过战队或伪装玩家进度', '字段映射、更新来源和原生画面一致', 'docs/delivery/2026-10-19-code-checkpoint.md:1'],
    ['Excel设计书', '从当前源码提取并渲染', '生成预览不代表原生游戏验收', '四本可打开、每sheet已渲染、公式错误扫描无异常', 'tools/build_design_workbooks.mjs:1'],
    ['Git提交与推送', releaseStatus.git.summary, releaseStatus.git.detail, releaseStatus.git.remote_commit, releaseStatus.evidence],
    ['Launcher构建CI', releaseStatus.ci.windows.status, releaseStatus.ci.windows.detail, releaseStatus.ci.windows.url, releaseStatus.evidence],
    ['Windows工具/前端CI', releaseStatus.ci.windows_checks.status, releaseStatus.ci.windows_checks.detail, releaseStatus.ci.windows_checks.url, releaseStatus.evidence],
    ['综合CI', releaseStatus.ci.regression.status, releaseStatus.ci.regression.detail, releaseStatus.ci.regression.url, releaseStatus.evidence],
    ['本机演示账号', `${releaseStatus.local_demo.player} / ${releaseStatus.local_demo.admin}`,
      releaseStatus.local_demo.authentication, `${releaseStatus.local_demo.player_url} / ${releaseStatus.local_demo.admin_url}；${releaseStatus.local_demo.status}`,
      await source('docs/delivery/2026-10-19-launcher-portal-admin.md', '## 独立测试账号')],
  ], [28, 72, 87, 98, 74]))
  await finish(book, '部署手册及打包清单', sheets)
}

await fs.writeFile(verificationPath, JSON.stringify(verification, null, 2))
console.log(JSON.stringify({ verification: path.join(outputDir, 'design-workbooks-verification.json'), workbooks: verification.workbooks.length }))
