# Quickstart：双通道网页 Agent

```bash
./scripts/build.sh
./scripts/start.sh
```

以上命令在仓库根目录执行。页面：`http://127.0.0.1:8818`；运行时接口文档：`/docs`。

1. 进入 Talker，输入问题或载入示例对话。
2. 点击“打开 Hinter”，在同一 Session 中选择问题生成英文预回答，可附带私有提示；也可开启 **AI 模式**，让每个问题自动拟答。
3. Hinter 查看预回答，可直接编辑、输入 Hint 让 AI 润色，或取消发送；只有点击“确认发送”后 Talker 才收到英文答案。确认后自动生成并尝试播放英文语音，支持暂停、继续和重播。
4. Hinter 可逐题或批量“拒绝回答”；Talker 会收到 `Sorry, I dont know.`，拒绝事项记录在两个用户的 Wiki 中。
5. 问题列表按等待时长排序；超过 3 分钟标橙、超过 6 分钟标红。取消草稿不重置等待时间。
6. 在“个人 Wiki”编辑 `context.md`、轮次文档和主题记忆。Hinter 点击“删除全部 Wiki”后，必须输入 `我已确认删除所有的Wiki信息` 才会清空两个用户的 Wiki。
7. 点击“新建对话 / 清屏”，两窗口同步进入新 Session，用户 Wiki 保留。

模型配置在 `.env` 中维护，支持主模型与有序回退，也可使用本地 Ollama。导入参考 Go 配置的方式、Docker 一键部署、Agent 分层和验收记录见 [技术实现文档](../../docs/technical-implementation.md)。

```bash
./scripts/docker-up.sh
./scripts/docker-down.sh
```

部署脚本根据 `.env` 自动选择外部模型或本地 Ollama。默认端口 8818，数据保存在持久卷中。
