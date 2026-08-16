---
name: weekly-plan
description: 根据 mastery summary 生成或调整初三一周学习计划，写入 plans/current-week.md。在学生要周计划、本周安排、复习规划时使用。
---

# 周计划

## 何时使用

学生要求「本周计划」「安排复习」「下周学什么」时。

## 步骤

1. 读取 `profile/student.yaml`、`mastery/summary.yaml`、各一期科目 mastery（至少看 summary 薄弱列表）。
2. 读取或创建 `plans/current-week.md`。
3. 规划原则：
   - 每天 1 门主攻 + 1 门复习（与 `learning_preferences` 一致）
   - 优先 `exam_weight: high` 且 level ≤ L2 或 `wrong_count` 高的点
   - 语数英物权重高；化学虽折算仍保持每周至少 1 次触达
   - 单日总学习建议不超过学生可承受时长（默认参考 session 45 分钟 × 2）
4. 写入 `plans/current-week.md`：日期范围、每日主攻/复习、对应 `knowledge_id`、建议考核类型。
5. 可选：在 `sessions/` 追加一条简短会话摘要（用日期文件名）。

## 输出给学生

- 本周目标一句话
- 按日表格或列表
- 本周结束时的验收方式（例如周日综合小测）
