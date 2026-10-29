# 执行协议

## 产物

运行目录：`evaluation/reports/evolution/<UTC时间-随机ID>/`。

- `state.json`：冻结的 suite/代码/题库/模型配置摘要、问题台账、有效版本。
- `index.html`：固定模板总报告，每步刷新，未完成也可读。
- `round-00/report.json|html`：真实基线；旧报告重渲染不算基线。
- `round-XX/report.json|html`：候选实跑、完整返回、隔离进程启动证据。
- `round-XX/change.json`：原因、关联问题、before/after/diff、接受或回退。
- `round-XX/analysis.json`：当前 agent 的逐题归因。

分析和补丁草稿也写入本运行目录。完整回答与私有 hint 仅供本地审阅，不自动发布或写到 Talker Wiki。

## 分析文件

`record` 接收 JSON，包含 `summary`、`issues`。每个当前失败检查点都要被问题引用；一个问题可覆盖多个同源检查点。证据键固定为 `<suite-id>/<case-id或memory_chain>/<check-name>`，来自报告 `failure_keys`。

```json
{
  "summary": "用本轮证据概括失败，不把假设写成已证实根因。",
  "issues": [{
    "id": "confirmed-preference-retention",
    "kind": "memory_prompt",
    "title": "已确认的偏好未被整理",
    "evidence": ["travel-flight-booking/memory_chain/preference_persisted_in_context"],
    "diagnosis": "根据完整返回与隔离 context 证据给出解释，不照抄示例结论。",
    "hypothesis": "补充已确认偏好的整理规则，预计存储检查恢复且隔离不回退。",
    "action": "修改 memory.md 的整理原则，不写实际 Wiki。",
    "reason": "说明证据和已尝试方案；三轮未解时补充限制与人工建议。"
  }]
}
```

`kind`：`memory_prompt` / `core_prompt` / `code` / `context` / `evaluation` / `infra`。当前仅 `memory_prompt` 可关联写入，其余立即只报告。连续未解按证据键计数，改问题 ID 不能重置。非法或遗漏失败证据拒绝保存。通过报告用 `issues: []`。

## 补丁文件

```json
{
  "changes": [{
    "path": "backend/app/agent/prompts/memory.md",
    "before_sha256": "原始字节的64位SHA256",
    "content": "完整新 prompt 文本，保留现有约束及末尾换行。",
    "issue_ids": ["confirmed-preference-retention"]
  }]
}
```

拒绝：非白名单、路径穿越/软链接、内容未变、空文本、摘要不匹配、无分析、上一候选未复测、预算耗尽、非 prompt 问题、三轮未解项，以及运行以来题库/代码/实际记忆变动。保留工作区原有改动，以初始化字节摘要为准；禁止 git reset/checkout。发现并发修改停止，不自动抹掉用户工作。

核心 prompt 独立入口尚不存在，不能给补丁指定 core.md。未来如用户授权实现入口，应在独立开发任务验证运行时绑定，再更新白名单；不能在自进化轮次自行加入口。

## 比较与停止

- round-00 不计预算，默认 3 个候选；用户可用 `--max-rounds` 设置总预算。
- 每轮固定题库、suite 集合、评分和模型配置，不挑题提分。
- 接受候选要求：总分不下降、原先通过的全部检查点仍通过、无 runner error。人工量表仍待复核。
- 回退仅写本轮保存的 prompt 原文，再校验磁盘仍等于候选摘要，避免覆盖并发修改。不为回退后的版本伪造新分数。
- 同一检查点连续三个优化轮次失败后冻结；报告展示连续失败次数、针对性修改次数、每轮证据与最新原因。
- 启动/运行异常不伪造零分或通过。保存错误、关闭隔离进程、回退候选、刷新 HTML，非零退出；环境恢复后新建运行。
- 隔离 Wiki 是测试夹具。实际记忆保护默认 `backend/data/` 及初始化时显式设置的 `DUET_DATA_DIR`，不跟随软链接。不得让隔离测试写进这些目录。

## 本地验证

```bash
backend/.venv/bin/python -m unittest discover -s evaluation/skills/eval-driven-self-evolving/scripts -p 'test_*.py'
```

测试用临时仓库与假评测，不调用真实模型，不改实际 prompt。使用历史真实 JSON 渲染示例时必须注明没有新增实跑。
