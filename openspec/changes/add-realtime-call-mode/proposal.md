## Why

题面要求英文语音持续以流输入、中文文字随时提供私有提示、AI 低延迟用英文语音回答；现有实现实际是“停录上传 → 问题队列 → 草稿逐条人工确认 → 整段语音”，与持续通话存在行为差距。需要在保留现有审核聊天能力的基础上，增加电话客服式实时通话，并让 Hinter 从逐题批准转为例外监管和随时干预。

## What Changes

- 新增 `realtime_call`，保留默认 `review_chat`，支持接通、持续收音、自动分轮、打断、静音、挂断与恢复。
- 普通问题依据通话上下文、Wiki 与模型能力自动短答；Intake 持续拆分问题、保留来源，但不再逐题阻断所有回答。
- 新增版本化策略路由：`auto / notify / hold / deny`。人生意义等开放新领域默认 `hold`；实际数据/ID 查询必须 `hold`；危险违法协助默认 `deny` 并提醒 Hinter。命中多个策略取最严格动作。
- Hinter 页面滚动呈现正在处理及已回答的问题、实际播出内容、策略原因与待办；普通内容无需盯盘，重要命中聚合提醒。
- Hinter 可随时用中文“补充 / 立即纠正 / 别回答 / 暂停自动回答”，不要求选中某条问题才能输入。输入先持久化为 LLM Wiki 核心约束，再参与回答和输出校验。
- 每条实质答复先生成简短完整英文候选，按最新有效约束做最终校验，再分段 TTS 和流式播放；播放途中约束变更立即停止旧输出。
- 先测量并缩短当前 session 的软件等待，再引入真正持续音频链路，定义首音、打断、提示生效与恢复验收。
- **BREAKING（仅新通话模式的内部不变量）**：不再要求每个回答引用人工 ReviewDecision；改为必须引用有效 PolicyDecision，`hold` 另需人工批准。旧模式和旧接口不改变逐题确认语义。

## Capabilities

### New Capabilities

- `realtime-call-session`：增量双模式、通话生命周期、持续媒体与降级恢复。
- `streaming-turn-orchestration`：流式转写、自然分轮、问题拆分、自动短答和可取消播放。
- `risk-based-review-routing`：新领域、危害内容、业务数据查询的策略与工具授权门禁。
- `hinter-live-supervision`：滚动监管时间线、例外提醒和即时中文干预。
- `constraint-wiki-output-gate`：持久核心约束、版本冲突、可追踪召回和最终输出校验。
- `voice-latency-observability`：现有 session 提速、端到端延迟指标和可复现验收。

### Modified Capabilities

无已有 OpenSpec capability；历史非 OpenSpec 文档与冲突处理见 design.md 的迁移计划。

## Impact

- 前端：新增通话控制与媒体 hook；复用 ChatRoom、Conversation、WikiEditor，扩展 Hinter 监管面板；call 模式仅 Talker 默认播放，避免双标签页回声。
- 后端：保留 SessionService/Store/VoiceAgent/WikiStore，新增 CallCoordinator、音频 adapter、PolicyEngine、ConstraintService、OutputGate；收窄现有通话工具权限。
- 接口：新增 call 生命周期、信令、干预、风险待办及约束事件；现有录音上传与草稿审核 API 保持可用。
- 依赖建议：Pipecat + SmallWebRTC、Silero VAD、sherpa-onnx 英文流式 ASR、Kokoro 英文分句 TTS；精确版本、模型许可和目标硬件性能在实施前做小型验证并锁定。
- 持久化：沿用 SQLite 和 Markdown，增加 call/epoch/policy/constraint/output-check/delivery 信息与可重建 Wiki 投影；本期继续单进程。
- 范围假设：先做浏览器内“像打电话”的 AI 通话；真实手机号、PSTN/SIP 接入作为后续扩展，不属于这次 Demo 必须交付。
- 本次产出仅为 OpenSpec 设计包，不改动运行时代码、模型配置、线上策略和用户 Wiki。
