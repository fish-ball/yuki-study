---
name: grade-and-update
description: 批改学生作答，归因到 knowledge_id，回写 mastery 与 summary。在学生交卷、要求批改、订正错题后使用。
---

# 批改并更新掌握度

## 何时使用

学生提交答案、要求批改、订正后复评时。

## 步骤

1. 定位对应考核文件（`assessments/...`）与科目 `mastery/<subject>.yaml`。
2. 逐题批改：对/错、失分点、正确思路（简洁）。
3. 每道错题绑定至少一个 `knowledge_id`；统计该 id 的对错。
4. 若出现**新的错因**或与已有教程表述冲突：按 `ensure-tutorial` 修订该点教程（易错点/纠正），并对同卷关联 id 做整合。
5. 按规则更新 `level`（不可跳级超过 1 档，除非连续两次同级考核全对且学生明确要求）：
   - 该点基础题全错或空白 → 保持或降到 L0/L1，`wrong_count += 1`
   - 基础题基本对、变式不稳 → L2
   - 中考常规题正确率约 80%+ → 可升 L3
   - 综合变式稳定 → 可升 L4
   - 同级考核再次大面积失误 → `wrong_count += 1`，必要时降 1 级
6. 写入：`level`、`last_assessed`（当天日期）、`wrong_count`、`notes`（短句）。
7. 刷新 `mastery/summary.yaml`：各科等级计数、薄弱列表（优先 `wrong_count` 高且 `exam_weight: high`）、`next_focus`。
8. 给学生：得分概况、错题归因表、下一步 1–3 个强化点（含具体 id 与中文名）。

## 禁止

- 只口头评价不改文件
- 修改未考查科目的 mastery
- 自创等级名
