# 回答链路现状与一手调研

核对日期：2026-10-22。以下区分代码事实、厂商公开说明和本项目设计判断；没有实测豆包 App，也不把厂商首包数字当本系统端到端延迟。

## 代码事实

| 位置 | 当前行为 | 影响 |
| --- | --- | --- |
| `backend/app/application/service.py`：intake / review / generate / decide_answer / synthesize | 默认 `ai_mode=False`；问题进 `pending_review`；AI 模式只自动准备；整答成为 `pending_confirmation` 后，确认才公开和 TTS | 文本、上传语音均继承两次人工阻塞 |
| `backend/app/agent/voice_agent.py`：generate | 工具模式用 `.astream` 收集 `chunks`，但直到模型该轮结束且确认没有 tool_calls 才 yield；中文输出整答完成后再翻译并 reset | SDK 流式不等于产品可增量消费；后置翻译还会重写已输出内容 |
| `backend/app/agent/model_router.py`：stream | 失败后对下一个模型发 reset 并重新生成整答 | 对私有草稿可用；对已播放声音不可回滚，可能重复说话 |
| `backend/app/agent/voice_agent.py` / `tools.py` / `tool_config.py` | 仍提示模型生成 human-approved 私有草稿；ToolRuntime 初始化可发现 MCP，默认 bash_enabled=True；链接文档可能预加载 | 自动回答迁移必须改 Prompt 和工具权限配置，不能仅跳过确认；普通题不应等待无关连接器发现/预加载 |
| `backend/app/agent/call_policy.yaml` / `.py` | open_domain、data_lookup、ambiguous 默认 hold；正则强制意义问题/DAU等类别；未知分类落 ambiguous | 普通题也容易转人工 |
| `backend/app/agent/call_reasoner.py`：plan / answer / check | 计划 JSON 包含完整短答；回答 `.ainvoke`；完整候选额外 `.ainvoke` 做 Wiki 检查 | 首句等待多个串行模型往返 |
| `backend/app/application/calls.py`：process / hold_processing / respond | 歧义和处理超时可创建 held；`prepare` 完整答案 + check 后才 `speech_chunks` 和第一次 synthesize | 当前是完整文本后的分句音频发送，不是真正生成中出声 |
| `backend/app/adapters/realtime_speech.py`：synthesize | 当前调用 native `tts.generate` 返回整段 PCM；持有全局模型锁；取消后仍要等 native 线程退出 | 长合成占锁，后续短回应也可能排队 |
| `frontend/src/hooks/useVoiceCall.ts` | 输入协议3连续 PCM；输出按 epoch 播放，`audio_end` 后整条 delivered | 缺逐段播出确认和音频内明确 segment 身份 |
| `calls.py`：context / intervene / project_constraints | 已有持久 call 约束与私有 Markdown 投影、版本、最近五题回查、手动撤回；并非所有文本 Hint 共用此链路 | 应提取共用约束服务，复用撤回，不重建另一套记忆 |

当前白色圆环/紧凑音频线、自适应判停、直连转写校正、双向持续采集均可保留。最近一次暖机短问记录约 5.6 秒到首包，是有限本地样本，不代表 P95 或真实扬声器可听时延；详见[历史实施记录](changes/improve-natural-voice-interaction/implementation-status.md)。

## 一手资料与可借鉴边界

### 1. 豆包：自然节奏来自持续理解和发言权控制

公开研究资料描述了全双工语音、声学与语义判停、边听边说和干扰抑制等方向；这些资料不构成本项目的接入合同、内部实现说明或模型权重许可。

**项目判断：** 保留已有持续采集和动态判停，解除等待整答/人工的业务阻塞；不为了照搬模型架构推倒已经可用的链路。

### 2. 当前 Seed 产品还在更新，不能把旧描述当永恒架构

公开模型展示不等于本仓库接入合同或许可，也不证明与可用 API 同版。

**项目判断：** 本次只取自然声音交互原则，不引入摄像头、永久声纹、多模态场景理解或未核验的 API 能力。

### 3. 豆包语音：双向 TTS 与一次性输入后流式输出有区别

[火山引擎大模型 TTS 双向 WebSocket](https://docs.volcengine.com/docs/DoubaoVoice/WebSocketBidirectionalStreaming-V3?lang=zh)的官方检索摘要区分一次输入文本/音频流出和通过会话持续提交文本同时接收音频。[双向语音合成参数](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-text-to-speech-websocket?lang=zh)摘要列有 PCM 与字幕时间戳支持。页面正文依赖 JavaScript，本次未取得完整可核验协议正文，不能认定具体版本的取消、flush、时间戳边界或音色可用性已验证。

**项目判断：** 云增强优先候选为豆包双向 TTS，实施前核验当前账号支持的版本/英文音色/取消语义/计费。将 `start/send_text/finish/cancel` 作为项目 adapter 抽象，不冒充已核对的供应商事件字段。禁止把全部文本准备完再发送，包装成“双向”。

### 4. Pipecat：短句聚合、取消与已播历史可分开实现

[官方 TTSService](https://reference-server.pipecat.ai/en/stable/api/pipecat.services.tts_service.html)区分句子聚合和 token 直送模式，并定义音频上下文。[官方中断说明](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)描述中断取消推理、清聚合和未播音频，同时把实际说出的部分纳入上下文。

**项目判断：** 采用短句聚合、明确取消事件、按播出确认记忆这三个模式。复用本项目状态机即可，不为此引入整个 Pipecat runtime；逐 token 朗读会破坏自然韵律和限制验证边界。

### 5. LiveKit：慢工具不应冻结对话，填充语音只在空隙说

[官方 Async tools](https://docs.livekit.io/agents/logic/tools/async/)区分关键进度通知和空隙填充语音，支持异步执行及取消。它强调填充语音等待持续空闲才播放，避免压住用户或排在其他语音后堆积。

**项目判断：** 本项目用独立任务与统一输出队列实现先回应、后结果；不逐轮强塞“收到”，不在打断后继续播旧“正在查询”。这是一种设计借鉴，未声称豆包内部也用相同实现。

### 6. 本地与开源 TTS 选型

[sherpa-onnx 官方 Kokoro 示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/cxx-api-examples/kokoro-tts-en-cxx-api.cc)展示生成回调，但不能据此断定当前安装的 Python binding/模型有相同取消粒度。[FunAudioLLM 官方 CosyVoice 模型卡](https://huggingface.co/FunAudioLLM/CosyVoice-300M)说明其双流文本输入/音频输出能力和英文覆盖，宣传延迟不能套用本机硬件。

**项目判断：** P0 用现有 Kokoro 处理生成中提交的短句；核验本机回调后可以进一步减少首句内等待。离线严格要求双向模型时，CosyVoice 可作后续候选，但需要单独核验当前模型版本、运行内存、硬件、许可和部署成本，不作为这四项功能的必做前置。云双向 TTS 可选，不启动训练、不引入声纹克隆。

## 延迟设计依据

削减串行路径：`整段规划含答案 → 整答校验 → 整段合成` 改为 `轻量路由 + 约束快照 → 增量回答 → 短句验证/合成/播放流水线`。有工具时即时回应与后台任务并行；下一句准备可与当前句播放并行。

首次回应、首个实质答案、工具总耗时、段间断音和确认插话后停止各自计时。不能用 0.5 秒的“Okay”掩盖真实答案仍要等待 15 秒。具体拟定目标见 C / D，不声称本轮已经测量或达标。
