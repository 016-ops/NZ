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
// 第二轮导入首轮工作簿再定向修改，旧输出和历史发行归档保持原字节不覆盖。
const roundV2 = process.argv.includes('--round=portal-v2')
// 等待最新CI时允许显式预览排版；普通最终导出仍要求证据finalized，预览不作为交付终态。
const finalPreview = roundV2 && process.argv.includes('--preview-final')
const outputArgument = process.argv.find(value => value.startsWith('--output-dir='))
const legacyDir = path.join(root, 'outputs/01a15734-c809-7ff2-8313-f617805a4441')
const outputDir = path.resolve(root, outputArgument ? outputArgument.slice('--output-dir='.length) : roundV2 ? 'outputs/portal-v2-2026-10-20' : legacyDir)
if (!outputDir.startsWith(root + path.sep)) throw new Error('设计书输出必须位于当前项目目录中')
const packages = process.env.NZ_ARTIFACT_NODE_MODULES || '/Users/zjh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
await fs.mkdir(outputDir, { recursive: true })
const moduleLink = path.join(outputDir, 'node_modules')
try { await fs.symlink(packages, moduleLink, 'dir') } catch (error) { if (error.code !== 'EEXIST') throw error }
const resolveArtifact = createRequire(path.join(outputDir, 'artifact-authoring.mjs'))
const { Workbook, SpreadsheetFile, FileBlob } = await import(pathToFileURL(resolveArtifact.resolve('@oai/artifact-tool')).href)
const sources = JSON.parse(await fs.readFile(path.join(outputDir, 'design-sources.json'), 'utf8'))
// 发布记录与业务源码快照分开维护；仅更新部署状态时无需重提模型或重写其他三本。
const releaseStatus = JSON.parse(await fs.readFile(path.join(root, 'tools/design_release_status.json'), 'utf8'))
// 最终证据是公开的交付元数据，独立于首轮历史；缺失时保持“待本轮QA”，不默认通过。
let finalEvidence = null
if (roundV2) {
  try {
    finalEvidence = JSON.parse(await fs.readFile(path.join(outputDir, 'final-evidence.json'), 'utf8'))
    if (finalEvidence.format !== 'nz-portal-v2-evidence-v1' || !/^[a-f0-9]{40}$/.test(finalEvidence.runtime_commit)) throw new Error('最终证据格式或运行源码提交不合法')
    if (finalEvidence.deployed !== false) throw new Error('本轮用户决定不部署，最终证据不得声明已部署')
    if (finalEvidence.finalized !== true && !finalPreview) throw new Error('最终MySQL/Windows包证据尚未齐全，暂不导出最终设计书')
  } catch (error) { if (error.code !== 'ENOENT') throw error }
}
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
const v2ChangedSheets = ['设计概览', '表目录', '字段定义', '约束与索引', '外键关系', '迁移与数据保护', '接口概览', 'HTTP路由', '命令权限', '新增请求合同', '既有请求合同', '页面架构', '角色与权限', '业务流程', '交互与状态', '验收清单', '部署总览', '环境配置', '发布与回退步骤', '打包文件清单', '实际归档文件', '交付与阻塞', '第二轮变更', '第二轮接口合同', '第二轮页面流程', '媒体与邮箱部署', '最终验收', '本轮包清单', '本轮归档文件']
const renderOnly = renderArgument ? new Set(renderArgument.slice('--render-only='.length).split(',')) : roundV2 ? new Set(v2ChangedSheets) : null
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
      const completePage = ['部署总览', '打包文件清单', '交付与阻塞', '第二轮变更', '第二轮接口合同', '第二轮页面流程', '媒体与邮箱部署', '最终验收', '本轮包清单'].includes(sheet.name)
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

/** 对本轮交付包独立重算大小和摘要；只接收输出目录内的文件，不从元数据读取任意私密路径。 */
async function verifiedFinalPackages(evidence) {
  const packages = []
  for (const item of evidence?.packages || []) {
    if (!['web', 'launcher_inner'].includes(item.kind)) throw new Error('交付包必须明确区分Web归档和Launcher内部ZIP')
    const target = path.resolve(root, item.file || '')
    if (!target.startsWith(outputDir + path.sep) || !/^[a-f0-9]{64}$/.test(item.sha256 || '')) throw new Error('本轮包路径或SHA256不合法')
    if (item.source_commit && item.source_commit !== evidence.runtime_commit) throw new Error('本轮包与冻结运行源码不是同一提交：' + item.file)
    const bytes = await fs.readFile(target)
    const sha256 = createHash('sha256').update(bytes).digest('hex')
    if (bytes.length !== item.bytes || sha256 !== item.sha256) throw new Error('本轮包大小或摘要不匹配：' + item.file)
    let manifest = null, manifestSha256 = '', manifestSize = 0
    if (item.manifest_file) {
      const manifestPath = path.resolve(root, item.manifest_file)
      if (!manifestPath.startsWith(outputDir + path.sep)) throw new Error('本轮manifest必须位于输出目录中')
      const manifestBytes = await fs.readFile(manifestPath)
      manifest = JSON.parse(manifestBytes.toString('utf8'))
      manifestSha256 = createHash('sha256').update(manifestBytes).digest('hex')
      manifestSize = manifestBytes.length
      if (manifest.source_commit && manifest.source_commit !== evidence.runtime_commit) throw new Error('本轮manifest源码提交不一致')
      if (item.kind === 'web') {
        if (manifest.credentials_included !== false || manifest.game_server_included !== false) throw new Error('Web包manifest未明确排除私密与游戏服务')
      } else {
        // Windows是另一种明确schema，不能套用Web字段，也不因字段缺失而泛化放行。
        if (manifest.schema_version !== 'nz-launcher-package-v1' || manifest.target !== 'windows-x86_64'
            || manifest.native_start_available !== false || manifest.service_smoke?.bootstrap_close_passed !== true
            || manifest.service_smoke?.api_login_executed !== false) throw new Error('Windows平台包manifest或烟测边界不符合本轮交付')
        if (!['SSH credentials', '.env*', 'runtime', 'databases', 'node_modules', 'Git metadata'].every(value => manifest.excluded?.includes(value))) throw new Error('Windows包未明确列出必要的私密排除边界')
      }
      if (!Array.isArray(manifest.files) || manifest.files.length !== item.payload_files) throw new Error('本轮manifest payload计数不一致')
    }
    packages.push({ ...item, bytes: bytes.length, sha256, manifest, manifestSha256, manifestSize })
  }
  return packages
}

