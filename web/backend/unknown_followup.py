# -*- coding: utf-8 -*-
"""「不会」题目专学：按知识点拆分学习页与巩固卷。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import PRACTICE_DIR, ROOT, get_conn
from .sync import export_paper_json
from .tutorials import ensure_tutorial, get_tutorial, revise_tutorial

DONT_KNOW_ANSWER = "【不会】"
FOLLOWUP_DIR = PRACTICE_DIR / "followups"


def is_dont_know_answer(user_answer: str | None) -> bool:
    s = (user_answer or "").strip()
    return s in (DONT_KNOW_ANSWER, "不会", "不知道", "不懂")


def collect_dont_know_kids(session_id: str) -> list[str]:
    """本场标记「不会」的题目所绑定的知识点（去重，保序）。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT a.question_id, a.user_answer
        FROM attempt_records a
        WHERE a.session_id = ?
        ORDER BY a.created_at
        """,
        (session_id,),
    ).fetchall()
    qids: list[str] = []
    seen_q: set[str] = set()
    for r in rows:
        if not is_dont_know_answer(r["user_answer"]):
            continue
        qid = r["question_id"]
        if qid in seen_q:
            continue
        seen_q.add(qid)
        qids.append(qid)
    kids: list[str] = []
    seen_k: set[str] = set()
    for qid in qids:
        for row in conn.execute(
            "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
            (qid,),
        ).fetchall():
            kid = row["knowledge_id"]
            if kid not in seen_k:
                seen_k.add(kid)
                kids.append(kid)
    conn.close()
    return kids


def build_unknown_followup(
    session_id: str,
    *,
    paper_id: str | None = None,
    subject_id: str | None = None,
) -> dict[str, Any] | None:
    """
    按知识点拆分：每个不会的点各自一个专学包 + 一份巩固卷。
    返回 { packs: [...], pack_count }；兼容旧字段取 packs[0]。
    """
    kids = collect_dont_know_kids(session_id)
    if not kids:
        return None

    FOLLOWUP_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H%M%S")
    packs: list[dict[str, Any]] = []

    for i, kid in enumerate(kids):
        pack = _build_one_kid_pack(
            session_id=session_id,
            paper_id=paper_id,
            knowledge_id=kid,
            subject_id=subject_id,
            today=today,
            stamp=f"{stamp}-{i+1:02d}",
        )
        if pack:
            packs.append(pack)

    if not packs:
        return None
    return {
        "packs": packs,
        "pack_count": len(packs),
        # 兼容旧前端：取第一份
        **packs[0],
        "id": packs[0]["id"],
        "all_knowledge_ids": kids,
    }


def _build_one_kid_pack(
    *,
    session_id: str,
    paper_id: str | None,
    knowledge_id: str,
    subject_id: str | None,
    today: str,
    stamp: str,
) -> dict[str, Any] | None:
    try:
        tut = ensure_tutorial(knowledge_id, force=False, target_level="L1")
    except ValueError:
        tut = None
    try:
        revise_tutorial(
            knowledge_id,
            mode="patch",
            notes="学生在练习中标记「不会」",
            student_mistake=f"本场（会话 {session_id[:8]}）标记「不会」，请先精读本知识点教程再做巩固。",
        )
        tut = get_tutorial(knowledge_id) or tut
    except (ValueError, OSError, RuntimeError):
        pass

    sid = subject_id or (tut or {}).get("subject_id") or knowledge_id.split(".", 1)[0]
    title = (tut or {}).get("title") or knowledge_id
    # 查节点名
    conn = get_conn()
    row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (knowledge_id,)).fetchone()
    conn.close()
    if row and row["name"]:
        title = row["name"]

    pack_id = f"unknown-{today}-{stamp}-{_slug(knowledge_id)}"
    consol = create_consolidation_paper(
        knowledge_ids=[knowledge_id],
        subject_id=sid,
        source_session_id=session_id,
        pack_id=pack_id,
        knowledge_name=title,
    )

    pack = {
        "id": pack_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "source_paper_id": paper_id,
        "subject_id": sid,
        "knowledge_id": knowledge_id,
        "knowledge_ids": [knowledge_id],
        "tutorials": [
            {
                "knowledge_id": knowledge_id,
                "title": title,
                "subject_id": sid,
                "path": (tut or {}).get("path"),
                "has_tutorial": bool(tut),
            }
        ],
        "consolidation_paper": consol,
        "title": f"不会专学 · {title}",
        "status": "open",
    }
    path = FOLLOWUP_DIR / f"{pack_id}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    pack["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
    return pack


def _slug(knowledge_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", knowledge_id).strip("_")[:48]


def create_consolidation_paper(
    *,
    knowledge_ids: list[str],
    subject_id: str,
    source_session_id: str,
    pack_id: str,
    knowledge_name: str = "",
) -> dict[str, Any]:
    """为单个（或明确列表）知识点生成课后巩固卷；过滤元题目。"""
    from .day_drills import _is_meta_stem, _question_bank

    today = date.today().isoformat()
    paper_id = f"drill-{pack_id}"
    kid0 = knowledge_ids[0] if knowledge_ids else "unknown"
    name = knowledge_name or kid0
    theme = f"不会巩固 · {name}"
    note = f"单知识点巩固；来源会话 {source_session_id}；{kid0}"

    conn = get_conn()
    avoid: set[str] = set()
    for kid in knowledge_ids:
        for r in conn.execute(
            """
            SELECT q.stem FROM questions q
            JOIN question_knowledge qk ON qk.question_id = q.id
            WHERE qk.knowledge_id = ?
            """,
            (kid,),
        ).fetchall():
            key = re.sub(r"\s+", "", r["stem"] or "")
            if key:
                avoid.add(key)

    questions: list[dict[str, Any]] = []
    for kid in knowledge_ids:
        row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (kid,)).fetchone()
        kname = row["name"] if row else kid
        bank = _question_bank(kid, kname)
        ordered = list(reversed(bank)) + bank
        taken = 0
        for q in ordered:
            stem = (q.get("stem") or "").strip()
            if not stem or _is_meta_stem(stem):
                continue
            stem_key = re.sub(r"\s+", "", stem)
            if stem_key in avoid:
                continue
            qq = dict(q)
            qq["stem"] = stem  # 不再堆「巩固」前缀造成重复观感；卷主题已标明
            qq["knowledge_ids"] = [kid]
            questions.append(qq)
            avoid.add(stem_key)
            taken += 1
            if taken >= 5:
                break

    if not questions:
        for kid in knowledge_ids:
            row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (kid,)).fetchone()
            kname = row["name"] if row else kid
            for q in _question_bank(kid, kname):
                stem = (q.get("stem") or "").strip()
                if not stem or _is_meta_stem(stem):
                    continue
                qq = dict(q)
                qq["knowledge_ids"] = [kid]
                questions.append(qq)
                if len(questions) >= 5:
                    break

    conn.execute(
        """
        INSERT INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, 20, 'L2', 'ready', '', ?)
        ON CONFLICT(id) DO UPDATE SET
          theme=excluded.theme,
          note=excluded.note,
          status='ready',
          date=excluded.date
        """,
        (
            paper_id,
            f"practice/papers/{paper_id}.json",
            subject_id,
            theme,
            today,
            note,
        ),
    )
    qids = [
        r["id"]
        for r in conn.execute("SELECT id FROM questions WHERE paper_id = ?", (paper_id,)).fetchall()
    ]
    for qid in qids:
        conn.execute("DELETE FROM question_options WHERE question_id = ?", (qid,))
        conn.execute("DELETE FROM question_knowledge WHERE question_id = ?", (qid,))
        conn.execute("DELETE FROM questions WHERE id = ?", (qid,))

    now = datetime.now().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%H%M%S")
    for i, t in enumerate(questions, 1):
        kid = (t.get("knowledge_ids") or knowledge_ids[:1] or ["unknown"])[0]
        qid = f"{paper_id}__{stamp}_q{i:02d}"
        conn.execute(
            """
            INSERT INTO questions
            (id, paper_id, subject_id, qtype, stem, score, sort_order, explanation,
             answer_key, answer_accept, auto_gradable, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'unknown_consol', ?, ?)
            """,
            (
                qid,
                paper_id,
                subject_id,
                t.get("qtype") or "judge",
                t.get("stem") or "",
                t.get("score") or 2,
                i,
                t.get("explanation") or "",
                t.get("answer_key") or "",
                json.dumps(t.get("answer_accept") or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        for j, opt in enumerate(t.get("options") or []):
            conn.execute(
                """
                INSERT INTO question_options (question_id, label, content, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (qid, opt["label"], opt["content"], j),
            )
        conn.execute(
            "INSERT OR IGNORE INTO question_knowledge (question_id, knowledge_id) VALUES (?, ?)",
            (qid, kid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assessment_knowledge (assessment_id, knowledge_id) VALUES (?, ?)",
            (paper_id, kid),
        )

    export_paper_json(conn, paper_id)
    conn.commit()
    conn.close()
    return {
        "id": paper_id,
        "theme": theme,
        "question_count": len(questions),
        "knowledge_ids": knowledge_ids,
        "knowledge_id": kid0,
        "label": f"巩固 · {name} · {len(questions)} 题",
    }


def retire_meta_question_papers() -> int:
    """把含 knowledge_id/元题目的卷标为 retired，不再出现在练习列表。"""
    from .day_drills import _is_meta_stem

    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT paper_id, stem FROM questions").fetchall()
    bad_papers: set[str] = set()
    for r in rows:
        if _is_meta_stem(r["stem"] or ""):
            bad_papers.add(r["paper_id"])
    n = 0
    for pid in bad_papers:
        conn.execute(
            """
            UPDATE assessments
            SET status = 'retired',
                note = CASE
                  WHEN note LIKE '%元题目已下架%' THEN note
                  ELSE trim(IFNULL(note,'') || '；元题目已下架')
                END
            WHERE id = ? AND COALESCE(status,'ready') NOT IN ('retired','archived')
            """,
            (pid,),
        )
        if conn.total_changes:
            n += 1
    conn.commit()
    conn.close()
    return n


def list_followups(limit: int = 40) -> list[dict[str, Any]]:
    FOLLOWUP_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧的多知识点合并包：仅展示含单个 knowledge_id 的新包；旧包若 knowledge_ids>1 仍可显示但标题标明
    files = sorted(FOLLOWUP_DIR.glob("unknown-*.json"), reverse=True)
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
        # 兼容：补 knowledge_id
        if not data.get("knowledge_id"):
            kids = data.get("knowledge_ids") or []
            if len(kids) == 1:
                data["knowledge_id"] = kids[0]
        out.append(data)
        if len(out) >= limit:
            break
    return out


def get_followup(pack_id: str) -> dict[str, Any] | None:
    path = FOLLOWUP_DIR / f"{pack_id}.json"
    if not path.exists():
        path = FOLLOWUP_DIR / pack_id
        if not path.exists():
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
    return data
