"""做题会话、判分与永久记录。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .db import PRACTICE_DIR, get_conn
from .llm_grade import (
    explain_question,
    grade_short_with_llm,
    llm_configured,
    needs_detailed_explanation,
)
from .question_parse import count_blanks, grade_answer
from .sync import export_mastery_to_yaml, refresh_summary_yaml


def is_calc_drill_paper(paper_id: str | None, theme: str | None = None) -> bool:
    """计算专题卷：仿可汗学院换数据反复练，不受免练退役。"""
    pid = str(paper_id or "")
    theme_s = str(theme or "")
    return pid.startswith("drill-calc-") or theme_s.startswith("计算专题")


def is_unknown_consolidation_paper(paper_id: str | None, theme: str | None = None) -> bool:
    """不会专学课后巩固卷：可在已达练习上限时仍开卷，不得按免练退役。"""
    pid = str(paper_id or "")
    theme_s = str(theme or "")
    return pid.startswith("drill-unknown-") or theme_s.startswith("不会巩固")


def list_papers(subject: str | None = None) -> list[dict[str, Any]]:
    from .assessment_progress import cleanup_assessment_queue
    from .day_drills import session_covers_paper
    from .mastery_policy import (
        format_assessment_label,
        format_practice_label,
        is_assessment_at_cap,
        is_practice_exempt,
        load_policy,
        practice_max_level,
    )

    # 先清掉已完成/到顶/重复考核，并按知识清单补下一卷
    cleanup_assessment_queue(subject_id=subject)

    conn = get_conn()
    sql = """
        SELECT a.id, a.subject_id, a.theme, a.date, a.minutes, a.target_level, a.status, a.note,
               COUNT(q.id) AS question_count
        FROM assessments a
        LEFT JOIN questions q ON q.paper_id = a.id
        WHERE COALESCE(a.status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
    """
    params: list[Any] = []
    if subject:
        sql += " AND a.subject_id = ?"
        params.append(subject)
    sql += " GROUP BY a.id ORDER BY a.date DESC, a.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    policy = load_policy()
    pmax = practice_max_level(policy)
    visible: list[dict[str, Any]] = []
    for r in rows:
        kids = [
            x["knowledge_id"]
            for x in conn.execute(
                "SELECT knowledge_id FROM assessment_knowledge WHERE assessment_id = ?",
                (r["id"],),
            ).fetchall()
        ]
        if not kids:
            kids = [
                x["knowledge_id"]
                for x in conn.execute(
                    """
                    SELECT DISTINCT qk.knowledge_id
                    FROM question_knowledge qk
                    JOIN questions q ON q.id = qk.question_id
                    WHERE q.paper_id = ?
                    """,
                    (r["id"],),
                ).fetchall()
            ]
        r["knowledge_ids"] = kids
        is_drill = str(r["id"]).startswith("drill-") or "drill" in (r.get("theme") or "")
        r["paper_kind"] = "practice" if is_drill else "assessment"

        # 已完整完成过的卷：从待练列表隐藏（记录仍在会话历史）
        if _paper_fully_completed(conn, r["id"], session_covers_paper):
            _set_paper_status(conn, r["id"], "completed")
            continue

        levels = []
        for kid in kids:
            m = conn.execute(
                "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
            ).fetchone()
            levels.append(m["level"] if m else "L0")
        cur = (
            min(
                levels,
                key=lambda lv: ["L0", "L1", "L2", "L3", "L4"].index(
                    lv if lv in ["L0", "L1", "L2", "L3", "L4"] else 0
                ),
            )
            if levels
            else "L0"
        )
        if is_drill:
            info = format_practice_label(current_level=cur, policy=policy)
            r["level_label"] = info["title"]
            r["practice_max_level"] = pmax
            r["goal_level"] = info["goal_level"]
            r["exempt"] = info["exempt"]
            r["display_title"] = info["title"]
            # 因考核晋级超过练习上限：免练并从列表消失（不会巩固卷除外）
            if (
                not is_unknown_consolidation_paper(r.get("id"), r.get("theme"))
                and not is_calc_drill_paper(r.get("id"), r.get("theme"))
                and kids
                and all(
                    is_practice_exempt(
                        (
                            conn.execute(
                                "SELECT level FROM mastery_items WHERE knowledge_id = ?", (k,)
                            ).fetchone()
                            or {"level": "L0"}
                        )["level"],
                        policy,
                    )
                    for k in kids
                )
            ):
                _set_paper_status(conn, r["id"], "retired")
                continue
        else:
            # 全部知识点已达 L4：不再出现在考核列表
            if levels and all(is_assessment_at_cap(lv) for lv in levels):
                _set_paper_status(conn, r["id"], "retired")
                continue
            tgt = r.get("target_level") or "L2"
            r["level_label"] = format_assessment_label(target_level=tgt, policy=policy)
            r["display_title"] = f"{r.get('theme') or r['id']} · {r['level_label']}"
            r["exempt"] = False
            r["pass_rate_need"] = float((policy.get("assessment") or {}).get("pass_rate") or 0.8)
        # 未完成会话进度（刷新后续练）
        active = conn.execute(
            """
            SELECT id, current_index, total_questions, correct_count, started_at
            FROM practice_sessions
            WHERE paper_id = ? AND status = 'in_progress'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (r["id"],),
        ).fetchone()
        if active:
            answered = conn.execute(
                """
                SELECT COUNT(DISTINCT question_id) AS c
                FROM attempt_records
                WHERE session_id = ?
                """,
                (active["id"],),
            ).fetchone()["c"]
            r["active_session"] = {
                "session_id": active["id"],
                "current_index": active["current_index"],
                "answered_count": answered,
                "total_questions": active["total_questions"],
                "correct_count": active["correct_count"],
                "started_at": active["started_at"],
            }
        else:
            r["active_session"] = None
        visible.append(r)
    conn.commit()
    conn.close()
    return visible


