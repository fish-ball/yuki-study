"""按日计划知识点生成练习题，并跟踪完成状态。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from .db import PRACTICE_DIR, get_conn, init_db
from .sync import export_paper_json


def ensure_drill_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS day_drills (
          id TEXT PRIMARY KEY,
          day_plan_id TEXT NOT NULL,
          plan_date TEXT NOT NULL,
          knowledge_id TEXT NOT NULL,
          knowledge_name TEXT NOT NULL DEFAULT '',
          paper_id TEXT NOT NULL,
          subject_id TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          question_count INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT,
          last_session_id TEXT,
          created_at TEXT,
          updated_at TEXT,
          UNIQUE(day_plan_id, knowledge_id)
        );
        CREATE INDEX IF NOT EXISTS idx_day_drills_date ON day_drills(plan_date, status);
        """
    )
    conn.commit()


def ensure_day_drills(
    plan_date: str | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """为指定日计划的每个知识点生成/补齐练习卷（含题目）。

    force_refresh=True 时：清空旧题并按题库重出（含已完成卷，便于「刷新题目」）。
    已达练习上限但未到 L4：不新建练习，改走考核；到顶则记完成。
    """
    from .mastery_policy import is_assessment_at_cap, is_practice_exempt, load_policy
    from .practice import _set_paper_status

    plan_date = plan_date or date.today().isoformat()
    conn = get_conn()
    init_db(conn)
    ensure_drill_tables(conn)
    policy = load_policy()

    day = conn.execute(
        "SELECT * FROM day_plans WHERE plan_date = ?", (plan_date,)
    ).fetchone()
    if not day:
        conn.close()
        raise ValueError(f"没有 {plan_date} 的日计划，请先在「学习计划」生成日计划")

    items = conn.execute(
        """
        SELECT i.knowledge_id, i.sort_order, k.name, k.subject_id
        FROM day_plan_items i
        LEFT JOIN knowledge_nodes k ON k.id = i.knowledge_id
        WHERE i.day_plan_id = ?
        ORDER BY i.sort_order, i.id
        """,
        (day["id"],),
    ).fetchall()

    created = 0
    updated = 0
    skipped = 0
    drills = []
    now = datetime.now().isoformat(timespec="seconds")

    for it in items:
        kid = it["knowledge_id"]
        name = it["name"] or kid
        subject_id = it["subject_id"] or (kid.split(".", 1)[0] if "." in kid else None)
        drill_id = f"drill_{plan_date}__{kid}"
        paper_id = f"drill-{plan_date}-{_safe_id(kid)}"

        m = conn.execute(
            "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
        ).fetchone()
        cur_lv = m["level"] if m else "L0"
        at_cap = is_practice_exempt(cur_lv, policy)

        existing = conn.execute(
            "SELECT * FROM day_drills WHERE id = ?", (drill_id,)
        ).fetchone()

        # 已达练习最高档但未到 L4：不刷练习，留给考核；到顶则记完成
        if is_assessment_at_cap(cur_lv):
            skipped += 1
            conn.execute(
                """
                UPDATE day_plan_items
                SET done = 1, done_at = COALESCE(done_at, ?)
                WHERE day_plan_id = ? AND knowledge_id = ?
                """,
                (now, day["id"], kid),
            )
            if existing:
                conn.execute(
                    """
                    UPDATE day_drills
                    SET status = 'completed', completed_at = COALESCE(completed_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, drill_id),
                )
                try:
                    _set_paper_status(conn, existing["paper_id"] or paper_id, "retired")
                except (KeyError, TypeError, ValueError):
                    pass
            continue
        if at_cap:
            skipped += 1
            if existing:
                try:
                    _set_paper_status(conn, existing["paper_id"] or paper_id, "retired")
                except (KeyError, TypeError, ValueError):
                    pass
            continue

        # 已完成且未强制刷新：保留
        if existing and existing["status"] == "completed" and not force_refresh:
            drills.append(dict(existing))
            continue

        qcount = _ensure_drill_paper(
            conn,
            paper_id,
            plan_date,
            kid,
            name,
            subject_id,
            force=force_refresh or not existing,
        )
        if existing:
            if force_refresh:
                conn.execute(
                    """
                    UPDATE day_drills
                    SET paper_id = ?, knowledge_name = ?, subject_id = ?,
                        question_count = ?, status = 'pending',
                        completed_at = NULL, last_session_id = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (paper_id, name, subject_id, qcount, now, drill_id),
                )
                conn.execute(
                    """
                    UPDATE day_plan_items
                    SET done = 0, done_at = NULL
                    WHERE day_plan_id = ? AND knowledge_id = ?
                    """,
                    (day["id"], kid),
                )
            else:
                conn.execute(
                    """
                    UPDATE day_drills
                    SET paper_id = ?, knowledge_name = ?, subject_id = ?,
                        question_count = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (paper_id, name, subject_id, qcount, now, drill_id),
                )
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO day_drills
                (id, day_plan_id, plan_date, knowledge_id, knowledge_name, paper_id,
                 subject_id, status, question_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    drill_id,
                    day["id"],
                    plan_date,
                    kid,
                    name,
                    paper_id,
                    subject_id,
                    qcount,
                    now,
                    now,
                ),
            )
            created += 1

        row = conn.execute("SELECT * FROM day_drills WHERE id = ?", (drill_id,)).fetchone()
        drills.append(dict(row))

    if force_refresh:
        conn.execute(
            """
            UPDATE day_plans
            SET status = 'pending', completed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, day["id"]),
        )

    conn.commit()
    conn.close()
    return {
        "plan_date": plan_date,
        "day_plan_id": day["id"],
        "created": created,
        "updated": updated,
        "skipped_at_cap": skipped,
        "refreshed": force_refresh,
        "drills": _enrich_drills(drills),
    }


def refresh_day_drills(plan_date: str | None = None) -> dict[str, Any]:
    """强制刷新某日全部练习题（去重、换新题干）。"""
    return ensure_day_drills(plan_date, force_refresh=True)


