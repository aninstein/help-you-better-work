# 通话模式数据与事件契约（拟定）

> 第一版实际接口以代码和implementation-status.md为准。输入分轮、转写与插话的后续协议修订见 [fix-call-turn-taking/contracts.md](../fix-call-turn-taking/contracts.md)，其中turn_protocol=2与三秒聚合/独立校正语义优先于本文旧turn.final描述。

这是后续实施的逻辑契约，**不是现有已实现 API**。路径均以 `/api/v1` 为前缀。OpenSpec 六项 capability 定义验收行为；本文统一字段、幂等与事件语义。

## 1. 身份与写操作

沿用后端 Actor 认证；session、用户/客户 scope 从服务端解析，禁止信任请求体 role。Demo 仍是固定用户，需验证 session 访问关系，不把它描述成生产租户鉴权。

所有 POST/PATCH 命令携带 `Idempotency-Key`，同 key 同 payload 返回原结果，同 key 不同 payload 返回 `409 IDEMPOTENCY_CONFLICT`。约束、问题审批携带预期 revision；SQLite 状态、命令结果和 outbox 在同一事务提交，不能沿用分离 save 后再缓存命令的非原子模式。

## 2. HTTP 命令

|路径|身份|主要输入|结果/限制|
|---|---|---|---|
|POST `/sessions/{session}/calls`|Talker|expected_session_version、transport_preference|call_id、state=connecting、短效一次性媒体凭据；busy/已有 call 返回409|
|GET `/sessions/{session}/calls/{call}`|成员|无|按角色投影的 call snapshot、恢复游标|
|POST `/sessions/{session}/calls/{call}/signal`|媒体所有者|connection_epoch、SDP/ICE|认证后 WebRTC 信令；不能使用跨 call/过期凭据|
|POST `/sessions/{session}/calls/{call}/mute`|Talker|muted|确认 capture_state；不停止接收 AI|
|POST `/sessions/{session}/calls/{call}/hangup`|Talker/Hinter|reason|推进 epoch、结束与释放；重复无副作用|
|POST `/sessions/{session}/calls/{call}/interventions`|Hinter|action、text、target、scope、expected_constraint_revision|intervention_id、resolved_target、effective_revision/status|
|POST `/sessions/{session}/calls/{call}/reviews`|Hinter|question_id/version、decision、hint、expected_constraint_revision|仅批准一次/拒绝/延后/补充；过期需重新激活|
|POST `/sessions/{session}/calls/{call}/tool-grants`|有资源权限的 Hinter|question/version、tool/resource、args_hash、purpose、expires_at|授权记录；默认有效期最多300秒，不能代替底层资源权限|
|PATCH `/sessions/{session}/constraints/{id}`|Hinter|expected_revision、supersedes/revoke、scope、expires_at|新 revision，不原地无痕覆盖|
|GET `/sessions/{session}/calls/{call}/alerts`|Hinter|status/cursor|风险待办及状态，不向 Talker 提供该集合|

intervention.action：`correct_now / add_for_next / block_target / pause_auto / resume_auto`；省略时 `correct_now`。target 可是 question/response ID，缺省由服务端解析当前活动输出或 next_turn，并在 ack 明示。默认 scope `call`；跨会话使用服务端可授权的 customer/user scope。

短提示同步持久提交；长输入可返回202 `indexing`，不宣称 semantic effective。stop 即使后续保存失败也先停播，返回可恢复失败状态。resuming 不直接复活已撤销输出。

旧 `/audio`、`/reviews`、草稿确认/编辑 API 保留 review_chat 语义；活动 call 的模式冲突返回409。通用 Wiki 编辑若涉及核心约束须转 ConstraintService，清空活动核心约束返回 ACTIVE_CALL_CONSTRAINTS。

## 3. 逻辑实体

|实体|必须保留的关键字段|
|---|---|
|Call|id、session_id、mode、owner_user_id、state、capture_state、supervision_mode、connection_epoch、response_epoch、version、started_at、ended_at、end_reason|
|Turn|id、call_id、audio_segment_refs、audio_start/end、transcript、transcript_revision、finalized_at、confidence、intent|
|Question|id、turn_id、version、source_spans、original_text、normalized_text、entity_confidence、order、depends_on、status|
|PolicyDecision|id、question_id/version、policy_revision、action、rule_ids、evidence、reason、created_at、expires_at|
|CallReview|id、question/version、actor_id、decision、constraint_revision、hint_id、status、expires_at|
|ToolGrant|id、question/version、tool、resource、args_hash、scope、purpose、authorized_actor、expires_at、revoked_at|
|ConstraintRecord|id、original_text、normalized_interpretation、source_intervention_id、logical_key、scope、revision、status、effective_at、expires_at、supersedes、sensitivity、wiki_projection_status|
|ConstraintOutbox|id、revision、target_path、projection_hash、attempts、last_error、synced_at|
|Response|id、call_id、turn_id、question_ids、policy_decision_ids、epoch、candidate_hash、status、public_text、delivery_cursor、interrupted_reason、human_reviewed、fact_status|
|OutputCheck|id、response_id、candidate_hash、policy/constraint/facts_revision、evidence_refs、deterministic_checks、semantic_result、decision、reason、checked_at|
|OutputPermit|response_id、epoch、candidate_hash、policy/constraint/facts_revision、revoked_at|
|Delivery|response_id、epoch、chunk_id、sent/received/play_started/play_ended_at、audio_offset、confidence/measurement_method|
|Alert|id、question/version、rule_group、severity、status、first_seen_at、ack_at、resolved_by|

