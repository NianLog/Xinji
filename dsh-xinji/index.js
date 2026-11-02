/**
 * 心记 Xinji × DeepSeek Harness —— 面向长期陪伴的关系记忆插件。
 *
 * 接入架构：
 *
 *   DSH agent loop ──(session/event)──▶ 本插件 ──(HTTP)──▶ xinji serve (Python, xinji-api/v1)
 *        ▲                                  │
 *        └──(ctx.systemPrompt.section)◀─────┘  记忆包缓存
 *
 * 三个承诺如何在插件层落地：
 *
 * - 记得住：`user/message` / `assistant/message` 事件原话入账（hear）；
 *   每轮拿到记忆包，经 `ctx.systemPrompt.section` 注入上下文（约 8k 预算内
 *   的 3–5 条 + 分寸指引 + 有效承诺）。
 * - 说得对：注入文本自带「分寸」指引——被边界覆盖的旧事默认沉默
 *   （默认沉默是心记最重要的产品决定），她主动提起时才自然接上。
 * - 忘得掉：治理工具（xinji_view / xinji_forget / xinji_correct / xinji_replay）
 *   注册为 agent 工具，她说删就删，删除自带重放验收。
 *
 * 降级原则（先降级，不装记得）：xinji 服务不可达时静默退出注入，
 * 不编造任何记忆；恢复后自动重连。
 */

/** 插件名（Cordis 约定）。 */
export const name = 'xinji-memory'

const API = '/api/v1'

// ---------------------------------------------------------------------------
// 配置：实现 Standard Schema (v1) 的最小校验器，填默认值。
// （刻意不依赖 Schemastery：配置全部有默认值，插件在最小环境也能加载。）
// ---------------------------------------------------------------------------

export const SCHEMA_DEFAULTS = {
  /** xinji serve 的地址（python -m xinji serve 启动） */
  serverUrl: 'http://127.0.0.1:8642',
  /** 心的路由：single=全部对话一颗心；per-user=每人一颗（私聊陪伴）；
   *  per-session=每会话一颗。多心模式要求 serve 以 --hearts-dir 启动。 */
  heartMode: 'single',
  /** 群聊建心方式：per-session=每个群一颗（群内共同记忆，推荐）；
   *  per-user=群内每人一颗（只在私聊式群聊时用）。 */
  groupHeartMode: 'per-session',
  /** 群聊入账策略：at=仅 @机器人的消息入账（隐私优先，推荐）；
   *  all=群聊全部入账（请确保告知群成员）。私聊始终入账。 */
  groupCapture: 'at',
  /** single 模式使用的 heart id */
  defaultHeart: 'default',
  /** 是否把记忆包注入 system prompt */
  injectMemories: true,
  /** 是否注册治理工具（view/forget/correct/replay/stage） */
  governanceTools: true,
  /** 是否自动把 user/assistant 消息写入账本 */
  autoHear: true,
  /** 是否同时把 agent 的合成注入（文件通知、cron 等）也入账 */
  captureSynthetic: false,
  /** 记忆段在 system prompt 中的排序（越大越靠后） */
  memorySectionOrder: 900,
  /** 注入文本里单条记忆的最大长度（超出截断） */
  maxItemChars: 160,
  /** 单次 HTTP 请求超时（毫秒） */
  requestTimeoutMs: 4000,
  /** 账本文件标签（仅用于日志提示；账本由 xinji serve 持有） */
  dbLabel: 'companion.db',
}

/** Cordis 通过 Standard Schema 接口在加载时校验 cordis.yml 里的 config。 */
export const Config = {
  '~standard': {
    version: 1,
    validate(value) {
      return { value: { ...SCHEMA_DEFAULTS, ...(value ?? {}) } }
    },
  },
}

// ---------------------------------------------------------------------------
// xinji serve HTTP 客户端（Node 20+ 自带 fetch）
// ---------------------------------------------------------------------------

