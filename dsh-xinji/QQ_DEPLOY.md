# QQ「大肥鱼」部署指南 —— 心记 × DeepSeek Harness × QQ

把心记接入 QQ，打造一条**安全可靠稳定持久**的 AI 陪伴 bot（社区俗称"大肥鱼"）。
本文给出两条成熟链路的部署步骤、隐私模型与运维手册。

## 架构：QQ 只是传输层，记忆在 DSH 记忆层

```
QQ 客户端 ⇄ [协议端] ⇄ [DSH agent loop] ⇄ [dsh-xinji 插件] ⇄ [xinji serve] ⇄ 心记账本
             传输层        大脑（模型+工具）      记忆层（关系状态）      SQLite/JSONL
```

- **协议端**（二选一，见下文链路 A / B）只负责收发 QQ 消息；
- **DSH** 是 agent 运行时（模型调用、工具执行、会话管理）；
- **心记**是记忆层：原话入不可变账本、整理出关系状态、开口前判断该不该说、
  删除可验证——**与传输层解耦**，换协议端不用动任何记忆数据。

## 链路 A · 腾讯官方 QQ Bot（推荐，最省心）

腾讯官方 [`dsh-qqbot`](https://github.com/tencent-connect/dsh-qqbot) 插件，
无需命令行，QQ Bot 平台沙箱内运行：

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 AppID / Token；
2. DSH 里安装：`dsh plugin add @tencent-connect/dsh-qqbot@latest`，扫码绑定；
3. 按本文「心记层」一节安装 dsh-xinji-memory 并配置 heartMode；
4. 群聊 @机器人 触发，单聊直接对话。

> 官方链路受 QQ Bot 平台能力限制（消息频率、主动消息规则），但最稳定合规。

## 链路 B · NapCat / OneBot（社区玩法，能力最全）

社区方案 [`dsh-qq-bot`](https://github.com/arrow1031/dsh-qq-bot) 用
[NapCat](https://github.com/NapNeko/NapCatQQ) 这类 OneBot 协议端驱动普通 QQ 号：

1. 部署 NapCat（登录 bot QQ 号），开启 OneBot v11 WebSocket 服务端；
2. DSH 安装社区 QQ 适配插件并连接 NapCat；
3. **注意**：DSH 当大脑时，协议端不要再接 AstrBot/NoneBot 等（两边都会抢答）；
4. 安装 dsh-xinji-memory（见下）。

> 社区链路能力最全（主动消息、表情回应等），但用个人号有风控风险——
> 建议专用小号，遵守平台规则。

## 心记层（本插件）

```bash
# 1. 启动心记服务（多心模式：QQ 场景必须，一段关系一颗心）
python -m xinji serve --hearts-dir ./hearts --port 8642

# 2. DSH 安装插件
dsh plugin add ./dsh-xinji
```

插件配置（cordis.yml / 组合包默认值）：

```yaml
config:
  serverUrl: 'http://127.0.0.1:8642'
  heartMode: 'per-user'        # 私聊：每人一颗心（u-<QQ号>）
  groupHeartMode: 'per-session' # 群聊：每群一颗心（g-<群号>，群内共同记忆）
  groupCapture: 'at'            # 群聊仅 @机器人 的消息入账（隐私优先）
  injectMemories: true
  governanceTools: true
  autoHear: true
```

### 隐私模型（务必向群成员说明）

| 场景 | 入账 | 记忆归属 | 注入 |
| --- | --- | --- | --- |
| 私聊 | 全部 | `u-<QQ号>`（私人关系） | 只注入到该用户的对话 |
| 群聊 @ | 该条消息 | `g-<群号>`（群内共同记忆） | 只注入到该群的对话 |
| 群聊未 @ | **不入账** | — | — |
| 合成注入（文件通知/cron） | 默认不入账（`captureSynthetic: false`） | — | — |

多心隔离由服务端保证：不同 heart 的 read 绝不返回彼此的记忆
（有专门的安全验收测试 `tests/test_multi_heart.py`）。

## 运维手册（稳定持久）

- **持久化**：账本是 SQLite（WAL 模式）+ 追加式事件，进程崩溃后重启即恢复；
  派生层（记忆/关系档案）可随时从账本重建（`xinji replay` 验证）。
- **备份**：`POST /api/v1/backup`（body `{"heart": "u-111"}`）导出 JSON 到
  `hearts/backups/`；建议 cron 每日全量 `GET /api/v1/export?full=1` 留存。
- **恢复**：备份 JSON 含完整账本（full 导出含哈希链），新库上重放即恢复；
  `GET /api/v1/verify` 校验账本未被篡改。
- **监控**：`GET /api/v1/health`（进程存活）、`GET /api/v1/hearts`（有多少颗心）。
- **资源**：多心模式内置 LRU 池（默认同时打开 64 颗心），上千用户不会耗尽句柄。
- **常驻**：Linux 用 systemd（`Restart=always`）；Windows 用 任务计划程序/NSSM。
  心记服务与 DSH 分开两个进程，各自独立重启互不影响。
- **一键停用**：`POST /api/v1/disable`——读路径立刻降级（不注入旧记忆），
  聊天照常，账本继续记录；`/enable` 恢复。用户要求「闭嘴反省」时可用。

## 安全清单

- [ ] `serve` 只监听 `127.0.0.1`（不要暴露公网——API 无鉴权，靠本机隔离）；
- [ ] 心 id 白名单校验已内置（路径穿越防护），不要绕过它自建路由；
- [ ] 群聊务必保持 `groupCapture: 'at'`，并向群成员公告记忆策略；
- [ ] 定期备份 + 抽查 `xinji replay`（删除不复活的持续验收）；
- [ ] 治理工具只操作「当前对话的心」——A 无法删除 B 的记忆（联调测试覆盖）。

## 常见问题

- **机器人抢答 / 双回复**：DSH 当大脑时，协议端不要再接其他 bot 框架。
- **记忆串了**：检查 `heartMode`——QQ 多人场景必须 `per-user`/`per-session`
  且 serve 用 `--hearts-dir`；单心模式下所有人的对话混在一颗心。
- **想重置某段关系**：直接删除 `hearts/<heart-id>.db`（先 backup），
  或对那颗心逐条 forget（保留可审计的墓碑）。
- **消息字段识别不到 sender**：插件用防御式提取（user_id/senderId/uid/qq…）；
  若协议端字段不同，发 Issue 提供一条样例 payload 即可。
