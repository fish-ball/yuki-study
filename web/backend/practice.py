"""做题会话、判分与永久记录。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .db import PRACTICE_DIR, get_conn
from .llm_grade import grade_short_with_llm, llm_configured
from .question_parse import count_blanks, grade_answer
from .sync import export_mastery_to_yaml, refresh_summary_yaml


def list_papers(subject: str | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    sql = """
        SELECT a.id, a.subject_id, a.theme, a.date, a.minutes, a.target_level, a.status, a.note,
               COUNT(q.id) AS question_count
        FROM assessments a
        LEFT JOIN questions q ON q.paper_id = a.id
        WHERE 1=1
    """
    params: list[Any] = []
    if subject:
        sql += " AND a.subject_id = ?"
        params.append(subject)
    sql += " GROUP BY a.id ORDER BY a.date DESC, a.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


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


def start_session(paper_id: str) -> dict[str, Any]:
    paper = get_paper(paper_id, include_answers=False)
    if not paper:
        raise ValueError("试卷不存在")
    if not paper["questions"]:
        raise ValueError("该卷尚无结构化题目，请先点「从仓库同步」")

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
        "questions": [
            {"id": q["id"], "sort_order": q["sort_order"], "qtype": q["qtype"]}
            for q in paper["questions"]
        ],
    }


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
        SELECT id, question_id, user_answer, is_correct, feedback, attempt_no, created_at
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
        SELECT user_answer, is_correct, feedback, attempt_no, created_at
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
    conn.close()
    return {
        "session_id": session_id,
        "index": index,
        "total": len(qs),
        "question": q,
        "history": [dict(h) for h in hist],
    }


def submit_answer(
    session_id: str,
    question_id: str,
    user_answer: str | None = None,
    elapsed_ms: int | None = None,
    update_mastery: bool = True,
    user_answers: list[str] | None = None,
) -> dict[str, Any]:
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
    if user_answers is not None:
        parts = [str(x).strip() for x in user_answers if str(x).strip()]
        user_answer = "；".join(parts)
    user_answer = (user_answer or "").strip()
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

    kids = [
        r["knowledge_id"]
        for r in conn.execute(
            "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
            (question_id,),
        ).fetchall()
    ]

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
    aid = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO attempt_records
        (id, session_id, question_id, paper_id, user_answer, is_correct, feedback, attempt_no, created_at, elapsed_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    conn.execute(
        "UPDATE practice_sessions SET attempt_count = attempt_count + 1 WHERE id = ?",
        (session_id,),
    )
    if is_correct is True and attempt_no == 1:
        conn.execute(
            "UPDATE practice_sessions SET correct_count = correct_count + 1 WHERE id = ?",
            (session_id,),
        )

    subject_id = q["subject_id"]
    if update_mastery and is_correct is False and kids:
        for kid in kids:
            conn.execute(
                """
                UPDATE mastery_items
                SET wrong_count = wrong_count + 1,
                    last_assessed = ?,
                    notes = CASE WHEN notes = '' OR notes IS NULL THEN '练习答错' ELSE notes END
                WHERE knowledge_id = ?
                """,
                (now[:10], kid),
            )
    if update_mastery and is_correct is True and kids:
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
        "explanation": q["explanation"],
        "answer_key": q["answer_key"],
        "knowledge_ids": kids,
        "llm_graded": q["qtype"] == "short" and is_correct is not None,
    }
    if is_correct is False and kids:
        result["tutorial_revise_hint"] = {
            "knowledge_ids": kids,
            "message": "可将本题错因整合进对应教程的「易错点」（打开教程 → 补充修订）。",
        }
    conn.close()

    if update_mastery and subject_id and kids and is_correct is not None:
        export_mastery_to_yaml(subject_id)
        refresh_summary_yaml()

    return result

def finish_session(session_id: str) -> dict[str, Any]:
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
    # 写会话汇总文件
    if s:
        out = PRACTICE_DIR / "attempts" / f"session_{session_id}.json"
        out.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    return s or {}


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
        SELECT p.*, a.theme
        FROM practice_sessions p
        LEFT JOIN assessments a ON a.id = p.paper_id
        ORDER BY p.started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _append_attempt_file(record: dict[str, Any]) -> None:
    day = (record.get("created_at") or "")[:10] or "unknown"
    path = PRACTICE_DIR / "attempts" / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
