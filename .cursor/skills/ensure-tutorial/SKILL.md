---
name: ensure-tutorial
description: 为知识点确保存在可浏览的教程 Markdown；缺失则生成，已有则按需修订并把关联新内容整合进讲义。讲解、预习、练习、批改触达 knowledge_id 时使用。
---

# 确保 / 修订知识点教程

## 何时使用

- 精讲 / 预习某个 `knowledge_id` 之前
- 学生要「学一学」「看教程」「讲义」
- 出题或批改后需要补充、纠正讲解材料
- **新学了关联知识点**（先修、同模块相邻点、同卷考查点）之后
- 学生在练习中暴露出新的易错点

## 硬原则（动态修正）

1. 教程是**可演进文档**，不是一次性成品。
2. 已有正确内容：**保留并在原基础上补充**，禁止无故整篇覆盖删除。
3. 发现错误、过时表述、或学生新错因：必须**落盘修订**（升 `version`，写 `updated_at`），禁止只口头改口。
4. 关联内容加入后：必须**整合**进相关教程（互链 + 必要对照），不能只新建孤立讲义。

## 步骤

1. 读取 `knowledge/<subject>/tree.yaml`，确认 `knowledge_id`，记下 `name`、`exam_weight`、`prerequisites`。
2. 检查 `knowledge/<subject>/tutorials/<knowledge_id>.md`：
   - **不存在** → 按模板新建（`source: agent` 或经管理台 LLM）。
   - **已存在且内容仍适用** → 打开引用；若本次讲解补充了新例子/新易错点 → 执行「修订」。
   - **已存在但与本次精讲/批改结论冲突** → 执行「修订（纠正）」，不得并存矛盾说法。
3. **关联整合**（满足任一即做）：
   - 本次同时讲解了 `prerequisites` 或同卷多个 `knowledge_id`
   - 新建了相邻点教程
   - 批改归因到多个 id  
   动作：在各方教程中更新 `related_ids`，并在正文增加或改写 `## 关联知识点`（各用 1～2 句说明关系与易混点）；需要时把对照例补进「例题演示 / 易错点」。
4. 写入后提醒学生在管理台「学习 / 知识点」刷新浏览；需要时「从仓库同步」。
5. 对话精讲：以**最新教程**做摘要，再立刻练习。

## 修订方式

| 场景 | 做法 |
|------|------|
| 小补充（多一例、多一条易错） | 直接改对应小节，`version += 1` |
| 纠正错误结论 | 改掉错误句，必要时在易错点写「曾易混成…」 |
| 整合关联点 | 更新 `related_ids` + `## 关联知识点`；双方教程都要改 |
| 管理台 | `POST /api/tutorials/{id}/revise`（`patch` / `integrate` / `correct`） |

可用管理台或 Agent 直接改 Markdown；Agent 改完应保证五段标题仍在。

## 教程文件约定

路径：`knowledge/<subject>/tutorials/<knowledge_id>.md`

文首 YAML：

```yaml
---
knowledge_id: chemistry.basic.change
subject: chemistry
title: 物理变化与化学变化
target_level: L1
version: 2
source: agent   # agent | llm | human
updated_at: YYYY-MM-DD
related_ids:
  - chemistry.basic.element
  - chemistry.basic.molecule
revision_note: 补充与分子原子的对照；订正易错表述
---
```

正文必须含（可增不可删）：

1. `## 一句话记住`
2. `## 核心概念`
3. `## 例题演示`
4. `## 易错点`
5. `## 自测提示`  
可选：`## 关联知识点`、`## 自测参考`

## 内容要求

- 面向初三、简体中文；暑假预习可略降起点。
- 禁止空话；修订时写清「补了什么 / 改了什么」。
- 关联整合要具体（对照、先后、易混），禁止只贴 id 列表。

## 与练习闭环

修订或整合后：若该点仍薄弱，引导短测或管理台「练习」；错题归因后优先修订「易错点」再让学生订正。
