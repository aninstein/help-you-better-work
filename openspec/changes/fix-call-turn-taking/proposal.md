## Why

实时通话已跑通，但当前把约 0.65 秒的 ASR endpoint 直接当成用户说完，前端又通过音量阈值直接打断 AI，导致短停顿被切碎、噪声误打断。英文 ASR 原始大写结果未经二次识别和文本校正就进入主 Agent，影响理解与阅读。

用户先确认了本轮OpenSpec，随后批准直接实施；功能已按主链路落地，实际验证和校正超时预算调整见 [implementation-status.md](implementation-status.md)。不新增审批平台、测试题库或大规模验收工程。

## What Changes

- 保留 PCM WebSocket 持续双向流和三个紧凑图标入口，分离音频帧、ASR 内部分片、用户对话轮次。
- 以服务端 Silero VAD 判断有效语音；连续约 **3000ms 无有效语音**才封存一轮。0.5～2.9 秒停顿、ASR endpoint、内部长度切片均不创建聊天消息或触发回答。
- 有效插话立即取消旧回答，不等待三秒；三秒只决定新输入何时提交。去掉前端 RMS 阈值直接推进输出 epoch 的行为。
- 在线 sherpa-onnx 用于实时字幕；完整语音窗口使用预热的 faster-whisper 英文模型二次识别，解决实际声学识别问题。
- 新增 `TranscriptRefiner` 独立 subagent：指定模型直连、非思考、一次结构化调用，恢复自然英文大小写、标点和有依据的纠错；不经过主 Agent、不检索 Wiki、不调用工具。
- 校正完成后只新增一个 Talker 气泡，再由现有主 Agent 拆题、处理 Wiki/Hint 与流式回答。原始识别、校正版本和音频保持关联，不把译文或猜测当作原话。
- 噪声、数字/ID 歧义、超时、重复 final、迟到结果和校正期间继续说话有明确处理方式。
- 延迟分开记录三秒停顿、最终 ASR、校正、主 Agent 与播放；旧“语音结束到首音 P50≤1.5s”目标不能用于新的三秒等待策略。

## Capabilities

### New Capabilities

- `pause-buffered-call-turns`：持续采集、三秒停顿聚合、轮次封存、单气泡提交及顺序处理。
- `confirmed-speech-interruption`：有效语音插话、噪声过滤、输出取消及控制事件优先。
- `direct-transcript-refinement`：英文二次识别、直连校正 subagent、大小写恢复、歧义与失败处理。

### Modified Capabilities

无已归档 capability：`openspec/specs/` 尚无基线，因此三个新 capability 使用 ADDED。已实施但未归档的 `add-realtime-call-mode` 为前置背景，其 streaming/latency 条目同步注明本轮修订，不能按历史参数实施。本 change 不代替先前的风险策略、Hint/Wiki 输出检查和撤回机制。

## Impact

- 前端：`useVoiceCall`、`TalkerComposer`、`Conversation` 与媒体事件协议；只显示一个可更新的临时字幕区，保留图标布局。
- 后端：`RealtimeSpeech`、`CallService.receive_audio/submit/process`、call WebSocket 路由，新增每通话 `TurnAssembler` 与独立 `TranscriptRefiner`。
- 依赖：Silero VAD ONNX；复用已安装 faster-whisper，建议完整轮次默认 `small.en` / CPU int8，保留当前 sherpa 字幕与 Kokoro 输出。模型与参数需在实施时按目标机器小规模实听确认，未声称已提升准确率。
- 数据：新增 call/turn/revision/audio-range/transcription provenance；保留 raw、final ASR 与校正文案，普通快照仍按角色和撤回状态投影。
- 兼容：录音上传/文字消息不走三秒缓冲；原主 Agent 事实边界、Hinter 优先控制、最近五题自动回查和全历史单条人工撤回不变。

技术取舍与来源见 [design.md](design.md)、[research.md](research.md)，接口见 [contracts.md](contracts.md)，功能任务见 [tasks.md](tasks.md)。
