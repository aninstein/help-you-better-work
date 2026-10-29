## ADDED Requirements

### Requirement: Explicit additive call mode
系统 SHALL 默认使用 `review_chat`，仅在 Talker 显式开始通话且前置检查成功后进入 `realtime_call`；旧录音、文字、草稿确认、Wiki 和新建 session 能力 MUST 保持可用。活动 call 内禁止旧模式在途答案混入通话。

#### Scenario: Existing session remains compatible
- **WHEN** 加载没有 interaction_mode 字段的历史 session
- **THEN** 按 review_chat 展示并继续要求人工确认，不自动回答旧 pending_review 问题

#### Scenario: Start while legacy generation is active
- **WHEN** 旧模式仍有录音、生成或待播放 TTS，Talker 请求开始通话
- **THEN** 返回 MODE_TRANSITION_BUSY，不自动取消或播出旧草稿，完成或取消后可重试

### Requirement: Continuous bidirectional media
系统 SHALL 在接通后持续捕获并传送音频，持续接收 AI 音频，直到静音、挂断或故障；MUST 不依赖手动停录、整段上传或每个问题重新开启麦克风。SSE MUST 仅用于控制事件。

#### Scenario: Consecutive spoken turns
- **WHEN** Talker 接通后连续完成三轮英文问答
- **THEN** 三轮均使用同一活动媒体连接，不要求点击录音/发送，输入仍在 AI 播放时被接收

#### Scenario: Unsupported media is explicit
- **WHEN** WebRTC 不可用且没有通过验证的持续 PCM 备用通道
- **THEN** 显示通话不可用与重试/返回聊天，不将上传录音标记为通话成功

### Requirement: Authorized call lifecycle
系统 SHALL 由后端身份及 session 成员关系校验 start/connect/mute/hangup，保证每 session 最多一个活动 call 和一个 Talker 媒体发布者；Hinter MUST 无默认麦克风采集权限。命令 MUST 幂等并在冲突时返回明确状态。

#### Scenario: Two tabs attempt ownership
- **WHEN** 两个 Talker 标签同时申请不同 key 的活动 call
- **THEN** 仅一个成功，另一个得到 ACTIVE_CALL_EXISTS，且不能复用他人的媒体凭据

#### Scenario: Hinter cannot publish voice
- **WHEN** Hinter 使用自己身份请求发布 Talker 音轨
- **THEN** 后端拒绝，中文提示通道继续可用

### Requirement: Visible call controls and release
Talker SHALL 能看到连接/收听/思考/讲话/恢复/结束状态、通话时长、静音和挂断控件；挂断 MUST 撤销输出许可、停止音频、关闭麦克风轨道并保存已播记录。

#### Scenario: Hangup during generation
- **WHEN** Talker 在 AI 生成期间挂断
- **THEN** 立即进入结束流程，迟到的生成/音频不得发出；服务端收到挂断后 1 秒内完成本地资源释放，底层不能取消的推理结果被丢弃

#### Scenario: Muting microphone
- **WHEN** Talker 开启静音
- **THEN** 停止上传麦克风内容并显示 muted，仍能听 AI；取消静音沿用 call 而非新建 session

### Requirement: Separate media and control health
系统 SHALL 分别跟踪媒体和控制通道健康；任一关键通道丢失 MUST 暂停新实质输出、撤销旧 permit，并以新 connection_epoch 恢复，恢复超时默认 10 秒后结束 call。

#### Scenario: Control disconnect while audio survives
- **WHEN** 媒体尚连通但控制心跳超时，无法接收 Hinter 干预
- **THEN** 停止继续回答并显示恢复中；不能以音频仍连通为由继续自动输出

#### Scenario: Reconnection does not replay stale speech
- **WHEN** 客户端携带最后事件游标重连
- **THEN** 恢复有序状态并同步最新约束，旧 epoch 音频全部丢弃；只显示历史已播内容，不自动重播

### Requirement: Independent archival and safe recovery
系统 SHALL 异步归档原语音分段及 final transcript 的来源关联；归档失败 MUST 可观察且不伪造可重播状态。进程重启 MUST 不自动复活旧 call，恢复约束索引后才允许新 call。

#### Scenario: Archive storage failure
- **WHEN** 通话正常但音频归档写入失败
- **THEN** 实时交流可继续，Hinter 看见归档失败，原音频标记 unavailable，转写和已播记录保留

#### Scenario: Process restarts with active call
- **WHEN** 服务重启发现旧活动 call
- **THEN** 将其结束为 process_restart，恢复约束日志/投影后允许重拨，不回放未完成候选
