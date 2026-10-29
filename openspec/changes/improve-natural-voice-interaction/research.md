# 现状审视与一手调研

审视日期：2026-10-22。依据当前工作区代码、此前保留的隔离验证记录和本次只读探针；没有重新声称完成真人口音、扬声器外放、咖啡厅多人重叠实听。用户提供的豆包介绍是研究线索，不能直接当成本项目已具备的能力或某个云接口的完整契约。

## 当前实现的真实能力

| 项目 | 证据 | 结论 |
| --- | --- | --- |
| 边听边说 | `frontend/src/hooks/useVoiceCall.ts:103,127,150` 持续采集与播放；`frontend/public/voice-worklet.js:25` 每512采样发PCM | 已有双向并行媒体，不是录音上传。对话决策仍是 ASR→文本推理→校验→TTS 的级联，不能称为原生全双工语音模型 |
| 判停 | `backend/app/application/turns.py:123` 只比较 quiet_ms 与 silence_ms | 有效静音约3000ms后一律封存，没有韵律、句意完整度或当前语速适配 |
| 插话 | `backend/app/application/calls.py:749` 时长/新词，300ms或800ms路径；词表排除部分uh/yeah | 比音量峰值可靠，但不等于受话者意图判断；长附和或他人人声仍可能命中800ms路径；确认后直接取消，没有候选恢复阶段 |
| 抗噪/回声 | 浏览器请求 AEC、noiseSuppression、autoGainControl；服务端 Silero | 请求不代表已证明生效；没有主说话人分离或声纹锁定，也没有完整的回声/外放测量证据 |
| 两遍识别 | `realtime_speech.py:69,129` sherpa在线 + small.en完整音频；静音800ms时可预跑 | 已有正确基础，没必要推翻；现在没有声学判停模型、术语偏置，也没把ASR置信信号校准成可靠的“听懂了”概率 |
| 文字纠错 | `transcript_refiner.py:171` 每轮固定直连；`calls.py:893` 最近两条Talker上下文 | 能改善自然大小写和明显错误，但额外串行模型调用仍在热路径。它不听原声，不能靠语言流畅度补回丢失信息 |
| 音频完整性 | `voice-worklet.js:19` 简单均值降采样；hook:134 网络积压时跳过帧；二进制PCM无seq | 实际非16k采样时需验证抗混叠质量；丢帧可能在不足1秒间隙内不被当前间隔检测识别。它们是待验证风险，不据代码断言已造成某次错字 |
| 长内容与业务协作 | 多问题独立准备、串行播出、持续held、原题补答、Wiki/撤回 | 已有价值，应保留；音频切片、用户表达、业务问题和可播放答案必须继续分开 |

### 本次可复现探针

直接调用现有 `TurnAssembler`，两次使用相同16kHz/512采样人声与静音序列，只替换字幕：

| 字幕 | 首次ready时的有效静音 | 状态 |
| --- | --- | --- |
| `What does HTTP stand for?` | 3008ms | finalizing |
| `My account number is ...` | 3008ms | finalizing |

这是状态机探针，不是两条真实ASR准确率测试。它证明完整问句与明显未完句在当前判停层被同样处理：前者固定多等，后者也可能过早交接；后续澄清无法补救打断体验本身。

此前完整合成语音样本显示：冻结到首音发送约6.52秒、此前有3008ms有意等待，合计约9.52秒；确认插话800ms后无旧epoch迟到PCM。说明取消隔离已有证据，**不代表停止确认足够快，也不代表真人感知延迟已达标**。来源：[既有实施记录](../call-conversation-scheduling/implementation-status.md)。

## 豆包资料如何用于本方案