def list_day_drills(
    plan_date: str | None = None,
    include_completed: bool = False,
) -> dict[str, Any]:
    plan_date = plan_date or date.today().isoformat()
    conn = get_conn()
    ensure_drill_tables(conn)

    # 若日计划有条目但 drills 不足，自动补齐
    day = conn.execute(
        "SELECT id FROM day_plans WHERE plan_date = ?", (plan_date,)
    ).fetchone()
    item_n = 0
    if day:
        item_n = conn.execute(
            "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
            (day["id"],),
        ).fetchone()["c"]
    drill_n = conn.execute(
        "SELECT COUNT(*) AS c FROM day_drills WHERE plan_date = ?",
        (plan_date,),
    ).fetchone()["c"]
    empty_q = conn.execute(
        """
        SELECT COUNT(*) AS c FROM day_drills
        WHERE plan_date = ? AND (question_count IS NULL OR question_count = 0)
          AND status != 'completed'
        """,
        (plan_date,),
    ).fetchone()["c"]
    conn.close()

    if day and item_n > 0 and (drill_n < item_n or empty_q > 0):
        ensure_day_drills(plan_date)

    conn = get_conn()
    ensure_drill_tables(conn)
    sql = "SELECT * FROM day_drills WHERE plan_date = ?"
    params: list[Any] = [plan_date]
    if not include_completed:
        sql += " AND status != 'completed'"
    sql += " ORDER BY knowledge_id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    completed = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM day_drills
            WHERE plan_date = ? AND status = 'completed'
            ORDER BY completed_at DESC, knowledge_id
            """,
            (plan_date,),
        ).fetchall()
    ]
    conn.close()
    return {
        "plan_date": plan_date,
        "pending": _enrich_drills(rows),
        "completed": _enrich_drills(completed),
        "groups": _group_by_knowledge(_enrich_drills(rows)),
    }


def list_completed_drills(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    ensure_drill_tables(conn)
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM day_drills
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    conn.close()
    return _enrich_drills(rows)


def session_covers_paper(conn, session_id: str | None, paper_id: str) -> bool:
    """至少答过该卷全部题目，才算真正完成练习。"""
    if not session_id:
        return False
    q_total = conn.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE paper_id = ?", (paper_id,)
    ).fetchone()["c"]
    if q_total <= 0:
        return False
    answered = conn.execute(
        """
        SELECT COUNT(DISTINCT question_id) AS c
        FROM attempt_records
        WHERE session_id = ? AND paper_id = ?
        """,
        (session_id, paper_id),
    ).fetchone()["c"]
    return answered >= q_total


def reopen_incomplete_drills(plan_date: str | None = None) -> dict[str, Any]:
    """撤回「未真正做题却标完成」的日练习，并同步日计划进度。

    已达练习上限的知识点：保持完成（免练），不撤回。
    """
    from .mastery_policy import is_assessment_at_cap, is_practice_exempt, load_policy

    plan_date = plan_date or date.today().isoformat()
    conn = get_conn()
    init_db(conn)
    ensure_drill_tables(conn)
    policy = load_policy()
    now = datetime.now().isoformat(timespec="seconds")
    reopened = 0
    rows = conn.execute(
        "SELECT * FROM day_drills WHERE plan_date = ?", (plan_date,)
    ).fetchall()
    for row in rows:
        if row["status"] != "completed":
            continue
        m = conn.execute(
            "SELECT level FROM mastery_items WHERE knowledge_id = ?",
            (row["knowledge_id"],),
        ).fetchone()
        cur_lv = m["level"] if m else "L0"
        # 已到 L4：保持完成
        if is_assessment_at_cap(cur_lv):
            continue
        # 免练但未真正考核：日计划条目不算完成
        if is_practice_exempt(cur_lv, policy):
            conn.execute(
                """
                UPDATE day_plan_items
                SET done = 0, done_at = NULL
                WHERE day_plan_id = ? AND knowledge_id = ? AND done = 1
                """,
                (row["day_plan_id"], row["knowledge_id"]),
            )
            continue
        if session_covers_paper(conn, row["last_session_id"], row["paper_id"]):
            continue
        conn.execute(
            """
            UPDATE day_drills
            SET status = 'pending', completed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, row["id"]),
        )
        conn.execute(
            """
            UPDATE day_plan_items
            SET done = 0, done_at = NULL
            WHERE day_plan_id = ? AND knowledge_id = ?
            """,
            (row["day_plan_id"], row["knowledge_id"]),
        )
        reopened += 1

    # 若该日仍有未完成练习，日计划不得保持 completed
    pending_n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM day_drills
        WHERE plan_date = ? AND status != 'completed'
        """,
        (plan_date,),
    ).fetchone()["c"]
    if pending_n > 0:
        conn.execute(
            """
            UPDATE day_plans
            SET status = 'pending', completed_at = NULL, updated_at = ?
            WHERE plan_date = ? AND status = 'completed'
            """,
            (now, plan_date),
        )
        # 若已有进行中练习，则标为进行中
        inprog = conn.execute(
            """
            SELECT COUNT(*) AS c FROM day_drills
            WHERE plan_date = ? AND status = 'in_progress'
            """,
            (plan_date,),
        ).fetchone()["c"]
        if inprog > 0:
            conn.execute(
                """
                UPDATE day_plans
                SET status = 'in_progress', updated_at = ?
                WHERE plan_date = ?
                """,
                (now, plan_date),
            )
    conn.commit()
    conn.close()
    return {"plan_date": plan_date, "reopened": reopened}


def mark_drill_completed_by_paper(paper_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    """练习会话结束后，仅在真正答完该卷全部题时标为已完成。"""
    conn = get_conn()
    ensure_drill_tables(conn)
    row = conn.execute(
        "SELECT * FROM day_drills WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    now = datetime.now().isoformat(timespec="seconds")
    if not session_covers_paper(conn, session_id, paper_id):
        # 未做完：仅记进行中，绝不标完成
        conn.execute(
            """
            UPDATE day_drills
            SET status = CASE WHEN status = 'completed' THEN 'completed' ELSE 'in_progress' END,
                last_session_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (session_id, now, row["id"]),
        )
        conn.execute(
            """
            UPDATE day_plans
            SET status = CASE WHEN status = 'pending' THEN 'in_progress' ELSE status END,
                updated_at = ?
            WHERE id = ?
            """,
            (now, row["day_plan_id"]),
        )
        conn.commit()
        out = dict(
            conn.execute("SELECT * FROM day_drills WHERE id = ?", (row["id"],)).fetchone()
        )
        out["completed_now"] = False
        conn.close()
        return out

    conn.execute(
        """
        UPDATE day_drills
        SET status = 'completed', completed_at = ?, last_session_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, session_id, now, row["id"]),
    )
    # 同步勾选日计划知识点
    conn.execute(
        """
        UPDATE day_plan_items
        SET done = 1, done_at = ?
        WHERE day_plan_id = ? AND knowledge_id = ?
        """,
        (now, row["day_plan_id"], row["knowledge_id"]),
    )
    # 日计划进入进行中（全部练完后由计划层提示问卷，不在此直接 completed）
    conn.execute(
        """
        UPDATE day_plans
        SET status = CASE WHEN status = 'completed' THEN status ELSE 'in_progress' END,
            updated_at = ?
        WHERE id = ?
        """,
        (now, row["day_plan_id"]),
    )
    conn.commit()
    out = dict(
        conn.execute("SELECT * FROM day_drills WHERE id = ?", (row["id"],)).fetchone()
    )
    out["completed_now"] = True
    conn.close()
    return out


def mark_drill_in_progress(paper_id: str, session_id: str) -> None:
    conn = get_conn()
    ensure_drill_tables(conn)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE day_drills
        SET status = CASE WHEN status = 'completed' THEN 'completed' ELSE 'in_progress' END,
            last_session_id = ?, updated_at = ?
        WHERE paper_id = ?
        """,
        (session_id, now, paper_id),
    )
    conn.commit()
    conn.close()


