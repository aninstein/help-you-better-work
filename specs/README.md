# Duet AI Demo：交互契约与当前完成状态

> 已新增第一版实时通话：[使用与实现说明](../docs/realtime-calls.md)、[OpenSpec 设计](../openspec/changes/add-realtime-call-mode/proposal.md)。本文以下仍描述保留的审核聊天；通话使用持续 PCM、普通问题自动回答、Hinter 即时干预和 Wiki 输出校验。

## 定位

`frontend/src/` 已接入 FastAPI 与 LangChain Agent，支持真实问题识别、草稿审核、英文文本及语音回答、两个固定用户的可编辑 Wiki 和新建 Session。当前 Demo 使用 SQLite、用户 ID 目录和 Prompt 驱动的 Markdown Wiki；当前实现完成状态详见 [实现状态](001-dual-channel-voice-agent/implementation-status.md)，部署及技术边界详见 [技术实现文档](../docs/technical-implementation.md)。

## 核心流程

> Talker 连续发送语音或文字 → final transcript 保留原音频 → AI Intake 拆分/去重问题 → 问题队列 → Hinter 请求英文预回答 → 草稿仅 Hinter 可见，可编辑或 Hint 润色 → Hinter 确认发送 → Talker 收到答案 → 英文 TTS 自动播放。

### 角色契约

- **Talker**（`talker-001`）：可提交语音/文字；查看原始输入、final transcript、公开问题状态、已确认答案和自己的 Wiki；不可见草稿、hint、reviewer、审核备注、内部 Prompt、Tool trace、Hinter Wiki。
- **Hinter**（`hinter-001`）：查看完整问题队列；可手动请求预回答、打开 AI 模式自动拟答、编辑/润色草稿、确认/取消发送或拒绝回答；查看并编辑自己的 Wiki。
- 进入房间后角色固定，切换角色必须退出；真实系统由后端身份声明决定角色，不能信任查询参数。

## 页面与交互

- 顶部状态区：语音通道、Intake、Review Gate、连接/恢复状态，Hinter 可切换 AI 自动预回答模式。
- 中央时间线：SourceMessage、AI Intake 摘要、Hinter 私有 hint、Hinter 私有预回答、已确认 Answer。
- Talker 底栏：语音/文字输入；语音保留可暂停、重播的原始音频。
- Hinter 底栏：所选问题摘要、可选 hint、生成草稿/提示后生成草稿、批量拒绝。
- 右侧问题列表：稳定问题编号、重复计数、每秒等待时钟；等待超过 3 分钟为橙色，超过 6 分钟为红色，未处理问题按等待时间优先展示。
- 右侧 Wiki：每个固定用户独立的 `context.md`、轮次记录和主题文档；Hinter 可二次确认清空全部 Wiki。

## 音频契约

- STT 只有 `final` transcript 才触发 Intake；interim 仅用于录音反馈。
- 原音频与 SourceMessage 绑定保留，可暂停、继续和重播。
- 最终 Answer 始终为英文；Hinter 草稿确认前不合成音频。确认后后端自动发起英文 TTS，前端收到 `audio.ready` 后在 Talker 和 Hinter 页面尝试自动播放。
- 浏览器阻止自动播放时展示“点击播放”状态，不把答案标记失败；支持暂停、继续、从头重播。

## 实时与恢复契约

- UI 经 WebSocket 接收有序事件；SSE 是受限网络环境的降级通道。
- 每个事件包含 `event_id`、`session_id`、`sequence`、`occurred_at`；断线后用最后游标恢复。
- 发现 sequence gap 时停止本地推断并拉取会话快照，再恢复订阅。
- 重复事件必须幂等；提交审核动作携带 `Idempotency-Key`。

## 原型演示入口

- `?role=talker`
- `?role=hinter`
- `?role=hinter&panel=memory`

当前页面使用后端真实状态，Talker 私有字段隔离和审核由后端实现。原始生产级需求见 [一期需求规格](001-dual-channel-voice-agent/spec.md)；Demo 的实际边界、简化项和运行时接口以 [技术实现文档](../docs/technical-implementation.md) 与 `/docs` 为准。
