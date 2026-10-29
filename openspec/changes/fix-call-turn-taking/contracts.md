# 轮次、校正与事件契约

本文件描述拟实施协议；当前运行时仍使用旧 `transcript {text,final}`。新call协商 `turn_protocol=2`，同一call不能混用两种提交语义，具体实现不影响PCM连续媒体格式。

## 轮次记录

```typescript
type InputTurn = {
  call_id: string;
  turn_id: string;
  revision: number;
  state: "collecting" | "waiting_pause" | "finalizing" | "refining" |
         "committed" | "cancelled" | "needs_clarification" | "input_limit";
  first_sample: number;           // 16kHz 单通道 sample index
  last_voiced_sample: number;
  audio_end_sample?: number;
  segments: { id: string; revision: number; text: string; final: boolean }[];
  raw_transcript: string;
  final_asr_text?: string;
  corrected_text?: string;
  transcription_status?: "corrected" | "degraded" | "needs_clarification";
  message_id?: string;            // commit前不存在
};
```

- `(call_id, turn_id, revision)` 是同次输入的版本键；response epoch仅用于输出取消，不充当输入revision。
- `audio_end_sample`、VAD silence覆盖范围及ASR flush均以服务器接收的采样范围关联；monotonic用于超时与处理耗时，不跨机器直接相减墙钟。
- 内部segment final只能追加该segment一次；同segment partial按revision替换。保留停顿处单词边界/空格；不得把不同音频范围的相同措辞误去重为一次。
- 掉线/静音/挂断不伪造静音封存；挂断时未提交turn标记cancelled并明确未提交，不迟到自动发起回答。
- commit原子关联一条Talker消息，问题拆分来源是该消息的最终文本；音频对齐只能使用有依据的范围，不伪造LLM校正后的精确词级时间戳。

## TranscriptRefiner 输入与结果

```json
{
  "call_id": "...",
  "turn_id": "...",
  "revision": 3,
  "language": "en",
  "raw_transcript": "CAN YOU EXPLAIN DAU AND API LIMITS",
  "final_asr_text": "Can you explain DAU and API limits?",
  "quality": {"final_asr_status": "ready", "uncertain_spans": []},
  "recent_caller_text": []
}
```

```json
{
  "text": "Can you explain DAU and API limits?",
  "status": "corrected",
  "unclear_spans": [],
  "edits": [{"kind": "case_and_punctuation", "source": "final_asr"}]
}
```

结果由应用附加route/model/耗时，模型不得自行写入call/turn/revision或改变它们。JSON解析、结果长度和slot检查均在接收方处理。`recent_caller_text`最多2条、合计400字符，只取当前call已提交且未隐藏的Talker原话。超长当前turn不能静默截断后称为完整校正；按输入上限进入明确状态。

`unclear_spans`含原文位置、候选及类别（number/id/negation/inaudible等），不是模型自报百分比准确率。来源缺失或候选冲突时不能盲信结果；needs_clarification提交一条带不确定状态的用户消息，仅触发简短澄清，尚不创建/执行猜测出的业务查询。

## 服务端到浏览器控制事件

| 事件 | 必要字段 | 行为 |
| --- | --- | --- |
| `input.speech_started` | call/turn/revision, sample | 临时字幕状态变为正在聆听，不立即取消输出 |
| `input.transcript` | call/turn/revision, segment_id, segment_revision, text, segment_final | text为整轮累积的临时文案，用于替换唯一字幕槽；分片字段仅表示内部识别边界，不直接append气泡 |
| `input.waiting_pause` | call/turn/revision, last_voiced_sample, required_silence_ms | 表示仍在等用户说完，不显示大倒计时面板 |
| `input.refining` | call/turn/revision | 显示小号“整理中”，尚无正式消息 |
| `input.committed` | call/turn/revision, message_id | 刷新正式气泡；只清除同revision临时字幕，不能清掉较新字幕 |
| `input.degraded` | call/turn/revision, reason_code | 原位提示未完成校正或需重说，保留可靠原文 |
| `input.cancelled` / `input.limit_reached` | call/turn/revision, reason_code | 明确未提交/需暂停，旧任务不能复活 |
| `interruption.confirmed` | call/turn, interrupted_response_id, next_output_epoch | 立即停播并清队列；无需等待3秒 |
| 既有 `stop` / `retracted` / `ended` | 保留现有字段 | 显式控制继续优先生效 |

事件按per-call输入序号排序。浏览器丢弃旧call/旧turn revision，不能将两轮partial拼到一起。`interruption.confirmed`与其兼容`stop`事件引用同一次服务端epoch推进，重复到达不得再增epoch或重复取消。浏览器原 `speech_start` 在协议2中只可作非权威提示或忽略，绝不能再独立触发 `discarded=epoch`、silence或服务端interrupt。二进制下行仍按response epoch拒绝旧音频。

## 配置建议

| 配置 | 初始值/约束 |
| --- | --- |
| `CALL_TURN_END_SILENCE_MS` | 3000；只控制提交轮次 |
| `CALL_VAD_THRESHOLD` / `CALL_VAD_NEG_THRESHOLD` | 0.6 / 0.35；实施实听后调整 |
| `CALL_VAD_MIN_SPEECH_MS` | 96；有效语音起点去抖 |
| `CALL_BARGE_IN_MIN_SPEECH_MS` | 300；配合新的可辨识词 |
| `CALL_BARGE_IN_NO_TEXT_MS` | 800；ASR滞后时持续人声确认 |
| `CALL_FINAL_ASR_MODEL` | small.en，CPU int8，独立于旧上传tiny默认值 |
| `CALL_FINAL_ASR_SPECULATE_SILENCE_MS` | 800；最多一项当前revision本地预结果，不提前提交 |
| `CALL_FINAL_ASR_WAIT_MS` | 1500；封存后的额外等待上限 |
| `CALL_TRANSCRIPT_MODEL` | 启动时解析到固定的现有非思考模型配置 |
| `CALL_TRANSCRIPT_TIMEOUT_MS` | 4500；整个请求截止，max_retries=0，无池级串行重试；实施实测后由初始1500调整 |
| `CALL_MAX_TURN_AUDIO_SECONDS` | 60；明确提示超限，不自动截断回答 |

以上参数已接入运行时，初始值不是已验证的P95。当前模型服务少量实测为约1～4秒，故校正默认上限调整为4500ms；完整ASR封存后等待仍为1500ms。VAD开启为96ms去抖、退出采用约100ms音节间隙缓冲，不把短词间空隙当作新轮次。