def _enrich_drills(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .mastery_policy import format_practice_label, load_policy

    policy = load_policy()
    conn = get_conn()
    out = []
    for r in rows:
        kid = r.get("knowledge_id")
        m = conn.execute(
            "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
        ).fetchone() if kid else None
        cur = m["level"] if m else "L0"
        info = format_practice_label(
            current_level=cur,
            knowledge_name=r.get("knowledge_name") or kid or "",
            policy=policy,
        )
        out.append(
            {
                **r,
                "label": info["title"],
                "level_label": info["title"],
                "current_level": cur,
                "practice_max_level": info["practice_max_level"],
                "goal_level": info["goal_level"],
                "exempt": info["exempt"],
            }
        )
    conn.close()
    return out


def _group_by_knowledge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按知识点分组（每个知识点一条 drill，仍返回列表便于前端）。"""
    groups = []
    for r in rows:
        groups.append(
            {
                "knowledge_id": r["knowledge_id"],
                "knowledge_name": r.get("knowledge_name") or r["knowledge_id"],
                "subject_id": r.get("subject_id"),
                "drills": [r],
                "paper_id": r["paper_id"],
                "question_count": r.get("question_count") or 0,
                "status": r.get("status"),
                "drill_id": r["id"],
            }
        )
    return groups


def _safe_id(kid: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", kid)


def _clear_paper_questions(conn, paper_id: str) -> None:
    """清空卷面题目。若题目已被作答引用，则归档到旁路卷，避免外键失败。"""
    qids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM questions WHERE paper_id = ?", (paper_id,)
        ).fetchall()
    ]
    if not qids:
        return
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    archive_paper = f"{paper_id}__archived_{stamp}"
    src = conn.execute(
        "SELECT subject_id, theme, date FROM assessments WHERE id = ?",
        (paper_id,),
    ).fetchone()
    # 先建归档卷（questions.paper_id 外键指向 assessments）
    conn.execute(
        """
        INSERT OR IGNORE INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, 15, 'L2', 'archived', '', ?)
        """,
        (
            archive_paper,
            f"practice/papers/{archive_paper}.json",
            (src["subject_id"] if src else None),
            f"（归档）{(src['theme'] if src else paper_id)}",
            (src["date"] if src else date.today().isoformat()),
            f"由 {paper_id} 刷新归档",
        ),
    )
    for qid in qids:
        used = conn.execute(
            "SELECT 1 FROM attempt_records WHERE question_id = ? LIMIT 1",
            (qid,),
        ).fetchone()
        if used:
            conn.execute(
                "UPDATE questions SET paper_id = ? WHERE id = ?",
                (archive_paper, qid),
            )
            continue
        conn.execute("DELETE FROM question_options WHERE question_id = ?", (qid,))
        conn.execute("DELETE FROM question_knowledge WHERE question_id = ?", (qid,))
        conn.execute("DELETE FROM questions WHERE id = ?", (qid,))


def _is_meta_stem(stem: str) -> bool:
    """识别空洞/系统元题目（非学科可答题）。"""
    s = stem or ""
    markers = (
        "本练习对应的知识点名称",
        "更有效的方式是",
        "下列说法最恰当的是",
        "既要记住结论，也要能解释为什么",
        "既要记住结论，也要能用自己的话解释含义",
        "只需记住名称，不必理解含义",
        "该知识点与考试无关",
        "knowledge_id",
        "本课主攻知识点",
        "正确的学习态度是",
        "用一句话概括",
        "可写关键词",
        "先抓定义与适用条件，再做变式题",
        "只背名称，不做题",
        "抄答案就算掌握",
        "不会的题永远跳过",
        "属于中考相关考点，需要结合例题巩固",
        "网址",
        "http://",
        "https://",
        "URL",
        "网站地址",
    )
    return any(m in s for m in markers)


def _ensure_drill_paper(
    conn,
    paper_id: str,
    plan_date: str,
    knowledge_id: str,
    name: str,
    subject_id: str | None,
    *,
    force: bool = False,
) -> int:
    """确保试卷与题目存在；优先用知识点专属题库，避免空洞模板与重复复制。"""
    now = date.today().isoformat()
    theme = f"日计划练习 · {name}"
    note = f"知识点 {knowledge_id}（{plan_date}）"
    conn.execute(
        """
        INSERT INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, 15, 'L2', 'ready', '', ?)
        ON CONFLICT(id) DO UPDATE SET
          subject_id=excluded.subject_id,
          theme=excluded.theme,
          date=excluded.date,
          note=excluded.note
        """,
        (
            paper_id,
            f"practice/papers/{paper_id}.json",
            subject_id,
            theme,
            plan_date,
            note,
        ),
    )

    if force:
        _clear_paper_questions(conn, paper_id)

    current = conn.execute(
        "SELECT id, stem FROM questions WHERE paper_id = ? ORDER BY sort_order",
        (paper_id,),
    ).fetchall()
    # 已有足够且无元题目：直接返回
    if not force and len(current) >= 5 and not any(_is_meta_stem(r["stem"]) for r in current):
        export_paper_json(conn, paper_id)
        return len(current)

    # 卷内有元题目或题量不足：清空后按专属题库重出
    if current and (force or any(_is_meta_stem(r["stem"]) for r in current) or len(current) < 5):
        _clear_paper_questions(conn, paper_id)

    bank = _question_bank(knowledge_id, name)
    seen_stems: set[str] = set()
    idx = 0
    stamp = datetime.now().strftime("%H%M%S")
    for t in bank:
        stem = (t.get("stem") or "").strip()
        if not stem or stem in seen_stems or _is_meta_stem(stem):
            continue
        seen_stems.add(stem)
        idx += 1
        if idx > 5:
            break
        # 带时间戳，避免与归档题 id 冲突
        qid = f"{paper_id}__{stamp}_q{idx:02d}"
        if conn.execute("SELECT 1 FROM questions WHERE id = ?", (qid,)).fetchone():
            qid = f"{paper_id}__{stamp}_{idx:02d}_{idx}"
        conn.execute(
            """
            INSERT INTO questions
            (id, paper_id, subject_id, qtype, stem, score, sort_order, explanation,
             answer_key, answer_accept, auto_gradable, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'drill_bank', ?, ?)
            """,
            (
                qid,
                paper_id,
                subject_id,
                t["qtype"],
                stem,
                t["score"],
                idx,
                t["explanation"],
                t["answer_key"],
                json.dumps(t["answer_accept"], ensure_ascii=False),
                1,
                now,
                now,
            ),
        )
        for i, opt in enumerate(t.get("options") or []):
            conn.execute(
                """
                INSERT INTO question_options (question_id, label, content, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (qid, opt["label"], opt["content"], i),
            )
        conn.execute(
            "INSERT OR IGNORE INTO question_knowledge (question_id, knowledge_id) VALUES (?, ?)",
            (qid, knowledge_id),
        )

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE paper_id = ?", (paper_id,)
    ).fetchone()["c"]
    export_paper_json(conn, paper_id)
    return total


