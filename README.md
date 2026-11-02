<div align="center">
  <img src="assets/brand-hero.svg" alt="心记 Xinji：给硅基生命一颗会记的心" width="760">
</div>

<br>

<p align="center">
  <strong>一套面向长期陪伴的关系记忆系统。</strong><br>
  <sub>RAG 找得到；OAG 连得清；心记还要判断该不该说、删了以后能不能真的忘掉。</sub>
</p>

<br>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/NianLog/Xinji?style=flat-square&amp;color=2e6b64" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/status-v1.0%20%C2%B7%20runnable-2e6b64?style=flat-square" alt="Status: v1.0 runnable">
  <img src="https://img.shields.io/badge/python-3.10%2B%20%C2%B7%20%E9%9B%B6%E4%BE%9D%E8%B5%96-1f2d2b?style=flat-square" alt="Python 3.10+, zero dependencies">
  <img src="https://img.shields.io/badge/tests-58%20passed-2e6b64?style=flat-square" alt="58 tests passed">
  <img src="https://img.shields.io/badge/DSH%20plugin-ready-cf6b4f?style=flat-square" alt="DSH plugin ready">
  <a href="https://github.com/NianLog/Xinji/stargazers"><img src="https://img.shields.io/github/stars/NianLog/Xinji?style=flat-square&amp;color=b99a62" alt="GitHub stars"></a>
</p>

<br>

<p align="center">
  <a href="#为什么做心记">为什么</a> ·
  <a href="#它怎么工作">怎么工作</a> ·
  <a href="#记忆分五层">记忆分层</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#接入你的产品">接入</a> ·
  <a href="#接入-qq大肥鱼">QQ 大肥鱼</a> ·
  <a href="#评测这真的有用吗">评测</a> ·
  <a href="#开发与协作">协作</a>
</p>

---

## 为什么做心记

上下文窗口越来越长，存储越来越便宜，但长期陪伴仍然卡在同一个地方：**记得多，不等于记得好。**

真正的长期关系，不是把所有旧事摊在桌上。它至少需要三种能力：

- **记得住**：几个月前的约定、偏好和共同故事，下次还能自然接上。
- **说得对**：知道什么该提、什么不该提，边界比“相关”更重要。
- **忘得掉**：删除就是删除，不能因为补整理或重放历史又偷偷回来。

心记就为这三件事而生。它不是新的模型，也不是一段更长的系统提示词，而是夹在模型与存储之间的一层**关系记忆系统**：让记忆可组织、可判断、可纠正、可回滚，也真的可删除。

它适合 AI 伴侣、长期陪伴型助手、QQ / 微信等社交平台上的陪伴 bot，以及任何需要跨会话关系连续性的产品。

> **芯生命，用心记。** “心”是关系与分寸，“记”是一本可追溯的账。给硅基生命一颗会记的心，不是让它背诵一切，而是让它知道何时开口、何时安静。

## 三个承诺

<table>
  <tr>
    <td align="center" width="33%">
      <img src="assets/seal-remember.svg" width="58" alt="记得住"><br>
      <strong>记得住</strong><br>
      <sub>跨会话保存约定、偏好与共同故事，不依赖模型自己想起来。</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/seal-speak.svg" width="58" alt="说得对"><br>
      <strong>说得对</strong><br>
      <sub>边界、关系阶段与承诺优先于相似度；不该提的时候就沉默。</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/seal-forget.svg" width="58" alt="忘得掉"><br>
      <strong>忘得掉</strong><br>
      <sub>删除真正生效；补整理、重放历史，都不能让旧内容复活。</sub>
    </td>
  </tr>
</table>

