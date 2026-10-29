# 调研：实时语音组件与风险策略

调研日期：2026-10-22。以下是官方仓库/文档核查与基于现有工程的选型判断，**没有完成安装、性能对比或实际通话验证**。链接内容会变；实施时记录精确版本/commit、模型快照和校验值。

## 1. 现状与真正需要替换的部分

|代码位置|实际行为|设计影响|
|---|---|---|
|`frontend/src/hooks/useRecorder.ts`|MediaRecorder 停止后合并 Blob；浏览器转写只辅助展示|保留录音入口，call 新建持久在线媒体通道|
|`backend/app/api/routes.py` upload_audio|整文件读取后 submit|不能将该接口循环调用包装为实时语音|
|`backend/app/adapters/speech.py`|本地 Whisper 每次新建模型；TTS `.save()` 等文件完成|旧模式先复用已加载模型；call 使用在线 ASR 与分句音频输出|
|`SessionService.run_intake`|持 session lock 等待模型、装载整个问题列表/Wiki|锁内只做快照/提交，外部推理移出；版本校验防止陈旧提交|
|`SessionService.generate`|已有 LLM 文本流，但为私有草稿，结束后等待确认|复用模型路由，新增 call prompt 和受控自动发布路径|
|routes realtime|每 150ms 查事件并发送最新全量 snapshot|call 增量事件和直接唤醒，不能每个音频帧写全 session|
|WikiStore.bundle|最多 20 文档、26000 字符，超出截断|核心约束独立索引，禁止随普通文档被截断|
|ToolConfig / VoiceAgent|拟答时可调用 bash、MCP、文档预读|call 默认无副作用工具；数据查询在预读之前授权|

这些是代码观察，不是耗时排名；先埋点才知道各阶段贡献。

## 2. 实时编排/传输选择

|候选|核实的能力|对本项目的取舍|
|---|---|---|
|[Pipecat](https://github.com/pipecat-ai/pipecat) + [SmallWebRTC](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc)|Python 实时媒体框架，BSD-2-Clause；SmallWebRTC 双向音频/数据，基于 aiortc 的直接连接|**本期推荐**：在 FastAPI 旁建立媒体 pipeline，业务门禁仍自研；自定义处理器桥接现有 Agent，不能接上默认 LLM→TTS 路径绕过 Wiki gate|
|[LiveKit Agents](https://github.com/livekit/agents) + [Telephony](https://docs.livekit.io/telephony/)|Agents Apache-2.0，实时会话；电话文档覆盖 SIP、呼入和呼出；turn 模型另有许可证|后续手机号、多人房间/媒体基础设施需求明确时优先重新评估；本期单个 Talker 无需立即引入房间服务|
|原生 aiortc / WebSocket 自研|能构建持续媒体链路|控制最细，但需自行处理音频时钟、背压、取消与浏览器兼容；不作为主线重复建设|

推荐是工程判断，并无“Pipecat 延迟一定比 LiveKit 低”的实测证据。SmallWebRTC 不要求托管平台，但跨 NAT/受限网络仍可能需要 STUN/TURN，不能把“直连”解释成公网无需中继。HTTPS、TURN 凭据与防火墙连通性在部署验收中单独验证。

Pipecat 提供[打断机制](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)与[轮次策略](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies)，但业务 epoch、策略优先级、Wiki 校验与角色投影仍须自行实现和测试。不会引入 Pipecat Flows 代替确定性业务状态机。

## 3. ASR / VAD / TTS

|层|推荐基线|可替换项与验证条件|
|---|---|---|
|VAD|[Silero VAD](https://github.com/snakers4/silero-vad)，MIT，ONNX 路径|VAD 只检测语音，不理解一句话是否讲完；先配可调静音阈值及文本完整性策略，测慢语速/长停顿误切|
|ASR|[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)，Apache-2.0 代码；在线英文 Zipformer 候选|官方[在线模型目录](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html)区分语言与流式模型。选英文 online checkpoint，保持每通话独立状态；具体权重许可另查，不能用代码许可替代|
|TTS|[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)，模型卡标注 Apache-2.0，支持英文和分段生成示例|将已校验短答按自然句分块合成并立即发帧；这是**分句合成 + 持续音频传输**，不宣称模型具备任意 token 级原生合成。首块时间、音质、CPU/内存需实测|
|现有能力|faster-whisper 文件转写、edge-tts 与系统语音|保留 review_chat；call 未通过能力探测不得静默回到“录完上传”。edge-tts 的存在不等于可自托管开源 TTS 模型|
|中文扩展|[FunASR](https://github.com/modelscope/FunASR)|工具代码 MIT、权重另授权；官方指出在线 Paraformer-zh 是中文流式路径，不以它替代本题英文 ASR 基线|

基线以本机 CPU 可验证为起点；部署形态可以把 ASR/TTS 放到隔离推理 worker，但控制面仍为原 FastAPI 单实例。模型预热一次、超时与取消隔离、每通话缓存独立，是选型必须验证的条件。若本机达不到目标，在同一 adapter 接入经验证的在线供应商并展示实际能力；不能把未验证的服务名称写成已满足指标。

不选原生 speech-to-speech 直通作为第一版：其便利性不足以抵消“音频已经生成/播出后才做文字约束校验”的控制难度。先采用可检查的 ASR→文本候选→Wiki gate→TTS。

## 4. 风控条目从哪里来

- [LDNOOBW 词表](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words)包含 `en`、`zh` 等语言，适合提取辱骂/粗俗等**词汇信号**；[许可证](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/blob/master/LICENSE)为 CC BY 4.0，引用/改编需保留归属和修改说明。它不是“所有违法犯罪词”的权威清单，也不能替代意图判断。
- [ShieldLM 官方项目](https://github.com/thu-coai/ShieldLM)支持中英文安全检测和自定义规则，可参考其安全/不安全/争议分类思想作为离线评测候选；模型依赖与基座许可单独核实。本期不默认在每个音频帧运行大规模安全模型。
- 业务问题（DAU、用户 ID、订单、账户等）与新领域规则由项目自己定义，见 [policy-seed.yaml](policy-seed.yaml)；这部分是产品策略，不冒称网络词表/法律结论。

实施时生成 `policy/lexicons/manifest.json`，至少记录 source_url、upstream_commit、retrieved_at、license、attribution、sha256、语言、保留/排除条目与变更原因；只从固定来源离线导入。**本次没有下载或加载第三方全量词表**。无清晰许可/来源的转载大词库不自动入库。

处理链：Unicode NFKC/大小写/空格和常见混淆归一化 → 英文词边界/中文短语匹配 → 请求意图与上下文分类 → 版本化策略决策。对“kill a process”“预防诈骗”“解释 DAU”必须有反例；对无敏感词的隐晦危害请求、同音转写、跨句补充必须有正例。关键词命中只提供 evidence，不能单独推断违法。

## 5. 尚需验证的事实

1. 目标 Mac 与 Docker/Linux 上所选 ASR/TTS checkpoint 的许可证、运行依赖、预热、峰值内存与实时率；包与模型全部锁版本。
2. Pipecat/aiortc 与当前 `av>=13,<17` 的依赖交集；冲突时隔离媒体 worker，避免无依据升级全栈。
3. 浏览器远端音轨的打断和旧 RTP 尾音：暂停接收端、清队列并按 epoch 重建播放的实际停止耗时。
4. ASR 英文口音、数字/ID、噪声与回声；不能承诺“拆分永不出错”，必须保留原文并能澄清/纠正。
5. 完整短答校验增加的延迟、策略分类误报和漏报；对各阶段分布做基线，不以框架宣传数字作为本项目结果。
