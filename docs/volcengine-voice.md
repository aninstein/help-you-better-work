# 火山实时语音接入

## 配置与启动

在项目根目录的本地 `.env` 填写一个变量（已经预留空值）：

```dotenv
VOLCENGINE_API_KEY_NAME=控制台里的名称_仅用于本地标记
VOLCENGINE_API_KEY=填入豆包语音新版控制台的API_Key
```

名称是可选的本地标记，用于区分所配置的 Key；官方接口通过 `X-Api-Key` 鉴权，不传名称。

已有服务运行时，更新 Key 后执行（只重载 Web，不重建镜像）：

```sh
./scripts/reload-voice.sh
```

首次完整部署仍使用 `./scripts/docker-up.sh`。重载 Web 会断开当前语音连接，可重新拨入；已提交的 Agent 任务继续执行。

访问 `http://127.0.0.1:8818`。Compose 的 `env_file` 将配置传给服务，Key 不进入前端构建。`VOLCENGINE_APP_KEY` 是该变量的兼容别名；此处需要**豆包语音控制台的 API Key**，不是火山账号 IAM AccessKey。Key 所属项目需开通并授权流式识别 2.0、语音合成 2.0，对应资源和默认音色可用时只填 Key 即可。旧版控制台可另配 `VOLCENGINE_APP_ID` 和 `VOLCENGINE_ACCESS_TOKEN`。

默认 `VOICE_ASR_PROVIDER=auto`、`VOICE_TTS_PROVIDER=auto`：有 Key 即选火山，没有 Key 时保留本地通话/朗读回退。实时语音输入需要火山；未配置时直接提示配置，不冒充实时识别。已选择火山但鉴权/网络/额度失败时，会显示错误，不偷偷切换供应商。也可显式设置 `volcengine` 或 `local`。

| 可选配置 | 默认值 | 用途 |
| --- | --- | --- |
| `VOLCENGINE_ASR_RESOURCE_ID` | `volc.seedasr.sauc.duration` | 流式 ASR 2.0 时长版 |
| `VOLCENGINE_ASR_END_WINDOW_MS` | `800` | 判停窗口，600–3000 ms |
| `VOLCENGINE_ASR_LANGUAGE` | 空 | 使用产品默认中英文模型；其他语种按服务商支持范围配置 |
| `VOLCENGINE_TTS_RESOURCE_ID` | `seed-tts-2.0` | 语音合成 2.0 |
| `VOLCENGINE_TTS_SPEAKER` | `zh_female_vv_uranus_bigtts` | 可替换为已授权、与资源匹配的音色 |
| `VOLCENGINE_API_KEY_FILE` | 空 | 可选服务端 Key 文件，优先于环境变量；路径必须在容器可读挂载内 |
| `AGENT_RUN_TIMEOUT_SECONDS` | `120` | 单次任务总时限，30–1800 秒 |
| `AGENT_RUN_IDLE_TIMEOUT_SECONDS` | `45` | 无模型/工具进展时限，至少 15 秒，最多总时限 |

## 两种交互

- **通话**：默认显示底部声波条，隐藏文本输入框；可展开圆环、静音或挂断。火山临时结果实时显示字幕，二遍识别的稳定分句才写入 MySQL 并创建 Run。采集、识别、Agent 与朗读分别推进；挂断不取消已提交任务。
- **实时语音输入**：点击麦克风后边说边在输入框更新临时草稿；再次点击结束并等最终识别校正，之后可手工修改并发送。中间字幕不创建 Run。校正不是让 LLM 猜测用户想说什么，不改变原意；超时/断线保留已显示的草稿。
- 两种模式复用 `useSpeechStream` 采集；会话/账号切换释放录音，缩小/展开不重建录音。协议保留 PCM16/16 kHz 和采样序号检查，检测缺口后提示重录。
- 火山连接约每 180 秒完成尾包并滚动建立下一段通话识别；语音输入单段最多约 180 秒，结束后回到可编辑草稿。稳定分句按连接和时间边界去重，避免临时结果/重复返回创建重复任务。

当前 `/voice/transcribe` 上传兼容接口也会遵循所选 ASR 服务；新版输入框已不使用录完再上传的方式。

## 排队问题