/** 包装层留在runtime，不作为输出文件读取；只按公开独立验包证据核对层级、大小与摘要。 */
async function verifiedExternalLayers(evidence) {
  const layers = []
  for (const item of evidence?.external_layers || []) {
    if (item.kind !== 'github_artifact_wrapper' || !/^[a-f0-9]{64}$/.test(item.sha256 || '') || item.source_commit !== evidence.runtime_commit) throw new Error('GitHub包装层证据不合法')
    const evidencePath = path.resolve(root, item.evidence || '')
    if (!evidencePath.startsWith(path.join(root, 'evidence') + path.sep)) throw new Error('包装层只引用公开验包记录')
    const proof = JSON.parse(await fs.readFile(evidencePath, 'utf8'))
    if (proof.source_commit !== evidence.runtime_commit || proof.wrapper?.sha256 !== item.sha256 || proof.wrapper?.bytes !== item.bytes
        || proof.checks?.wrapper_digest !== true) throw new Error('包装层与独立验包证据不一致')
    layers.push(item)
  }
  return layers
}

/** 分层列出实际证据，SMTP、干净Windows和原生玩法限制始终单列，不能被CI成功覆盖。 */
function finalEvidenceRows(evidence, packages, externalLayers) {
  if (!evidence) return []
  const rows = [
    ['运行源码', evidence.runtime_commit, '已推送并冻结', '运行代码标识独立于后续设计书提交；打包清单逐项核对对应提交。', evidence.source_evidence || 'git rev-parse HEAD'],
    ['前端检查', `${evidence.frontend.files}个文件 / ${evidence.frontend.tests}项通过；构建${evidence.frontend.modules}模块`, '通过', '类型检查、前端用例及生产构建；不等于Windows/WebView实机。', evidence.frontend.evidence],
    ['本机浏览器', evidence.ui.summary, '指定流程实检', evidence.ui.limitations, evidence.ui.evidence.join('；')],
    ['Mac Launcher', '布局与表单预览，无本机capability', '仅布局预览', '未绕过本机桥；不代表原生账号登录、图片桥或完整游戏可运行。', evidence.launcher_preview_evidence],
    ['管理员申请码', '页面显示“管理员邮件服务尚未配置，无法完成认证”', '失败关闭提示已实检', '没有SMTP投递，也未完成真实管理员邮箱登录；不能用自动测试或演示流程替代收件验收。', evidence.admin_mail_evidence || '本轮本机管理员页面实检'],
  ]
  for (const check of evidence.automated || []) rows.push([check.name, check.summary, check.status, check.limitations, check.evidence])
  for (const item of packages) rows.push([item.label, `${item.bytes} bytes；SHA256 ${item.sha256}`, '摘要复核通过，未部署', item.limitations, item.evidence || item.file])
  for (const item of externalLayers) rows.push([item.label, `${item.bytes} bytes；SHA256 ${item.sha256}`, '独立验包证据核对，不交付', item.limitations, item.evidence])
  rows.push(
    ['真实SMTP', '未完成正式环境真实收件验收', '未验证', '浏览器演示/自动测试中的邮件适配不代表正式SMTP投递。管理员登录和Owner再验证正式启用前必须验证真实收件。', 'revival/mailing.py；docs/WEB_DEPLOYMENT.md'],
    ['Windows干净机器', '冻结包/CI与独立机器安装验收分开', '未验证', 'WebView2、安装/启动/升级/退出恢复、真实API与签名仍需独立验收。', 'docs/native-launcher.md'],
    ['原生游戏实玩', '完整客户端、可信结算和多人通关另验', '未验证', '正式成绩入口保持关闭，不将平台功能、CI或布局预览描述为游戏实玩通过。', 'docs/delivery/2026-10-19-code-checkpoint.md'],
    ['部署决定', '用户暂不部署', '未部署', '包和设计书供审阅；未更新远端服务或启用正式游戏入口。', '本轮用户确认'],
  )
  return rows
}

