# 拟定公共内部契约

不是当前已上线 API；实施时复用既有路由并提供短期兼容。

```text
Guidance:
  id, tenant_id, customer_id, session_id?, call_id?, question_id?
  raw_text, kind[], scope: session|call|customer
  effective_from: now|next_turn, effective_turn_id?
  revision, source_id, supersedes_id?, revoked_at?
  disclosure_scope?, fact_subject?, fact_time?, verification_status?
  projection_status: pending|synced|failed

GuidanceSnapshot:
  guidance_revision, knowledge_revision, applicable_hash
  constraints[], facts[], ordinary_evidence[], source_revisions[]

PublicationDecision:
  response_id, segment_id, question_revision, output_generation
  applicable_hash, candidate_hash, committed_prefix_hash
  verdict: allow|rewrite|refuse|clarify|await_human
  matched_rule_ids[], reason_code, expires_on_context_change=true
```

PublicationDecision 是服务端一次提交的内部结果，不是客户端可复用许可。检查后到发送前再次核对 hash/ownership；版本变化废弃结果。无关知识编辑可重新计算相同 applicable_hash，不能用全局 revision 改动必然中断所有 session。

拟定 Hint 命令：`submit_hint(text, scope=session, effective_from=now, question_id?, idempotency_key)`、`revise_hint(id, expected_revision, ...)`、`revoke_hint(id, expected_revision)`。原始内容仅 Hinter 可见。

Hinter 事件：`guidance.effective(id,revision,target)`、`guidance.projection(status)`、`guidance.retrospection(status)`；ack 不等待回查/TTS/文件投影。Talker 仅收到 `response.interrupted`、`response.withdrawn`、新的公开补答，不收到命中规则原文或私有校验原因。

原有 `correct_now/add_for_next/block_target/pause_auto/resume_auto` 映射到共用命令；显式暂停单独持久状态，不从某条风格 Hint 隐式推导。任何撤回先写 tombstone 再广播，迟到段与已撤回音频读取均返回不可用。