def _question_bank(knowledge_id: str, name: str) -> list[dict[str, Any]]:
    """按知识点返回专属题干（去空洞模板）。"""
    banks: dict[str, list[dict[str, Any]]] = {
        "chemistry.basic.molecule": [
            {
                "qtype": "judge",
                "stem": "分子是保持物质化学性质的最小粒子。",
                "score": 2,
                "options": [],
                "answer_key": "对",
                "answer_accept": ["对"],
                "explanation": "对。同种分子化学性质相同。",
            },
            {
                "qtype": "judge",
                "stem": "在化学变化中，原子可以再分成更小的粒子。",
                "score": 2,
                "options": [],
                "answer_key": "错",
                "answer_accept": ["错"],
                "explanation": "错。化学变化中分子可分、原子不可分。",
            },
            {
                "qtype": "choice",
                "stem": "下列关于分子、原子的说法正确的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "分子一定比原子大"},
                    {"label": "B", "content": "原子在任何情况下都不能再分"},
                    {"label": "C", "content": "化学变化中分子可分，原子不可分"},
                    {"label": "D", "content": "同种原子构成的分子一定是化合物分子"},
                ],
                "answer_key": "C",
                "answer_accept": ["C"],
                "explanation": "C。A 错（如汞原子可大于氢分子）；B 错（原子由原子核和电子构成）；D 错（同种原子构成单质分子，如 O₂）。",
            },
            {
                "qtype": "fill",
                "stem": "水分子用符号 ______ 表示；一个水分子由 ______ 个氢原子和 ______ 个氧原子构成。",
                "score": 4,
                "options": [],
                "answer_key": "H2O；2；1",
                "answer_accept": ["H2O", "H₂O", "2", "1"],
                "explanation": "H2O（或 H₂O）；2；1。",
            },
            {
                "qtype": "fill",
                "stem": "保持水的化学性质的最小粒子是 ______；电解水时不能再分的粒子是 ______。",
                "score": 4,
                "options": [],
                "answer_key": "水分子；原子",
                "answer_accept": ["水分子", "原子"],
                "explanation": "水分子；原子。",
            },
            {
                "qtype": "choice",
                "stem": "氧分子（O₂）与氧原子（O）的关系，说法正确的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "一个氧分子就是一个氧原子"},
                    {"label": "B", "content": "一个氧分子由两个氧原子构成"},
                    {"label": "C", "content": "氧分子和氧原子化学性质完全相同"},
                    {"label": "D", "content": "氧原子不能构成氧分子"},
                ],
                "answer_key": "B",
                "answer_accept": ["B"],
                "explanation": "B。O₂ 由 2 个 O 构成；分子与原子是不同层次的粒子。",
            },
            {
                "qtype": "judge",
                "stem": "离子是带电的原子或原子团。",
                "score": 2,
                "options": [],
                "answer_key": "对",
                "answer_accept": ["对"],
                "explanation": "对。预习阶段先记住「带电粒子」即可。",
            },
        ],
        "chemistry.basic.element": [
            {
                "qtype": "judge",
                "stem": "元素符号 O 既可以表示氧元素，也可以表示一个氧原子。",
                "score": 2,
                "options": [],
                "answer_key": "对",
                "answer_accept": ["对"],
                "explanation": "对。元素符号有双重含义。",
            },
            {
                "qtype": "judge",
                "stem": "双字母元素符号书写时，两个字母都要大写，例如氯写作 CL。",
                "score": 2,
                "options": [],
                "answer_key": "错",
                "answer_accept": ["错"],
                "explanation": "错。必须「首字母大写、第二个字母小写」，氯写作 Cl。",
            },
            {
                "qtype": "choice",
                "stem": "下列元素符号书写正确的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "CA（钙）"},
                    {"label": "B", "content": "zn（锌）"},
                    {"label": "C", "content": "Mg（镁）"},
                    {"label": "D", "content": "AL（铝）"},
                ],
                "answer_key": "C",
                "answer_accept": ["C"],
                "explanation": "C。A 应为 Ca；B 应为 Zn；D 应为 Al。",
            },
            {
                "qtype": "fill",
                "stem": "写出下列元素的元素符号：氢 ______；氧 ______；碳 ______；氮 ______。",
                "score": 4,
                "options": [],
                "answer_key": "H；O；C；N",
                "answer_accept": ["H", "O", "C", "N"],
                "explanation": "H；O；C；N。",
            },
            {
                "qtype": "fill",
                "stem": "写出下列元素的元素符号：钠 ______；镁 ______；铝 ______；铁 ______；铜 ______。",
                "score": 4,
                "options": [],
                "answer_key": "Na；Mg；Al；Fe；Cu",
                "answer_accept": ["Na", "Mg", "Al", "Fe", "Cu"],
                "explanation": "Na；Mg；Al；Fe；Cu。注意双字母「一大一小」。",
            },
            {
                "qtype": "choice",
                "stem": "下列说法正确的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "元素符号 fe 可以表示铁元素"},
                    {"label": "B", "content": "元素符号 He 只表示氦元素，不表示原子"},
                    {"label": "C", "content": "碳的元素符号是 C"},
                    {"label": "D", "content": "氯的正确写法是 CL"},
                ],
                "answer_key": "C",
                "answer_accept": ["C"],
                "explanation": "C。A 应为 Fe；B 错（也可表示一个氦原子）；D 应为 Cl。",
            },
            {
                "qtype": "fill",
                "stem": "元素符号书写规则：一个字母时 ______ 写；两个字母时第一个字母 ______，第二个字母 ______。",
                "score": 4,
                "options": [],
                "answer_key": "大写；大写；小写",
                "answer_accept": ["大写", "大写", "小写"],
                "explanation": "大写；大写；小写。",
            },
        ],
        "chemistry.basic.change": [
            {
                "qtype": "judge",
                "stem": "有新物质生成的变化一定是化学变化。",
                "score": 2,
                "options": [],
                "answer_key": "对",
                "answer_accept": ["对"],
                "explanation": "对。化学变化的本质特征是生成新物质。",
            },
            {
                "qtype": "choice",
                "stem": "下列属于化学变化的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "水结成冰"},
                    {"label": "B", "content": "酒精挥发"},
                    {"label": "C", "content": "镁条燃烧"},
                    {"label": "D", "content": "玻璃破碎"},
                ],
                "answer_key": "C",
                "answer_accept": ["C"],
                "explanation": "C。镁条燃烧生成氧化镁等新物质；其余无新物质。",
            },
            {
                "qtype": "judge",
                "stem": "发光发热的变化一定是化学变化。",
                "score": 2,
                "options": [],
                "answer_key": "错",
                "answer_accept": ["错"],
                "explanation": "错。电灯发光发热是物理变化；判断关键仍是「有无新物质」。",
            },
            {
                "qtype": "fill",
                "stem": "物理变化与化学变化的根本区别是有没有 ______ 生成。",
                "score": 4,
                "options": [],
                "answer_key": "新物质",
                "answer_accept": ["新物质"],
                "explanation": "新物质。",
            },
            {
                "qtype": "choice",
                "stem": "下列变化属于物理变化的是（　　）",
                "score": 4,
                "options": [
                    {"label": "A", "content": "铁生锈"},
                    {"label": "B", "content": "蜡烛燃烧"},
                    {"label": "C", "content": "食盐溶于水"},
                    {"label": "D", "content": "纸张燃烧"},
                ],
                "answer_key": "C",
                "answer_accept": ["C"],
                "explanation": "C。溶解一般看作物理变化（初中口径）；其余有新物质。",
            },
        ],
    }
    if knowledge_id in banks:
        return banks[knowledge_id]
    # 按 id / 名称匹配更多数学考点题库
    matched = _math_topic_bank(knowledge_id, name)
    if matched:
        return matched
    # 未知知识点：给学科内容题，禁止 knowledge_id / 学习态度等元题目
    return _subject_content_fallback(knowledge_id, name)