Question.status：`queued / auto_ready / notify_ready / held / denied / answering / answered / expired / interrupted / failed`。Response.status：`drafting / checking / verified / synthesizing / speaking / delivered / interrupted / held / failed`。这些是 call 新状态，不原地改写旧模式状态枚举。

PolicyDecision 与 CallReview 是不同对象：auto 没有伪造 reviewer；human_reviewed 仅在确有人工行为时为 true。fact_status 默认 `unverified_model_response`，有效授权数据可记 `source_verified`，人工明确确认的知识才成为 `human_confirmed`。自动回答成功不自动升级长期事实。

## 4. 有序控制事件

复用 `/ws/sessions/{session}` 与 `/sessions/{session}/events`，增加可恢复 call 事件。通话控制增量必须逐条发送，不再只取一批 events 的最后一条；sequence gap 必须 snapshot resync，event_id 去重，重连不重播音频。

```json
{
  "event_id": "evt-...",
  "session_id": "session-...",
  "sequence": 143,
  "occurred_at": "2026-10-22T06:00:00Z",
  "type": "response.playback_started",
  "call_id": "call-...",
  "data": {"response_id": "response-...", "epoch": 8}
}
```

|事件|内部/Hinter内容|Talker 投影|
|---|---|---|
|`call.state_changed`|状态/连接代次/原因|必要通话状态、安全错误|
|`transcript.partial/final/corrected`|原文/修订/置信度|自身转写与修订|
|`question.created/updated`|拆分、依赖、策略|公开问题文本/处理状态，不含私有依据|
|`risk.alert_created/updated`|规则/证据/原因/待办|无私有内容；需要保游标时用空 session.updated|
|`hint.accepted/effective/wiki_synced/failed`|原文/target/revision/错误|无私有内容，不透露 hint 事件类型|
|`constraint.updated/revoked`|scope/revision/证据|无私有内容|
|`response.candidate_delta/check_started/check_failed`|私有候选与检查|无私有内容|
|`response.verified`|OutputCheck/证据|仅通过后的英文 public_text 和公开ID|
|`response.playback_started/progress/completed`|chunk/offset/精度|本方播放必要信息|
|`playback.stop`|response_id、epoch、stop reason|response_id、新 epoch、generic reason|
|`call.degraded/ended`|原因、诊断|可操作的安全原因|

序列为 session 业务游标，隐藏事件不能产生虚假的缺口：向 Talker 发无敏感 data/type 的 `session.updated` 占位或显式合法 cursor advance。信令与媒体包使用独立序号，不为每帧占业务 sequence。RTVI 默认输出必须关闭或经相同投影，不另开私有内容旁路。

## 5. 媒体、停播与播放确认

- WebRTC：协商 codec/采样率；服务端受控适配为模型 PCM。媒体连接凭据绑定 call/owner/epoch、短效且单次使用；不在日志或持久 URL 暴露长效 token。
- 应用层 `playback.begin(response_id, epoch)` 控制当前接收路径；旧 epoch 音频不得写入新路径。原生 RTP 本身无应用 epoch，必须验证 SDK 可清除/重建接收缓冲，不能只丢 JSON。
- `playback.stop` 走最高优先级事件；客户端立即停止旧音轨/清队列，返回带 response_id/epoch 的 `playback.stopped_ack`。终端处于后台/未确认时不能假称已停播，标记 delivery_unknown 并停止新输出。
- 自实现 PCM 备用链路若加入，每帧含 call/epoch/chunk_seq/sample_rate/channels/时长；收发两端校验 epoch、帧尺寸、时序和背压。其验收与 WebRTC 同标准。
- 播放开始由用户“开始通话”手势解锁音频；浏览器阻止时显示“点击接通声音”，保持收音/状态一致，不把音频文件 ready 当作实际播放。
- 输入音频实时处理、分段异步归档。保存原始 final 及修订证据；可重播只有归档完成后才展示。

## 6. 事务与错误边界

同 call 的状态提交、constraint revision、epoch 与许可判定由有界临界区串行化；网络/模型/文件处理在锁外。提交前 compare-and-set：call active、question version、epoch 和所有约束/事实版本均匹配。写失败不得发成功 ack 或公开内容。

错误代码至少包含：`MODE_TRANSITION_BUSY`、`ACTIVE_CALL_EXISTS`、`MEDIA_UNAVAILABLE`、`VERSION_CONFLICT`、`CONSTRAINT_CONFLICT`、`CONSTRAINT_WRITE_FAILED`、`ACTIVE_CALL_CONSTRAINTS`、`STALE_EPOCH`、`POLICY_UNAVAILABLE`、`TOOL_GRANT_REQUIRED`、`OUTPUT_CHECK_FAILED`、`CALL_RECOVERY_TIMEOUT`。内部 provider/私有 Wiki 细节仅 Hinter 受控诊断可见；Talker 使用通用可操作错误。
