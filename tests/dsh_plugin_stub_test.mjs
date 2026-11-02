/**
 * DSH 插件端到端联调（无 DSH 依赖）—— QQ「大肥鱼」多心场景版：
 *   1. 启动真实 `xinji serve --hearts-dir`（Python 子进程，多心模式）
 *   2. 用最小桩宿主（模拟 Cordis ctx）加载 dsh-xinji 插件（heartMode=per-user）
 *   3. 两个 QQ 用户私聊 + 一个群聊（@ 与非 @）→ 验证多心隔离与隐私策略
 *   4. 治理工具作用于「当前心」；删除话题级连原话 + 重放验收
 * 运行：node tests/dsh_plugin_stub_test.mjs   （需 PYTHONPATH=src）
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const PLUGIN_URL = pathToFileURL(join(ROOT, 'dsh-xinji', 'index.js')).href

const AUTUMN = '2026-10-15T12:00:00'
let passed = 0
let failed = 0
function check(name, cond, extra = '') {
  if (cond) { passed++; console.log(`  ✅ ${name}`) }
  else { failed++; console.error(`  ❌ ${name} ${extra}`) }
}

// ---- 1. 启动 xinji serve（多心模式） -------------------------------------------

const heartsDir = mkdtempSync(join(tmpdir(), 'xinji-dsh-hearts-'))
const port = 9300 + (process.pid % 500)
const proc = spawn('python', ['-m', 'xinji', 'serve',
                              '--hearts-dir', heartsDir,
                              '--host', '127.0.0.1', '--port', String(port)],
                   { env: { ...process.env, PYTHONPATH: join(ROOT, 'src'),
                            PYTHONIOENCODING: 'utf-8' }, stdio: 'pipe' })
proc.stderr.on('data', (d) => process.stderr.write(`[serve] ${d}`))

async function api(method, path, body, heart) {
  const headers = { 'Content-Type': 'application/json' }
  if (heart) headers['X-Xinji-Heart'] = heart
  const resp = await fetch(`http://127.0.0.1:${port}/api/v1${path}`, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return { status: resp.status, data: await resp.json() }
}

/** 等到某颗心的 view 满足条件（入账是异步 fire-and-forget）。 */
async function waitView(heart, pred, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  let view = null
  while (Date.now() < deadline) {
    try { view = (await api('GET', '/view', undefined, heart)).data } catch { view = null }
    if (view && pred(view)) return view
    await sleep(100)
  }
  return view ?? { memories: [] }
}

let ready = false
for (let i = 0; i < 80 && !ready; i++) {
  try { ready = (await api('GET', '/health')).status === 200 } catch { await sleep(100) }
}
if (!ready) { console.error('xinji serve 未就绪'); process.exit(1) }
console.log('== xinji serve 已就绪（多心模式）')

// ---- 2. 桩宿主 ----------------------------------------------------------------

const sections = []
const listeners = {}
const tools = []
const fakeCtx = {
  systemPrompt: { section: (spec) => sections.push(spec) },
  on: (name, fn) => { (listeners[name] ??= []).push(fn) },
  tools: { register: (t) => tools.push(t) },
  effect: () => {},
}
async function emit(name, ...args) {
  for (const fn of listeners[name] ?? []) await fn(...args)
}

/** 等到注入文本满足条件（插件刷新记忆包是异步的，轮询代替固定 sleep）。 */
async function waitSection(pred, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  let text = section ? section.text() : ''
  while (Date.now() < deadline) {
    text = section ? section.text() : ''
    if (pred(text)) return text
    await sleep(100)
  }
  return text
}

// ---- 3. 加载插件（heartMode=per-user，群聊按群建心，群聊仅 @ 入账） -------------

console.log('== 加载 dsh-xinji 插件（heartMode=per-user，groupCapture=at）')
const plugin = await import(PLUGIN_URL)
plugin.apply(fakeCtx, {
  serverUrl: `http://127.0.0.1:${port}`,
  heartMode: 'per-user',
  groupHeartMode: 'per-session',
  groupCapture: 'at',
  dbLabel: 'companion.db',
})
await sleep(500)

check('system prompt 段已注册', sections.some((s) => s.name === 'xinji-memory'))
check('治理工具已注册（5 个）', tools.length === 5)
const section = sections.find((s) => s.name === 'xinji-memory')