function makeClient(config) {
  const base = String(config.serverUrl || '').replace(/\/+$/, '')
  async function call(method, path, body, heart) {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), config.requestTimeoutMs)
    const headers = { 'Content-Type': 'application/json' }
    if (heart) headers['X-Xinji-Heart'] = heart
    try {
      const resp = await fetch(base + API + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data?.error ?? `HTTP ${resp.status}`)
      return data
    } finally {
      clearTimeout(timer)
    }
  }
  return {
    health: () => call('GET', '/health'),
    hearts: () => call('GET', '/hearts'),
    hear: (text, role, heart) => call('POST', '/hear', { text, role }, heart),
    read: (input, now, heart) => call('POST', '/read', { input, now }, heart),
    view: (heart) => call('GET', '/view', undefined, heart),
    forget: (kind, targets, reason, heart) =>
      call('POST', '/delete', { kind, targets, reason }, heart),
    correct: (memoryId, to, reason, heart) =>
      call('POST', '/correct', { memory_id: memoryId, to, reason }, heart),
    replay: (heart) => call('POST', '/replay', {}, heart),
    stage: (value, heart) => call('POST', '/stage', { value }, heart),
  }
}

// ---------------------------------------------------------------------------
// 记忆包 → 注入文本
// ---------------------------------------------------------------------------

const KIND_ZH = { fact: '事实', episode: '情景', story: '共同故事', profile: '画像', summary: '摘要' }

function clip(text, max) {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim()
  return t.length > max ? t.slice(0, max) + '…' : t
}

/** 把一个记忆包渲染成给模型的「记忆与分寸」段落。空包返回空串（默认沉默）。 */
export function renderPack(pack, config) {
  if (!pack) return ''
  const lines = []
  if (pack.mode && pack.mode !== 'normal') {
    return '[心记] 记忆服务处于降级/停用状态：不引用任何可选旧记忆，正常聊天即可。'
  }
  const mems = pack.memories ?? []
  if (mems.length > 0) {
    lines.push('[心记] 你们之间可能有这些旧事（自然接上即可，不要生硬复述）：')
    for (const m of mems) {
      const kind = KIND_ZH[m.kind] ?? m.kind
      const topic = m.topic ? `（${m.topic}）` : ''
      lines.push(`- ${kind}${topic}：${clip(m.content, config.maxItemChars)}`)
    }
  }
  if ((pack.commitments ?? []).length > 0) {
    lines.push('[心记] 还有效的约定：')
    for (const c of pack.commitments) lines.push(`- ${clip(c.what, config.maxItemChars)}`)
  }
  if ((pack.hard_boundaries ?? []).length > 0) {
    lines.push('[心记] 分寸（必须遵守）：')
    for (const b of pack.hard_boundaries) lines.push(`- ${b.directive}（话题：${b.topic}）`)
  }
  if (pack.guidance) lines.push(`[心记] 本轮口径：${pack.guidance}`)
  return lines.join('\n')
}

// ---------------------------------------------------------------------------
// session/event 载荷的防御式解析
// ---------------------------------------------------------------------------

function eventOf(...args) {
  // 兼容 (event) 与 (event, session) 两种监听器签名
  for (const a of args) {
    if (a && typeof a === 'object' && typeof a.type === 'string') return a
  }
  return args[0]
}

function blockText(content) {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && (b.type === 'text' || typeof b.text === 'string'))
      .map((b) => b.text ?? '')
      .join('')
  }
  if (content && typeof content.text === 'string') return content.text
  return ''
}

/** 从 session/event 信封提取 (role, text)；非消息事件返回 null。 */
export function extractMessage(...args) {
  const ev = eventOf(...args)
  if (!ev || typeof ev.type !== 'string') return null
  if (ev.type !== 'user/message' && ev.type !== 'assistant/message') return null
  const msg = ev.message ?? ev
  const text = blockText(msg.content ?? msg.text ?? ev.content).trim()
  if (!text) return null
  const source = String(msg.source ?? ev.source ?? '').toLowerCase()
  const synthetic = /synthetic|inject|cron|file|notice/.test(source)
  return {
    role: ev.type === 'user/message' ? 'user' : 'assistant',
    text,
    synthetic,
    turn: ev.turn,
    raw: ev,
  }
}