| 资料 | 可支持的判断 | 不作出的推断 |
| --- | --- | --- |
| 公开全双工语音研究资料 | 描述同时听说、声学与语义共同控制交接、干扰抑制 | 公开效果数字不是本项目实测，也不代表模型权重已开放 |
| 公开语音模型资料 | 描述语音理解与生成的统一建模方向 | 模型延迟口径不同，不能当作带知识库检查的浏览器端到端指标 |
| [火山端到端语音接入文档](https://docs.volcengine.com/docs/real_time_communication/Accessend-to-endreal-timespeechlargemodel?lang=zh) | 检索可见文档列出判停窗口、二遍识别和热词配置 | 参数需按实际SKU、接口版本核验；“ASR 2.0”“Seeduplex”“云端二遍识别”不是可随意互换的同一个组件。本次直开正文受JS限制，不将搜索可见字段当成已联调契约 |
| [火山StartVoiceChat](https://docs.volcengine.com/docs/real_time_communication/StartAIconversationStartVoiceChat?lang=zh&redirect=1) | 官方文档列出独立的降噪、声纹和打断控制，能力依赖采集与AEC | 普通VAD无法替代主说话人分离；不能承诺单通道浏览器音频在任意多人重叠中锁定某个人 |
| [Seed-ASR论文，2024](https://arxiv.org/abs/2407.04675) | 原始论文讨论音频与上下文联合识别 | 不能用2024论文证明ASR 2.0全部新特性。用户提到的20亿编码器、关键词提升20%、13种海外语种及视觉能力，本次未逐项获得同一ASR 2.0一手接口正文支持，不作为实施依赖或验收数值 |

因此采用的产品原则是：完整时快接、未完时等、非交互声不抢、字幕快而最终文字准。视觉输入和环境声音推理不是现有英文电话客服的必要范围。

## 组件选择

| 方案 | 判断 |
| --- | --- |
| **Smart Turn v3.2 CPU ONNX，首选** | [官方仓库](https://github.com/pipecat-ai/smart-turn) 与 [权重目录](https://huggingface.co/pipecat-ai/smart-turn-v3/tree/main) 提供BSD-2-Clause的音频判停模型；直接处理16k单声道、最多约8秒窗口，可结合Silero。适合当前PCM与ONNX运行时，单独做adapter即可，不必迁移整个Pipecat。输入长度、补零方式和预处理必须与所选权重一致 |
| LiveKit最新音频Turn Detector，备选 | [现行文档](https://docs.livekit.io/agents/logic/turns/turn-detector/) 已区分音频v1/v1-mini与旧文本检测器；旧文本版本已标弃用。集成和模型可用性与AgentSession/部署环境相关，适合作为未来迁移RTC时的备选，不能照旧博客直接推荐旧文本模型 |
| 仅主LLM判断“说完了吗” | 容易依赖不稳定字幕，又增加网络往返；不能当每次停顿的唯一裁决器。局部语义规则只辅助音频模型，必要的专用推理必须有严格预算，不走主Agent |
| 豆包流式ASR/RTC声纹降噪 | P1可选对照路线，需核验实际接入权限、数据路径和所需独立凭证；复用已有文本模型API不代表已有语音服务权限。未配置时本地P0可以实施 |
| 直接换原生S2S | 暂不作为主线。当前要求每个最终文本先经Wiki检查，原生语音直出会冲突；只有证明可在公开音频前控制内容和撤销时，才值得另立变更 |

Smart Turn只判断发言是否结束，不负责转写、受话者/声纹判定或降噪。官方[版本说明](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/)介绍短句和噪声改进，不等于本机实测。主仓示例仍可能引用v3.1文件名，实施时固定v3.2权重revision、文件哈希与对应预处理，不能仅按“main/latest”下载。

ASR词汇偏置另有现成基础：[sherpa官方热词文档](https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html)要求transducer搭配modified_beam_search；不是填词表就对当前默认解码自动生效。[faster-whisper源码](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)提供上下文参数，实施时以本机安装版本为准；热词只是识别提示，不是事实或ID的替换表。

浏览器[AEC等约束与getSettings规范](https://w3c.github.io/mediacapture-main/)支持检查实际采集配置；单靠请求echoCancellation=true不能证明真实外放回声已消除。