这三个承诺不是口号，每一条都有机器验收：58 项测试、1000 段合成对话的多轮压测、真实长对话数据集上的对照评测，见[评测](#评测这真的有用吗)。

## 它怎么工作

<p align="center">
  <img src="assets/how-it-works.svg" alt="心记工作流程：听见、记下、整理、掂量，最后选择自然提起或保持沉默" width="100%">
</p>

一次对话只有四个动作：**听见 → 记下 → 整理 → 掂量**。最后不一定开口；说不，也是正确答案。

| 动作 | 发生了什么 |
| --- | --- |
| **写：先记下来** | 原话先进入不可变账本 L0，马上得到确认；摘要、情景、事实和画像在后台慢慢整理，不阻塞聊天。 |
| **读：想好再说** | 先翻相关记忆，再对照关系阶段、硬边界和承诺，判断该不该提。可以提，就挑 3–5 条装进约 8k token 的预算。 |
| **错：先降级** | 故障或一键停用时，关掉可选旧事，只带当前输入、最近几轮、有效画像摘要和当前硬边界，聊天照常。 |

> 默认沉默，是心记最重要的产品决定：宁可少说一句，也不把旧记忆变成冒犯或压力。

### 一个场景

> 她说过，想在秋天去看海。也说过，那阵子不想被追问计划。

到了秋天，普通记忆系统可能一检测到“海”就主动追问：“看海计划怎么样了？” 心记会先看边界：**“别催我”仍然有效。** 于是它记得海，却不把旧愿望变成压力。等她主动提起时，它才自然接上。

这就是“记得住”和“说得对”的区别。记忆本身不是答案，**在正确的时候保持安静，才是。** 这个场景可以一条命令跑出来：`python -m xinji demo`。

## 记忆分五层

<p align="center">
  <img src="assets/memory-layers.svg" alt="心记记忆分层：L0 原始记录、L1 整理后的记忆、L2 关系档案、L3 开口前的判断，以及横切所有层的 L4 治理" width="100%">
</p>

- **L0 · 原始记录**：唯一原件。用户的话、助手的话、纠正与删除操作，只追加，不改写；每条事件带哈希链，篡改可检出。
- **L1 · 整理后的记忆**：会话摘要、情景、事实、画像与共同故事，都从原件派生；记忆 id 由内容确定性生成，这是「删除不复活」的前提。
- **L2 · 关系档案**：阶段、边界、承诺和没聊完的话题；只保留现在有效的版本，每次改动留底，可回滚。
- **L3 · 开口前的判断**：阶段门 → 证据门 → 边界门 → 新鲜度门；同步读路径上的最后一道门。
- **L4 · 治理与观测**：查看、纠正、删除、导出、重放、审计、一键停用；横切所有层，不属于处理链。

**一句话：上面几层都可以重做，最底下的原话不能改。** 协议细节见 [PROTOCOL.md](PROTOCOL.md)。

### RAG 与 OAG

RAG 解决“找得到”，OAG（Ontology-Augmented Generation）解决“连得清”。心记不替代它们，而是在上面补上关系记忆里最难的一层：**该不该说，以及删了以后能不能真的忘掉。**

| 路线 | 核心问题 | 它擅长 | 心记补上什么 |
| --- | --- | --- | --- |
| RAG | 哪些片段和当前问题相关？ | 语义召回、向量 / 关键词检索 | 提及判断、关系状态、可验证的删除 |
| OAG | 实体和关系怎么连起来？ | 本体、实体关系、结构化推理 | 同上，并让记忆可版本化、可回滚 |
| 心记 | 这段关系此刻该怎么回应？ | 组织记忆与关系，再决定说或不说 | 把“记忆”变成可治理的关系状态 |

### 设计原则

| 原则 | 落地方式 |
| --- | --- |
| 边界先于相似度 | 硬约束先过滤，再做相关性与排序；被相似度召回也算「主动」，她没提起就不出口。 |
| 默认沉默 | 没有足够证据就不提；说不也是正确结果。 |
| 原话不可变 | L0 只追加；L1–L3 都可以从原件重建。 |
| 关系是版本化的 | 不用一个分数代表关系；每次变化都带证据，可回滚。 |
| 删除可验证 | 删除后重放历史，已删除内容不得复活。 |
| 先降级，不装记得 | 出错时关掉可选旧事，聊天继续，恢复后再补整理。 |

## 快速开始

Python 3.10+，**零第三方依赖**（仅标准库）：SQLite 追加式账本 + 哈希链，检索与判断完全离线、可复现。

```bash
git clone https://github.com/NianLog/Xinji.git
cd Xinji

PYTHONPATH=src python -m xinji demo                          # 跑「秋天看海」场景：听见、记下、整理、掂量、删除、停用
PYTHONPATH=src python -m xinji chat                          # 和她聊聊（离线模板回复，演示记忆包如何组装）
PYTHONPATH=src python -m unittest discover -s tests -t .     # 58 项验收测试
```

也可以 `pip install -e .` 后直接用 `xinji demo` / `xinji serve ...`。

用代码接入你的模型只要三步——`hear()` 先记下来、`read()` 拿到记忆包（哪些能提、怎么提、什么被压下及原因）、回复再入账：

```python
from xinji import XinjiEngine

eng = XinjiEngine("companion.db")
eng.hear("我想在秋天去看海。")          # 原话立即进 L0，不阻塞
eng.organize()                          # 后台慢慢整理出事实与开放话题
pack = eng.read("最近降温了。")          # 边界先于相似度 → 默认沉默
pack = eng.read("我决定十月去看海！")     # 她主动提起 → 允许，并附「分寸」提示
eng.delete_memory(pack["memories"][0]["id"])   # 删除
eng.replay()                            # 重放验收：没有复活
```

治理操作都有命令行入口：`view / audit / export / replay / verify / delete / correct / stage / boundary / topic / commitment / rollback / disable / enable / serve`（`python -m xinji --help`）。完整接入样例见 [examples/basic_usage.py](examples/basic_usage.py)。

## 接入你的产品

心记以**开放接入规范 `xinji-api/v1`** 对外服务——纯标准库 HTTP JSON API，任何语言、任何宿主都能接：

```bash
python -m xinji serve --db companion.db            # 单心：一个账本、一段关系
python -m xinji serve --hearts-dir ./hearts        # 多心：请求头 X-Xinji-Heart 路由到不同的心（多人场景必须）
```

| 端点 | 作用 |
| --- | --- |
| `POST /api/v1/hear` · `POST /api/v1/read` | 写路径入账 / 读路径拿记忆包 |
| `POST /api/v1/delete` · `/correct` · `/replay` | 删除（自带重放验收）、纠正、重放 |
| `POST /api/v1/stage` · `/boundary` · `/disable` · `/enable` | 关系阶段、边界、一键停用 / 恢复 |
| `GET /api/v1/view` · `/audit` · `/export` · `/verify` · `/health` · `/hearts` | 全景、审计、导出、哈希链校验、健康、心列表 |
| `POST /api/v1/backup` | 导出一颗心的 JSON 备份 |

**一段关系一颗心**：多心模式下每个用户 / 会话一个独立账本，不同心的读取绝不互通；heart id 白名单校验是路径穿越的唯一防线，LRU 池让上千用户长期运行不耗尽句柄。

### DeepSeek Harness（DSH）插件

[dsh-xinji](dsh-xinji/README.md) 是面向 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的官方插件（零构建 ESM 组合包，`dsh plugin add ./dsh-xinji` 即装）：

- `session/event` 自动把 user / assistant 原话入账（合成注入默认不记）；
- 每轮记忆包经 `ctx.systemPrompt.section` 注入——被边界覆盖的旧事**只注入「分寸」指令，不注入内容**；
- 五个治理工具 `xinji_view / xinji_forget / xinji_correct / xinji_replay / xinji_stage`——她说「忘了吧」就删，话题级删除连原话一起抹去并重放验收；
- 服务不可达时以无记忆模式静默运行（先降级，不装记得），恢复自动重连。

## 接入 QQ（「大肥鱼」）

把心记装进 QQ 陪伴 bot（社区俗称“大肥鱼”）：**QQ 只是传输层，记忆在 DSH 记忆层就位**，换协议端不用动任何记忆数据。

```
QQ 客户端 ⇄ [协议端] ⇄ [DSH agent loop] ⇄ [dsh-xinji] ⇄ [xinji serve --hearts-dir] ⇄ 每段关系一颗心
```

两条成熟链路任选：腾讯官方 [dsh-qqbot](https://github.com/tencent-connect/dsh-qqbot)（QQ 开放平台，最稳合规）或社区 NapCat / OneBot 链路（[dsh-qq-bot](https://github.com/arrow1031/dsh-qq-bot)，能力最全）。心记层的隐私模型：

| 场景 | 入账 | 记忆归属 |
| --- | --- | --- |
| 私聊 | 全部 | `u-<QQ号>`，每人一颗心 |
| 群聊 @机器人 | 该条消息 | `g-<群号>`，每群一颗共同记忆 |
| 群聊未 @ | **不入账** | — |

多心隔离由服务端保证并有专门的安全验收（两颗心互不串线、A 无法删除 B 的记忆、`../evil` 一类 id 一律拒绝）。完整部署步骤、运维手册（WAL 崩溃自恢复、备份 / 恢复、监控、一键停用）与安全清单见 [dsh-xinji/QQ_DEPLOY.md](dsh-xinji/QQ_DEPLOY.md)。

## 评测：这真的有用吗？

心记不打算只靠“聊起来像不像”证明自己。`bench/` 内置双轨评测，与 README 承诺的四个 baseline 对照：全量长上下文、滑动窗口、摘要压缩、OAG 式结构化记忆（与心记共用同一抽取器，公平比较）。指标：事实召回、提及准确率、越界率、删除后复活率、关系阶段迁移正确率、成本 / 延迟。

**Track B · XinjiBench**（合成，种子确定性可复现，现象可组合：边界、解除、意愿变更、承诺兑现、重申、一句话多事实、删除复活、阶段迁移）。1000 段跨月关系脚本 ≈ 32,000 条消息 / 7,000 个探针：

| 策略 | 事实召回↑ | 越界率↓ | 复活率↓ | 阶段正确↑ | 平均 token |
| --- | --- | --- | --- | --- | --- |
| 全量长上下文 | 100% | 100% | 100% | — | 346 |
| 滑动窗口 | 58.5% | 17.5% | 0% | — | 136 |
| 摘要压缩 | 100% | 100% | 100% | — | 455 |
| OAG 式结构化 | 69.0% | 92.8% | 100% | — | 62 |
| **心记** | **100%** | **0%** | **0%** | **100%** | 178 |

另有 **5 轮多种子 soak 连续压测**（5,000 段对话、约 35,000 个探针），心记全部硬不变量零违例（`python -m bench soak`）。

**Track A · LoCoMo**（真实数据：10 段超长对话、1,977 个带证据 QA，[CC BY-NC 4.0](https://github.com/snap-research/LoCoMo)，仅研究用途，数据不随仓库分发）：心记以**平均 32 token** 的上下文拿到 **45.7%** 证据召回——全量基线用 5,377 token 才到 100%，摘要压缩花 3,475 token 只有 18.6%。

> 口径说明：评测比较的是**记忆层**把什么交给了下游模型，无 LLM 参与；「越界」测的是上下文层面的暴露风险。评测过程中揪出并修复了多处真实缺陷（边界门内容匹配、来源不区分、装包去重、整理器识别解除 / 变更 / 兑现），全部有回归测试守护。

```bash
PYTHONPATH=src python -m bench run --synthetic 1000                     # 合成基准
PYTHONPATH=src python -m bench soak --rounds 5 --conversations 1000     # 多轮压测
python scripts/fetch_locomo.py                                          # 下载真实数据（CC BY-NC）
PYTHONPATH=src python -m bench run --locomo bench/data/locomo10.json   # 真实数据
```

完整报告（含按现象分拆的缺陷定位与已知边界）：[bench/BENCH_REPORT.md](bench/BENCH_REPORT.md)。

## 路线图

- [x] 产品定位、边界与核心场景
- [x] 五层记忆架构、读写与降级路径
- [x] 公开的品牌资产与概念图示
- [x] 可运行原型：L0 账本、写入 / 读取路径（Python，零依赖）
- [x] 四个 baseline 的对照评测（XinjiBench 合成基准 + LoCoMo 真实数据 + soak 压测）
- [x] 开放接入规范（`xinji-api/v1`）与 DeepSeek Harness 插件
- [x] QQ「大肥鱼」接入：多心隔离、群聊隐私策略、部署与运维手册
- [ ] LLM 整理器（替换规则式整理器，接口已预留）
- [ ] 记忆时效标注（意愿变更的 supersede 链）
- [ ] 治理台 Web UI

## 开发与协作

公开仓库包含产品说明、视觉资产与 V1 实现；内部设计资料（PRD、设计评审与技术探索记录）不随开源发布，已在 `.gitignore` 中排除。

### 仓库结构

```text
.
├── README.md               # 本文档
├── PROTOCOL.md             # 记忆协议：事件、层间派生、删除语义、不变量、接入规范
├── pyproject.toml          # 零依赖 Python 包（3.10+）
├── src/xinji/              # 实现：L0–L4
│   ├── engine.py           #   引擎：写 / 读 / 降级三路径与治理操作
│   ├── store.py            #   L0 存储：SQLite 追加式账本 + 哈希链
│   ├── organizer.py        #   L1 整理器（规则式，可替换为 LLM）
│   ├── relationship.py     #   L2 关系档案：版本化、可回滚
│   ├── retrieval.py        #   BM25 召回 + 开放话题的时节触发
│   ├── judgment.py         #   L3 开口前的判断：四道门 + 装包
│   ├── server.py           #   开放接入服务（xinji-api/v1，单心 / 多心）
│   ├── events.py / tokens.py
│   └── cli.py / demo.py    #   命令行与「秋天看海」场景
├── dsh-xinji/              # DeepSeek Harness 插件（零构建 ESM 组合包）
│   ├── index.js            #   入账 / 注入 / 多心路由 / 五个治理工具
│   ├── cordis.patch.yml    #   插入层
│   ├── README.md           #   安装与配置
│   └── QQ_DEPLOY.md        #   QQ「大肥鱼」部署、隐私模型、运维手册
├── tests/                  # 58 项 Python 验收 + Node 端到端联调
│   └── dsh_plugin_stub_test.mjs  # 多心 QQ 场景联调（两用户 + 一群，真实服务）
├── bench/                  # 双轨评测：XinjiBench 合成基准 + LoCoMo 真实数据 + soak
│   ├── scenarios.py        #   合成关系脚本生成器（种子确定性，带 ground truth）
│   ├── baselines.py        #   四个 baseline 与心记的统一策略接口
│   ├── metrics.py / runner.py
│   ├── BENCH_REPORT.md     #   最新评测报告
│   └── results/            #   聚合结果 JSON（可复现）
├── scripts/fetch_locomo.py # LoCoMo 下载脚本（CC BY-NC，数据不入库）
├── examples/basic_usage.py # 把心记接到你自己的模型上
└── assets/                 # 品牌与概念图示（SVG）
```

### 运行验收

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .     # Python：58 项
node tests/dsh_plugin_stub_test.mjs                          # Node：DSH 插件多心联调（需 Node 20+）
```

### 参与贡献

- 有想法或发现问题，先开 Issue；涉及记忆语义、删除与隐私的改动，请先讨论验收标准。
- 分支使用 `feat/…` 或 `fix/…`，提交信息建议使用 Conventional Commits。
- 改动引擎请保证 `bench soak` 的硬不变量（召回 100%、越界 0、复活 0、阶段全对）仍成立。
- 修改图示时请附前后截图，并确认在 1200px 宽度下文字不溢出、SVG 不引用外部资源。
- 视觉规范：墨色 `#1F2D2B`、青色 `#2E6B64`、珊瑚色 `#CF6B4F`、砂色 `#F3EADB`、纸色 `#FBF8F1`；字体使用系统字体栈。

## License

本项目以 [MIT License](LICENSE) 开源。
Copyright © 2026 HouKai.

你可以自由使用、修改和分发，请保留版权与许可声明。评测用到的 LoCoMo 数据集为 CC BY-NC 4.0，仅供研究，不随本仓库分发。

<div align="center">
  <sub><strong>心记 · Xinji</strong> — 记得住，说得对，忘得掉。</sub><br>
  <sub>芯生命，用心记。</sub>
</div>