// ---------------------------------------------------------------------------
// 身份识别（QQ 大肥鱼多人心路由）：防御式找 sender / group 字段
// ---------------------------------------------------------------------------

const USER_KEYS = ['userId', 'user_id', 'senderId', 'sender_id', 'uid', 'qq']
const GROUP_KEYS = ['groupId', 'group_id', 'chatGroupId', 'channelId']

function pickDeep(obj, keys, depth = 3) {
  if (!obj || typeof obj !== 'object' || depth <= 0) return undefined
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return String(obj[k])
  }
  for (const v of Object.values(obj)) {
    if (v && typeof v === 'object') {
      const hit = pickDeep(v, keys, depth - 1)
      if (hit !== undefined) return hit
    }
  }
  return undefined
}

/** 从事件里识别 (userId, groupId, isGroup, isAt)。识别不到时全部为 null。 */
export function extractIdentity(...args) {
  const ev = eventOf(...args)
  if (!ev) return { userId: null, groupId: null, isGroup: false, isAt: null }
  const msg = ev.message ?? ev
  const userId = pickDeep(msg, USER_KEYS) ?? pickDeep(ev, USER_KEYS) ?? null
  const groupId = pickDeep(msg, GROUP_KEYS) ?? pickDeep(ev, GROUP_KEYS) ?? null
  const isGroup = groupId !== null
    || /group/.test(String(msg.chatType ?? ev.chatType ?? '').toLowerCase())
  const atFlag = msg.isAt ?? msg.atMe ?? msg.mentionedMe ?? ev.isAt ?? ev.atMe
    ?? (Array.isArray(msg.mentions) && msg.mentions.length > 0) ?? null
  return { userId, groupId, isGroup, isAt: atFlag === null ? null : Boolean(atFlag) }
}

/** 按 heartMode / 群聊形态解析这颗对话属于哪颗心。 */
export function resolveHeart(config, identity) {
  if (config.heartMode === 'per-user' && identity.userId) {
    return identity.isGroup && config.groupHeartMode === 'per-session' && identity.groupId
      ? `g-${identity.groupId}`
      : `u-${identity.userId}`
  }
  if (config.heartMode === 'per-session') {
    if (identity.isGroup && identity.groupId) {
      return config.groupHeartMode === 'per-user' && identity.userId
        ? `g-${identity.groupId}-u-${identity.userId}`
        : `g-${identity.groupId}`
    }
    if (identity.userId) return `u-${identity.userId}`
  }
  return config.defaultHeart ?? 'default'
}

// ---------------------------------------------------------------------------
// 治理工具（dsh-tools 可选：缺席时退回原始描述符形状）
// ---------------------------------------------------------------------------

async function loadDefineTool() {
  try {
    const mod = await import('@deepseek-ai/dsh-tools')
    return mod.defineTool ?? null
  } catch {
    return null
  }
}