/** 第二轮在导入的原工作簿上追加/修订；旧原生消息、历史归档和未关联格式不重新生成。 */
async function buildPortalV2() {
  const names = ['表设计', '游戏服务端接口设计', 'Web端设计书', '部署手册及打包清单']
  const newTables = new Set(['admin_auth_challenges', 'feedback_tickets', 'feedback_messages', 'feedback_attachments', 'public_media', 'campaign_presentations'])
  const registry = JSON.parse(await fs.readFile(path.join(root, 'admin/src/lib/item-presentation.json'), 'utf8'))
  const artwork = Object.values(registry.items || {})
  const exactImages = artwork.filter(value => value.image_status === 'exact').length
  const finalPackages = await verifiedFinalPackages(finalEvidence)
  const externalLayers = await verifiedExternalLayers(finalEvidence)
  const finalRows = finalEvidenceRows(finalEvidence, finalPackages, externalLayers)
  verification.asOf = '2026-10-20 America/Los_Angeles'
  verification.currentRound = { name: 'portal-v2', status: '待本轮QA', deployed: false, exactImages, totalItems: artwork.length }
  verification.releaseStatus = { historicalV1: releaseStatus, currentRound: '未部署，未生成本轮发行包，待本轮QA' }
  verification.scopePreservation = []
  if (finalEvidence) {
    verification.currentRound.status = finalEvidence.finalized ? '指定验收已记录，正式环境与实玩限制保留' : '仅设计书样式预览，最新MySQL终态待补'
    verification.runtimeCommit = finalEvidence.runtime_commit
    verification.finalEvidence = finalEvidence
    verification.finalPackages = finalPackages.map(({ manifest, ...row }) => ({ ...row, payloadFiles: manifest?.files?.length ?? row.payload_files ?? null }))
    verification.externalArtifactLayers = externalLayers
    verification.releaseStatus.currentRound = '本轮包摘要已复核，暂不部署；限制见最终验收'
  }
  const overviewRows = [
    ['版本与日期', '第二轮设计 v2，2026-10-20', '保留首轮证据与旧发行归档。当前工作树不冒充已部署版本。', '待本轮QA', '本轮用户确认'],
    ['部署决定', '暂不部署，仅本机预览与代码开发', '不连接远端，不把首轮包摘要当作本轮发行证明。', '未部署', '本轮用户确认'],
    ['认证策略', '新注册玩家无邀请/审批，邮箱验证后ACTIVE并创建角色。管理员密码与邮箱挑战。', '旧PENDING仍需管理员批准，迁移/登录/补验/恢复均不改变其状态。TOTP/恢复码退休，旧管理员会话由0024撤销。', '待本轮QA', 'revival/service.py；revival/migrations/0024_email_admin_auth.py'],
    ['反馈与活动展示', '反馈四态、鉴权截图、公共封面、独立活动slug和受控模板', '0025新增五表。奖励冻结快照与展示配置分离。', '待本轮QA', 'revival/feedback.py；revival/activitypresentation.py'],
    ['视觉与配图', '深蓝灰与暖金。艾米/芭比双人同框二次元主视觉。', '已核实道具配图 ' + exactImages + '/' + artwork.length + '，其他图明确标记分类示意。', '待本轮QA', 'admin/src/blossom.css；admin/src/lib/item-presentation.json'],
    ['证据边界', '首轮记录维持原日期。本轮验收须重新记录。', '旧CI、旧截图和旧包不代表新邮箱认证、截图上传或当前UI已验收。', '待本轮QA', 'docs/delivery/2026-10-20-frontend-account-activities-feedback.md'],
  ]
  const interfaceRows = [
    ['玩家发码', 'POST /api/player/email-code', '匿名，同源JSON', 'email,purpose=register/reset/verify；不传invitation', 'challenge_id,expires_in=600,retry_after=60；持久限流', 'revival/email_verification.py'],
    ['玩家注册', 'POST /api/player/register', '邮箱验证码与完整幂等元数据', 'email,password,nickname,challenge_id,code,request_id,idempotency_key,reason', '事务内ACTIVE+verified+角色初装；前端成功后调用login自动登录', 'revival/service.py'],
    ['旧账号补角色', 'POST /api/player/login；character.create', '本人Session，旧ACTIVE且邮箱verified，character_required=true可补建', 'character.create={nickname,...元数据}', '旧ACTIVE verified无角色可登录补建；邮箱未验证时character.create返回EMAIL_UNVERIFIED 403', 'revival/service.py；admin/src/components/character-onboarding.tsx'],
    ['旧玩家状态', '0024迁移；login / email-verify / password-reset', '旧账号沿用原状态与制裁', '邮箱验证或密码恢复不作为管理员批准', 'verified或未验证的PENDING_REVIEW都保持待审；DISABLED与sanctions保持，不批量激活、不隐式批准', 'revival/migrations/0024_email_admin_auth.py；revival/service.py'],
    ['管理员发码', 'POST /api/admin/login-code', '先校验login/password，固定管理员收件邮箱', 'login,password', 'challenge_id,expires_in=300,retry_after=60,recipient_hint；无SMTP则拒绝', 'revival/admin_email_auth.py'],
    ['管理员登录', 'POST /api/login', '密码与一次邮箱挑战', 'kind=admin,login,password,challenge_id,code', 'Session只驻内存；不接受旧otp_code作为验证', 'revival/service.py'],
    ['Owner再验证', 'POST /api/admin/step-up-code', '管理员Bearer，敏感动作额外校验', 'action,payload,metadata,current_password', '挑战绑定Session/安全版本/原业务正文及幂等键。返回operation_summary动作及12位指纹供弹窗与邮件核对；最终command补current_password/challenge_id/code', 'revival/admin_email_auth.py；admin/src/lib/api.ts'],
    ['商城边界', 'shop.purchase / player.shop.quote / player.shop.purchase / player-shop', '先鉴权后关闭平台玩家入口', '不提供前端购买表单或代理白名单', 'PLATFORM_SHOP_CLOSED 403；管理员商城运营与原生游戏商城仍保留', 'revival/service.py；tools/native_launcher.py'],
    ['反馈列表', 'GET /api/views/feedback；feedback-admin', '玩家仅本人。Owner/users处理，auditor只读，resources拒绝', 'offset,limit,state', '{tickets,total,offset,limit}；OPEN/IN_PROGRESS/RESOLVED/CLOSED', 'revival/feedback.py'],
    ['反馈详情', 'GET /api/views/feedback-detail', '本人或具反馈权限管理员', 'ticket_id', '工单平铺+messages+attachments。其他玩家工单404；resources角色403；未登录401。', 'revival/feedback.py'],
    ['反馈写入', 'feedback.create/reply/status，经/api/command', 'create仅玩家；status仅Owner/users', 'create:{title,body}；reply:{ticket_id,expected_revision,body}；status:{ticket_id,expected_revision,state}', '正文最多5000字；CAS冲突409；已解决后回复重开OPEN；CLOSED终态只读，禁回复/上传/改状态', 'revival/feedback.py'],
    ['反馈截图上传', 'POST /api/feedback/{ticket_id}/attachments', '当前Bearer+同源multipart', 'file,upload_key；整单最多3张，原图及重编码输出均不超过5MiB', 'PNG/JPEG/WebP解码后清理元数据转WebP；返回metadata+ticket_revision；同原图同键幂等，同键换图409', 'revival/media.py'],
    ['私图读取', 'GET /api/feedback/attachments/{id}', '必须Bearer且具工单归属/管理员权限', '附件id；不得由静态目录公开读取', '鉴权二进制；前端取Blob并及时撤销object URL', 'revival/media.py；admin/src/components/feedback-pages.tsx'],
    ['活动封面', 'POST /api/admin/media；GET /api/media/{id}', '上传Owner/resources。公共媒体GET可匿名。', '上传multipart(file,upload_key)，单图5MiB', '返回id/url/mime/width/height/bytes；公共封面与私有反馈图隔离', 'revival/media.py'],
    ['活动页面保存', 'campaign.presentation.save', 'Owner/resources；expected_revision针对展示修订', 'campaign_id,expected_revision,presentation:{slug,title,summary,theme,cover_id,sections}', 'slug发布后锁定；最多12模块，kind=text/rewards/leaderboards；纯文本存储，不解释HTML', 'revival/activitypresentation.py'],
    ['活动详情读取', 'GET /api/public/campaign-detail；/api/views/campaign-detail', '游客不见草稿/个人进度；玩家读本人资格', 'slug（或受控查询campaign_id）', '单个Campaign+presentation；已登录玩家另含progress/rewards', 'revival/activitypresentation.py'],
    ['桌面账号与图片', 'POST /launcher/api/account / feedback-upload / feedback-image', '随机本机操作能力；不下发业务Session到React', 'account固定operation+payload；图片有界base64及稳定upload_key', '缺cap仅布局表单预览，禁提交；远端仍multipart/binary，不提供任意URL代理', 'tools/native_launcher.py；admin/src/components/native-launcher.tsx'],
    ['桌面公共封面', 'POST /launcher/api/campaign-cover-image', '固定本机能力与公共媒体ID；远端公共GET不带业务Bearer', 'media_id为32位小写十六进制；受限base64输出', '只读取/api/media/{id}的5MiB内WebP；三张本地模板图严格白名单可直显，其他地址不代理', 'tools/native_launcher.py；admin/src/lib/campaign-cover.ts'],
    ['认证错误', 'REAUTH_REQUIRED / ADMIN_EMAIL_CODE / MAIL_NOT_CONFIGURED / MAIL_DELIVERY_FAILED', '分别403/403/503/503', '不显示验证码、原始SMTP错误或管理员真实邮箱', '失败不降级TOTP、不启用免码、不自动重放写操作', 'revival/admin_email_auth.py；revival/mailing.py'],
  ]
  const pageRows = [
    ['01', '玩家注册', '/player与Launcher首页的创建账号', '新注册邮箱、密码、确认密码、昵称、验证码；确认一致才调用接口，确认值不传后端，成功后自动登录。旧ACTIVE且邮箱verified无角色可补昵称；旧PENDING仍待管理员批准。', '待本轮QA', 'admin/src/components/email-access.tsx'],
    ['02', '管理员认证', '/admin与旧console管理员入口', '密码校验后发送邮箱码，Owner敏感动作独立邮箱再验证弹窗。', '待本轮QA', 'admin/src/components/admin-step-up.tsx'],
    ['03', '玩家商城关闭', '玩家Web、Launcher、legacy console', '移除导航、快捷卡、页面与购买调用；管理员运营保持。', '待本轮QA', 'admin/src/portal-app.tsx；admin/src/App.tsx'],
    ['04', '双人主视觉', '官网与Launcher场景', '艾米、芭比同框二次元，沿用深蓝灰暖金。粉白方案已取消。', '待本轮QA', 'admin/src/blossom.css；admin/public/assets/'],
    ['05', '中文显示', '玩家道具、状态、错误、后台选择器', '名称与资源键分开；地图ID去装饰，未核实难度只显示待确认选项，币种/槽位/状态技术码折叠，表单值不改写。', '待本轮QA', 'admin/src/components/catalog-item-picker.tsx；admin/src/lib/platform.ts'],
    ['06', '操作反馈', '两端共享提示与结果弹窗', '成功/失败明确，页面刷新不丢回执；幂等失败重试保留键。', '待本轮QA', 'admin/src/components/action-feedback.tsx'],
    ['07', '动态景深', '官网与Launcher公共背景', '限制位移，后台停止；触屏和prefers-reduced-motion静态降级。', '待本轮QA', 'admin/src/components/ambient-backdrop.tsx'],
    ['08', '道具配图', '仓库、奖励、后台资源', '准确图 ' + exactImages + '/' + artwork.length + '，其余分类示意明确标注，不伪装为原图。', '待本轮QA', 'admin/src/lib/item-presentation.json'],
    ['09', '活动专题', '/activities及/activities/<slug>', '导航统一活动。汇总/详情/后台预览共用三种模板预置封面，自定义公共封面优先；真实奖励/榜单模块，发布后奖励不变。', '待本轮QA', 'admin/src/components/platform-pages.tsx；admin/src/lib/campaign-cover.ts'],
    ['10', '反馈工单', '玩家/Launcher反馈中心；管理员玩家反馈', '新建/列表/详情/回复/四态/每单3张5MiB私图，严格当前身份。', '待本轮QA', 'admin/src/components/feedback-pages.tsx'],
  ]
  const deploymentRows = [
    ['当前决定', '暂不部署', '只更新代码、设计书和本机预览，不更新远端服务或旧发行包。', '待本轮QA', '本轮用户确认'],
    ['SMTP前置', '真实管理员收件必须先验证', 'NZ_MAIL_PROVIDER/HOST/PORT/FROM/USERNAME/PASSWORD/SENDER_NAME通过受保护NZ_MAIL_ENV_FILE注入；不把值写入文档。', '未提供投递证据时不得宣称完成', 'revival/mailing.py；revival/admin_email_auth.py'],
    ['邮箱认证切换', '0024_email_admin_auth', '新增挑战表；清空旧TOTP/恢复码，提升管理员安全版本并撤销旧会话。不批量改变任何玩家状态：旧PENDING/停用/制裁均保持。', '先备份并确认邮件可达再安排部署', 'revival/migrations/0024_email_admin_auth.py'],
    ['业务媒体迁移', '0025_feedback_and_campaign_presentation', '新增反馈/私图/公共媒体/活动展示五表，旧活动按id回填slug，不改奖励快照。', '迁移按Django依赖向前，不反迁移删新数据', 'revival/migrations/0025_feedback_and_campaign_presentation.py'],
    ['媒体存储', 'NZ_MEDIA_ROOT，私有可写目录', '默认仅本机runtime/media；生产独立持久卷，限制服务用户权限，同步备份数据库与文件。禁止Nginx直接暴露私图目录。', '目录路径与权限待目标环境确认', 'revival/media.py；nzproject/settings.py'],
    ['上传限制', '仅固定multipart路由允许5MiB图片', '反馈单总数3，PNG/JPEG/WebP，最多1600万像素/单边8192；清理元数据转WebP。一般JSON请求继续维持64KiB。', '代理体积限定只配置指定路由，不放宽全站', 'revival/media.py；revival/middleware.py'],
    ['图片权限', '反馈私有，活动封面公共', '私图GET必须Bearer+工单权限；公开封面仅/api/media/{id}；上传键绑定操作者与内容。', '需越权404、未登录、关闭工单与幂等测试', 'revival/media.py'],
    ['新依赖', 'requirements.txt中的Pillow固定版本', '图片解码/重编码依赖随服务包安装；前端assets包含本轮主视觉与展示目录。', '打包验收待本轮QA，不沿用旧包摘要', 'requirements.txt；admin/public/assets/'],
    ['Launcher限制', 'Tauri平台功能包，无cap时不可提交', '图片经受限本机base64桥；无私有SSH/完整仓库/服务端密钥分发。Mac只布局预览。', 'Windows冻结包和实玩分别验收', 'tools/native_launcher.py；tools/package_launcher.py'],
    ['回退注意', '认证策略迁移不可复活旧秘密', '保留0024/0025和新媒体数据。旧TOTP页面与新邮箱API不能混搭。应用回退需先验证兼容，不恢复旧会话。', '回退计划须在实际发布前演练', 'revival/migrations/0024_email_admin_auth.py'],
    ['本轮验收', '设计书暂记待本轮QA', '新测试、浏览器截图、SMTP真实收件和包摘要由本轮结果补充。保留首轮证据的原日期与范围。', '待本轮QA', 'docs/delivery/2026-10-20-frontend-account-activities-feedback.md'],
  ]
  if (finalEvidence) {
    // 当前验收状态只覆盖已有证据；发布和真实邮件、原生玩法限制不会被“通过”字样覆盖。
    overviewRows[0][3] = '指定验收已记录；未部署'
    overviewRows[2][3] = '自动测试；真实SMTP未验'
    overviewRows[3][3] = '自动测试及指定浏览器流程'
    overviewRows[4][3] = '本机预览；Windows实机未验'
    overviewRows[5][1] = '本轮证据见最终验收；首轮历史仍保留。'
    overviewRows[5][2] = '前端、浏览器、MySQL、CI及包分别记录。SMTP、干净Windows与游戏实玩始终未验。'
    overviewRows[5][3] = '按证据范围分层'
    const pageStates = {
      '01': '前端用例及登录实检；真实SMTP注册未验', '02': '自动测试；真实管理员收件未验',
      '03': '入口/构建与服务端测试', '04': '桌面/手机本机预览', '05': '仓库浏览器实检与显示用例',
      '06': '签到/重复签到弹窗实检', '07': '本机布局；长期性能未验', '08': '仓库中文分类图实检',
      '09': '活动详情与预置封面本机预览', '10': '文字工单及鉴权截图提交实检',
    }
    pageRows.forEach(row => { row[4] = pageStates[row[0]] })
    deploymentRows[0][3] = '未部署'
    deploymentRows.find(row => row[0] === '新依赖')[3] = '本轮包摘要已核对；目标机器安装未验'
    deploymentRows.find(row => row[0] === 'Launcher限制')[3] = 'CI/冻结包证据见最终验收；干净机器与实玩未验'
    const qa = deploymentRows.find(row => row[0] === '本轮验收')
    qa[1] = '指定验证及最终包清单已记录'
    qa[2] = '前端、真实浏览器、自动后端测试及包摘要分层记录；保留首轮历史。正式SMTP、干净Windows与实玩未验。'
    qa[3] = '见最终验收、本轮包清单'
  }
  for (const basename of names) {
    const original = path.join(legacyDir, basename + '.xlsx')
    const originalBytes = await fs.readFile(original)
    const book = await SpreadsheetFile.importXlsx(await FileBlob.load(original))
    const inventory = await book.inspect({ kind: 'sheet', include: 'id,name', maxChars: 12000 })
    const originalNames = inventory.ndjson.split('\n').filter(Boolean).map(line => JSON.parse(line)).filter(row => row.kind === 'sheet').map(row => row.name)
    const before = new Map()
    const changed = new Set(['第二轮变更'])
    for (const sheetName of originalNames) {
      const sheet = book.worksheets.getItem(sheetName)
      before.set(sheet.name, JSON.stringify({ values: sheet.getUsedRange().values, formulas: sheet.getUsedRange().formulas }))
    }
    /** 精确改动已匹配的业务行；只更改不同单元格，其他值、公式和样式不重建。 */
    function patchRows(sheetName, predicate, replace) {
      const sheet = book.worksheets.getItem(sheetName)
      const rows = sheet.getUsedRange().values
      rows.forEach((row, index) => {
        if (index < 5 || !predicate(row)) return
        const next = replace([...row])
        next.forEach((value, column) => { if (value !== row[column]) sheet.getCell(index, column).values = [[cell(value)]] })
        sheet.getRangeByIndexes(index, 0, 1, row.length).format.autofitRows()
        changed.add(sheetName)
      })
    }
    /** 只追加缺失的稳定标识行，扩展已有Excel Table，保留原筛选和冻结设置。 */
    function appendRows(sheetName, rows, keyColumns) {
      const sheet = book.worksheets.getItem(sheetName)
      const existing = sheet.getUsedRange().values
      const key = row => keyColumns.map(column => String(row[column])).join('\u001f')
      const keys = new Set(existing.slice(5).map(key))
      const added = rows.filter(row => !keys.has(key(row))).map(row => row.map(cell))
      if (!added.length) return
      sheet.tables.items[0].rows.add(null, added)
      const area = sheet.getRangeByIndexes(existing.length, 0, added.length, added[0].length)
      area.format.font = { name: FONT, size: 10, color: INK }; area.format.wrapText = true; area.format.verticalAlignment = 'center'; area.format.autofitRows()
      changed.add(sheetName)
    }
    if (basename === '表设计') {
      patchRows('设计概览', row => row[0] === '文档版本', row => [row[0], '第二轮设计 v2', '2026-10-20 America/Los_Angeles', finalEvidence ? '向前合并0024/0025；指定验收已记录，未部署。' : '向前合并0024/0025；本轮验收待补，未部署'])
      patchRows('设计概览', row => row[0] === '源码基线', row => [row[0], finalEvidence?.runtime_commit || sources.baseline_commit, finalEvidence ? '冻结运行源码提交，已推送' : '本轮工作树父提交，含尚未提交的修改', finalEvidence ? '设计书后续提交与运行源码分开；原生玩法/正式SMTP限制见最终验收。' : '当前模型元数据不等于该提交已包含本轮全部代码。'])
      patchRows('设计概览', row => row[0] === '模型覆盖', row => [row[0], '当前74个持久模型', row[2], '新增管理员挑战1表、反馈/媒体/展示5表。数量由表目录计算。'])
      patchRows('设计概览', row => row[0] === '字段覆盖', row => [row[0], '当前字段逐行定义', row[2], '本轮新增62个字段；源自模型注册表，非运行库DDL回读。'])
      appendRows('表目录', sources.tables.map(row => [row.table, row.model, row.description, row.field_count, row.constraint_count, row.index_count, row.source]), [0])
      appendRows('字段定义', sources.fields.map(row => [row.table, row.column, row.field, row.sql_type, row.nullable ? '是' : '否', row.primary_key ? '是' : '否', row.unique ? '是' : '否', row.default, row.collation, row.source]), [0, 1])
      appendRows('约束与索引', sources.constraints.map(row => [row.table, row.kind, row.name, row.definition, row.source]), [0, 2])
      appendRows('外键关系', sources.relations.map(row => [row.table, row.column, row.target_table, row.target_column, row.on_delete, row.nullable ? '是' : '否', row.source]), [0, 1])
      appendRows('迁移与数据保护', sources.migrations.map((name, index) => [index + 1, name, name.startsWith('0024') ? '新增挑战并切换邮箱认证，撤销旧管理员会话；不改变玩家状态、批准或制裁。' : name.startsWith('0025') ? '新增五表并回填旧活动slug，保留奖励快照。' : '按Django依赖向前执行；不覆盖旧数据库', 'revival/migrations/' + name]), [1])
      // 新模型插入导致原声明行号移动，仅刷新来源定位，原SQL类型、默认值和约束内容不重写。
      const tableSources = new Map(sources.tables.map(row => [row.table, row.source]))
      const fieldSources = new Map(sources.fields.map(row => [row.table + '/' + row.column, row.source]))
      const constraintSources = new Map(sources.constraints.map(row => [row.table + '/' + row.name, row.source]))
      const relationSources = new Map(sources.relations.map(row => [row.table + '/' + row.column, row.source]))
      patchRows('表目录', row => tableSources.has(row[0]), row => { row[6] = tableSources.get(row[0]); return row })
      // 管理员认证职责本轮发生变化，刷新对应模型说明；旧TOTP列仅为历史结构兼容。
      patchRows('表目录', row => row[0] === 'admins', row => { row[2] = sources.tables.find(value => value.table === 'admins').description; return row })
      patchRows('字段定义', row => fieldSources.has(row[0] + '/' + row[1]), row => { row[9] = fieldSources.get(row[0] + '/' + row[1]); return row })
      patchRows('约束与索引', row => constraintSources.has(row[0] + '/' + row[2]), row => { row[4] = constraintSources.get(row[0] + '/' + row[2]); return row })
      patchRows('外键关系', row => relationSources.has(row[0] + '/' + row[1]), row => { row[6] = relationSources.get(row[0] + '/' + row[1]); return row })
      patchRows('表目录', row => row[0] === 'admin_recovery', row => { row[2] = '历史恢复码表。0024清空，在线邮箱认证不再读写此表。'; return row })
      book.worksheets.getItem('设计概览').getRange('C9').formulas = [["=COUNTA('表目录'!A6:A" + (sources.tables.length + 5) + ')']]
      book.worksheets.getItem('设计概览').getRange('C10').formulas = [["=COUNTA('字段定义'!A6:A" + (sources.fields.length + 5) + ')']]
    } else if (basename === '游戏服务端接口设计') {
      patchRows('接口概览', row => row[0] === 'HTTP控制面', row => { row[3] = finalEvidence ? '第二轮接口已接入；分层证据见最终验收，真实SMTP与实玩未验，当前不部署。' : '第二轮账号/反馈/活动展示已接入，待本轮QA；当前不部署。'; return row })
      patchRows('HTTP路由', row => row[1] === '/api/login', row => ['POST', row[1], '管理员邮箱挑战登录', '匿名，先密码后邮箱验证码', 'kind=admin,login,password,challenge_id,code', '内存Bearer会话', '旧TOTP不作为登录验证', 'revival/service.py'])
      patchRows('HTTP路由', row => String(row[1]).includes('/register'), row => ['POST', row[1], '邮箱注册与角色创建', '邮箱验证码，无邀请或人工批准', 'email,password,nickname,challenge_id,code及幂等元数据', 'ACTIVE+verified+character标识；前端继续login', '昵称1至24字符且UTF8不超过48字节', 'revival/service.py'])
      patchRows('HTTP路由', row => row[1] === '/api/player/view', row => { row[4] = 'me/player-inventory/campaigns/campaign-detail/leaderboards/feedback/feedback-detail'; row[6] = '商城视图关闭；玩家仅本人；无管理员通道'; return row })
      appendRows('HTTP路由', [
        ['POST', '/api/admin/login-code', '管理员密码校验及邮件挑战', '匿名，先校验密码', 'login,password', 'challenge_id/recipient_hint/300秒有效/60秒冷却', '固定管理员收件，失败不降级', 'revival/admin_email_auth.py'],
        ['POST', '/api/admin/step-up-code', 'Owner敏感操作再验证', '管理员Bearer', 'action,payload,metadata,current_password', '绑定当前身份和原操作的邮箱挑战', '成功幂等重试不再次消耗验证码', 'revival/admin_email_auth.py'],
        ['POST', '/api/admin/media', '活动公共封面上传', 'Owner/resources', 'multipart(file,upload_key)', '公共媒体metadata与id', '5MiB图片，去元数据转WebP', 'revival/media.py'],
        ['POST', '/api/feedback/<str:ticket_id>/attachments', '反馈私有截图上传', '本人或具反馈写权限管理员', 'multipart(file,upload_key)', '附件metadata+ticket_revision', '整单3张、单图5MiB，CLOSED拒绝', 'revival/media.py'],
        ['GET', '/api/feedback/attachments/<str:attachment_id>', '读取私有反馈截图', 'Bearer及工单归属/管理权限', '附件id', '鉴权图片二进制', '其他玩家404、resources角色403、未登录401；不挂公开静态目录', 'revival/media.py'],
        ['GET', '/api/media/<str:media_id>', '读取活动公共封面', '匿名', '公共媒体id', 'image/webp二进制', '不读取反馈私有附件', 'revival/media.py'],
      ], [1])
      patchRows('新增请求合同', row => row[0] === '商城购买', row => ['平台商城关闭', 'shop.purchase/player.shop.quote/player.shop.purchase/player-shop', '不提供购买表单', 'PLATFORM_SHOP_CLOSED 403', '先鉴权后关闭；管理员运营与原生游戏商城保留。', 'revival/service.py'])
      patchRows('既有请求合同', row => /MFA|otp_code|PENDING_REVIEW|invitation/.test(row.join(' ')) && /register|注册|登录|login|再认证|Owner/.test(row.join(' ')), row => { row[2] = '新注册邮箱验证后直接ACTIVE；管理员改邮箱挑战，详见第二轮接口合同。'; row[3] = '新注册不再要求邀请/审批。历史PENDING仍须批准，登录/补验/恢复均不改变状态。旧TOTP认证退休。'; return row })
      patchRows('命令权限', row => ['admin.mfa.reset', 'admin.recovery.rotate'].includes(row[0]), row => [row[0], '已退休', '不提供在线入口', '当前COMMANDS移除，调用NOT_FOUND 404；历史记录保留。', 'revival/service.py'])
      appendRows('命令权限', sources.commands.map(row => [row.action, row.permission, row.permission === 'player' ? '仅本人玩家会话' : row.permission === '*' ? 'Owner专属' : '按服务端权限矩阵', '完整幂等元数据，敏感Owner动作额外邮箱挑战。', row.source]), [0])
    } else if (basename === 'Web端设计书') {
      patchRows('页面架构', row => row[0] === '/player', row => { row[3] = '角色资料、仓库、活动、排行榜、反馈；旧账号缺角色先补昵称。'; return row })
      patchRows('页面架构', row => row[0] === '/launcher', row => { row[3] = '参考导航轨/顶栏/主舞台/启动区，注册、角色、仓库、活动、榜单、反馈，无玩家商城。'; return row })
      patchRows('页面架构', row => row[0] === '/activities', row => { row[1] = '活动中心'; row[3] = '真实活动汇总卡片，点击独立slug详情。'; return row })
      appendRows('页面架构', [['/activities/<slug>', '独立活动专题', '访客与已认证玩家', '模板、封面、规则说明、真实奖励与当前赛季榜单；本人进度仅登录可见。', '/api/public/campaign-detail 或 /api/views/campaign-detail', 'admin/src/components/platform-pages.tsx']], [0])
      patchRows('角色与权限', row => ['owner', 'users', 'auditor'].includes(row[0]), row => { row[2] = row[0] === 'owner' ? '全部管理及反馈处理，敏感动作邮箱再认证。' : row[0] === 'users' ? '账号管理与反馈回复/状态处理。' : '管理只读，反馈只读，不允许写入。'; return row })
      patchRows('业务流程', row => row[0] === '玩家购买', row => ['玩家商城关闭', '导航、快捷卡和购买表单全部移除。', '后端PLATFORM_SHOP_CLOSED 403', '管理员商城运营不受此入口关闭影响。', '不保留旧console购买后门。', 'revival/service.py'])
      patchRows('验收清单', row => /WEB-01|WEB-03/.test(String(row[0])), row => { row[2] = row[0] === 'WEB-01' ? '无邀请注册自动登录，管理员密码/邮箱挑战，缺角色onboard。' : '管理员运营保持，所有平台玩家购买入口和调用关闭。'; row[3] = '待本轮QA'; return row })
      patchRows('验收清单', row => !['WEB-01', 'WEB-03', 'GAME-01', 'REL-01'].includes(row[0]), row => { row[3] = '首轮记录：' + row[3] + '。当前待本轮QA。'; return row })
      patchRows('验收清单', row => row[0] === 'REL-01', row => { row[3] = '用户本轮决定暂不部署。'; return row })
      if (finalEvidence) patchRows('验收清单', row => /^WEB-/.test(row[0]), row => {
        row[3] = ({ 'WEB-01': '前端表单/登录自动测试及玩家登录实检；真实SMTP注册/管理员挑战未验。', 'WEB-02': '本轮自动测试；仓库中文分类图浏览器实检。后台发放/回收未重新进行UI实检。', 'WEB-03': '平台玩家购买入口关闭；服务端测试与前端构建通过。后台商城写操作未重新UI实检。', 'WEB-04': '策略自动测试与中文/待确认难度显示；实际建房仍按原生验收边界。', 'WEB-05': '本机玩家签到与重复签到弹窗实检；普通奖励依据自动测试，未扩大为正式活动到账证明。', 'WEB-06': '排行自动测试与空榜分组；正式可信实玩结算仍未验收。', 'WEB-07': '本机已有采集证据；远端监控部署未实施。', 'WEB-08': `${finalEvidence.frontend.tests}项前端用例与${finalEvidence.frontend.modules}模块构建通过；桌面/390px布局实检。` })[row[0]] || '见最终验收。'
        return row
      })
      patchRows('交互与状态', row => /视觉|美术|主题/.test(row.join(' ')), row => { row[2] = '深蓝灰/暖金，艾米芭比双人主视觉，动态景深和减少动画降级。'; return row })
    } else {
      patchRows('部署总览', row => row[0] === '部署对象', row => [row[0], '暂不部署，仅本机预览与代码开发', '用户本轮决定，不执行远端发布', '旧Web包、CI和Windows包保留为首轮历史证据', '本轮用户确认'])
      patchRows('部署总览', row => row[0] === '新数据库迁移', row => [row[0], '0024_email_admin_auth.py；0025_feedback_and_campaign_presentation.py', '认证策略切换与六个新增表', '未来发布前先验证真实邮件收件、备份并向前迁移。', 'revival/migrations/'])
      patchRows('部署总览', row => row[0] === 'Launcher', row => { row[2] = '首轮历史包（2026-10-19）：' + row[2] + '。本轮更新尚未重新验收冻结包。'; return row })
      patchRows('部署总览', row => row[0] === '版本识别', row => { row[2] = '首轮历史归档的源码标识，本轮代码尚未生成新归档。'; return row })
      patchRows('部署总览', row => row[0] === '发布产物SHA', row => { row[2] = '首轮历史归档摘要，不含本轮变更。'; row[3] = '本轮尚未打包或部署，不将此SHA作为当前交付包。'; return row })
      patchRows('部署总览', row => row[0] === '远端与CI', row => { row[1] = '首轮历史记录（2026-10-19）：' + row[1]; row[2] = '首轮历史CI：' + row[2]; row[3] = '本轮暂不部署；旧CI不证明新邮箱认证、媒体和页面已验收。'; return row })
      patchRows('环境配置', row => row[0] === '邮件配置', row => [row[0], '受保护SMTP环境文件，真实收件为认证前置。', '保持固定TLS/465、发件身份与持久额度；收件固定管理员邮箱。', '玩家邮箱验证/恢复、管理员登录与Owner再验证。', 'revival/mailing.py；revival/admin_email_auth.py'])
      patchRows('环境配置', row => row[0] === '管理员', row => [row[0], '密码与邮箱挑战', 'TOTP/恢复码在线认证已退休，邮件失败时不降级。', 'bootstrap仅建立邮箱认证管理员，不输出TOTP种子。', 'revival/migrations/0024_email_admin_auth.py'])
      patchRows('发布与回退步骤', () => true, row => row.map(value => typeof value === 'string' ? value.replace(/管理员MFA/g, '管理员邮箱挑战').replace(/资产、MFA/g, '资产与认证状态').replace(/0022\/0023/g, '0024/0025') : value))
      patchRows('交付与阻塞', row => row[0] === 'Web部署', row => [row[0], '用户决定暂不部署', '仅本机预览，未来发布须先验证SMTP与媒体权限。', '待本轮QA，不沿用历史CI当作本轮证据。', '本轮用户确认'])
      patchRows('交付与阻塞', row => row[0] === 'Windows开发包', row => { row[1] = '首轮历史记录（2026-10-19）：' + row[1]; row[2] = '本轮页面、认证与媒体更新后尚未重新验收冻结包。'; return row })
      patchRows('交付与阻塞', row => /Git提交与推送|CI|本机演示账号/.test(String(row[0])), row => { row[1] = '首轮历史记录（2026-10-19）：' + row[1]; row[2] = '仅历史范围。本轮新认证、页面、图片与冻结包待本轮QA。'; return row })
      book.worksheets.getItem('实际归档文件').getRange('A2').values = [['首轮历史 Web 归档逐文件清单']]
      changed.add('实际归档文件')
      patchRows('打包文件清单', row => row[0] === 'Web归档产物', row => { row[0] = '首轮历史Web归档'; row[4] = '不含本轮0024/0025、邮箱认证、媒体与新UI，不能作为本轮发布包。'; return row })
      patchRows('打包文件清单', row => row[0] === '文档包', row => { row[5] = path.relative(root, outputDir) + '/'; return row })
      patchRows('打包文件清单', row => row[0] === 'Launcher实际归档', row => { row[0] = '首轮历史Launcher归档'; row[4] = '2026-10-19历史包校验。本轮新认证、媒体与页面尚未重新打包验收。'; return row })
      patchRows('打包文件清单', row => row[0] === 'GitHub下载包装层', row => { row[0] = '首轮历史GitHub包装层'; row[4] = '2026-10-19历史artifact包装层，不代表本轮发行包。'; return row })
      if (finalEvidence) {
        patchRows('部署总览', row => row[0] === '版本识别', row => [row[0], finalEvidence.runtime_commit, '最终冻结运行源码，已推送；文档后续提交分开。', '本轮Web与Windows包摘要见本轮包清单；未部署。', finalEvidence.source_evidence || 'git rev-parse HEAD'])
        patchRows('部署总览', row => row[0] === '发布产物SHA', row => [row[0], '见本轮包清单', '对当前文件逐项重算SHA256及大小；首轮摘要仅作历史。', '包校验不等于正式部署、SMTP或干净机器游戏验收。', path.relative(root, path.join(outputDir, 'final-evidence.json'))])
        patchRows('部署总览', row => row[0] === 'Launcher', row => { row[2] = '本轮CI/冻结包证据见最终验收；Mac仅无capability布局预览。'; row[3] = 'Windows干净安装、真实API/SMTP与游戏实玩分别待验。'; return row })
        patchRows('交付与阻塞', row => row[0] === 'Web部署', row => [row[0], '当前包已归档，用户决定暂不部署', '未来部署前验证SMTP真实收件与媒体权限。', '包摘要已核对；未更新远端服务。', '本轮包清单；最终验收'])
        patchRows('交付与阻塞', row => row[0] === 'Windows开发包', row => [row[0], '最终CI/冻结产物见本轮包清单', '干净Windows、正式签名、真实API及实玩未验。', 'CI/包检查与实机验收分开记录。', '最终验收；本轮包清单'])
      }
    }
    tableCount = 1000
    const extras = [addTable(book, '第二轮变更', '第二轮变更与验收边界', ['项目', '本轮设计', '行为与边界', '当前状态', '依据'], overviewRows, [25, 74, 95, 22, 80], finalEvidence ? '2026-10-20。指定验证、当前包与正式环境限制分层记录；暂不部署。' : '2026-10-20。保留首轮历史，当前暂不部署，验收状态待本轮补证据。')]
    if (finalEvidence) extras.push(addTable(book, '最终验收', '最终证据与未验边界', ['验证层', '结果或产物', '实际状态', '范围与限制', '证据'], finalRows, [29, 108, 32, 125, 112], '运行源码、自动测试、真实页面、包校验与正式环境分别记录；SMTP/干净机器/原生实玩未验。'))
    if (basename === '游戏服务端接口设计') extras.push(addTable(book, '第二轮接口合同', '邮箱认证、反馈与活动专题接口', ['业务', '入口', '认证与权限', '请求', '响应或约束', '依据'], interfaceRows, [25, 75, 70, 103, 110, 82], finalEvidence ? '新增合同与旧冲突字段以本页为准；验收证据及SMTP/实玩限制见最终验收。' : '新增接口与旧冲突字段以本页为准。所有新验收状态均待本轮QA。'))
    if (basename === 'Web端设计书') extras.push(addTable(book, '第二轮页面流程', '第二轮十项页面与交互', ['编号', '模块', '入口', '设计与行为', '验收状态', '依据'], pageRows, [12, 28, 67, 113, 22, 88], '用户已取消粉白，保留深蓝灰暖金。准确配图数量从当前公开展示目录读取。'))
    if (basename === '部署手册及打包清单') extras.push(addTable(book, '媒体与邮箱部署', '邮箱认证与媒体持久化准备', ['项目', '配置或选择', '要求与操作', '当前状态', '依据'], deploymentRows, [28, 67, 125, 65, 90], '当前不部署。以下是未来发布前置条件，不能解读为已配置或已投递成功。'))
    if (basename === '部署手册及打包清单' && finalEvidence) {
      const packageRows = finalPackages.map(item => [item.label, item.file, item.bytes, item.sha256, item.source_commit || finalEvidence.runtime_commit, item.limitations, item.evidence || item.file])
      packageRows.push(...externalLayers.map(item => [item.label, item.display_name, item.bytes, item.sha256, item.source_commit, item.limitations, item.evidence]))
      extras.push(addTable(book, '本轮包清单', '本轮最终产物与SHA256', ['产物层', '文件或证据层', '字节数', 'SHA256', '运行源码', '范围与限制', '依据'], packageRows, [30, 90, 18, 80, 53, 116, 110], '交付文件直接重算摘要/大小；不交付的GitHub包装层按独立验包证据核对，不混用内部ZIP哈希。'))
      const manifestRows = []
      for (const item of finalPackages) {
        const note = item.kind === 'web' ? 'Web payload；不含运行凭据或游戏服务' : 'Windows平台payload；不含完整游戏客户端、SSH或开发仓库'
        manifestRows.push(...(item.manifest?.files || []).map(entry => [item.label, entry.path, entry.bytes, entry.sha256, note, item.manifest_file]))
        if (item.release) manifestRows.push([item.label, 'RELEASE.json', item.release.bytes, item.release.sha256, '包内源码标识与排除说明；额外1项，不计入payload', item.file])
        if (item.kind === 'launcher_inner' && item.manifest) manifestRows.push([item.label, 'package-manifest.json', item.manifestSize, item.manifestSha256, '额外manifest；独立证据已核对与sidecar逐字节相同，不计入payload', item.evidence])
      }
      if (manifestRows.length) extras.push(addTable(book, '本轮归档文件', '本轮Web与Windows逐文件清单', ['产物', '路径', '字节数', 'SHA256', '说明', '依据'], manifestRows, [30, 105, 18, 80, 92, 100], 'Web 178payload+RELEASE；Windows 761payload+manifest，各按本轮独立校验清单；首轮页继续保留为历史。'))
    }
    const sheets = []
    const unchanged = []
    await fs.mkdir(path.join(outputDir, 'previews'), { recursive: true })
    for (const sheetName of [...originalNames, ...extras.map(row => row.name)]) {
      const sheet = book.worksheets.getItem(sheetName)
      const values = sheet.getUsedRange().values
      const width = Math.max(...values.map(row => row.length))
      sheets.push({ sheet, name: sheet.name, rows: Math.max(0, values.length - 5), lastRow: values.length, lastCol: col(width - 1) })
      if (before.has(sheet.name) && !changed.has(sheet.name)) {
        const after = JSON.stringify({ values, formulas: sheet.getUsedRange().formulas })
        if (after !== before.get(sheet.name)) throw new Error('未授权工作表发生数值或公式变化：' + basename + '/' + sheet.name)
        unchanged.push(sheet.name)
        try { await fs.copyFile(path.join(legacyDir, 'previews', basename + '-' + sheet.name + '.png'), path.join(outputDir, 'previews', basename + '-' + sheet.name + '.png')) } catch (error) { if (error.code !== 'ENOENT') throw error }
      }
    }
    verification.scopePreservation.push({ workbook: basename, originalSha256: createHash('sha256').update(originalBytes).digest('hex'), unchangedValueAndFormulaSheets: unchanged, changedSheets: [...changed, ...extras.map(row => row.name)] })
    await finish(book, basename, sheets)
    if (basename === '表设计') {
      const preview = await book.render({ sheetName: '字段定义', range: 'A408:J474', scale: 1, format: 'png' })
      await fs.writeFile(path.join(outputDir, 'previews/表设计-新增字段完整区.png'), new Uint8Array(await preview.arrayBuffer()))
    }
    if (basename === '部署手册及打包清单' && finalEvidence && finalPackages.some(row => row.kind === 'launcher_inner')) {
      // 附加首批Windows清单预览，避免长表默认前13行只看到Web文件而漏查另一种manifest布局。
      const firstWindowsRow = 6 + finalPackages.filter(row => row.kind === 'web').reduce((sum, row) => sum + (row.manifest?.files?.length || 0) + (row.release ? 1 : 0), 0)
      const preview = await book.render({ sheetName: '本轮归档文件', range: `A${firstWindowsRow}:F${firstWindowsRow + 12}`, scale: 1, format: 'png' })
      await fs.writeFile(path.join(outputDir, 'previews/部署手册及打包清单-本轮Windows字段区.png'), new Uint8Array(await preview.arrayBuffer()))
    }
  }
  await fs.writeFile(verificationPath, JSON.stringify(verification, null, 2))
  console.log(JSON.stringify({ round: 'portal-v2', workbooks: verification.workbooks.length, verification: verificationPath, newTables: [...newTables] }))
}

if (roundV2) {
  await buildPortalV2()
  process.exit(0)
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
    ['/activities', '活动', '访客与玩家', '活动奖励、北京时间累计进度、签到与普通奖励领取', '公开或本人campaigns', await source('admin/src/components/platform-pages.tsx', 'CampaignsPage')],
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
