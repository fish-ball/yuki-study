"""考核结束后：按通过线晋级，并刷新更高难度去重新卷。"""
from __future__ import annotations

import json
import re
import copy
import random
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import ROOT, get_conn
from .mastery_policy import (
    apply_assessment_pass,
    assessment_pass_rate,
    assessment_target_for_level,
    allow_skip_practice,
    format_assessment_label,
    is_assessment_at_cap,
    is_practice_exempt,
    level_index,
    load_policy,
    next_level,
)
from .question_parse import parse_assessment_markdown
from .sync import export_mastery_to_yaml, export_paper_json, refresh_summary_yaml, upsert_questions_from_markdown


def session_kid_stats(session_id: str) -> dict[str, dict[str, Any]]:
    """按知识点统计本场首次作答正确率。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT a.question_id, a.is_correct, a.attempt_no, qk.knowledge_id, q.subject_id
        FROM attempt_records a
        JOIN question_knowledge qk ON qk.question_id = a.question_id
        JOIN questions q ON q.id = a.question_id
        WHERE a.session_id = ? AND a.attempt_no = 1
          AND COALESCE(a.voided, 0) = 0
        """,
        (session_id,),
    ).fetchall()
    stats: dict[str, dict[str, Any]] = {}
    for r in rows:
        kid = r["knowledge_id"]
        if kid not in stats:
            cur = conn.execute(
                "SELECT level, subject_id FROM mastery_items WHERE knowledge_id = ?",
                (kid,),
            ).fetchone()
            stats[kid] = {
                "correct": 0,
                "total": 0,
                "current_level": cur["level"] if cur else "L0",
                "subject_id": (cur["subject_id"] if cur else r["subject_id"]),
            }
        stats[kid]["total"] += 1
        if r["is_correct"] == 1:
            stats[kid]["correct"] += 1
    conn.close()
    return stats


