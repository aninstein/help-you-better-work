# 版本化评测报告

本目录保存已审核的 JSON、HTML 与 Markdown 评测证据，便于离线查看历史结果、评分口径与迭代趋势。

## 阅读入口

- [综合评测与自更新轮次报告（Markdown）](综合评测与自更新轮次报告.md)
- [综合评测与自更新轮次报告（HTML）](综合评测与自更新轮次报告.html)
- [早期单套件迭代预览](evolution-preview.html)
- [完整迭代轮次](iterations/)

## 报告类别

| 名称模式 | 含义 |
| --- | --- |
| `dual-channel-eval-*.json/html` | 隔离服务真实模型实跑的原始评分证据；同时间戳的 `-rescored` 是使用更新后 catalog 的离线重评分。 |
| `iterations/<round>/` | round-00 至 round-15 及专项轮次的完整 JSON/HTML，记录每轮分数、模式分布、失败判据与结论。 |
| `user-feedback-*.json` | 新自动回答链路的确定性回归执行记录，包含场景数、通过数、pytest 退出码及限制说明。 |
| `综合评测与自更新轮次报告.*` | 基于迭代原始 JSON 汇总生成的人工复盘入口。 |

## 提交边界

只提交可复现的 JSON、HTML、Markdown 与目录结构。`server.log` 可能含本地短期签名 URL、会话查询参数或运行诊断，永远保持忽略。也不要把 API key、`.env`、SQLite、音频、原始用户录音或服务运行日志加入本目录。

重新生成、筛选和复评分法见 [评测说明](../README.md)。