此次现场查询发现：截图中的用户文字已经入库，等待的是 Agent 回复。Agent worker 日志有联网搜索超时和反复调用导致的长轮次；同会话按顺序处理，前一条未结束时后续都会排队。

处理方式：

- 独立子进程执行每个 Run，由父进程监督总时长、无进展时长、撤权与取消；超时真正终止执行进程，释放当前会话继续下一条，不只修改前端状态。保留 MySQL checkpoint/fencing 与部署后的恢复能力。
- 文本模型网络超时 30 秒，不做 SDK 自动重试；搜索使用单个后端并将失败作为明确结果返回。提示词要求搜索失败不无限重试，不把历史问题每轮重查，不编造实时结果。
- 前端分别显示“转写”和“等待 Agent 回复”，显示前方任务数、搜索等工具进展；仅拉取运行任务的增量事件，排队任务不逐个额外请求。失败记录可以放回输入框编辑后重发。会话接口保留全部未完成任务，避免被“最近 50 条”截断而看不到前面正在执行的任务。

## 协议与凭据边界

- ASR：服务端连接 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，使用官方 v3 大端二进制帧、gzip、PCM、最终尾包；`enable_nonstream=true`、`show_utterances=true`、`enable_punc=true`。`definite=false` 只上屏，定稿后才进入业务记录。
- TTS：服务端调用 `https://openspeech.bytedance.com/api/v3/tts/unidirectional`，校验流式 JSON 结束码，解码 MP3 并保存为有会话访问控制的附件。失败不发布半截音频。
- 浏览器仅连同源鉴权 BFF WebSocket，使用 HttpOnly session cookie + Origin 校验；没有 Key、token URL 参数或前端供应商直连。
- 没有配置真实 Key 时不能完成云端识别/音色/额度验收；健康就绪不代表云账户已开通。