function registerGovernanceTools(ctx, config, client, currentHeart, refreshPack, log) {
  const tools = ctx.tools
  if (!tools?.register) {
    log('警告：未找到 tools 注册表（inject: [\'tools\']），治理工具未注册。')
    return
  }
  const textOut = () => ({
    schema: { type: 'string' },
    render: (_args, v) => [{ type: 'text', text: String(v) }],
  })
  const register = (desc) => tools.register(desc)

  register({
    name: 'xinji_view',
    description: '查看心记库全景：关系阶段、边界、承诺、开放话题与全部记忆（含 id）。用户问「你还记得什么」时使用。',
    parameters: {},
    output: textOut(),
    async execute() {
      const view = await client.view(currentHeart())
      return JSON.stringify({
        heart: view.heart,
        stage: view.stage?.value,
        boundaries: view.boundaries?.filter((b) => b.status === 'active')
          .map((b) => ({ topic: b.topic, directive: b.directive })),
        commitments: view.commitments?.filter((c) => c.status === 'open')
          .map((c) => c.what),
        open_topics: view.open_topics?.filter((t) => t.status === 'open')
          .map((t) => t.topic),
        memories: view.memories?.map((m) => ({
          id: m.id, kind: m.kind, topic: m.topic, content: m.content,
        })),
      }, null, 2)
    },
  })

  register({
    name: 'xinji_forget',
    description: '从心记库删除记忆。用户要求忘记/删除某段记忆时使用。kind=memory 精确删除单条记忆；kind=topic 彻底抹去一个话题（连同原话与派生记忆，删除自动重放验收，不会复活）。',
    parameters: {
      kind: { type: 'string', required: true, description: 'memory | topic' },
      target: { type: 'string', required: true, description: '记忆 id 或话题名' },
      reason: { type: 'string', required: false, description: '删除原因' },
    },
    output: textOut(),
    async execute(args) {
      const heart = currentHeart()
      const reason = args.reason || '用户要求忘记'
      let result
      if (args.kind === 'topic') {
        // 彻底抹去：话题下记忆的溯源原话一并删除（否则情景记忆仍会被
        // 检索召回，用户会感到「没忘掉」），再关闭话题。
        const view = await client.view(heart)
        const seqs = new Set()
        for (const m of view.memories ?? []) {
          const hit = m.topic === args.target
            || String(m.topic ?? '').includes(args.target)
            || (m.topic === '' && String(m.content ?? '').includes(args.target))
          if (hit) for (const s of m.provenance ?? []) seqs.add(Number(s))
        }
        if (seqs.size > 0) {
          result = await client.forget('message', [...seqs].sort((a, b) => a - b),
                                       reason, heart)
        }
        const topicResult = await client.forget('topic', [args.target], reason, heart)
        result = {
          event: topicResult.event,
          dropped_memories: [
            ...(result?.dropped_memories ?? []),
            ...(topicResult.dropped_memories ?? []),
          ],
          replay: topicResult.replay,
        }
      } else {
        result = await client.forget(args.kind, [args.target], reason, heart)
      }
      const ok = result.replay?.ok !== false
      await refreshPack(heart, '')
      return `已删除（${result.dropped_memories?.length ?? 0} 条记忆，墓碑事件 seq ${result.event}）。`
        + `重放验收：${ok ? '通过，不会复活。' : '异常！' + JSON.stringify(result.replay)}`
    },
  })

  register({
    name: 'xinji_correct',
    description: '纠正一条记忆的内容（旧内容保留在账本可审计）。用户说「之前记错了，其实是…」时使用。',
    parameters: {
      memory_id: { type: 'string', required: true, description: '记忆 id（用 xinji_view 查）' },
      to: { type: 'string', required: true, description: '纠正后的内容' },
    },
    output: textOut(),
    async execute(args) {
      await client.correct(args.memory_id, args.to, '用户纠正', currentHeart())
      await refreshPack(currentHeart(), '')
      return '已纠正，旧内容留账可审计。'
    },
  })

  register({
    name: 'xinji_replay',
    description: '对心记账本做重放验收：从 L0 原话重建全部记忆并与现场比对。用户想验证「删除的东西真的没回来」时使用。',
    parameters: {},
    output: textOut(),
    async execute() {
      const r = await client.replay(currentHeart())
      return r.replay?.ok
        ? '重放验收通过：账本重建的状态与现场完全一致，没有已删除内容复活。'
        : '重放验收失败：' + JSON.stringify(r.replay?.differences)
    },
  })

  register({
    name: 'xinji_stage',
    description: '设置关系阶段（acquaintance 初识 / building 熟悉 / close 亲近 / deep 深厚）。共同故事等记忆只在关系足够近后才会被引用。',
    parameters: {
      value: { type: 'string', required: true, description: '目标阶段' },
    },
    output: textOut(),
    async execute(args) {
      await client.stage(args.value, currentHeart())
      return `关系阶段已设为 ${args.value}。`
    },
  })
}

