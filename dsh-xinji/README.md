# dsh-xinji-memory

**心记 Xinji × DeepSeek Harness** —— 面向长期陪伴的关系记忆插件。
记得住，说得对，忘得掉。芯生命，用心记。

心记不是「把聊天记录塞进 prompt」：它夹在模型与存储之间，把记忆组织成
关系状态（阶段 / 边界 / 承诺 / 开放话题），在每轮开口前判断**该不该说**——
边界先于相似度，默认沉默也是正确答案；删除真正生效（重放验收，不复活）。

## 工作方式

```
DSH agent loop ──(session/event)──▶ 本插件 ──(HTTP)──▶ xinji serve（Python，零依赖）
     ▲                                  │
     └──(ctx.systemPrompt.section)◀─────┘   记忆包缓存（每轮随用户消息刷新）
```

- **听见**：`user/message` / `assistant/message` 事件原话写入不可变账本（L0），
  整理出事实、边界、承诺与共同故事，不阻塞聊天；
- **掂量**：每轮拿到记忆包——该提的 3–5 条（约 8k token 预算）+ 分寸指引 +
  有效约定；被边界覆盖的旧事默认**不注入**（只注入「分寸」指令）；
- **治理**：五个 agent 工具——`xinji_view`（查全景）、`xinji_forget`（删除，
  话题级删除连原话一起抹去并自动重放验收）、`xinji_correct`（纠正，旧内容
  留账可审计）、`xinji_replay`（验证删除不复活）、`xinji_stage`（关系阶段）。
- **降级**：xinji serve 不可达时以无记忆模式静默运行（先降级，不装记得），
  恢复后自动重连。

## 安装

### 1. 启动心记服务（Python 3.10+，零第三方依赖）

```bash
git clone https://github.com/NianLog/Xinji.git
cd Xinji
python -m xinji serve --db companion.db        # 默认 127.0.0.1:8642
```

### 2. 安装插件组合包

```bash
dsh plugin --profile <name> add ./dsh-xinji     # 本地路径安装
# 或发布后：
dsh plugin --profile <name> add dsh-xinji-memory
```

组合包自带 `cordis.patch.yml`，会以如下默认配置插入：

```yaml
config:
  serverUrl: 'http://127.0.0.1:8642'
  injectMemories: true
  governanceTools: true
  autoHear: true
```

### 3. 也可以本地调试挂载

```bash
dsh web --patch ./dsh-xinji/cordis.patch.yml
```

## 配置

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `serverUrl` | `http://127.0.0.1:8642` | xinji serve 地址 |
| `injectMemories` | `true` | 把记忆包注入 system prompt |
| `governanceTools` | `true` | 注册 5 个治理工具 |
| `autoHear` | `true` | 自动写入 user/assistant 原话 |
| `captureSynthetic` | `false` | agent 合成注入（文件通知、cron 等）也入账 |
| `memorySectionOrder` | `900` | 记忆段排序（越大越靠后） |
| `maxItemChars` | `160` | 注入文本单条记忆最大字符数 |
| `requestTimeoutMs` | `4000` | HTTP 超时（毫秒） |
| `dbLabel` | `companion.db` | 账本文件标签（日志用） |

## 对话效果（README「秋天看海」场景）

> 她（春天）：我想在秋天去看海。
> 她：到时候别催我订计划，我不想被追问。
>
> 她（秋天）：最近降温了，有点想喝热奶茶。
> ← 心记注入：「本轮不主动提及任何旧记忆（默认沉默）」——它记得海，但不说。
>
> 她（秋天）：对了，我决定十月去看海！
> ← 心记注入：「想在秋天去看海」+ 分寸「别催：不要主动询问进度」——她主动提了，自然接上。
>
> 她：把看海的事忘了吧。
> ← agent 调用 `xinji_forget(kind=topic, target=看海)`：原话与派生记忆一起抹去，
>   重放验收通过，不会复活。

## 许可

MIT（随 [Xinji 主仓库](https://github.com/NianLog/Xinji)）。

## 接入 QQ（「大肥鱼」）

本插件是记忆层，与传输层解耦：腾讯官方 [`dsh-qqbot`](https://github.com/tencent-connect/dsh-qqbot)
或社区 NapCat/OneBot 链路（[`dsh-qq-bot`](https://github.com/arrow1031/dsh-qq-bot)）
都可以直接叠加。QQ 多人场景务必用多心模式（`heartMode: 'per-user'` +
`serve --hearts-dir`）——一段关系一颗心，绝不串线。完整步骤、隐私模型与
运维手册见 [QQ_DEPLOY.md](QQ_DEPLOY.md)。