def _math_topic_bank(knowledge_id: str, name: str) -> list[dict[str, Any]]:
    """按数学 knowledge_id 片段匹配可用题库。"""
    kid = knowledge_id or ""
    table: list[tuple[str, list[dict[str, Any]]]] = [
        ("math.number.radical", _bank_radical()),
        ("math.equation.quadratic", _bank_quadratic_eq()),
        ("math.function.linear", _bank_linear_fn()),
        ("math.function.quadratic.graph", _bank_quad_graph()),
        ("math.geometry.circle.angle", _bank_circle_angle()),
        ("math.geometry.similar", _bank_similar()),
        ("math.stats.probability", _bank_probability()),
        ("math.number.factor", _bank_factor()),
        ("math.equation.ineq", _bank_ineq()),
        ("math.integrated.geometry_hard", _bank_geometry_hard()),
    ]
    for prefix, bank in table:
        if kid == prefix or kid.startswith(prefix + "."):
            return bank
    # 名称兜底
    name_map = {
        "二次根式": _bank_radical,
        "一元二次方程": _bank_quadratic_eq,
        "一次函数": _bank_linear_fn,
        "二次函数图像与性质": _bank_quad_graph,
        "圆周角与圆心角": _bank_circle_angle,
        "相似三角形": _bank_similar,
        "概率初步": _bank_probability,
        "因式分解": _bank_factor,
        "一元一次不等式": _bank_ineq,
        "几何压轴": _bank_geometry_hard,
    }
    for key, fn in name_map.items():
        if key in (name or ""):
            return fn()
    return []


def _q(
    qtype: str,
    stem: str,
    answer_key: str,
    *,
    options: list[dict[str, str]] | None = None,
    accept: list[str] | None = None,
    explanation: str = "",
    score: int = 2,
) -> dict[str, Any]:
    return {
        "qtype": qtype,
        "stem": stem,
        "score": score,
        "options": options or [],
        "answer_key": answer_key,
        "answer_accept": accept or [answer_key],
        "explanation": explanation or answer_key,
    }


