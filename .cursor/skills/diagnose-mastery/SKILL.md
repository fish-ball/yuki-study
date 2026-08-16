---
name: diagnose-mastery
description: 对指定中考科目做摸底诊断，定位薄弱知识点并初始化或修正 mastery。在学生要求摸底、诊断、查漏补缺、评估掌握情况时使用。
---

# 摸底诊断

## 何时使用

学生说「摸底」「诊断」「看看我会什么」「查漏」或指定科目做学情评估时。

## 步骤

1. 读取 `profile/student.yaml`，确认科目属于 `phase1_subjects`（否则先征求是否进入二期）。
2. 读取 `knowledge/<subject>/tree.yaml` 与 `mastery/<subject>.yaml`。
3. 从 `exam_weight: high` 的叶节点优先选题，覆盖各一级模块；题量控制在 15–25 分钟可完成。
4. 将摸底卷写入 `assessments/YYYY-MM-DD-<subject>-diagnose.md`，文首列出 `knowledge_id`；题目与「参考答案」分区。
5. 更新 `assessments/_index.yaml`。
6. 若 `mastery` 缺少对应节点条目，补齐为 `L0`、`last_assessed: null`、`wrong_count: 0`。
7. 告知学生：先独立作答；交卷后用 `grade-and-update` Skill 批改回写。

## 输出给学生

- 考试范围一句话
- 建议用时
- 题目正文（不剧透答案）
- 交卷后如何继续的一句指引