// QQ 消息构造器（模拟 DSH QQ 插件的 session/event 载荷，字段名防御式匹配）
function userMsg(text, { userId, groupId, isAt = true } = {}) {
  const message = {
    role: 'user',
    content: [{ type: 'text', text }],
    sender: { user_id: userId },
    ...(groupId ? { group_id: groupId, isAt } : {}),
  }
  return { type: 'user/message', message }
}
function assistantMsg(text, { userId, groupId } = {}) {
  return { type: 'assistant/message',
           message: { role: 'assistant', content: [{ type: 'text', text }],
                      sender: { user_id: 10000 },
                      ...(groupId ? { group_id: groupId } : {}) } }
}

// ---- 4. 两个 QQ 用户私聊：各自许愿，互不串 --------------------------------------

console.log('== QQ 私聊：alice(111) 与 bob(222) 各自许愿')
await emit('session/event', userMsg('我想在秋天去青岛看海。', { userId: 111 }))
await emit('session/event', userMsg('我最讨厌香菜，别给我加香菜。', { userId: 222 }))
await emit('session/event', assistantMsg('嗯，我在听。', { userId: 111 }))
const viewA = await waitView('u-111', (v) => JSON.stringify(v).includes('青岛'))
const viewB = await waitView('u-222', (v) => JSON.stringify(v).includes('香菜'))

const hearts = (await api('GET', '/hearts')).data.hearts
check('两颗心已建立（u-111 / u-222）',
      hearts.includes('u-111') && hearts.includes('u-222'), JSON.stringify(hearts))
check('alice 的心有她的愿望', JSON.stringify(viewA).includes('青岛'))
check('bob 的心有他的偏好', JSON.stringify(viewB).includes('香菜'))
check('bob 的心绝无 alice 的记忆', !JSON.stringify(viewB).includes('青岛'))

console.log('== alice 的注入：她自己提起 → 允许接上（section 读当前心缓存）')
await emit('session/event', userMsg('对了，我决定十月去看海！', { userId: 111 }))
const injectedA = await waitSection((t) => t.includes('看海'))
check('alice 注入包含看海记忆', injectedA.includes('看海'), injectedA)
check('alice 注入不包含 bob 的偏好', !injectedA.includes('香菜'), injectedA)

// ---- 5. 群聊：@ 才入账（隐私策略），群心独立 -------------------------------------

console.log('== 群聊(987654)：非 @ 消息不入账；@ 消息入群心')
await emit('session/event', userMsg('大家中午吃什么？', { userId: 111, groupId: 987654, isAt: false }))
await emit('session/event', userMsg('@bot 我最近想学吉他。', { userId: 111, groupId: 987654, isAt: true }))
const viewG = await waitView('g-987654', (v) => JSON.stringify(v).includes('吉他'))
check('群心已建立（g-987654）', viewG.memories.length >= 1)
check('非 @ 的闲聊未入账', !JSON.stringify(viewG).includes('中午吃什么'))
check('@ 的消息已入账', JSON.stringify(viewG).includes('吉他'))

// ---- 6. 治理工具作用于当前心 ------------------------------------------------------

console.log('== 治理工具：当前心是 g-987654（最后一条用户消息所在的心）')
const viewTool = tools.find((t) => t.name === 'xinji_view')
const forgetTool = tools.find((t) => t.name === 'xinji_forget')
const replayTool = tools.find((t) => t.name === 'xinji_replay')
const viewJson = JSON.parse(await viewTool.execute({}))
check('xinji_view 返回当前心（g-987654）',
      viewJson.heart === 'g-987654', viewJson.heart)
check('群心里有吉他记忆', viewJson.memories.some((m) => m.content.includes('吉他')))

const forgetMsg = await forgetTool.execute({ kind: 'topic', target: '吉他',
                                             reason: '她要求忘记' })
check('群心删除吉他：重放验收通过', forgetMsg.includes('通过'), forgetMsg)
const viewAfter = JSON.parse(await viewTool.execute({}))
check('删除后群心不再有吉他', !viewAfter.memories.some((m) => m.content.includes('吉他')))

const replayMsg = await replayTool.execute({})
check('xinji_replay 验收通过', replayMsg.includes('通过'), replayMsg)
// 私聊心不受群心删除影响
const viewA2 = (await api('GET', '/view', undefined, 'u-111')).data
check('alice 私聊心不受群心治理影响', JSON.stringify(viewA2).includes('看海'))

// ---- 收尾 -----------------------------------------------------------------------

console.log(`\n结果：${passed} 通过，${failed} 失败`)
proc.kill()
try { rmSync(heartsDir, { recursive: true, force: true, maxRetries: 5 }) } catch {}
process.exit(failed > 0 ? 1 : 0)