def _bank_radical() -> list[dict[str, Any]]:
    return [
        _q(
            "judge",
            "二次根式被开方数必须是非负数。",
            "对",
            explanation="【考点】二次根式的定义。\n【思路】二次根式 √a 要求 a≥0，否则在实数范围内无意义。所以这句话正确。\n【易错】不要和三次根式搞混：∛a 的被开方数可以是负数。",
        ),
        _q(
            "fill",
            "化简：√16 = ______。",
            "4",
            accept=["4"],
            explanation="【考点】完全平方式的化简。\n【思路】16=4²，且 4>0，所以 √16=4。\n【易错】不要写成 ±4。二次根式 √ 表示非负算术平方根，只取 4。",
            score=2,
        ),
        _q(
            "choice",
            "下列各式中是二次根式的是（　　）",
            "B",
            options=[
                {"label": "A", "content": "√(-4)"},
                {"label": "B", "content": "√9"},
                {"label": "C", "content": "³√8"},
                {"label": "D", "content": "√a（a 可为任意实数）"},
            ],
            accept=["B"],
            explanation="【考点】二次根式：形如 √a 且 a≥0。\n【思路】B 的 √9 被开方数 9≥0，是二次根式。A 被开方数为负，实数内无意义；C 是三次根式；D 中 a 可为负数，不保证是二次根式。\n【易错】看见根号就当成二次根式；三次根式、被开方数为负都不算。",
            score=4,
        ),
        _q(
            "fill",
            "√(a²) = ______（a≥0）。",
            "a",
            accept=["a"],
            explanation="【考点】√(a²)=|a|。\n【思路】题目已限定 a≥0，所以 |a|=a，故 √(a²)=a。\n【易错】忘记绝对值：a 为负时应取 -a，本题因 a≥0 才直接等于 a。",
        ),
        _q(
            "judge",
            "√2 + √8 = √10。",
            "错",
            explanation="【考点】二次根式加减要先化成同类根式。\n【思路】√8=√(4×2)=2√2，所以 √2+√8=√2+2√2=3√2，不等于 √10。根式一般不能直接把被开方数相加。\n【易错】误用 √a+√b=√(a+b)。只有在特殊情况下才可能相等，这里不相等。",
        ),
    ]


def _bank_quadratic_eq() -> list[dict[str, Any]]:
    return [
        _q(
            "judge",
            "一元二次方程的一般形式可写成 ax²+bx+c=0（a≠0）。",
            "对",
            explanation="【考点】一元二次方程的一般式。\n【思路】只含一个未知数，未知数最高次数是 2，所以二次项系数 a 必须不为 0。b、c 可以为 0。\n【易错】漏写 a≠0。若 a=0 就变成一次方程或恒等式，不再是一元二次方程。",
        ),
        _q(
            "fill",
            "方程 x²-5x+6=0 的两根是 ______ 和 ______（从小到大）。",
            "2；3",
            accept=["2", "3"],
            score=4,
            explanation="【考点】因式分解法解一元二次方程。\n【思路】x²-5x+6=(x-2)(x-3)=0，所以 x-2=0 或 x-3=0，两根为 2 和 3。也可用求根公式：Δ=25-24=1，x=(5±1)/2。\n【易错】只写出一个根，或没按从小到大填写。",
        ),
        _q(
            "choice",
            "方程 x²=4 的解是（　　）",
            "C",
            options=[
                {"label": "A", "content": "x=2"},
                {"label": "B", "content": "x=-2"},
                {"label": "C", "content": "x=±2"},
                {"label": "D", "content": "无实数解"},
            ],
            accept=["C"],
            explanation="【考点】两边开平方或移项因式分解。\n【思路】x²-4=0，(x-2)(x+2)=0，得 x=2 或 x=-2，即 x=±2。A、B 都只写了一半；D 错，Δ=16>0 有两个实根。\n【易错】开平方漏掉负根，只写 x=2。",
            score=4,
        ),
        _q(
            "fill",
            "用求根公式时，判别式 Δ = ______。",
            "b²-4ac",
            accept=["b^2-4ac", "b²-4ac", "b2-4ac"],
            explanation="【考点】求根公式与判别式。\n【思路】一般式 ax²+bx+c=0（a≠0），Δ=b²-4ac。Δ>0 两不等实根，Δ=0 两相等实根，Δ<0 无实根。\n【易错】写成 4ac-b²，或漏掉平方。",
        ),
        _q(
            "judge",
            "若 Δ<0，则一元二次方程没有实数根。",
            "对",
            explanation="【考点】判别式与根的情况。\n【思路】实数范围内，Δ<0 时求根公式里根号下为负，没有实数根。\n【易错】和「没有根」混淆：在复数里可以有根，初中说「没有实数根」即可。",
        ),
    ]


def _bank_linear_fn() -> list[dict[str, Any]]:
    return [
        _q(
            "judge",
            "一次函数的一般式可写成 y=kx+b（k≠0）。",
            "对",
            explanation="【考点】一次函数的解析式。\n【思路】自变量次数为 1，所以 k 不能为 0。k 是斜率，b 是纵截距。\n【易错】漏写 k≠0。k=0 时 y=b 是常函数，不是一次函数。",
        ),
        _q(
            "fill",
            "直线 y=2x+1 的斜率是 ______，纵截距是 ______。",
            "2；1",
            accept=["2", "1"],
            score=4,
            explanation="【考点】y=kx+b 中 k、b 的意义。\n【思路】与 y=2x+1 对照：k=2（斜率），b=1（与 y 轴交于 (0,1)）。\n【易错】把斜率和截距写反；纵截距是 1 不是 (0,1) 这个点（填空要填数）。",
        ),
        _q(
            "choice",
            "函数 y=-3x+2 的图像经过（　　）",
            "A",
            options=[
                {"label": "A", "content": "第一、二、四象限"},
                {"label": "B", "content": "第一、三象限"},
                {"label": "C", "content": "第二、四象限"},
                {"label": "D", "content": "只过原点"},
            ],
            accept=["A"],
            explanation="【考点】一次函数 y=kx+b 的图象位置。\n【思路】k=-3<0，直线从左上到右下；b=2>0，与 y 轴正半轴相交。因此经过第一、二、四象限，不过第三象限，也不过原点。B 是正比例函数 k>0 的情形；C 是过原点且 k<0；D 要求 b=0。\n【易错】看见 k<0 就选第二、四象限，那是 b=0 的正比例函数。",
            score=4,
        ),
        _q(
            "fill",
            "若一次函数过原点，则 b = ______。",
            "0",
            accept=["0"],
            explanation="【考点】一次函数过原点的条件。\n【思路】点 (0,0) 代入 y=kx+b，得 b=0。这时就是正比例函数 y=kx（k≠0）。\n【易错】写成 k=0。k=0 不是一次函数。",
        ),
        _q(
            "judge",
            "正比例函数是一次函数的特例（b=0）。",
            "对",
            explanation="【考点】正比例函数与一次函数的关系。\n【思路】正比例函数 y=kx（k≠0）就是一次函数 y=kx+b 中 b=0 的情况。一次函数范围更大。\n【易错】说成「一次函数都是正比例函数」——反了，只有过原点的才是。",
        ),
    ]


