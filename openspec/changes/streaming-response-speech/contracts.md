# 拟定响应流与音频协议

本文件是待实现合同，字段不表示当前服务已支持。ASR 输入仍用既有 protocol=3 / DVP3，不复用输出协议号。

## 1. 内部流事件

```text
ResponseEvent:
  response.started(response_id, question_ids, output_generation, reply_kind)
  content.delta(private_text)                # 内部候选，禁止直接对 Talker 转发
  tool.requested(job_id, allowed_arguments)  # 控制流，不可当正文
  tool.state(job_id, state, safe_progress?)
  generation.completed
  generation.failed(reason_code)
```

聚合器消费 content.delta，验证后产生 Segment。任何工具参数、模型 reasoning 或私有标记都不能混入 text_delta。

```text
Segment:
  response_id, segment_id, segment_seq, reply_kind
  question_ids, output_generation, question_revision
  text, char_start, char_end
  applicable_hash, candidate_hash
  text_status: committed|withdrawn
  audio_status: queued|synthesizing|sending|played|cancelled|failed
  audio_context_id?, sample_rate?, sample_count?, timestamps?
```

## 2. 对外语义事件

所有事件含 session_id、单调 event_seq、response_id、output_generation；服务端按角色投影。Hinter 可另看私有策略原因，不能借公共事件字段泄漏。

| 事件 | 关键字段 | 语义 |
| --- | --- | --- |
| `response.started` | question_ids, reply_kind | 创建一条公开 AI 气泡，不表示全文完成 |
| `response.segment` | segment_id, segment_seq, char_start/end, text_delta | 只追加已校验完整单元；按序幂等，不公开候选 |
| `response.text_done` | last_segment_seq | 后续不再追加正文；不表示播完 |
| `response.interrupted` | reason_code, preserved_text_end, next_output_generation | 停止剩余段，保留已公开前缀；原因必须是公开中性文本 |
| `response.withdrawn` | response_ids, next_output_generation | 清显示/音频，只留占位，持久 tombstone |
| `response.failed` | public_error, partial | 保留允许前缀，不整答 reset |
| `audio.segment_start` | stream_id, segment_seq, sample_rate, format=pcm_s16le | 宣告音频身份/格式，后续 binary 可关联 |
| `audio.segment_end` | stream_id, segment_seq, final_sample_count | 该段发送结束，不代表已播完 |
| `audio.response_end` | last_segment_seq | 服务端无后续音频；客户端排程可能尚未清空 |

消息模式复用现有 SSE/WS 传递文本事件；需要边读边播时建立轻量 reply-audio WS，仅订阅已创建的 response，不为文本消息开启麦克风。上传语音仍是上传转写，不冒称实时电话；回复声音使用同一增量订阅。浏览器拒绝自动播放时显示手动播放，文本继续公开，不能伪造 played ACK。完整音频文件后台归档，非必要前置。

## 3. 输出音频 protocol 4（拟定）

握手分别协商 `input_protocol=3`、`output_protocol=4`；输出独立版本可用于无麦克风的消息播放订阅。二进制头固定 32 字节，小端：

| offset | 类型 | 字段 |
| --- | --- | --- |
| 0 | ASCII[4] | `DVA4` |
| 4 | uint32 | output_generation |
| 8 | uint32 | stream_id（会话内递增映射 response_id，重新连接重新声明） |
| 12 | uint32 | segment_seq |
| 16 | uint32 | frame_seq（该段从0递增） |
| 20 | uint64 | sample_offset（该段从0，单位单声道样本） |
| 28 | uint16 | sample_count |
| 30 | uint16 | flags（首版0，保留） |
| 32 | bytes | PCM，长度必须为 sample_count × 2 |

sample_rate 由已接收 `audio.segment_start` 指定，当前 segment 内不可变。旧 generation、撤回 response、未知 stream_id、重复 frame、错误长度/offset 不得播放；缺帧不能补静音伪装完整送达，标该段不完整并停止/请求可用恢复。单连接 WS 已有有序传输，序号用于重连/排程/实现错误检测，不构建不必要的媒体重传栈。计数耗尽前停止并重建连接，不允许 uint32 回绕复活旧帧。

客户端单个活动播放队列，约120ms排程，接到取消立即 stop 已调度 AudioBufferSource 并清 meter/timers；控制消息优先于尚未发出的音频，服务端发送锁内不做推理或整段 sleep。同一 response 后续段持续播放，不每段等待新的用户点击。

旧输出只有 epoch 前缀，缺 response/segment 身份。发布新前后端时拒绝未协商的新会话，提示刷新；活动旧会话在版本切换边界正常结束或明确重连，不热切为混合帧，不维护两套永久 publisher。

## 4. 真实播放确认与重连

```text
playout.ack:
  response_id, segment_seq, output_generation,
  played_sample_offset, complete, client_audio_time
```

ACK 单调、幂等、不得超已发送样本；按 AudioContext 已经过的播放位置发，不在收到段时发。确认完整短句才能推进整句 spoken_prefix；有供应商词级时间戳才可定位已说词。中断时无对齐的半句标 partial_unknown，保留实际样本范围，不用文本比例猜词。

快照携带公开 committed segments、text_done、音频状态、last_event_seq 和 tombstone；不携带候选 token/私有验证。重连先对齐快照，默认不重播旧音频。缺 ACK 标 delivery_unknown，不能当已听，也不能自行全部补播。重复旧事件不能重复追加文本或音频。

## 5. 成功标准

```text
first_segment_committed_at < llm_completed_at
first_substantive_audio_sent_at < llm_completed_at   # 足够长的输出样本
client_play_started_at < llm_completed_at           # 实际实听证据
```

以上是行为验收，不要求新建测试题库；使用一个明显长于首句的正常回答和一次途中插话即可观察关键事实。性能目标及软/硬等待分别见 design，不将发送时间冒充用户实际听到时间。
