"""掌握度策略：练习上限、考核通过线、免练与标题标注。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .db import ROOT

_POLICY_PATH = ROOT / "profile" / "mastery-policy.yaml"
_LEVELS = ["L0", "L1", "L2", "L3", "L4"]


def load_policy() -> dict[str, Any]:
    if not _POLICY_PATH.exists():
        return {
            "practice_max_level": "L2",
            "practice_level_up": {"pass_rate": 0.75, "min_questions": 4},
            "assessment": {
                "pass_rate": 0.80,
                "max_step_up": 1,
                "auto_refresh_next": True,
                "next_level_step": 1,
            },
            "practice_exempt_when_above_max": True,
            "labels": {},
            "basics": [],
        }
    with _POLICY_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def level_index(level: str | None) -> int:
    if not level or level not in _LEVELS:
        return 0
    return _LEVELS.index(level)


def next_level(level: str | None, step: int = 1) -> str:
    i = min(len(_LEVELS) - 1, level_index(level) + step)
    return _LEVELS[i]


def clamp_level(level: str, max_level: str) -> str:
    return level if level_index(level) <= level_index(max_level) else max_level


def practice_max_level(policy: dict[str, Any] | None = None) -> str:
    p = policy or load_policy()
    return p.get("practice_max_level") or "L2"


def assessment_pass_rate(policy: dict[str, Any] | None = None) -> float:
    p = policy or load_policy()
    return float((p.get("assessment") or {}).get("pass_rate") or 0.8)


def practice_pass_rate(policy: dict[str, Any] | None = None) -> float:
    p = policy or load_policy()
    return float((p.get("practice_level_up") or {}).get("pass_rate") or 0.75)


def is_practice_exempt(current_level: str | None, policy: dict[str, Any] | None = None) -> bool:
    """当前等级已达/超过练习上限 → 免练（改走考核）。"""
    p = policy or load_policy()
    if not p.get("practice_exempt_when_above_max", True):
        return False
    # 含练习上限本身：到顶后无需再刷新练习
    return level_index(current_level) >= level_index(practice_max_level(p))


def is_assessment_at_cap(current_level: str | None) -> bool:
    """已达全局最高档 L4。"""
    return level_index(current_level) >= level_index("L4")


def practice_goal_level(current_level: str | None, policy: dict[str, Any] | None = None) -> str:
    """练习本场可冲的目标等级（不超过练习上限，且相对当前 +1）。"""
    p = policy or load_policy()
    pmax = practice_max_level(p)
    if is_practice_exempt(current_level, p):
        return current_level or "L0"
    cur = current_level or "L0"
    if level_index(cur) >= level_index(pmax):
        return pmax
    return clamp_level(next_level(cur, 1), pmax)


def format_practice_label(
    *,
    current_level: str | None,
    knowledge_name: str = "",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = policy or load_policy()
    labels = p.get("labels") or {}
    cur = current_level or "L0"
    pmax = practice_max_level(p)
    exempt = is_practice_exempt(cur, p)
    goal = practice_goal_level(cur, p)
    if exempt:
        title = (labels.get("practice_exempt") or "免练 · 已达 {current}（练习上限 {practice_max}，请走考核）").format(
            current=cur, practice_max=pmax, goal=goal
        )
    else:
        title = (labels.get("practice_title") or "练习 · 可升至 {practice_max}（当前 {current}→目标 {goal}）").format(
            practice_max=pmax, current=cur, goal=goal
        )
    if knowledge_name:
        title = f"{knowledge_name} · {title}"
    return {
        "title": title,
        "current_level": cur,
        "practice_max_level": pmax,
        "goal_level": goal,
        "exempt": exempt,
        "practice_pass_rate": practice_pass_rate(p),
        "assessment_pass_rate": assessment_pass_rate(p),
    }


def format_assessment_label(
    *,
    target_level: str | None,
    policy: dict[str, Any] | None = None,
) -> str:
    p = policy or load_policy()
    labels = p.get("labels") or {}
    pct = int(round(assessment_pass_rate(p) * 100))
    return (labels.get("assessment_title") or "考核 · 冲 {target} · 通过线 {pass_pct}%").format(
        target=target_level or "L2",
        pass_pct=pct,
    )


def apply_practice_level_ups(
    per_kid_stats: dict[str, dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    per_kid_stats[kid] = {correct, total, current_level, subject_id}
    返回升级结果列表。
    """
    p = policy or load_policy()
    rate_need = practice_pass_rate(p)
    min_q = int((p.get("practice_level_up") or {}).get("min_questions") or 4)
    pmax = practice_max_level(p)
    results = []
    for kid, st in per_kid_stats.items():
        total = int(st.get("total") or 0)
        correct = int(st.get("correct") or 0)
        cur = st.get("current_level") or "L0"
        if is_practice_exempt(cur, p):
            results.append(
                {
                    "knowledge_id": kid,
                    "changed": False,
                    "reason": "exempt",
                    "from": cur,
                    "to": cur,
                }
            )
            continue
        if total < min_q:
            results.append(
                {
                    "knowledge_id": kid,
                    "changed": False,
                    "reason": "too_few",
                    "from": cur,
                    "to": cur,
                    "rate": (correct / total) if total else 0,
                }
            )
            continue
        rate = correct / total if total else 0
        if rate < rate_need:
            results.append(
                {
                    "knowledge_id": kid,
                    "changed": False,
                    "reason": "below_pass",
                    "from": cur,
                    "to": cur,
                    "rate": rate,
                    "need": rate_need,
                }
            )
            continue
        goal = practice_goal_level(cur, p)
        if level_index(goal) <= level_index(cur):
            results.append(
                {
                    "knowledge_id": kid,
                    "changed": False,
                    "reason": "at_cap",
                    "from": cur,
                    "to": cur,
                    "rate": rate,
                }
            )
            continue
        results.append(
            {
                "knowledge_id": kid,
                "changed": True,
                "reason": "pass",
                "from": cur,
                "to": goal,
                "rate": rate,
                "subject_id": st.get("subject_id"),
                "practice_max_level": pmax,
            }
        )
    return results


