---
name: generate-assessment
description: 按目标知识点与目标掌握等级生成中考风格考核卷并写入 assessments。在学生要求练习、小测、出题、刷某考点时使用。
---

# 生成考核

## 何时使用

学生指定科目/知识点/时长，或要求从 Lx 升到 Ly 的针对性练习时。

## 步骤

1. 读取目标科目 `knowledge` 树与 `mastery`，确认目标 `knowledge_id` 存在。
2. 若对应教程 `knowledge/<subject>/tutorials/<knowledge_id>.md` 不存在，先执行 `ensure-tutorial`（或提醒学生在管理台点「学一学」）。
3. 按目标等级出题：
   - 冲 L1：定义/公式默写、概念判断
   - 冲 L2：基础计算或短问答
   - 冲 L3：中考常规题（选择题+填空+简答/解答）
   - 冲 L4：变式、综合、易错陷阱
3. 写入 `assessments/YYYY-MM-DD-<subject>-<theme>.md`：
   - 文首 YAML 或清单：`subject`、`target_level`、`knowledge_ids`、`minutes`
   - 分区：选择题 / 填空题 / 解答题（按学科调整）
   - 文末 `## 参考答案`（含评分要点与对应 `knowledge_id`）
4. 登记 `assessments/_index.yaml`。
5. 提醒学生：在管理台打开「练习」页做题；同步后题目会结构化写入 SQLite，并镜像到 `practice/papers/`，做题记录永久保存在 `attempt_records` 与 `practice/attempts/`。
6. 对话中只发题目与用时建议，提醒交卷后批改（或直接在网页练习）。

## 约束

- 每题标注考查的 `knowledge_id`（可写在题号旁括号内）。
- 不一次堆砌超过约定时长的题量。
- 英语听说机考可用「听说专项占位题」文字模拟，并注明真实机考另练。
- **练习架构兼容**（写入 Markdown 时遵守，便于同步到 SQLite / `practice/papers`）：
  - 选项必须写成 `- A. ...` 行格式
  - 多空填空答案用中文分号 `；` 分隔（如 `H；C；N`）
  - 简答参考答案以 `评分要点：` 或 `示例：` 开头（识别为 `short`，不自动判分）
  - 判断题答案以 `对` / `错` 开头；选择题以 `A`–`D` 开头
  - 参考答案每题先写答案，下一行写 `解析：`（考点、思路、易错），不要只写选项字母
  - 参考答案行不要把 `（knowledge_id）` 写进可接受答案正文
- **晋级口径**（`profile/mastery-policy.yaml`）：
  - 练习卷目标不超过 `practice_max_level`（默认 L2）
  - 考核卷文首写清 `target_level`，通过线默认 80%；完成后应能刷新更高档去重卷
  - 题干避免与同知识点历史题完全重复
