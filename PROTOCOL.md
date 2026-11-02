# 心记记忆协议 · V1

本文定义心记 V1 原型落地的记忆协议：账本事件、五层派生关系、读写降级路径，
以及六条设计原则如何变成可机器验证的不变量。实现见 `src/xinji/`，验收见 `tests/`。

> 一句话：上面几层都可以重做，最底下的原话不能改。

## 1 · 账本（L0）：一本可追溯的账

心记库 = 一个 SQLite 文件。`ledger` 表只追加、永不改写，每行是一条事件：

| 字段 | 含义 |
| --- | --- |
| `seq` | 全局递增序号（1 起，无空洞） |
| `id` / `ts` | 事件 id 与 UTC 时间 |
| `type` | 事件类型（见下表） |
| `payload` | 类型化载荷（JSON） |
| `prev_hash` / `hash` | 哈希链：`hash = sha256(prev_hash \| seq \| type \| canonical(payload))` |

哈希链让「账被改过没有」可以被机器验证（`verify_ledger()`），任何对历史行的
篡改都会在校验时暴露。

### 事件类型

| type | 载荷要点 | 说明 |
| --- | --- | --- |
| `message` | `role`（user/assistant）、`text`、`turn` | 原话，唯一原件 |
| `relationship.op` | `op`、`params`、`evidence`（来源 seq）、`reason` | L2 关系变更：`set_stage` / `set_boundary` / `lift_boundary` / `add_commitment` / `fulfill_commitment` / `open_topic` / `close_topic` / `restore`（回滚快照） |
| `memory.correction` | `memory_id`、`old_content`、`new_content`、`reason` | 纠正一条 L1 记忆；旧内容留账可审计 |
| `deletion` | `kind`（message/memory/topic）、`targets`、`reason` | 删除 = 追加墓碑事件，原事件永不改写 |
| `system` | `kind`（disable/enable/degrade/recover/note）、`reason` | 一键停用与降级留痕 |

## 2 · 五层派生关系

所有派生层都存在派生表里，**随时可以清空后从账本重放重建**
（`replay()` / `rebuild()`）：

- **L1 整理后的记忆**：事实（意愿 / 偏好）、情景、共同故事、画像、片段摘要。
  每条带 `provenance`（来源消息 seq）、确定性 `id` / `content_hash`
  （同内容必同 id——这是删除不复活的前提）、`min_stage`、`confidence`。
- **L2 关系档案**：四个分片 `stage` / `boundaries` / `commitments` / `open_topics`。
  每次变更是一条 `relationship.op` 事件，折叠函数是纯函数；`relationship_log`
  留下每次变更的 before/after 与证据，可回滚（回滚本身也是新事件）。
- **L3 开口前的判断**：不落库，是读路径上的一次纯计算（见 §4）。
- **L4 治理**：全部体现为账本事件 + 派生表视图，天然可审计、可重放。

V1 的 L1 整理器是**规则式**的（`organizer.py`，`rules-v1`）：认识「我想…」
（意愿 + 时节触发）、「别催我 / 别问了」（边界）、「我喜欢…」（偏好）、
「我叫…」（画像）、「还记得我们…」（共同故事，需阶段≥熟悉）、
「说好了 / 我保证」（承诺）这几类话语；接口是纯函数
`消息 → (记忆, 关系变更提案)`，未来可无缝替换为 LLM 整理器。

## 3 · 写路径（听见 → 先记下来）

```
hear(text) → message 事件立刻入账，返回确认（seq / turn / pending）
organize() → 后台整理：派生 L1 记忆 + 把边界等提案落成 relationship.op 事件
```

- 整理不阻塞聊天；`organize` 幂等（同一条消息不会重复提案）。
- 片段摘要按**固定 seq 桶**（每 8 条消息一桶）生成：桶的划分只取决于 seq，
  与整理时机无关，因此「现场整理」与「重放重建」永远得到同一批摘要；
  桶内若有已删除消息则整桶跳过。

## 4 · 读路径（想好再说）

```
read(input, now)
  ① 检索：BM25 词袋召回（得分>0 才算证据） + 开放话题的时节主动扫描
  ② L3 判断（先硬后软）：
     阶段门 → 证据门（置信度不足=默认沉默） → 边界门 → 新鲜度门
  ③ 装包：挑 3–5 条，总预算约 8k token；附 guidance（分寸提示）
     与 suppressed（被压下的候选及原因）
```

**边界门**是心记区别于 RAG 的关键：

- 话题命中有效边界、且当前输入**没有**自己聊到该话题 → 主动提及被挡下
  （`boundary-proactive`）。记得，但不把旧愿望变成压力。
- 当前输入自己聊到了该话题（用户主动）→ 允许接上，但记忆包里附上
  「分寸」指令（例如：可以自然接上，但别追问计划进度）。

一条都不该提时返回空包 + 默认沉默说明——**说不，也是正确答案**。

## 5 · 错路径（先降级，不装记得）

触发条件：一键停用（`disable`）、读路径故障（自动）、整理故障（自动）。
降级包只带：当前输入、最近几轮、有效画像摘要、当前硬边界——
**关掉的是可选旧事，聊天照常，账本继续记录**。恢复（`recover` /
`enable`）后补整理积压。

## 6 · 删除：三重登记，验收到位

| 操作 | 落账 | 生效 |
| --- | --- | --- |
| `delete_messages(seqs)` | 墓碑 `kind=message` | 原话事件保留但所有读取路径跳过；溯源与之相交的派生记忆（含摘要）立即撤下 |
| `delete_memory(id)` | 墓碑 `kind=memory`，登记 id + 内容哈希 | 派生记忆撤下；因整理器确定性，补整理重derive 出的同内容记忆同 id，仍被墓碑挡住 |
| `delete_topic(topic)` | 墓碑 `kind=topic` | 话题下记忆撤下，对应开放话题关闭 |