def apply_assessment_pass(
    per_kid_stats: dict[str, dict[str, Any]],
    *,
    target_level: str,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    p = policy or load_policy()
    rate_need = assessment_pass_rate(p)
    max_step = int((p.get("assessment") or {}).get("max_step_up") or 1)
    results = []
    for kid, st in per_kid_stats.items():
        total = int(st.get("total") or 0)
        correct = int(st.get("correct") or 0)
        cur = st.get("current_level") or "L0"
        rate = correct / total if total else 0
        passed = total > 0 and rate >= rate_need
        if not passed:
            results.append(
                {
                    "knowledge_id": kid,
                    "passed": False,
                    "changed": False,
                    "from": cur,
                    "to": cur,
                    "rate": rate,
                    "need": rate_need,
                }
            )
            continue
        # 通过：向 target_level 靠拢，但每场最多 +max_step
        want = target_level or next_level(cur, 1)
        stepped = next_level(cur, max_step)
        to = want if level_index(want) <= level_index(stepped) else stepped
        if level_index(to) < level_index(cur):
            to = cur
        results.append(
            {
                "knowledge_id": kid,
                "passed": True,
                "changed": level_index(to) > level_index(cur),
                "from": cur,
                "to": to,
                "rate": rate,
                "need": rate_need,
                "subject_id": st.get("subject_id"),
            }
        )
    return results


def list_basics(policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    p = policy or load_policy()
    items = []
    for it in p.get("basics") or []:
        rel = it.get("path") or ""
        path = ROOT / rel
        content = ""
        if path.exists():
            content = path.read_text(encoding="utf-8")
        items.append(
            {
                "id": it.get("id"),
                "title": it.get("title"),
                "subject": it.get("subject"),
                "path": rel,
                "has_content": bool(content.strip()),
                "content_md": content,
            }
        )
    return items