def _bank_quad_graph() -> list[dict[str, Any]]:
    return [
        _q("judge", "二次函数 y=ax²+bx+c（a≠0）的图像是抛物线。", "对"),
        _q("fill", "抛物线 y=x² 的开口方向是 ______（向上/向下）。", "向上", accept=["向上"]),
        _q(
            "choice",
            "抛物线 y=-2x²+1 的顶点坐标是（　　）",
            "B",
            options=[
                {"label": "A", "content": "(0,0)"},
                {"label": "B", "content": "(0,1)"},
                {"label": "C", "content": "(1,0)"},
                {"label": "D", "content": "(1,-2)"},
            ],
            accept=["B"],
            explanation="B。对称轴 x=0，顶点 (0,1)。",
            score=4,
        ),
        _q("fill", "若 a>0，抛物线开口 ______。", "向上", accept=["向上"]),
        _q("judge", "抛物线 y=(x-1)²+2 的顶点是 (1,2)。", "对"),
    ]


def _bank_circle_angle() -> list[dict[str, Any]]:
    return [
        _q(
            "judge",
            "同弧所对的圆周角等于圆心角的一半。",
            "对",
            explanation="【考点】圆周角定理。\n【思路】一条弧所对的圆心角是圆周角的 2 倍，所以圆周角 = 圆心角 / 2。必须是同一条弧。\n【易错】把「同弧」看成「等弧」时也可以，但不同弧不能直接用这个倍数关系。",
        ),
        _q(
            "fill",
            "若圆心角是 80°，则它所对的圆周角是 ______°。",
            "40",
            accept=["40", "40°"],
            explanation="【考点】同弧：圆周角是圆心角的一半。\n【思路】圆周角 = 80° ÷ 2 = 40°。\n【易错】误乘 2 写成 160°，那是「已知圆周角求圆心角」。",
        ),
        _q(
            "choice",
            "直径所对的圆周角是（　　）",
            "B",
            options=[
                {"label": "A", "content": "锐角"},
                {"label": "B", "content": "直角"},
                {"label": "C", "content": "钝角"},
                {"label": "D", "content": "平角"},
            ],
            accept=["B"],
            explanation="【考点】推论：直径所对的圆周角是直角。\n【思路】直径所对圆心角是 180°（平角），圆周角是它的一半，等于 90°。A、C 是劣弧或优弧所对的情况；D 是直径所对圆心角，不是圆周角。\n【易错】记成「直径所对圆周角是平角」。平角是圆心角。",
            score=4,
        ),
        _q(
            "judge",
            "同圆中，相等的圆周角所对的弧相等。",
            "对",
            explanation="【考点】圆周角定理的推论：在同圆或等圆中，相等的圆周角所对的弧相等。\n【思路】圆周角相等 → 所对圆心角相等 → 所对弧相等。\n【易错】漏掉「同圆或等圆」这个前提。",
        ),
        _q(
            "fill",
            "半圆（或直径）所对圆周角等于 ______°。",
            "90",
            accept=["90", "90°"],
            explanation="【考点】半圆（直径）所对圆周角是直角。\n【思路】半圆对应圆心角 180°，圆周角取其一半，得 90°。\n【易错】填 180。那是半圆所对的圆心角。",
        ),
    ]


def _bank_similar() -> list[dict[str, Any]]:
    return [
        _q("judge", "相似三角形对应角相等，对应边成比例。", "对"),
        _q("fill", "若两个三角形相似比为 2:3，则面积比为 ______。", "4:9", accept=["4:9", "4：9"]),
        _q(
            "choice",
            "判定两三角形相似，下列可用的是（　　）",
            "A",
            options=[
                {"label": "A", "content": "两角对应相等"},
                {"label": "B", "content": "一角相等即可"},
                {"label": "C", "content": "三边对应相等（全等）才能相似"},
                {"label": "D", "content": "面积相等必相似"},
            ],
            accept=["A"],
            explanation="A。AA 可判定相似。",
            score=4,
        ),
        _q("judge", "全等三角形一定相似，相似比是 1。", "对"),
        _q("fill", "相似比为 k 时，对应高的比是 ______。", "k", accept=["k"]),
    ]


def _bank_probability() -> list[dict[str, Any]]:
    return [
        _q("judge", "必然事件的概率是 1，不可能事件的概率是 0。", "对"),
        _q("fill", "掷一枚均匀硬币，正面朝上的概率是 ______。", "1/2", accept=["1/2", "0.5", "½"]),
        _q(
            "choice",
            "一个袋中有 3 红 2 白共 5 球，随机摸一球是红球的概率是（　　）",
            "C",
            options=[
                {"label": "A", "content": "2/5"},
                {"label": "B", "content": "1/2"},
                {"label": "C", "content": "3/5"},
                {"label": "D", "content": "3/2"},
            ],
            accept=["C"],
            explanation="C。3/5。",
            score=4,
        ),
        _q("judge", "概率越大，事件越容易发生。", "对"),
        _q("fill", "掷骰子一次，点数为偶数的概率是 ______。", "1/2", accept=["1/2", "0.5", "½"]),
    ]


def _bank_factor() -> list[dict[str, Any]]:
    return [
        _q("judge", "因式分解是把一个多项式化为几个整式乘积的形式。", "对"),
        _q("fill", "提公因式：2x+4 = ______。", "2(x+2)", accept=["2(x+2)", "2（x+2）"]),
        _q(
            "choice",
            "x²-9 因式分解结果是（　　）",
            "A",
            options=[
                {"label": "A", "content": "(x+3)(x-3)"},
                {"label": "B", "content": "(x-3)²"},
                {"label": "C", "content": "(x+9)(x-1)"},
                {"label": "D", "content": "x(x-9)"},
            ],
            accept=["A"],
            explanation="A。平方差公式。",
            score=4,
        ),
        _q("fill", "x²+2x+1 = ______。", "(x+1)²", accept=["(x+1)^2", "(x+1)²"]),
        _q("judge", "多项式乘积展开是因式分解的逆运算。", "对"),
    ]


