# 分轮与英文识别调研

调研日期：2026-10-22。以下为公开的一手资料与代码观察；没有声称知道 Codex 或豆包客户端的内部实现，也不把这些产品的体感当成已验证算法。

## 1. 修复前代码观察（设计时取样）

| 位置 | 当前行为 | 与用户问题的关系 |
| --- | --- | --- |
| `backend/app/adapters/realtime_speech.py` | endpoint rule1=1.5s、rule2 默认0.65s、rule3=18s，endpoint 后 reset | 短停顿或长度限制均可能切出一段；仅调 rule2 到3秒不能排除其他规则提前结束 |
| `backend/app/application/calls.py::receive_audio` | 任意 ASR final 立即 `submit`，清空输入音频 | 声学分片直接变成聊天交互，多次短停顿会产生多轮 |
| `frontend/public/voice-worklet.js` | 16kHz、512 samples/帧，约32ms | 音频帧是传输单元，不能作为用户分轮依据 |
| `frontend/src/hooks/useVoiceCall.ts` | RMS>0.022 连续4帧就本地丢弃 epoch，静音24帧后允许再次触发 | 约128ms音量变化即可取消；不识别人声/回声，也不代表用户说完 |
| 当前英文模型 `tokens.txt` | 包含 `▁THE`、`▁AND` 等大写 token | 全大写与当前模型输出形式一致；现有链路无 truecasing/标点恢复层 |
| `CallReasoner.plan` | 直接读 Wiki/约束并拆题、分类、候选回答 | 不适合承担每个 ASR 分片的轻量校正 |
| `SpeechAdapter` | 已有 faster-whisper 缓存，但旧上传模式默认 tiny/beam_size=1 | 可复用库和加载机制，通话完整轮次需要独立英文配置，不应直接沿用旧上传默认值 |

## 2. 开源做法与本项目决定

### LiveKit：分开判断何时接话与何时让出话语权

官方文档区分 VAD、endpoint delay、turn detector 与 interruption，并说明 min duration/words 用于过滤短噪声，存在 false interruption。借鉴其职责划分：用独立的语音活动证据判断插话，用三秒停顿判断用户轮次完成；本轮不引入 LiveKit 服务或依赖云端 adaptive interruption。

来源：[LiveKit turns overview](https://docs.livekit.io/agents/logic/turns/)。

### Pipecat / Smart Turn：ASR 分片不等于自然交接

Smart Turn 在 VAD 检测到停顿后分析音频，判断表达是否完成；VAD 的短 stop 窗口和最终结束轮次的等待不是同一参数。本项目先遵守用户指定的三秒聚合策略；Smart Turn 留作可选后续增强，不允许它在三秒之前提前提交，不引入整套 Pipecat 媒体框架。

来源：[Smart Turn 文档](https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview)、[开源模型](https://github.com/pipecat-ai/smart-turn)。

### Silero VAD：替代单纯音量判断

开源仓库提供人声检测、ONNX 路径与8/16kHz支持；源码的流式包装处理16kHz的512采样块，可对接现有32ms帧。采用独立的每通话状态，原 PCM 连续传输；检测窗口与ASR重置不能复用为业务轮次。浏览器 echoCancellation 继续启用，VAD 本身不等于回声消除或说话人识别。

来源：[Silero VAD](https://github.com/snakers4/silero-vad)、[流式包装实现](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py)。

### sherpa-onnx：endpoint 是声学边界，可以有二次识别

官方 recognizer 接口区分尾部静音规则和最大长度规则；`is_endpoint/reset` 本身不定义聊天消息。官方亦提供 online + offline 的 two-pass 示例。因此保留在线字幕，把 endpoint 输出交给 TurnAssembler 累积，不直接触发主 Agent。

来源：[OnlineRecognizer 参数](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/online_recognizer.py)、[two-pass 示例](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/two-pass-speech-recognition-from-microphone.py)。

### faster-whisper：用音频证据提高最终转写质量

官方实现接受音频数组，支持固定 `language="en"`、`task="transcribe"`、beam search、词时间戳及分段信息。采用完整轮次英文二次识别，建议预热 `small.en`/CPU int8；在线字幕保留为失败回退。此处是组件选择与待验证参数，未进行新旧模型准确率或目标机器速度对比。

来源：[faster-whisper 仓库](https://github.com/SYSTRAN/faster-whisper)、[transcribe 参数与返回结构](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)。

## 3. 最终选择

现有 AudioWorklet/PCM + **Silero VAD + TurnAssembler（三秒）+ sherpa 在线字幕 + faster-whisper 完整语音二次识别 + 直连 TranscriptRefiner + 现有主 Agent**。

优先修复用户可感知行为，不迁移 WebRTC，不搭建额外 Agent 调度平台。文本校正只能改善有文本或声学依据的错误，不能保证把错误原始识别“猜对”；数字、ID、否定等存在分歧时应澄清。