def _paper_fully_completed(conn, paper_id: str, session_covers_paper) -> bool:
    row = conn.execute("SELECT status FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    if row and (row["status"] or "") in ("completed", "retired", "exempt"):
        return True
    sessions = conn.execute(
        """
        SELECT id FROM practice_sessions
        WHERE paper_id = ? AND status = 'completed'
        ORDER BY finished_at DESC
        """,
        (paper_id,),
    ).fetchall()
    for s in sessions:
        if session_covers_paper(conn, s["id"], paper_id):
            return True
    return False


def _set_paper_status(conn, paper_id: str, status: str) -> None:
    conn.execute(
        "UPDATE assessments SET status = ? WHERE id = ?",
        (status, paper_id),
    )


def retire_exempt_practice_papers(knowledge_ids: list[str] | None = None) -> int:
    """掌握度已达/超过练习上限后，将对应练习卷标为 retired（列表不再展示）。"""
    from .mastery_policy import is_practice_exempt, load_policy

    policy = load_policy()
    conn = get_conn()
    sql = """
        SELECT a.id, a.theme
        FROM assessments a
        WHERE (a.id LIKE 'drill-%' OR IFNULL(a.theme,'') LIKE '%drill%')
          AND COALESCE(a.status, 'ready') NOT IN ('completed', 'retired', 'exempt')
    """
    papers = [dict(r) for r in conn.execute(sql).fetchall()]
    retired = 0
    for p in papers:
        if is_unknown_consolidation_paper(p.get("id"), p.get("theme")) or is_calc_drill_paper(
            p.get("id"), p.get("theme")
        ):
            continue
        kids = [
            x["knowledge_id"]
            for x in conn.execute(
                """
                SELECT DISTINCT qk.knowledge_id
                FROM question_knowledge qk
                JOIN questions q ON q.id = qk.question_id
                WHERE q.paper_id = ?
                """,
                (p["id"],),
            ).fetchall()
        ]
        if not kids:
            continue
        if knowledge_ids and not set(kids) & set(knowledge_ids):
            continue
        levels = []
        for kid in kids:
            m = conn.execute(
                "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
            ).fetchone()
            levels.append(m["level"] if m else "L0")
        if levels and all(is_practice_exempt(lv, policy) for lv in levels):
            _set_paper_status(conn, p["id"], "retired")
            retired += 1
    conn.commit()
    conn.close()
    return retired


def get_paper(paper_id: str, include_answers: bool = False) -> dict[str, Any] | None:
    conn = get_conn()
    paper = conn.execute("SELECT * FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        return None
    qs = conn.execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY sort_order",
        (paper_id,),
    ).fetchall()
    questions = []
    for q in qs:
        item = _question_public(conn, q, include_answers=include_answers)
        questions.append(item)
    conn.close()
    data = dict(paper)
    data["questions"] = questions
    return data


def _question_public(conn, q, include_answers: bool = False) -> dict[str, Any]:
    opts = conn.execute(
        "SELECT label, content, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order",
        (q["id"],),
    ).fetchall()
    kids = conn.execute(
        "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
        (q["id"],),
    ).fetchall()
    item = {
        "id": q["id"],
        "paper_id": q["paper_id"],
        "subject_id": q["subject_id"],
        "qtype": q["qtype"],
        "stem": q["stem"],
        "score": q["score"],
        "sort_order": q["sort_order"],
        "auto_gradable": bool(q["auto_gradable"]) or (q["qtype"] == "short" and llm_configured()),
        "blank_count": count_blanks(q["stem"]) if q["qtype"] == "fill" else 0,
        "options": [dict(o) for o in opts],
        "knowledge_ids": [k["knowledge_id"] for k in kids],
        "llm_grading": q["qtype"] == "short" and llm_configured(),
    }
    if include_answers:
        item["answer_key"] = q["answer_key"]
        item["answer_accept"] = json.loads(q["answer_accept"] or "[]")
        item["explanation"] = q["explanation"]
    return item


def start_session(paper_id: str, force_new: bool = False) -> dict[str, Any]:
    paper = get_paper(paper_id, include_answers=False)
    if not paper:
        raise ValueError("试卷不存在")
    if not paper["questions"]:
        raise ValueError("该卷尚无结构化题目，请先点「从仓库同步」")

    if not force_new:
        existing = find_active_session(paper_id)
        if existing:
            return existing

    sid = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO practice_sessions
        (id, paper_id, mode, subject_id, status, started_at, total_questions, correct_count, attempt_count, current_index)
        VALUES (?, ?, 'paper', ?, 'in_progress', ?, ?, 0, 0, 0)
        """,
        (sid, paper_id, paper.get("subject_id"), now, len(paper["questions"])),
    )
    conn.commit()
    conn.close()
    return {
        "session_id": sid,
        "paper_id": paper_id,
        "theme": paper.get("theme"),
        "subject_id": paper.get("subject_id"),
        "total_questions": len(paper["questions"]),
        "started_at": now,
        "current_index": 0,
        "resume_index": 0,
        "resumed": False,
        "answered_count": 0,
        "results": {},
        "questions": [
            {"id": q["id"], "sort_order": q["sort_order"], "qtype": q["qtype"]}
            for q in paper["questions"]
        ],
    }


def find_active_session(paper_id: str) -> dict[str, Any] | None:
    """同一试卷未完成会话：用于刷新/退出后续练。"""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id FROM practice_sessions
        WHERE paper_id = ? AND status = 'in_progress'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return build_resume_payload(row["id"])


def build_resume_payload(session_id: str) -> dict[str, Any] | None:
    data = get_session(session_id)
    if not data or data.get("status") != "in_progress":
        return None
    paper_id = data["paper_id"]
    conn = get_conn()
    paper = conn.execute(
        "SELECT id, theme, subject_id, status FROM assessments WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if not paper or (paper["status"] or "ready") in ("completed", "retired", "exempt", "archived"):
        conn.close()
        return None
    qs = conn.execute(
        "SELECT id, sort_order, qtype FROM questions WHERE paper_id = ? ORDER BY sort_order",
        (paper_id,),
    ).fetchall()
    conn.close()
    if not qs:
        return None
    qids = [q["id"] for q in qs]
    latest = data.get("latest_by_question") or {}
    results: dict[str, str] = {}
    for qid, att in latest.items():
        if att.get("voided"):
            continue
        if att.get("is_correct") == 1:
            results[qid] = "ok"
        elif att.get("is_correct") == 0:
            results[qid] = "bad"
        else:
            results[qid] = "pending"
    resume_index = 0
    for i, qid in enumerate(qids):
        if qid not in results:
            resume_index = i
            break
    else:
        resume_index = min(int(data.get("current_index") or 0), max(0, len(qids) - 1))

    return {
        "session_id": session_id,
        "paper_id": paper_id,
        "theme": paper["theme"] or data.get("theme"),
        "subject_id": paper["subject_id"] or data.get("subject_id"),
        "total_questions": len(qids),
        "started_at": data.get("started_at"),
        "current_index": int(data.get("current_index") or 0),
        "resume_index": resume_index,
        "resumed": True,
        "answered_count": len(results),
        "correct_count": int(data.get("correct_count") or 0),
        "results": results,
        "questions": [
            {"id": q["id"], "sort_order": q["sort_order"], "qtype": q["qtype"]}
            for q in qs
        ],
    }


def list_active_sessions(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.id, p.paper_id, p.subject_id, p.started_at, p.current_index,
               p.total_questions, p.correct_count, p.attempt_count, a.theme
        FROM practice_sessions p
        LEFT JOIN assessments a ON a.id = p.paper_id
        WHERE p.status = 'in_progress'
          AND COALESCE(a.status, 'ready') NOT IN ('completed', 'retired', 'exempt', 'archived')
        ORDER BY p.started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = build_resume_payload(r["id"])
        if payload:
            out.append(payload)
    return out


def get_session(session_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    s = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not s:
        conn.close()
        return None
    attempts = conn.execute(
        """
        SELECT id, question_id, user_answer, is_correct, feedback, attempt_no, created_at,
               COALESCE(voided, 0) AS voided
        FROM attempt_records WHERE session_id = ?
        ORDER BY created_at
        """,
        (session_id,),
    ).fetchall()
    # 每题最新一次
    latest: dict[str, dict] = {}
    for a in attempts:
        latest[a["question_id"]] = dict(a)
    conn.close()
    data = dict(s)
    data["latest_by_question"] = latest
    data["attempt_records"] = [dict(a) for a in attempts]
    return data


def get_session_question(session_id: str, index: int) -> dict[str, Any]:
    conn = get_conn()
    s = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not s:
        conn.close()
        raise ValueError("会话不存在")
    qs = conn.execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY sort_order",
        (s["paper_id"],),
    ).fetchall()
    if index < 0 or index >= len(qs):
        conn.close()
        raise ValueError("题目序号越界")
    q = _question_public(conn, qs[index], include_answers=False)
    # 该题历史
    hist = conn.execute(
        """
        SELECT user_answer, is_correct, feedback, attempt_no, created_at,
               COALESCE(voided, 0) AS voided, void_reason
        FROM attempt_records
        WHERE session_id = ? AND question_id = ?
        ORDER BY attempt_no
        """,
        (session_id, q["id"]),
    ).fetchall()
    conn.execute(
        "UPDATE practice_sessions SET current_index = ? WHERE id = ?",
        (index, session_id),
    )
    conn.commit()
    from .calc_drill import question_allows_report

    hist_rows = [dict(h) for h in hist]
    excluded = any(int(h.get("voided") or 0) == 1 for h in hist_rows)
    conn.close()
    return {
        "session_id": session_id,
        "index": index,
        "total": len(qs),
        "question": q,
        "history": hist_rows,
        "can_report": question_allows_report(s["paper_id"], q.get("subject_id"), q.get("qtype")),
        "excluded_from_stats": excluded,
    }


def submit_answer(
    session_id: str,
    question_id: str,
    user_answer: str | None = None,
    elapsed_ms: int | None = None,
    update_mastery: bool = True,
    user_answers: list[str] | None = None,
    dont_know: bool = False,
) -> dict[str, Any]:
    from .unknown_followup import DONT_KNOW_ANSWER

    conn = get_conn()
    s = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not s:
        conn.close()
        raise ValueError("会话不存在")
    q = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not q:
        conn.close()
        raise ValueError("题目不存在")

    # 多空：优先使用数组，落库仍存为分号串便于历史兼容
    if user_answers is not None and not dont_know:
        parts = [str(x).strip() for x in user_answers if str(x).strip()]
        user_answer = "；".join(parts)
    user_answer = (user_answer or "").strip()

    kids = [
        r["knowledge_id"]
        for r in conn.execute(
            "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
            (question_id,),
        ).fetchall()
    ]

    if dont_know:
        user_answer = DONT_KNOW_ANSWER
        is_correct = False
        key = (q["answer_key"] or "").strip()
        feedback = "已标记「不会」，按错误计入正确率。先看参考答案，结束练习后会进入专学巩固。"
        if key:
            feedback += f" 参考答案：{key}"
    else:
        if not user_answer:
            conn.close()
            raise ValueError("答案不能为空")
        accept = json.loads(q["answer_accept"] or "[]")
        blanks = count_blanks(q["stem"]) if q["qtype"] == "fill" else 0
        is_correct, feedback = grade_answer(
            q["qtype"],
            q["answer_key"],
            accept,
            user_answer,
            blank_count=blanks or len(accept) or None,
        )
        if q["qtype"] == "short" and is_correct is None:
            is_correct, feedback = grade_short_with_llm(
                stem=q["stem"],
                explanation=q["explanation"] or "",
                answer_key=q["answer_key"] or "",
                user_answer=user_answer,
                subject_id=q["subject_id"],
                knowledge_ids=kids,
            )

    prev = conn.execute(
        """
        SELECT COALESCE(MAX(attempt_no), 0) AS n FROM attempt_records
        WHERE session_id = ? AND question_id = ?
        """,
        (session_id, question_id),
    ).fetchone()
    attempt_no = int(prev["n"]) + 1
    already_voided = conn.execute(
        """
        SELECT 1 FROM attempt_records
        WHERE session_id = ? AND question_id = ? AND COALESCE(voided, 0) = 1
        """,
        (session_id, question_id),
    ).fetchone()
    aid = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    voided = 1 if already_voided else 0
    if voided:
        feedback = (feedback or "") + " 本题已报错，不计入正确率。"
    conn.execute(
        """
        INSERT INTO attempt_records
        (id, session_id, question_id, paper_id, user_answer, is_correct, feedback, attempt_no, created_at, elapsed_ms, voided, void_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            session_id,
            question_id,
            s["paper_id"],
            user_answer,
            None if is_correct is None else (1 if is_correct else 0),
            feedback,
            attempt_no,
            now,
            elapsed_ms,
            voided,
            "report_error" if voided else "",
        ),
    )
    conn.execute(
        "UPDATE practice_sessions SET attempt_count = attempt_count + 1 WHERE id = ?",
        (session_id,),
    )
    if is_correct is True and attempt_no == 1 and not voided:
        conn.execute(
            "UPDATE practice_sessions SET correct_count = correct_count + 1 WHERE id = ?",
            (session_id,),
        )

    subject_id = q["subject_id"]
    if (not voided) and update_mastery and is_correct is False and kids:
        note = "练习标记不会" if dont_know else "练习答错"
        for kid in kids:
            conn.execute(
                """
                UPDATE mastery_items
                SET wrong_count = wrong_count + 1,
                    last_assessed = ?,
                    notes = CASE WHEN notes = '' OR notes IS NULL THEN ? ELSE notes END
                WHERE knowledge_id = ?
                """,
                (now[:10], note, kid),
            )
    if (not voided) and update_mastery and is_correct is True and kids:
        for kid in kids:
            row = conn.execute(
                "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
            ).fetchone()
            if row and row["level"] in ("L0",):
                conn.execute(
                    """
                    UPDATE mastery_items
                    SET level = 'L1', last_assessed = ?
                    WHERE knowledge_id = ?
                    """,
                    (now[:10], kid),
                )

    explanation = (q["explanation"] or "").strip()
    if needs_detailed_explanation(explanation, q["answer_key"] or ""):
        opt_rows = conn.execute(
            """
            SELECT label, content FROM question_options
            WHERE question_id = ? ORDER BY sort_order
            """,
            (question_id,),
        ).fetchall()
        options = [{"label": r["label"], "content": r["content"]} for r in opt_rows]
        detailed = explain_question(
            stem=q["stem"] or "",
            qtype=q["qtype"] or "",
            answer_key=q["answer_key"] or "",
            options=options,
            existing_explanation=explanation,
            subject_id=q["subject_id"],
            knowledge_ids=kids,
        )
        if detailed:
            explanation = detailed
            conn.execute(
                "UPDATE questions SET explanation = ?, updated_at = ? WHERE id = ?",
                (explanation, now, question_id),
            )

    conn.commit()

    # 文件镜像单条 attempt
    _append_attempt_file(
        {
            "id": aid,
            "session_id": session_id,
            "question_id": question_id,
            "paper_id": s["paper_id"],
            "user_answer": user_answer,
            "is_correct": is_correct,
            "feedback": feedback,
            "attempt_no": attempt_no,
            "created_at": now,
            "elapsed_ms": elapsed_ms,
            "knowledge_ids": kids,
        }
    )

    result = {
        "attempt_id": aid,
        "is_correct": is_correct,
        "feedback": feedback,
        "attempt_no": attempt_no,
        "explanation": explanation,
        "answer_key": q["answer_key"],
        "knowledge_ids": kids,
        "llm_graded": q["qtype"] == "short" and is_correct is not None and not dont_know,
        "dont_know": dont_know,
        "excluded_from_stats": bool(voided),
    }
    if is_correct is False and kids:
        result["tutorial_revise_hint"] = {
            "knowledge_ids": kids,
            "message": (
                "可将本题错因整合进对应教程的「易错点」（打开教程 → 补充修订）。"
                if not dont_know
                else "本题已标记「不会」。若本场该知识点未达合格线且掌握度为 L0，结束练习后会进入不会专学。"
            ),
        }
    conn.close()

    if update_mastery and (not voided) and subject_id and kids and is_correct is not None:
        export_mastery_to_yaml(subject_id)
        refresh_summary_yaml()

    return result