官方依据：[双向流式识别](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)、[稳定分句与二遍识别](https://docs.volcengine.com/docs/DoubaoVoice/Accessmust-read_1?lang=en)、[HTTP 流式合成](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh)。

构建支持 `DEBIAN_MIRROR` 参数（镜像站根地址，例如 `https://mirrors.tuna.tsinghua.edu.cn`），由 `.env` 经 Compose 传入；APT 下载设置超时和重试上限。本机已选择实测可达的镜像站，避免软件源长期挂起。

## 本次部署记录

TypeScript/Vite 生产构建、改动 Python 的 Ruff/语法检查、UI 静态审查与 `git diff --check` 通过。实际 `MeetingCall` 组件在模拟状态预览中显示紧凑通话条、隐藏输入区，并在挂断后恢复输入区；未采集真实麦克风。

已部署至 8818，Web、Agent worker、投影 worker 和 MySQL 均 healthy，`/readyz` 返回 ready / schema v5。现场检查时交互任务没有 queued/running 项。首次部署检查时尚未配置火山 Key，服务报告 local 回退；当时未做真实云端识别/音色/计费或麦克风端到端实测。本轮没有新增或运行自动测试。

后续已将用户提供的 Key 名称与 Key 写入本地被 Git 忽略的 `.env`，并显式选择火山 ASR/TTS；重载 Web 后配置已载入、服务就绪。名称仅作本地标记，密钥不写入本说明。本次配置操作没有额外发起付费语音识别或合成请求。

## 通话立即退出的现场诊断

填入 Key 后首次实际排障发现：Web 接受了通话连接，但火山 ASR 握手返回 HTTP 403 / requested resource not granted，TTS 请求也返回资源未授权。它说明当前 Key 所关联项目未获所请求语音资源的访问授权；需要在控制台核对流式识别 2.0 小时版、语音合成 2.0 的服务开通及项目权限，不能以本地配置就绪代替远端授权。

已将该拒绝原因映射为独立的中文服务权限错误，补齐官方请求标识头；通话结束后的错误由语音状态单独保留，不再被会话轮询清空。构建和静态检查通过并重载 Web。远端语音联调仍受上述权限限制。

## 更新 Key 后的真实联调

用户完成语音产品开通并提供新 Key 后，已替换本地凭据并重载 Web。使用隔离的验证账号与独立会话，将一段火山合成的短语音按实际音频时钟回放到同源 BFF：

- 火山 TTS 成功返回完整 MP3；ASR 连接建立成功。
- 语音输入模式在录音回放过程中收到多次临时识别，最终仅一份校正草稿，未创建 Agent 任务/记录。
- 通话模式收到多次临时识别与一条稳定最终转写，创建一条语音用户记录和一条 Agent 任务，任务完成并产生一条回复。
- 回复经火山合成后，有权限控制的音频附件返回 HTTP 200。

测试样句为“这是一次语音连接检查，请只回答收到。”，未使用真实麦克风或用户历史音频。先前的资源未授权错误在新 Key 对应的开通状态下未复现。凭据不记入文档或测试报告。

## 回复延迟与噪声打断调整（2026-10-25）

此前后端虽然使用火山流式接口，却先收集整段 MP3、保存附件，再将文件链接交给前端。现改为 `POST /records/{id}/speech?stream=true` 快速签发当前登录会话绑定的单次短期票据，浏览器通过返回地址边接收边播放；兼容下载接口仍可返回完整附件。音频结束后才保存附件，不阻塞首包播放。票据在 Web 进程内有效，当前部署为单 Web worker，重启期间未开始的旧票据需重新点击播放。

取消播放会清理播放器 src，终止下载和上游流，而非只暂停扬声器。错误显示后允许手动重播，不在后台自动反复消耗额度。流中每隔三秒复核账号、记录修订及访问权限。

原先一帧 RMS > 0.02 就触发打断，512 samples 对应约 32ms，键盘或碰撞声也会触发。现在同时要求持续声音与新的识别文字；普通插话默认至少 480ms，中文至少四字或其他语言至少两词，明确停止指令可短一些。重复的累计字幕不会再次打断；界面倾听状态也使用确认后的插话状态。独立的“嗯、呃、um”等填充音现在由统一语音过滤规则拦截，不再仅限于播放期间或短于 1.5 秒的情况。仍启用浏览器回声消除、降噪与自动增益。这是保守的规则门控，不等同于说话人身份识别，持续的背景人声仍可能影响转写。

接口依据：[火山 HTTP Chunked/SSE 单向流式](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh)。插话的持续时间与识别文字分开判断，参考 [LiveKit turn-taking tuning](https://docs.livekit.io/agents/logic/turns/tuning/)。这些资料不代表本机已经测得具体首包延迟或噪声准确率。

## 无意义转写拦截

统一实现现位于 `backend/app/employee/input_preprocessing/`，`voice_text.py` 保留兼容导出与排版。规则先丢弃明确填充音、重复噪声和孤立引导语，再由一次受限模型判断未知短语音的意义与主要语言；不按字数直接丢弃。数字、短答、否定、停止指令保留，原始内容不改写。云端分句定稿、本地转写和统一 `transcript` 提交均在创建记录前拦截，因此无效片段没有气泡、Run、投影或 TTS。云端接收字幕与输入确认分开执行；待判断的短片段不通过 partial activity 打断播放，明确停止指令保留快速路径。语音输入的中间结果仍是草稿，提交时才做语义判断。模块、重试与独立快速模型配置见 [输入预处理层](input-preprocessing.md)。

HTTP transcript 提交被忽略时仍保存幂等响应（`status: ignored`，`record_id/run_id: null`），不保存聊天内容或占用会话序号。前端收到忽略结果后不会创建虚假的排队卡片。

## 缓存短回应与正式答复

语音分句通过噪声过滤并写入 MySQL 后，WebSocket 立即返回 `transcript.final`（携带 run/record 和 acknowledgement 元数据），不等待附件保存、主模型或正式 TTS。Web 服务启动后后台生成并缓存中英文固定短句到数据卷 `voice-cues/`，通话连接时预取并解码；播放由现有 AudioContext 持有。`GET /api/v1/voice/cues/{locale}` 需要登录，返回固定短句音频，缓存按服务商/音色/资源和文本区分，不包含凭证或用户内容。

短句不是正式回答，不产生额外聊天记录或 Agent 任务。每个 Run 去重并有五秒间隔；正式回复、真实插话及挂断取消它，缓存加载超时则跳过而非延后播放。实际播放报告绑定到该连接已接受的 Run，在三秒内排除匹配固定短句的回声。中文与英文由启动预热，其他已配置文字脚本在首次使用时缓存，不能保证冷启动同样快。

正式答案保留最后模型回答，去除工具阶段的规划和内部编号，朗读也使用同一公开文本。
