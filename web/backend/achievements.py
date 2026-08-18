# -*- coding: utf-8 -*-
"""学习成就：达成后写入数据库，供庆祝页展示。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .db import get_conn

DEFS: list[dict[str, str]] = [
    {
        "id": "first_perfect",
        "title": "一场全对",
        "detail": "单场练习、考核或计算专题全部答对。",
    },
    {
        "id": "streak5",
        "title": "连对五题",
        "detail": "同一场里连续答对 5 题。",
    },
    {
        "id": "first_l1",
        "title": "打下基础",
        "detail": "有知识点升到 L1。",
    },
    {
        "id": "consol_pass",
        "title": "巩固过关",
        "detail": "第一次通过不会专学巩固卷。",
    },
    {
        "id": "calc_set",
        "title": "计算专题",
        "detail": "完成一套换数据的计算专题。",
    },
    {
        "id": "first_assess_pass",
        "title": "考核过关",
        "detail": "考核中首次有知识点达到通过线。",
    },
    {
        "id": "first_l4",
        "title": "熟练掌握",
        "detail": "有知识点达到最高档 L4。",
    },
]


def list_achievements() -> dict[str, Any]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, detail, unlocked_at FROM student_achievements ORDER BY unlocked_at DESC"
    ).fetchall()
    conn.close()
    unlocked = {r["id"]: dict(r) for r in rows}
    catalog = []
    for d in DEFS:
        item = dict(d)
        got = unlocked.get(d["id"])
        item["unlocked"] = bool(got)
        item["unlocked_at"] = got["unlocked_at"] if got else None
        catalog.append(item)
    return {"items": catalog, "unlocked_count": len(unlocked), "total": len(DEFS)}


def _already(conn, aid: str) -> bool:
    row = conn.execute("SELECT 1 FROM student_achievements WHERE id = ?", (aid,)).fetchone()
    return bool(row)


def _unlock(conn, aid: str, now: str) -> dict[str, str] | None:
    if _already(conn, aid):
        return None
    meta = next((d for d in DEFS if d["id"] == aid), None)
    if not meta:
        return None
    conn.execute(
        "INSERT INTO student_achievements (id, title, detail, unlocked_at) VALUES (?, ?, ?, ?)",
        (aid, meta["title"], meta["detail"], now),
    )
    return {"id": aid, "title": meta["title"], "detail": meta["detail"], "unlocked_at": now}


def evaluate_after_session(session_result: dict[str, Any]) -> list[dict[str, str]]:
    """根据本场结束结果解锁新成就。"""
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    new: list[dict[str, str]] = []

    def add(aid: str) -> None:
        got = _unlock(conn, aid, now)
        if got:
            new.append(got)

    total = int(
        session_result.get("score_total")
        if session_result.get("score_total") is not None
        else session_result.get("total_questions")
        or 0
    )
    correct = int(
        session_result.get("score_correct")
        if session_result.get("score_correct") is not None
        else session_result.get("correct_count")
        or 0
    )
    if total > 0 and correct >= total:
        add("first_perfect")

    prog = session_result.get("progression") or {}
    kind = prog.get("kind") or ""
    items = prog.get("items") or prog.get("per_knowledge") or []
    if kind == "consolidation" and any(it.get("passed") for it in items):
        add("consol_pass")
    if kind == "assessment" and any(it.get("passed") for it in items):
        add("first_assess_pass")
    paper_id = str(session_result.get("paper_id") or "")
    theme = str(session_result.get("theme") or "")
    if paper_id.startswith("drill-calc-") or theme.startswith("计算专题"):
        if session_result.get("fully_done"):
            add("calc_set")

    sid = session_result.get("session_id") or session_result.get("id")
    if sid:
        rows = conn.execute(
            """
            SELECT is_correct FROM attempt_records
            WHERE session_id = ? AND attempt_no = 1 AND COALESCE(voided, 0) = 0
            ORDER BY created_at
            """,
            (sid,),
        ).fetchall()
        run = 0
        best = 0
        for r in rows:
            if r["is_correct"] == 1:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best >= 5:
            add("streak5")

    for it in items:
        if not it.get("changed"):
            continue
        if it.get("to") == "L1":
            add("first_l1")
        if it.get("to") == "L4":
            add("first_l4")

    conn.commit()
    conn.close()
    return new
