# 语音 API 选型与本轮改进

核对日期：2026-10-25。人民币公开按量价；阿里采用中国内地北京地域。价格不含免费额度、资源包、活动折扣及业务 Agent 的文本模型费用。

## 价格对比

| 用途 | 火山豆包 | 阿里百炼 |
| --- | --- | --- |
| 录音文件转写 | 豆包录音文件识别 2.0：0.8 元/小时 | Fun-ASR / Qwen3-ASR-Flash-Filetrans：0.00022 元/秒，即 0.792 元/小时 |
| 通话实时转写 | 豆包流式语音识别 2.0：1 元/小时 | Fun-ASR-Realtime / Qwen3-ASR-Flash-Realtime：0.00033 元/秒，即 1.188 元/小时 |
| 文本转语音 | 豆包语音合成 2.0：3 元/万字符 | CosyVoice-v3-Flash：1 元/万字符；v3-Plus：2 元/万字符 |
| 更新的合成选项 | 同上，音色与语种按产品列表选择 | CosyVoice-v3.5-Flash：0.8 元/万字符；v3.5-Plus：1.5 元/万字符，需设计或复刻音色，无系统音色 |

价格来源：[火山计费说明](https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh)、[阿里模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。阿里 v3.5 音色限制见[实时语音合成说明](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)。不要将旧版火山 ASR/TTS 的较高价格与 2.0 混用。不要把阿里不同地域、旧版/新版或按 token 计费的模型混为同一个价格。

按上述选定产品计算：100 小时录音转写，火山约 80 元、阿里约 79.2 元，差别很小。1 小时通话输入加 2000 字符朗读，火山约 1.60 元；阿里实时识别加 CosyVoice-v3-Flash 约 1.388 元。该例固定输入用量与输出字符数，不把挂钟通话时长当成识别时长，也不假定静音不计费；实际以服务商计量和账单为准。

## 建议

优先评估“豆包流式 ASR 2.0 + DeerFlow + 豆包 TTS 2.0”，符合当前用户对豆包声音的偏好，保留 MySQL 记录、工具、权限、任务队列和记忆。TTS 相比阿里 v3-Flash 多 2 元/万字符，是否值得取决于同文本试听；本轮没有两家付费账号的声音/延迟 A/B 实测，不能据此断言音质排名或承诺固定首包延迟。阿里可作为更低 TTS 成本的备选。

文件 ASR 适合会议录音归档；通话和语音输入更适合 WebSocket 实时 ASR，在采集时输出临时字幕，收到最终分句后才提交 DeerFlow。火山可输出标点、句级结果与时间信息，并用 `enable_nonstream` 做最终二遍识别；阿里也提供实时和语义断句能力。需区别“临时字幕被修正”与“已经提交的用户记录”，最终结果只能提交一次。

技术来源：[火山实时 ASR](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)、[阿里实时 ASR 参数](https://help.aliyun.com/en/model-studio/paraformer-real-time-speech-recognition-python-sdk)、[阿里 ASR 选型](https://help.aliyun.com/zh/model-studio/asr-model)。最新阿里 Qwen-Audio-3.1 ASR 使用 token 计费，不能直接套上表的秒价；若选择该系列应按实际音频与文本 token 另行测算。

## “全语音”与多语言

当前实际实现是 ASR → DeerFlow 文本 Agent → TTS 的串联结构，并非端到端语音大模型。取消英文输入/中文提示的固定规则；输入、Hinter 提示与输出语言由用户选择，默认跟随当前用户请求。ASR/TTS 是否覆盖某种语言仍取决于选定模型、参数与音色，不能承诺所有语言通用。当前本地 Whisper 可以自动识别多语种，但现有 Edge/eSpeak 朗读回退仍只选择中文或英文音色；其他语言的自然朗读应在接入云服务时按音色支持范围配置。

火山端到端实时语音模型另按 token 收费：输入音频 80 元/百万 token，输出音频 300 元/百万 token；按官方近似换算分别约 1.8 元/输入音频小时、27 元/输出音频小时，仅是音频部分，还要计算文本、上下文/缓存等用量。它的成本、接线和能力边界与独立 TTS 不同，不能把“豆包 TTS 3 元/万字符”当作整套端到端对话的价格。换算依据同火山计费页，比例可能更新。

## 本轮代码

- 复用原 `CallExperience` / `CallSignal` 的发光圆环与紧凑声波，恢复展开、缩小、状态、时长、静音、挂断和焦点行为；切换形态不重建媒体。输入振幅来自实际 RMS，朗读状态使用呼吸提示，不伪造输入音量。
- 连接状态在请求麦克风时即显示；异步权限返回若已挂断，释放后到的音轨，避免“已挂断却继续采集”。
- 接收 PCM 与本地分句转写使用独立异步队列，避免转写期间阻塞下一段音频接收。队列有上限并显示繁忙反馈；挂断后处理已经收集的尾句，不取消已提交 Run。
- 本地识别采用自动语言识别、转写而非翻译；beam 默认从 3 调到 1，可配置。分句默认从 800 ms 改为 1200 ms，可通过 `VOICE_END_SILENCE_MS` 在 600–3000 ms 内调整。它是本地停顿策略，不宣称已具备云端语义判停。
- 保留识别器标点和实际时间边界，按完整标点/较长停顿分行，中日韩连续片段不再统一插空格；不额外调用 LLM 润色，不改写事实和数字。

本轮完成研究与本地体验改进，没有开通付费云资源，也没有将用户音频切换到新的云服务。云端 ASR/TTS 的下一步需要相应产品的 API Key、开通状态和选定音色；云凭据应配置在服务器，不进入浏览器、URL 或 Git。

## 交付检查

已完成前端 TypeScript/Vite 生产构建、改动 Python 的 Ruff/语法检查与 UI 静态审查；实际通话组件的圆环、缩小声波、静音和状态已通过独立预览查看（模拟音量，未连接麦克风）。本轮没有新增或运行自动测试，也没有云端 ASR/TTS 或真实麦克风的端到端实测。

已使用现有 `scripts/docker-up.sh` 更新本地 Docker Compose 服务：`http://127.0.0.1:8818`。Web、Agent worker、投影 worker 和 MySQL 均为 healthy，`/readyz` 返回 ready / schema v5；运行容器与本地前端构建产物一致。