def finalize_assessment_session(session_id: str, paper_id: str) -> dict[str, Any]:
    """考核卷结束后调用：晋级 + 可选生成下一卷。"""
    from .day_drills import session_covers_paper

    conn = get_conn()
    paper = conn.execute("SELECT * FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    fully_done = session_covers_paper(conn, session_id, paper_id)
    conn.close()
    if not paper:
        return {"ok": False, "reason": "paper_missing"}

    # 练习 drill 卷不走考核晋级
    if str(paper_id).startswith("drill-") or "drill" in (paper["theme"] or ""):
        return {"ok": False, "reason": "not_assessment", "is_drill": True}

    policy = load_policy()
    target = paper["target_level"] or "L2"
    stats = session_kid_stats(session_id)
    results = apply_assessment_pass(stats, target_level=target, policy=policy)

    upgraded = []
    for r in results:
        if not r.get("changed"):
            continue
        _set_mastery_level(r["knowledge_id"], r["to"], r.get("subject_id"))
        upgraded.append(r)

    subjects = {r.get("subject_id") for r in upgraded if r.get("subject_id")}
    for sid in subjects:
        if sid:
            export_mastery_to_yaml(sid)
    if upgraded:
        refresh_summary_yaml()

    subject_id = paper["subject_id"] or (
        (list(stats.keys()) or [""])[0].split(".", 1)[0] if stats else None
    )
    next_paper = None
    cfg = policy.get("assessment") or {}
    # 未答完全卷不刷下一份，避免重复堆卷把列表/前端拖死
    if fully_done and cfg.get("auto_refresh_next", True):
        next_paper = _queue_next_assessment(
            subject_id=subject_id,
            finished_paper_id=paper_id,
            finished_target=target,
            results=results,
            policy=policy,
        )

    cleanup_assessment_queue(subject_id=subject_id)

    return {
        "ok": True,
        "paper_id": paper_id,
        "target_level": target,
        "pass_rate_need": assessment_pass_rate(policy),
        "per_knowledge": results,
        "upgraded": upgraded,
        "next_paper": next_paper,
        "label": format_assessment_label(target_level=target, policy=policy),
    }


def _paper_knowledge_ids(paper_id: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT DISTINCT knowledge_id FROM question_knowledge
        WHERE question_id IN (SELECT id FROM questions WHERE paper_id = ?)
        """,
        (paper_id,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT knowledge_id FROM assessment_knowledge WHERE assessment_id = ?",
            (paper_id,),
        ).fetchall()
    conn.close()
    return [r["knowledge_id"] for r in rows]


def _set_mastery_level(knowledge_id: str, level: str, subject_id: str | None) -> None:
    conn = get_conn()
    now = date.today().isoformat()
    row = conn.execute(
        "SELECT subject_id FROM mastery_items WHERE knowledge_id = ?", (knowledge_id,)
    ).fetchone()
    sid = (row["subject_id"] if row else None) or subject_id
    if not sid:
        sid = knowledge_id.split(".", 1)[0]
    if row:
        conn.execute(
            """
            UPDATE mastery_items
            SET level = ?, last_assessed = ?,
                notes = CASE WHEN notes IS NULL OR notes = '' THEN '考核通过晋级' ELSE notes END
            WHERE knowledge_id = ?
            """,
            (level, now, knowledge_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO mastery_items (knowledge_id, subject_id, level, last_assessed, wrong_count, notes)
            VALUES (?, ?, ?, ?, 0, '考核通过晋级')
            """,
            (knowledge_id, sid, level, now),
        )
    conn.commit()
    conn.close()


def _is_drill_row(paper_id: str, theme: str | None) -> bool:
    return str(paper_id).startswith("drill-") or "drill" in (theme or "")


def _kids_for_paper(conn, paper_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT knowledge_id FROM assessment_knowledge WHERE assessment_id = ?",
        (paper_id,),
    ).fetchall()
    kids = [r["knowledge_id"] for r in rows]
    if kids:
        return kids
    rows = conn.execute(
        """
        SELECT DISTINCT qk.knowledge_id
        FROM question_knowledge qk
        JOIN questions q ON q.id = qk.question_id
        WHERE q.paper_id = ?
        """,
        (paper_id,),
    ).fetchall()
    return [r["knowledge_id"] for r in rows]


def _kid_level(conn, knowledge_id: str) -> str:
    row = conn.execute(
        "SELECT level FROM mastery_items WHERE knowledge_id = ?",
        (knowledge_id,),
    ).fetchone()
    return row["level"] if row else "L0"


def _abandon_zombie_sessions(conn) -> int:
    """已完成/已取消卷上的进行中会话不再占用列表与续练接口。"""
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        UPDATE practice_sessions
        SET status = 'abandoned', finished_at = COALESCE(finished_at, ?)
        WHERE status = 'in_progress'
          AND paper_id IN (
            SELECT id FROM assessments
            WHERE COALESCE(status, 'ready') IN ('completed', 'retired', 'exempt', 'archived')
          )
        """,
        (now,),
    )
    return int(cur.rowcount or 0)


_CLEANUP_RUNNING = False


def cleanup_assessment_queue(subject_id: str | None = None) -> dict[str, Any]:
    """完成卷保持完成；全部到 L4 的卷取消；同知识点重复卷只留一份；按清单补下一考核。"""
    global _CLEANUP_RUNNING
    if _CLEANUP_RUNNING:
        return {"retired_at_cap": 0, "retired_duplicate": 0, "seeded": []}
    _CLEANUP_RUNNING = True
    try:
        return _cleanup_assessment_queue_body(subject_id)
    finally:
        _CLEANUP_RUNNING = False


def _cleanup_assessment_queue_body(subject_id: str | None = None) -> dict[str, Any]:
    policy = load_policy()
    conn = get_conn()
    _abandon_zombie_sessions(conn)
    sql = """
        SELECT id, subject_id, theme, target_level, status, date
        FROM assessments
        WHERE COALESCE(status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
    """
    params: list[Any] = []
    if subject_id:
        sql += " AND subject_id = ?"
        params.append(subject_id)
    papers = [dict(r) for r in conn.execute(sql, params).fetchall()]
    retired_cap = 0
    retired_dup = 0
    groups: dict[tuple[str, str, frozenset[str]], list[str]] = {}
    for p in papers:
        if _is_drill_row(p["id"], p.get("theme")):
            continue
        kids = _kids_for_paper(conn, p["id"])
        if kids and all(is_assessment_at_cap(_kid_level(conn, k)) for k in kids):
            conn.execute("UPDATE assessments SET status = 'retired' WHERE id = ?", (p["id"],))
            retired_cap += 1
            continue
        key = (p.get("subject_id") or "", p.get("target_level") or "", frozenset(kids))
        groups.setdefault(key, []).append(p["id"])
    for ids in groups.values():
        if len(ids) <= 1:
            continue
        # 有进行中会话的优先保留，否则留最新 id
        keep = None
        for pid in ids:
            row = conn.execute(
                """
                SELECT id FROM practice_sessions
                WHERE paper_id = ? AND status = 'in_progress'
                LIMIT 1
                """,
                (pid,),
            ).fetchone()
            if row:
                keep = pid
                break
        if keep is None:
            keep = sorted(ids)[-1]
        for pid in ids:
            if pid != keep:
                conn.execute("UPDATE assessments SET status = 'retired' WHERE id = ?", (pid,))
                retired_dup += 1
    _abandon_zombie_sessions(conn)
    conn.commit()
    conn.close()

    seeded = []
    subjects = [subject_id] if subject_id else _phase1_subjects()
    for sid in subjects:
        if not sid:
            continue
        if _has_ready_assessment(sid):
            continue
        paper = _queue_next_assessment(
            subject_id=sid,
            finished_paper_id=None,
            finished_target=None,
            results=[],
            policy=policy,
        )
        if paper:
            seeded.append(paper)
    return {
        "retired_at_cap": retired_cap,
        "retired_duplicate": retired_dup,
        "seeded": seeded,
    }


def _phase1_subjects() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM subjects WHERE phase = 1 ORDER BY sort_order, id"
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _has_ready_assessment(subject_id: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT a.id FROM assessments a
        WHERE a.subject_id = ?
          AND COALESCE(a.status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
          AND a.id NOT LIKE 'drill-%'
          AND IFNULL(a.theme, '') NOT LIKE '%drill%'
        LIMIT 1
        """,
        (subject_id,),
    ).fetchone()
    conn.close()
    return bool(row)


def _find_ready_assessment(
    subject_id: str,
    knowledge_ids: list[str],
    target_level: str,
    *,
    exclude_paper_id: str | None = None,
) -> str | None:
    want = frozenset(knowledge_ids)
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, theme, target_level FROM assessments
        WHERE subject_id = ?
          AND COALESCE(status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
        """,
        (subject_id,),
    ).fetchall()
    found = None
    for r in rows:
        if r["id"] == exclude_paper_id:
            continue
        if _is_drill_row(r["id"], r["theme"]):
            continue
        if (r["target_level"] or "") != target_level:
            continue
        kids = frozenset(_kids_for_paper(conn, r["id"]))
        if kids == want:
            found = r["id"]
            break
    conn.close()
    return found


def pick_next_assessment_kids(
    subject_id: str,
    *,
    exclude: set[str] | None = None,
    limit: int = 4,
) -> tuple[list[str], str]:
    """按知识树顺序取下一批可考核叶节点（已达练习上限且未到 L4）。返回 (ids, target_level)。"""
    from .knowledge_topo import compute_topo_depths, parse_json_list

    exclude = set(exclude or [])
    policy = load_policy()
    conn = get_conn()
    # 已在待做考核里的点不再重复入队
    queued_rows = conn.execute(
        """
        SELECT ak.knowledge_id
        FROM assessment_knowledge ak
        JOIN assessments a ON a.id = ak.assessment_id
        WHERE a.subject_id = ?
          AND COALESCE(a.status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
          AND a.id NOT LIKE 'drill-%'
          AND IFNULL(a.theme, '') NOT LIKE '%drill%'
        """,
        (subject_id,),
    ).fetchall()
    exclude |= {r["knowledge_id"] for r in queued_rows}
    nodes = [
        dict(r)
        for r in conn.execute(
            """
            SELECT k.id, k.name, k.parent_id, k.children_json, k.prerequisites_json,
                   k.sort_index, COALESCE(m.level, 'L0') AS level
            FROM knowledge_nodes k
            LEFT JOIN mastery_items m ON m.knowledge_id = k.id
            WHERE k.subject_id = ?
            """,
            (subject_id,),
        ).fetchall()
    ]
    conn.close()
    for n in nodes:
        n["children_ids"] = parse_json_list(n.get("children_json"))
        n["prerequisites"] = parse_json_list(n.get("prerequisites_json"))
    depths = compute_topo_depths(nodes)
    leaves = [n for n in nodes if not n.get("children_ids")]
    leaves.sort(
        key=lambda n: (int(depths.get(n["id"], 0)), int(n.get("sort_index") or 0), n["id"])
    )
    level_of = {n["id"]: (n.get("level") or "L0") for n in nodes}

    def eligible(n: dict[str, Any], want_level: str) -> bool:
        kid = n["id"]
        if kid in exclude:
            return False
        lv = n.get("level") or "L0"
        if lv != want_level:
            return False
        if is_assessment_at_cap(lv):
            return False
        if not is_practice_exempt(lv, policy):
            return False
        for pid in n.get("prerequisites") or []:
            if (level_of.get(pid) or "L0") == "L0":
                return False
        return True

    picked: list[str] = []
    target = "L3"
    for want_lv, to_lv in (("L3", "L4"), ("L2", "L3")):
        picked = [n["id"] for n in leaves if eligible(n, want_lv)][:limit]
        if picked:
            target = to_lv
            break
    return picked, target


def skip_practice_to_assessment(
    knowledge_ids: list[str],
    *,
    day_plan_id: str | None = None,
) -> dict[str, Any]:
    """跳过练习，按当前等级 +1 生成考核卷。"""
    policy = load_policy()
    if not allow_skip_practice(policy):
        raise ValueError("当前策略不允许跳过练习")
    conn = get_conn()
    kids: list[str] = []
    if day_plan_id and not knowledge_ids:
        rows = conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? ORDER BY sort_order",
            (day_plan_id,),
        ).fetchall()
        knowledge_ids = [r["knowledge_id"] for r in rows]
    for kid in knowledge_ids or []:
        lv = _kid_level(conn, kid)
        if not is_assessment_at_cap(lv):
            kids.append(kid)
    if not kids:
        conn.close()
        raise ValueError("这些知识点已达 L4，无需再考核")
    # 按科目分组，同一科目一份卷
    by_sub: dict[str, list[str]] = {}
    levels: dict[str, str] = {}
    for kid in kids:
        sid = kid.split(".", 1)[0]
        by_sub.setdefault(sid, []).append(kid)
        levels[kid] = _kid_level(conn, kid)
    conn.close()

    papers = []
    for sid, group in by_sub.items():
        cur_min = min(levels[k] for k in group)
        to_level = assessment_target_for_level(cur_min)
        existing = _find_ready_assessment(sid, group, to_level)
        if existing:
            conn = get_conn()
            qn = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE paper_id = ?", (existing,)
            ).fetchone()["c"]
            conn.close()
            papers.append(
                {
                    "id": existing,
                    "target_level": to_level,
                    "question_count": qn,
                    "knowledge_ids": group,
                    "reused": True,
                }
            )
            continue
        paper = generate_next_assessment_paper(
            subject_id=sid,
            knowledge_ids=group,
            from_level=f"跳过练习·{cur_min}",
            to_level=to_level,
        )
        if paper:
            paper["knowledge_ids"] = group
            papers.append(paper)
    if not papers:
        raise ValueError("未能生成考核卷")
    return {
        "ok": True,
        "skipped_practice": True,
        "papers": papers,
        "paper": papers[0],
    }


def ensure_day_assessments(plan_date: str | None = None) -> dict[str, Any]:
    """日计划里已达练习上限、未到 L4 的点，按科目补一份考核（不堆练习）。"""
    plan_date = plan_date or date.today().isoformat()
    policy = load_policy()
    conn = get_conn()
    day = conn.execute("SELECT * FROM day_plans WHERE plan_date = ?", (plan_date,)).fetchone()
    if not day:
        conn.close()
        return {"ok": False, "reason": "no_day"}
    items = conn.execute(
        """
        SELECT i.knowledge_id, COALESCE(m.level, 'L0') AS level
        FROM day_plan_items i
        LEFT JOIN mastery_items m ON m.knowledge_id = i.knowledge_id
        WHERE i.day_plan_id = ? AND i.done = 0
        ORDER BY i.sort_order
        """,
        (day["id"],),
    ).fetchall()
    conn.close()
    by_sub: dict[str, list[str]] = {}
    for it in items:
        lv = it["level"] or "L0"
        if is_assessment_at_cap(lv) or not is_practice_exempt(lv, policy):
            continue
        sid = it["knowledge_id"].split(".", 1)[0]
        by_sub.setdefault(sid, []).append(it["knowledge_id"])
    seeded = []
    for sid, group in by_sub.items():
        conn = get_conn()
        covered: set[str] = set()
        rows = conn.execute(
            """
            SELECT id, theme FROM assessments
            WHERE subject_id = ?
              AND COALESCE(status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
            """,
            (sid,),
        ).fetchall()
        for r in rows:
            if _is_drill_row(r["id"], r["theme"]):
                continue
            covered |= set(_kids_for_paper(conn, r["id"]))
        levels = [_kid_level(conn, k) for k in group]
        conn.close()
        uncovered = [k for k in group if k not in covered]
        if not uncovered:
            continue
        cur_min = min(levels, key=lambda x: level_index(x)) if levels else "L2"
        paper = generate_next_assessment_paper(
            subject_id=sid,
            knowledge_ids=uncovered[:4],
            from_level="日计划",
            to_level=assessment_target_for_level(cur_min),
        )
        if paper:
            seeded.append(paper)
    return {"ok": True, "seeded": seeded}


def _queue_next_assessment(
    *,
    subject_id: str | None,
    finished_paper_id: str | None,
    finished_target: str | None,
    results: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    """优先同批未到顶的点升一档；否则按知识清单取下一批。"""
    if not subject_id:
        return None
    keep: list[str] = []
    for r in results:
        kid = r.get("knowledge_id")
        if not kid:
            continue
        lv = r.get("to") or r.get("from") or "L0"
        if not is_assessment_at_cap(lv):
            keep.append(kid)
    to_level = None
    from_level = finished_target or "L2"
    if keep and finished_target:
        nxt = next_level(finished_target, int((policy.get("assessment") or {}).get("next_level_step") or 1))
        if level_index(nxt) > level_index(finished_target):
            to_level = nxt
        else:
            keep = []
    if not keep:
        keep, to_level = pick_next_assessment_kids(subject_id, exclude=set(), limit=4)
        from_level = "清单"
    if not keep or not to_level:
        return None
    existing = _find_ready_assessment(
        subject_id, keep, to_level, exclude_paper_id=finished_paper_id
    )
    if existing:
        conn = get_conn()
        row = conn.execute(
            "SELECT id, target_level, note FROM assessments WHERE id = ?", (existing,)
        ).fetchone()
        qn = conn.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE paper_id = ?", (existing,)
        ).fetchone()["c"]
        conn.close()
        return {
            "id": existing,
            "path": "",
            "target_level": (row["target_level"] if row else to_level),
            "question_count": qn,
            "label": row["note"] if row else "",
            "reused": True,
        }
    return generate_next_assessment_paper(
        subject_id=subject_id,
        knowledge_ids=keep,
        from_level=from_level,
        to_level=to_level,
        exclude_paper_id=finished_paper_id,
    )


def _existing_stems(knowledge_ids: list[str]) -> set[str]:
    conn = get_conn()
    stems: set[str] = set()
    for kid in knowledge_ids:
        rows = conn.execute(
            """
            SELECT q.stem FROM questions q
            JOIN question_knowledge qk ON qk.question_id = q.id
            WHERE qk.knowledge_id = ?
            """,
            (kid,),
        ).fetchall()
        for r in rows:
            s = re.sub(r"\s+", "", (r["stem"] or ""))
            if s:
                stems.add(s)
    conn.close()
    return stems


def generate_next_assessment_paper(
    *,
    subject_id: str,
    knowledge_ids: list[str],
    from_level: str,
    to_level: str,
    exclude_paper_id: str | None = None,
) -> dict[str, Any] | None:
    """生成更高难度考核卷 Markdown + 入库（题干去重）。到顶知识点不出卷。"""
    conn = get_conn()
    knowledge_ids = [
        k for k in (knowledge_ids or []) if not is_assessment_at_cap(_kid_level(conn, k))
    ]
    conn.close()
    if not knowledge_ids:
        return None
    today = date.today().isoformat()
    theme = f"exam-refresh-{to_level.lower()}"
    paper_id = f"{today}-{subject_id}-{theme}-{datetime.now().strftime('%H%M%S')}"
    avoid = _existing_stems(knowledge_ids)
    policy = load_policy()
    pass_pct = int(round(assessment_pass_rate(policy) * 100))

    # 用规则题库生成（不依赖 LLM，保证可用）；按 to_level 提高题型难度
    questions = _build_level_questions(subject_id, knowledge_ids, to_level, avoid)
    if not questions:
        questions = _build_level_questions(subject_id, knowledge_ids, to_level, set())

    label = format_assessment_label(target_level=to_level, policy=policy)
    md = _render_assessment_md(
        subject_id=subject_id,
        theme=theme,
        date_s=today,
        target_level=to_level,
        knowledge_ids=knowledge_ids,
        questions=questions,
        note=f"{label}；由 {from_level} 刷新，去重提高难度",
        pass_pct=pass_pct,
    )

    rel = f"assessments/{paper_id}.md"
    path = ROOT / rel
    path.write_text(md, encoding="utf-8")
    _register_index(paper_id, rel, subject_id, theme, today, to_level, knowledge_ids, label)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          content_md=excluded.content_md,
          target_level=excluded.target_level,
          note=excluded.note,
          status='ready'
        """,
        (
            paper_id,
            rel,
            subject_id,
            theme,
            today,
            25,
            to_level,
            md,
            label,
        ),
    )
    for kid in knowledge_ids:
        conn.execute(
            "INSERT OR IGNORE INTO assessment_knowledge (assessment_id, knowledge_id) VALUES (?, ?)",
            (paper_id, kid),
        )
    upsert_questions_from_markdown(
        conn,
        paper_id,
        md,
        {"subject": subject_id, "theme": theme, "target_level": to_level},
    )
    conn.commit()
    export_paper_json(conn, paper_id)
    conn.close()

    return {
        "id": paper_id,
        "path": rel,
        "target_level": to_level,
        "question_count": len(questions),
        "label": label,
        "pass_rate_need": assessment_pass_rate(policy),
    }


def _register_index(
    paper_id: str,
    rel: str,
    subject_id: str,
    theme: str,
    today: str,
    to_level: str,
    knowledge_ids: list[str],
    note: str,
) -> None:
    index_path = ROOT / "assessments" / "_index.yaml"
    data: dict[str, Any] = {"updated_at": today, "items": []}
    if index_path.exists():
        import yaml

        data = yaml.safe_load(index_path.read_text(encoding="utf-8")) or data
    items = data.get("items") or []
    items.insert(
        0,
        {
            "id": paper_id,
            "path": rel,
            "subject": subject_id,
            "theme": theme,
            "date": today,
            "minutes": 25,
            "target_level": to_level,
            "knowledge_ids": knowledge_ids,
            "status": "ready",
            "note": note,
        },
    )
    data["items"] = items
    data["updated_at"] = today
    import yaml

    index_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _build_level_questions(
    subject_id: str,
    knowledge_ids: list[str],
    level: str,
    avoid: set[str],
) -> list[dict[str, Any]]:
    """按目标等级组装题；L3/L4 必须变形加难，不用原题贴标签。"""
    from .day_drills import _is_meta_stem, _question_bank

    policy = load_policy()
    vary = bool((policy.get("assessment") or {}).get("vary_on_upgrade", True))
    out: list[dict[str, Any]] = []
    for kid in knowledge_ids:
        name = kid
        conn = get_conn()
        row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (kid,)).fetchone()
        conn.close()
        if row:
            name = row["name"]
        bank = _question_bank(kid, name)
        if level in ("L3", "L4"):
            ordered = list(reversed(bank)) + list(bank)
        elif level == "L2":
            ordered = bank[2:] + bank[:2] if len(bank) > 2 else bank
        else:
            ordered = bank
        taken = 0
        need = 6 if level in ("L3", "L4") else 5
        rng = random.Random(f"{kid}|{level}|{len(avoid)}")
        for i, q in enumerate(ordered):
            stem_raw = q.get("stem") or ""
            if _is_meta_stem(stem_raw):
                continue
            qq = copy.deepcopy(q)
            if vary and level in ("L3", "L4"):
                qq = _transform_question(qq, level=level, rng=rng, variant=i, subject_id=subject_id)
            stem_key = re.sub(r"\s+", "", qq.get("stem") or "")
            if not stem_key or stem_key in avoid:
                continue
            if _is_meta_stem(qq.get("stem") or ""):
                continue
            qq["knowledge_ids"] = [kid]
            out.append(qq)
            avoid.add(stem_key)
            taken += 1
            if taken >= need:
                break
    return out[:18]


def _transform_question(
    q: dict[str, Any],
    *,
    level: str,
    rng: random.Random,
    variant: int,
    subject_id: str,
) -> dict[str, Any]:
    """把基础题变成更高档变式：打乱选项、反设问、改数字，而不是加等级标签。"""
    qq = copy.deepcopy(q)
    qtype = qq.get("qtype") or ""
    stem = qq.get("stem") or ""

    if qtype == "choice" and qq.get("options"):
        qq = _vary_choice(qq, rng, invert=(level == "L4" or variant % 2 == 1))
        stem = qq.get("stem") or stem
    elif qtype == "judge":
        if level == "L4" or variant % 2 == 1:
            # 判断改成「对/错仍考同一事实」，措辞换成易错表述
            if (qq.get("answer_key") or "") == "对":
                stem = f"下列说法是否正确（易错）：{stem.rstrip('。.')}。"
            else:
                stem = f"有同学认为：{stem.rstrip('。.')}。这个判断对吗？"
            qq["stem"] = stem
        else:
            qq["stem"] = f"判断（变式）：{stem}"
    elif qtype == "fill":
        if subject_id == "math":
            qq = _vary_math_numbers(qq, rng)
        else:
            qq["stem"] = stem.replace("______", "________") if "______" in stem else f"填空（变式）：{stem}"
    else:
        qq["stem"] = f"{'综合' if level == 'L4' else '常规'}：{stem}"

    if level == "L4" and qtype == "choice":
        s = qq.get("stem") or ""
        if "不正确" not in s and "错误" not in s:
            qq["stem"] = s.replace("（　　）", "（易错）（　　）") if "（　　）" in s else s + "（易错）"
    return qq


def _vary_choice(q: dict[str, Any], rng: random.Random, invert: bool) -> dict[str, Any]:
    opts = list(q.get("options") or [])
    if len(opts) < 2:
        return q
    old_key = str(q.get("answer_key") or "A").strip()[:1].upper()
    pairs = [(o.get("label"), o.get("content")) for o in opts]
    correct_content = next((c for lab, c in pairs if str(lab).upper() == old_key), pairs[0][1])
    wrong = [c for lab, c in pairs if str(lab).upper() != old_key]
    stem = q.get("stem") or ""
    target_content = correct_content
    inverted = False
    if invert and wrong and re.search(r"正确的是|说法正确", stem):
        stem = re.sub(r"正确的是", "不正确的是", stem)
        stem = re.sub(r"说法正确", "说法错误", stem)
        target_content = rng.choice(wrong)
        inverted = True
    rng.shuffle(pairs)
    labels = "ABCD"
    new_opts = []
    new_key = old_key
    for i, (_, content) in enumerate(pairs[:4]):
        lab = labels[i]
        new_opts.append({"label": lab, "content": content})
        if content == target_content:
            new_key = lab
    q["options"] = new_opts
    q["stem"] = stem
    q["answer_key"] = new_key
    q["answer_accept"] = [new_key]
    exp = (q.get("explanation") or "").strip()
    body = re.sub(r"^[A-D对错][.。、．]?\s*", "", exp).strip()
    if inverted:
        q["explanation"] = f"{new_key}。本题改为选错误项。"
    elif body:
        q["explanation"] = f"{new_key}。{body}"
    else:
        q["explanation"] = new_key
    return q


_PYTHAG_LEGS = re.compile(
    r"(?:两)?直角边(?:分别为|为)\s*(\d+)\s*(?:[、,，]|和)\s*(\d+)"
)
_PYTHAG_TRIPLES = [
    (3, 4, 5),
    (5, 12, 13),
    (6, 8, 10),
    (7, 24, 25),
    (8, 15, 17),
    (9, 12, 15),
    (9, 40, 41),
    (12, 16, 20),
    (12, 35, 37),
    (15, 20, 25),
    (15, 36, 39),
    (18, 24, 30),
    (20, 21, 29),
]


def _simplify_sqrt(n: int) -> tuple[int, int]:
    """把 n 写成 coeff² · rest，rest 无平方因数。"""
    coeff = 1
    rest = int(n)
    d = 2
    while d * d <= rest:
        while rest % (d * d) == 0:
            rest //= d * d
            coeff *= d
        d += 1
    return coeff, rest


def _hyp_from_legs(a: int, b: int) -> tuple[str, list[str], str]:
    """由两直角边算出斜边标准答案与可接受写法。"""
    hyp2 = a * a + b * b
    root = int(round(hyp2 ** 0.5))
    if root * root == hyp2:
        key = str(root)
        return (
            key,
            [key],
            (
                f"【考点】勾股定理。\n"
                f"【思路】{a}²+{b}²={hyp2}={root}²，斜边是 {root}。\n"
                f"【易错】直角边变了，斜边就要重算，不能一律写 5。"
            ),
        )
    coeff, rest = _simplify_sqrt(hyp2)
    if coeff == 1:
        key = f"√{rest}"
        accept = [key, f"sqrt{rest}", f"\\sqrt{{{rest}}}"]
    else:
        key = f"{coeff}√{rest}"
        accept = [
            key,
            f"{coeff}sqrt{rest}",
            f"{coeff}*√{rest}",
            f"√{hyp2}",
            f"sqrt{hyp2}",
        ]
    return (
        key,
        accept,
        (
            f"【考点】勾股定理。\n"
            f"【思路】{a}²+{b}²={hyp2}，化简得 {key}。\n"
            f"【易错】不要硬写成 5；能化简的根式要化简。"
        ),
    )


def _vary_math_numbers(q: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """数学填空变式：必须重算答案。禁止只改题面数字却沿用旧答案（如斜边永远是 5）。"""
    if (q.get("qtype") or "") != "fill":
        return q
    stem = q.get("stem") or ""
    m = _PYTHAG_LEGS.search(stem)
    if m and "斜边" in stem:
        a, b, c = rng.choice(_PYTHAG_TRIPLES)
        if rng.random() < 0.5:
            a, b = b, a
        q["stem"] = (
            stem[: m.start(1)] + str(a) + stem[m.end(1) : m.start(2)] + str(b) + stem[m.end(2) :]
        )
        q["answer_key"] = str(c)
        q["answer_accept"] = [str(c)]
        q["explanation"] = (
            f"【考点】勾股定理：直角边 a、b，斜边 c，满足 a²+b²=c²。\n"
            f"【思路】{a}²+{b}²={a * a}+{b * b}={a * a + b * b}={c}²，所以斜边是 {c}。\n"
            f"【易错】数字一变就要重算，不能看见直角三角形就写 5。"
        )
        return q
    # 无法安全重算的填空：保持原题，避免改数字导致答案错误
    return q


def _rewrite_md_answer_line(md: str, number: int, key: str) -> str:
    """改参考答案里第 n 题的答案行，题干不动。"""
    parts = md.split("## 参考答案", 1)
    if len(parts) != 2:
        return md
    head, tail = parts
    new_tail, nsub = re.subn(
        rf"(?m)^{number}\. .*$",
        f"{number}. {key}",
        tail,
        count=1,
    )
    if nsub == 0:
        return md
    return head + "## 参考答案" + new_tail


def repair_computed_fill_answers() -> int:
    """修正库中「改了直角边却仍答 5」这类计算填空，并回写试卷文件。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, paper_id, stem, answer_key, sort_order
        FROM questions
        WHERE stem LIKE '%直角边%' AND stem LIKE '%斜边%'
        """
    ).fetchall()
    n = 0
    now = datetime.now().isoformat(timespec="seconds")
    touched: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        m = _PYTHAG_LEGS.search(r["stem"] or "")
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        key, accept, exp = _hyp_from_legs(a, b)
        old = (r["answer_key"] or "").strip()
        if old == key:
            continue
        conn.execute(
            """
            UPDATE questions
            SET answer_key = ?, answer_accept = ?, explanation = ?, updated_at = ?
            WHERE id = ?
            """,
            (key, json.dumps(accept, ensure_ascii=False), exp, now, r["id"]),
        )
        n += 1
        pid = r["paper_id"]
        touched.setdefault(pid, []).append((int(r["sort_order"] or 0), key))
    for paper_id, updates in touched.items():
        row = conn.execute(
            "SELECT path, content_md FROM assessments WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if row and (row["content_md"] or "").strip():
            md = row["content_md"]
            for num, key in updates:
                if num:
                    md = _rewrite_md_answer_line(md, num, key)
            if md != row["content_md"]:
                conn.execute(
                    "UPDATE assessments SET content_md = ? WHERE id = ?",
                    (md, paper_id),
                )
                path = row["path"] or ""
                if path.endswith(".md"):
                    fp = ROOT / path
                    if fp.is_file():
                        fp.write_text(md, encoding="utf-8")
        export_paper_json(conn, paper_id)
    conn.commit()
    conn.close()
    return n


def _render_assessment_md(
    *,
    subject_id: str,
    theme: str,
    date_s: str,
    target_level: str,
    knowledge_ids: list[str],
    questions: list[dict[str, Any]],
    note: str,
    pass_pct: int,
) -> str:
    kids_yaml = "\n".join(f"  - {k}" for k in knowledge_ids)
    lines = [
        "---",
        f"subject: {subject_id}",
        f"theme: {theme}",
        f"date: {date_s}",
        "minutes: 25",
        f"target_level: {target_level}",
        "knowledge_ids:",
        kids_yaml,
        "---",
        "",
        f"# 考核：{subject_id} · 冲 {target_level}",
        "",
        f"通过线：绑定各知识点的题目正确率 ≥ **{pass_pct}%** 视为该点通过；通过后可晋级（见 mastery-policy）。",
        f"说明：{note}",
        "",
        "## 题目",
        "",
    ]
    answers = ["## 参考答案", "", "（作答前请勿查看）", ""]
    for i, q in enumerate(questions, 1):
        kid = (q.get("knowledge_ids") or ["unknown"])[0]
        qtype = q.get("qtype") or "judge"
        stem = q.get("stem") or ""
        lines.append(f"{i}. （{kid}）{stem}")
        if qtype == "choice":
            for opt in q.get("options") or []:
                lines.append(f"   - {opt['label']}. {opt['content']}")
        lines.append("")
        key = q.get("answer_key") or ""
        exp = (q.get("explanation") or "").strip()
        exp_body = exp
        if key:
            exp_body = re.sub(rf"^{re.escape(key)}[.。、．]?\s*", "", exp).strip()
        if qtype == "short":
            answers.append(f"{i}. 评分要点：{key or exp}")
        else:
            answers.append(f"{i}. {key}" if key else f"{i}. {exp}")
        if exp_body:
            answers.append(f"   解析：{exp_body}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(answers)
    lines.append("")
    return "\n".join(lines)