def finish_session(session_id: str) -> dict[str, Any]:
    from .assessment_progress import finalize_assessment_session, session_kid_stats
    from .day_drills import mark_drill_completed_by_paper, session_covers_paper
    from .mastery_policy import (
        apply_consolidation_level_ups,
        apply_practice_level_ups,
        assessment_pass_rate,
        consolidation_pass_rate,
        load_policy,
        practice_max_level,
        practice_pass_rate,
    )
    from .sync import export_mastery_to_yaml, refresh_summary_yaml

    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE practice_sessions
        SET status = 'completed', finished_at = ?
        WHERE id = ?
        """,
        (now, session_id),
    )
    conn.commit()
    s = get_session(session_id)
    # 写会话汇总文件（练习记录留档）
    if s:
        out = PRACTICE_DIR / "attempts" / f"session_{session_id}.json"
        out.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()

    result = dict(s or {})
    paper_id = result.get("paper_id")
    policy = load_policy()
    result["practice_max_level"] = practice_max_level(policy)
    result["assessment_pass_rate"] = float((policy.get("assessment") or {}).get("pass_rate") or 0.8)
    result["practice_pass_rate"] = float((policy.get("practice_level_up") or {}).get("pass_rate") or 0.75)
    result["consolidation_pass_rate"] = consolidation_pass_rate(policy)
    from .calc_drill import session_scored_stats

    scored = session_scored_stats(session_id)
    result["score_correct"] = scored["correct"]
    result["score_total"] = scored["total"]
    result["voided_count"] = scored["voided"]

    if not paper_id:
        return result

    is_drill = str(paper_id).startswith("drill-") or "drill" in str(result.get("theme") or "")
    is_consol = is_unknown_consolidation_paper(paper_id, result.get("theme"))
    is_calc = is_calc_drill_paper(paper_id, result.get("theme"))
    stats = session_kid_stats(session_id)

    # 是否答完全卷
    conn = get_conn()
    fully_done = session_covers_paper(conn, session_id, paper_id)
    theme_row = conn.execute("SELECT theme FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    conn.close()
    if theme_row and not result.get("theme"):
        result["theme"] = theme_row["theme"]
        is_consol = is_unknown_consolidation_paper(paper_id, result.get("theme"))
        is_calc = is_calc_drill_paper(paper_id, result.get("theme"))
    result["fully_done"] = fully_done
    result["removed_from_list"] = False

    upgraded: dict[str, str] = {}
    if is_drill:
        if is_consol:
            ups = apply_consolidation_level_ups(stats, policy)
        else:
            ups = apply_practice_level_ups(stats, policy)
        changed_subjects: set[str] = set()
        for u in ups:
            if u.get("changed"):
                conn = get_conn()
                conn.execute(
                    """
                    UPDATE mastery_items
                    SET level = ?, last_assessed = ?,
                        notes = CASE WHEN notes IS NULL OR notes = '' THEN '练习达标晋级' ELSE notes END
                    WHERE knowledge_id = ?
                    """,
                    (u["to"], now[:10], u["knowledge_id"]),
                )
                conn.commit()
                conn.close()
                upgraded[u["knowledge_id"]] = u["to"]
                if u.get("subject_id"):
                    changed_subjects.add(u["subject_id"])
            if is_consol and u.get("passed"):
                from .unknown_followup import close_packs_for_knowledge

                close_packs_for_knowledge(u["knowledge_id"])
        for sid in changed_subjects:
            export_mastery_to_yaml(sid)
        if changed_subjects:
            refresh_summary_yaml()
        drill_mark = mark_drill_completed_by_paper(paper_id, session_id)
        if fully_done:
            conn = get_conn()
            _set_paper_status(conn, paper_id, "completed")
            conn.commit()
            conn.close()
            result["removed_from_list"] = True
        result["progression"] = {
            "kind": "consolidation" if is_consol else "practice",
            "items": ups,
            "drill": drill_mark,
        }
        # 若练习后仍有点达到上限，清理可免练卷（巩固卷除外）
        if not is_consol and not is_calc:
            retire_exempt_practice_papers(list(stats.keys()))
    else:
        prog = finalize_assessment_session(session_id, paper_id)
        kids_for_retire = list(stats.keys())
        if not kids_for_retire:
            kids_for_retire = [
                x.get("knowledge_id")
                for x in (prog.get("per_knowledge") or [])
                if x.get("knowledge_id")
            ]
        if fully_done:
            conn = get_conn()
            _set_paper_status(conn, paper_id, "completed")
            conn.commit()
            conn.close()
            result["removed_from_list"] = True
            from .plan_service import mark_day_items_done_for_kids

            mark_day_items_done_for_kids([k for k in kids_for_retire if k])
        retired_n = retire_exempt_practice_papers(kids_for_retire or None)
        prog["retired_practice_count"] = retired_n
        result["progression"] = {"kind": "assessment", **prog}
        for it in (prog.get("upgraded") or []) + (prog.get("per_knowledge") or []):
            if it.get("changed") and it.get("knowledge_id") and it.get("to"):
                upgraded[it["knowledge_id"]] = it["to"]

    # 不会专学：仅 L0 且本场该点未达合格线；巩固卷本身不再重复开专学
    from .unknown_followup import build_unknown_followup, retire_meta_question_papers

    retire_meta_question_papers()
    result["unknown_knowledge_ids"] = []
    result["unknown_followup"] = None
    result["unknown_followups"] = []
    if not is_consol and not is_calc:
        if is_drill:
            pass_need = practice_pass_rate(policy)
        else:
            pass_need = assessment_pass_rate(policy)
        bundled = build_unknown_followup(
            session_id,
            paper_id=paper_id,
            subject_id=result.get("subject_id"),
            stats=stats,
            pass_rate=pass_need,
            upgraded=upgraded,
        )
        if bundled:
            packs = bundled.get("packs") or [bundled]
            result["unknown_followups"] = packs
            result["unknown_followup"] = packs[0] if packs else None
            result["unknown_knowledge_ids"] = bundled.get("all_knowledge_ids") or [
                p.get("knowledge_id") for p in packs if p.get("knowledge_id")
            ]

    from .achievements import evaluate_after_session

    result["session_id"] = session_id
    result["new_achievements"] = evaluate_after_session(result)
    return result


def list_history(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT a.id, a.session_id, a.question_id, a.paper_id, a.user_answer,
               a.is_correct, a.feedback, a.attempt_no, a.created_at,
               q.stem, q.qtype, s.theme
        FROM attempt_records a
        LEFT JOIN questions q ON q.id = a.question_id
        LEFT JOIN assessments s ON s.id = a.paper_id
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.*, a.theme, a.target_level, a.note, a.status AS paper_status
        FROM practice_sessions p
        LEFT JOIN assessments a ON a.id = p.paper_id
        ORDER BY p.started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        item = dict(r)
        pid = item.get("paper_id") or ""
        is_drill = str(pid).startswith("drill-") or "drill" in str(item.get("theme") or "")
        item["paper_kind"] = "practice" if is_drill else "assessment"
        item["kind_label"] = "练习" if is_drill else "考核"
        out.append(item)
    return out


def _append_attempt_file(record: dict[str, Any]) -> None:
    day = (record.get("created_at") or "")[:10] or "unknown"
    path = PRACTICE_DIR / "attempts" / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
