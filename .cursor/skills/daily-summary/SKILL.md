---
name: daily-summary
description: 汇总当日学习时长、Cursor 对话、系统改进、学习内容与按小时时间统计，写入 plans/summaries。在学生说今日小结、每日小结、学习总结、/daily-summary 时使用。
---

# 每日小结

## 何时使用

学生说「今日小结」「每日小结」「学习总结」，或执行斜杠命令 `/daily-summary` 时。未指定日期则用当天（本地日期）。

## 步骤

1. 读取 `profile/student.yaml`、`profile/mastery-policy.yaml`、当日 `plans/days/YYYY-MM-DD.json`、`plans/current-week.md`、`mastery/summary.yaml`。
2. 跑收集脚本（输出 JSON，含做题 + Cursor 对话）：

```text
python .cursor/skills/daily-summary/scripts/collect.py YYYY-MM-DD
```

3. **时长口径**（必须三行都写）：
   - 做题：`totals.study_label`（作答时间轴合并，间隔 ≤ 15 分钟）
   - Cursor 对话：`totals.chat_label`（用户消息时间轴，间隔 ≤ 30 分钟；来自本机 `agent-transcripts`）
   - 合计投入：`totals.combined_label`（做题 ∪ 对话，重叠时段不重复计）
   - 纯作答：`active_label`（单题封顶 10 分钟）
   - 不计 0 次作答的空会话；不计系统自动的 “Briefly inform the user” 消息
4. **按小时统计**用 `hourly[]` 四列：做题 / Cursor 对话 / 合计 / 纯作答。只列出有数据的小时。
5. **Cursor 对话内容**：读 `chats.sessions`。每条写时段、条数、`kind`（study 学科 / system 系统 / mixed）、一句话主题。系统改进必须能对上这些对话，禁止只根据 git log。凭据、Token、密钥不得写入小结。
6. **学习内容**：对照日计划、当日卷、`mastery` 当日 `last_assessed`。每科写 `knowledge_id`、等级、首次正确率、错因。
7. 写入 `plans/summaries/YYYY-MM-DD.md`（覆盖同日旧稿），对话里用同一结构发给学生。
8. 文末 1～3 条明日建议（具体 `knowledge_id` + 中文名）。

## 落盘模板

```markdown
# YYYY-MM-DD 每日小结

## 整体学习时长

- 合计投入：…（做题 ∪ Cursor 对话，重叠不重复计）
- 其中做题：…（N 个学段）
- 其中 Cursor 对话：…（M 条用户消息 / K 个对话）
- 纯作答：…

做题学段：

- HH:MM–HH:MM（时长）

对话时段：

- HH:MM–HH:MM（时长）

## 按小时统计

| 时段 | 合计 | 做题 | Cursor 对话 | 纯作答 |
|------|------|------|-------------|--------|
| HH:00–HH:59 | … | … | … | … |

## Cursor 对话

- [短标题](会话id)：HH:MM–HH:MM，N 条，学科/系统。一句话。

## 学习内容

### 化学 / 数学 / …

- …

## 系统改进

- 条目须能对上上面的对话，不写密钥

## 明日建议

- …
```

## 禁止

- 只统计做题、不读 Cursor `agent-transcripts`
- 把超长挂机整段算进纯作答
- 把密钥、Token 写入小结
- 修改 mastery
- 做 README、写测试