def _bank_ineq() -> list[dict[str, Any]]:
    return [
        _q("judge", "解不等式两边同乘负数时，不等号方向要改变。", "对"),
        _q("fill", "解不等式 2x>6，得 x ______ 3（填 > 或 <）。", ">", accept=[">"]),
        _q(
            "choice",
            "不等式 x+1≤0 的解集是（　　）",
            "B",
            options=[
                {"label": "A", "content": "x≥-1"},
                {"label": "B", "content": "x≤-1"},
                {"label": "C", "content": "x>1"},
                {"label": "D", "content": "x<-1"},
            ],
            accept=["B"],
            explanation="B。x≤-1。",
            score=4,
        ),
        _q("fill", "不等式组 x>1 且 x<3 的解集可写成 ______。", "1<x<3", accept=["1<x<3", "x∈(1,3)"]),
        _q("judge", "不等式的解集在数轴上通常用阴影或空心/实心点表示。", "对"),
    ]


def _bank_geometry_hard() -> list[dict[str, Any]]:
    return [
        _q("judge", "几何压轴常综合相似、圆、三角函数或坐标法。", "对"),
        _q(
            "fill",
            "直角三角形中，若两直角边为 3、4，则斜边是 ______。",
            "5",
            accept=["5"],
            explanation="【考点】勾股定理。\n【思路】3²+4²=9+16=25=5²，斜边是 5。\n【易错】直角边一变就要重算，不能一律写 5。",
        ),
        _q(
            "choice",
            "解决综合几何题较稳妥的第一步通常是（　　）",
            "A",
            options=[
                {"label": "A", "content": "标已知、找等量与相似/全等关系"},
                {"label": "B", "content": "直接猜答案"},
                {"label": "C", "content": "只看最后一问"},
                {"label": "D", "content": "放弃辅助线"},
            ],
            accept=["A"],
            explanation="A。先梳理条件再选方法。",
            score=4,
        ),
        _q("judge", "作辅助线要有目的（构造相似、直角、等腰等）。", "对"),
        _q("fill", "等腰直角三角形锐角是 ______°。", "45", accept=["45", "45°"]),
    ]


def _subject_content_fallback(knowledge_id: str, name: str) -> list[dict[str, Any]]:
    """无专属题库时：按学科给可作答的内容题（禁止系统 id / 网址类题）。"""
    subject = (knowledge_id or "").split(".", 1)[0]
    title = name or "本知识点"
    if subject == "math":
        return [
            _q("judge", f"学习「{title}」时，公式适用条件必须看清楚再套用。", "对"),
            _q(
                "choice",
                f"遇到「{title}」类题目，更合理的做法是（　　）",
                "A",
                options=[
                    {"label": "A", "content": "先写清已知与所求，再选公式"},
                    {"label": "B", "content": "不看条件直接写结论"},
                    {"label": "C", "content": "只抄同学答案"},
                    {"label": "D", "content": "跳过所有计算"},
                ],
                accept=["A"],
                explanation="A。审题→建模→计算→检验。",
                score=4,
            ),
            _q("fill", f"请写出一个与「{title}」直接相关的公式或定理名称：______。", title, accept=[title], score=4),
            _q("judge", f"「{title}」的练习应包含基础题与变式题。", "对"),
            _q(
                "choice",
                f"检验「{title}」是否掌握，较可靠的方式是（　　）",
                "B",
                options=[
                    {"label": "A", "content": "只看一眼标题"},
                    {"label": "B", "content": "独立完成同类题并核对要点"},
                    {"label": "C", "content": "只背口诀不解题"},
                    {"label": "D", "content": "不做任何练习"},
                ],
                accept=["B"],
                explanation="B。独立完成并订正。",
                score=4,
            ),
        ]
    if subject == "chemistry":
        return [
            _q("judge", f"「{title}」相关概念要以教材定义为准。", "对"),
            _q(
                "choice",
                f"关于「{title}」，正确的是（　　）",
                "A",
                options=[
                    {"label": "A", "content": "先辨清概念，再联系实验或实例"},
                    {"label": "B", "content": "概念可以随便替换"},
                    {"label": "C", "content": "化学式与元素符号完全一样"},
                    {"label": "D", "content": "不用区分物理变化和化学变化"},
                ],
                accept=["A"],
                explanation="A。",
                score=4,
            ),
            _q("fill", "空气中含量最多的气体是 ______。", "氮气", accept=["氮气", "N2", "N₂"]),
            _q("judge", "化学变化的本质是有新物质生成。", "对"),
            _q("fill", "水的化学式是 ______。", "H2O", accept=["H2O", "H₂O"]),
        ]
    # 语文/英语等：以识记型内容题为主
    return [
        _q("judge", f"「{title}」需要结合原文或例句理解，不能只记空词。", "对"),
        _q(
            "choice",
            f"复习「{title}」时更合适的是（　　）",
            "A",
            options=[
                {"label": "A", "content": "对照原文/例句，再做默写或翻译"},
                {"label": "B", "content": "只看目录"},
                {"label": "C", "content": "完全不看材料"},
                {"label": "D", "content": "完全依赖别人代做"},
            ],
            accept=["A"],
            explanation="A。",
            score=4,
        ),
        _q("fill", f"请写出「{title}」中你最需要记住的一个要点关键词：______。", title, accept=[title], score=4),
        _q("judge", "不会的地方应先回看材料，再独立练习。", "对"),
        _q(
            "choice",
            "下列做法不利于掌握考点的是（　　）",
            "C",
            options=[
                {"label": "A", "content": "分段识记"},
                {"label": "B", "content": "及时订正"},
                {"label": "C", "content": "跳过所有不会的内容且不再看"},
                {"label": "D", "content": "限时小练"},
            ],
            accept=["C"],
            explanation="C。",
            score=4,
        ),
    ]


def _template_questions(
    knowledge_id: str, name: str, subject_id: str | None
) -> list[dict[str, Any]]:
    """兼容旧调用：转专属题库。"""
    return _question_bank(knowledge_id, name)
