## Why

目标应是自然电话交流：用户完整表达时及时接话，犹豫或补充时耐心等待，不因附和、回声或无关声音频繁中断。此前按固定三秒停顿实现，解决了碎片化提交，却没有判断用户是否真的说完。用户最新要求允许重新审视原先的参数和流程，以体验而非字面参数为准。

现状已经具有持续双向 PCM、在线字幕、完整音频二遍识别、直连校正、多问先答/人工后补、Wiki 最终检查与撤回；这些继续保留。缺口在于动态交接、插话意图、识别上下文、前端音频质量和真实体验证据。详见 [现状审视与调研](research.md)。本方案借鉴豆包的交互原则，不宣称现有级联架构等同 Seeduplex 原生端到端模型。

## What Changes

- 默认设计改为**声学与语义共同判停**，完整短问可早于三秒接话；未完成的号码、否定、自我纠正和长句可等更久。三秒保留为检测器故障时的兼容回退，不再是每轮硬条件。
- 首选 **Silero + Smart Turn v3.2 CPU ONNX + 少量转写完整性规则**，嵌入现有 FastAPI/PCM 链路。端点模型直接看音频韵律，不依赖大模型文本补标点后才工作。
- 区分附和、真正抢话、疑似回声/背景声。候选插话先短暂降低 AI 音量，确认后才撤销输出；“stop/wait”和 Hinter 显式控制走快速路径，不因短句被过滤。
- 保留流式字幕与二遍识别，补充受控术语词汇和有依据的纠错；校正 subagent 仍为固定直连，但高质量识别结果不再每轮强制额外串行调用 LLM。需要修复、冲突或歧义时才调用/澄清。
- 以实时流完整性、实际采样率、AEC/降噪生效情况及用户可听延迟定位问题；用少量真实录音/实听验证效果。
- 继续保留倾听/思考两个主活动状态、十秒软件处理上限、多问分流、人工补答、Hinter 私有约束、最终 Wiki 检查及撤回。

## Capabilities

### New Capabilities
- `adaptive-voice-turns`: 自适应发言权交接、长句续说与有界回退。
- `intent-aware-interruption`: 插话意图、附和、候选降音及真实停止。
- `contextual-streaming-transcription`: 实时字幕、上下文辅助二遍识别和音频质量。

## Impact

未来实施会修改 `TurnAssembler`、`CallService`、语音 adapter、TranscriptRefiner、AudioWorklet 与 `useVoiceCall`，增加独立 `TurnController/EndOfTurnAdapter/InterruptionDecision`。新增组件不接管业务工具、Hinter/Wiki 或消息权限。

用户已确认实施，本轮P0在原架构内落地，详见[实施与验证记录](implementation-status.md)和[任务状态](tasks.md)。协议3采用动态交接、意图插话与按需校正；协议2继续兼容固定三秒。此前fix-call-turn-taking的固定参数仅作为旧模式/故障回退；call-conversation-scheduling的十秒预算从实际语义冻结起计算。云ASR、RTC主说话人聚焦仍属条件性P1，未启用云语音，也不复用文本模型凭证传输原声。
