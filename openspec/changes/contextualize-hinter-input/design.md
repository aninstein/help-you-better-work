# 设计

## 三部分上下文
1. `original_messages` 和 `extracted_question`：保留 Talker 原始发言与拆题后的题目。
2. `assistance_request`：问题概述、人工需要提供的具体事实或帮助、答案合成要求。这是发给 Hinter 的私有补充任务，不是重新询问 Talker，也不是预设答案。随现有 planner 一次返回；历史题目使用与原题绑定的保守说明，不多走模型。
3. `hinter_input` / `input_kind`：人工原文及其类型，事实用于填补问题，禁止事项用于限制表达，不把禁止事项误当成数据。

## 合成和绑定
Guidance 保存 `binding`，记录原问题 ID、源消息及补充任务。生成器在 `turn_context.human_assistance` 中读取三部分上下文；约束快照和私有 Wiki 投影也写入该关联，避免只有孤立的数值。修订保留题目绑定，撤销后不再纳入生成。绑定的补充事实使用原有 resolve/correct 路径触发补答，不额外调用 planner 重新解释裸数值。公开回复不暴露任务、Hint 通道或私有文档。

## 两个例子
- 原题：`how mach DAU of tiktok?`；任务：用户询问 TikTok DAU，请提供对应数值；Hint：`200`；输出：`TikTok's DAU is 200.` 不擅自增加 million 或统计日期。
- 原题：`How much efficiency does AICO product bring?`；任务：用户询问 AICO 的效率提升，请提供提升比例；Hint：`50%`；输出：`AICO improves efficiency by 50%.`

## 范围与歧义
明确选择题目优先。没有选择时，仅一个待补题可承接裸数值；同时存在多题时保持输入，提示选择对应题。批量选题不能把一个无主体数值复制为所有题的答案。完整会话提示沿用原范围。新生成的任务不改变普通问题的路由策略。

问题、任务和 Hint 继续属于当前 Talker/Session 的适用范围，私有任务不投影到 Talker 页面。禁止事项回查接收含原题关联的 Hint，不能把“这个不能说”错误扩展到全部主题。资料只通过现有参考检索内容进入回复，不重复塞入整份长文。