// ---------------------------------------------------------------------------
// 插件主体（apply 保持同步：Cordis 约定；异步工作全部 fire-and-forget）
// ---------------------------------------------------------------------------

export function apply(ctx, rawConfig) {
  const config = { ...SCHEMA_DEFAULTS, ...(rawConfig ?? {}) }
  const log = (...a) => console.log('[xinji-memory]', ...a)
  const client = makeClient(config)

  /** 每颗心一个记忆包缓存；section 同步提供者读「当前心」的缓存 */
  const packByHeart = new Map()
  let currentHeartId = config.defaultHeart ?? 'default'

  async function refreshPack(heart, input) {
    try {
      const out = await client.read(input, undefined, heart)
      packByHeart.set(heart, out.pack)
    } catch (err) {
      packByHeart.delete(heart)
      log(`读取记忆包失败（heart=${heart}，先降级，不装记得）：${err.message}`)
    }
  }

  // 健康检查：服务不可达只警告，不阻塞 DSH 启动（先降级，不装记得）
  client.health()
    .then(() => log(`已连接 xinji serve（${config.serverUrl}，账本：${config.dbLabel}，`
      + `heartMode=${config.heartMode}）`))
    .catch((err) => log(`警告：xinji serve 不可达（${config.serverUrl}）：${err.message}。`
      + '将以无记忆模式运行，服务恢复后自动重连。启动方式：python -m xinji serve'
      + (config.heartMode !== 'single' ? ' --hearts-dir <目录>' : '')))

  // 1) 记忆注入：system prompt 的「记忆与分寸」段（同步读取当前心缓存）
  if (config.injectMemories) {
    ctx.systemPrompt?.section?.({
      name: 'xinji-memory',
      order: config.memorySectionOrder,
      required: false,
      text: () => renderPack(packByHeart.get(currentHeartId), config),
    })
  }

  // 2) 自动入账：user/assistant 原话写进对应的心；群聊按隐私策略过滤
  if (config.autoHear) {
    ctx.on('session/event', (...args) => {
      const msg = extractMessage(...args)
      if (!msg) return
      if (msg.synthetic && !config.captureSynthetic) return
      const identity = extractIdentity(...args)
      const heart = resolveHeart(config, identity)
      currentHeartId = heart
      // 群聊隐私策略：仅 @机器人 的消息进入心记（私聊始终入账）
      if (identity.isGroup && config.groupCapture === 'at' && identity.isAt === false) {
        return
      }
      if (msg.role === 'user') {
        client.hear(msg.text, 'user', heart)
          .catch((err) => log(`写入用户原话失败（heart=${heart}）：${err.message}`))
        void refreshPack(heart, msg.text)
      } else {
        client.hear(msg.text, 'assistant', heart)
          .catch((err) => log(`写入助手回复失败（heart=${heart}）：${err.message}`))
      }
    })
  }

  // 3) 治理工具（异步加载 dsh-tools 不阻塞 apply）；工具作用于「当前心」
  if (config.governanceTools) {
    loadDefineTool()
      .catch(() => null)
      .then((defineTool) => {
        try {
          registerGovernanceTools(ctx, config, client,
                                  () => currentHeartId, refreshPack, log)
        } catch (err) {
          log(`治理工具注册失败：${err?.message ?? err}`)
        }
      })
  }

  // 4) 清理（effect 返回的函数在插件卸载时执行）
  return ctx.effect?.(() => {
    log('插件已卸载；xinji serve 不受影响（账本随写随存）。')
  })
}

export default { name, Config, apply }
