# 双通道客服 Agent 评测集初检报告

- 初检时间：2026-10-22（Asia/Shanghai）
- 检查对象：[catalog.json](../catalog.json)、[README.md](../README.md)、三个执行脚本
- 题库版本：`schema_version: 1.0`
- 范围：20 个场景包；每包 5 条主链路和 1 条跨会话记忆链
- 结论：🟢 **初检通过（可作为本地 Demo 的评测输入）**

本报告是入库前可运行性与评审性检查，不代表模型在所有题上已经通过。结构检查结果见 [catalog-validation.json](catalog-validation.json)。

## 批量检查结论

| 检查项 | 结论 | 证据 |
| --- | --- | --- |
| Query 清楚、材料完备 | 通过 | 每条 `case` 固定 Talker 输入、Hinter 动作/私有 hint、预期拆题数、状态、关键术语及人工量表；每个场景提供 `talker_context`。题目使用虚构地点/人物/订单，不依赖外部订单、病历、航班库存或在线文档。 |
| 多轮传递 | 通过 | 每个 `memory_chain` 包含首问、hint、正向反馈、跨新会话的 recall 问题与术语断言；运行器实际调用 reset 后再追问。 |
| 角色与安全边界 | 通过 | 阻断题明确 `reject`、禁止输出对象和安全替代；运行器分别检查“无 Answer”及 Talker 投影中无私有审核字段。 |
| AI 起草残留 | 未发现明显残留 | Query 和量表没有“作为 AI”“填入 XXX”“请按模板生成”等遗留指令；所有业务实体是合成示例。 |
| 工作场景与价值 | 通过 | 差旅、酒店、医疗前台、项目会议、学习支持、订单、支付、账号安全、网络、保险和订阅均对应客服/协作/服务支持中的实际决策与分流。 |
| 得分点与 Query 匹配 | 通过 | 每条都有可复现的 `expected` 和至少 3 条 `manual_rubric`；自动证据与人工质量判断分开，避免把关键词命中当成唯一高分答案。 |
| 在线材料 Owner | 不适用 | 本题库为本地 JSON/Markdown 和脚本，不包含需核验 Owner 的线上文档。 |

## 覆盖与可运行性

离线校验已实际执行并通过：

```text
20 suites
100 atomic cases
20 memory chains
direct / hinter_hint / hinter_block / multi_question / rapid_burst: 各 20 条
```

场景组分布：差旅 3、酒店 2、医院前台 2、项目会议 4、学习上课 2、通用客服 5、金融客服 2。覆盖来自公开任务型对话资料的域设计参考；题库没有复制公开数据集的原始对话、个人信息或标注。

## 材料核验

| 材料及用途 | 可阅读性 | Owner | 结果 |
| --- | --- | --- | --- |
| `catalog.json`：题目、预期和人工量表 | 本地 JSON，可由 `json.tool` 解析 | 本地文件，不适用 | 通过 |
| `scripts/validate_catalog.py`：离线结构守门 | Python 编译通过 | 本地文件，不适用 | 通过 |
| `scripts/run_live.py`：真实 API/模型评测 | Python 编译通过；默认隔离 SQLite/Wiki | 本地文件，不适用 | 通过 |
| `scripts/render_report.py`：离线 HTML 图表 | Python 编译通过 | 本地文件，不适用 | 通过 |

## 必须修改

无。

## 非阻断建议

1. 当前自动分只验证状态、拆题、关键术语、拒绝、投影隔离和记忆回调；上线前可在 `manual_rubric` 之外接入经校准的 LLM-as-judge，专门评审事实性、同理心、是否乱承诺和 Hint 的语义遵循。
2. 真实外部系统接入后，将各场景的 mock 订单、航班、保单和政策版本作为冻结测试夹具，不能让模型依靠实时网页检索来“猜对”。
3. 阻断题应保留“安全阻断”和“Talker 友好拒答”两个分数。当前 Demo 已能拒绝和不生成，但是否对 Talker 显示拒答是产品体验问题，评测报告会单独暴露。
4. 连续输入量大时，Intake 在当前实现中串行执行；压测时单独记录每条消息的 Intake 延迟，不能仅看最终拆题数。