**验收方法**：`replay(verify=True)`——把账本逐字复制到临时库、从零重放出
全部派生状态、与现场逐项比对（记忆 / 关系 / 墓碑 / 模式 / 元信息）。
一致 = 没有任何已删除内容复活、也没有状态凭空出现。
比对前会先把现场积压的整理补齐，因此「补整理不复活」同时被验证。

## 7 · 设计原则 → 不变量 → 测试

| 原则 | 不变量 | 验收测试 |
| --- | --- | --- |
| 边界先于相似度 | 边界命中且非用户主动 → 高分候选也被压下 | `test_judgment.BoundaryBeforeSimilarityTest` |
| 默认沉默 | 无证据 / 低置信 / 全被压下 → 空包 | `DefaultSilenceTest` 等 |
| 原话不可变 | 账本无 UPDATE/DELETE 接口；篡改可检出 | `test_ledger` |
| 关系是版本化的 | 每次变更留底可回滚，回滚也留底 | `RelationshipVersioningTest` |
| 删除可验证 | 删除后补整理 / 重放 / 原地重建均不复活 | `test_forget` |
| 先降级不装记得 | 停用 / 读故障 / 整理故障 → 降级包，聊天照常 | `DegradeTest` |

## 8 · 运行

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .   # 全部验收
PYTHONPATH=src python -m xinji demo                        # README「秋天看海」场景
PYTHONPATH=src python -m xinji chat                        # 交互演示
PYTHONPATH=src python examples/basic_usage.py              # 接入你自己模型的样例
```

更详细的 CLI 用法见 README「快速开始」与 `python -m xinji --help`。

## 9 · 评测协议（bench/）

评测比较**记忆层**把什么交给了下游模型，不比较模型本身：五个策略（心记 +
四个 baseline）接收相同的对话流与治理动作，判分只看各自组装出的上下文，
全程无 LLM、可离线复现。

- **Track B · XinjiBench v2**（`bench/scenarios.py`）：合成关系脚本，种子确定性，
  **现象可组合**——每段对话抽样组合边界、解除（「现在可以聊了」）、意愿变更
  （「改主意了，不去X了」）、承诺兑现（「答应你的X带来了」）、重申（同义派生
  重复记忆）、一句话多事实等真实现象；探针 ground truth 随现象联动（解除后
  时节探针从「不许提」翻转为「应想起」，兑现后约定探针反向）。语料自检保证
  探针话术不自带答案（复活探针除外——判分剔除当前输入）、闲聊不污染事实词。
  指标按现象分拆，用于缺陷定位。
- **Track A · LoCoMo**（`scripts/fetch_locomo.py` 下载，数据不随仓库分发）：
  真实超长对话上测证据召回（QA 类别 1–4）与诱导暴露（类别 5 adversarial，
  正确行为是沉默）。

六项指标与判分口径见 `bench/metrics.py` 模块注释；最新结果（含按现象分拆与
已知边界）见 `bench/BENCH_REPORT.md` 与 `bench/results/`。心记的硬不变量
（越界 0、复活 0、阶段迁移全对、新现象闭环）同时由 `tests/test_bench.py`
在小规模上持续守护。

评测迭代中已修复的缺陷（回归测试覆盖）：边界门补内容匹配、边界门不区分
来源、装包按话题去重、整理器识别解除 / 变更 / 兑现的自然语言。

## 10 · 开放接入规范（xinji-api/v1）

任何宿主通过 `xinji serve`（`src/xinji/server.py`，纯标准库 HTTP）接入心记：

```
POST /api/v1/hear      {text, role?, organize?}        写路径：原话入账（+整理）
POST /api/v1/read      {input, now?, recent_limit?}    读路径：返回完整记忆包
POST /api/v1/organize  补整理          POST /api/v1/replay    重放验收
POST /api/v1/delete    {kind: message|memory|topic, targets, reason?, verify?}
POST /api/v1/correct   {memory_id, to, reason?}
POST /api/v1/stage     {value}         POST /api/v1/boundary  {action: set|lift, ...}
POST /api/v1/disable   {reason?}       POST /api/v1/enable    POST /api/v1/recover
GET  /api/v1/view      五层全景        GET  /api/v1/audit?tail=
GET  /api/v1/export    JSON 导出       GET  /api/v1/verify    哈希链校验
GET  /api/v1/health    健康检查
```

响应均为 JSON；错误返回 `{"error": ...}` 与 4xx/5xx。删除类请求默认附带
`replay` 验收结果。多线程宿主安全：服务内部以全局锁串行化引擎调用。

**DSH（DeepSeek Harness）插件**：`dsh-xinji/`——组合包（`dsh.bundle`），
`session/event` 驱动 hear/refresh，`ctx.systemPrompt.section` 注入记忆包，
`ctx.tools` 注册治理五件套；服务不可达时以无记忆模式静默降级。
详见 `dsh-xinji/README.md`。

**多心（multi-heart）**：多人宿主（QQ 群聊等）按 heart id 路由——
`--hearts-dir` 模式下，请求头 `X-Xinji-Heart: <id>` 把不同用户 / 会话路由到
独立的心（一段关系一颗心）。heart id 白名单校验（1–64 位字母数字）是路径
穿越的唯一防线；LRU 池限制同时打开的心数；不同心之间读取绝不互通
（安全验收：`tests/test_multi_heart.py`）。QQ 部署双链路与隐私模型见
`dsh-xinji/QQ_DEPLOY.md`。
