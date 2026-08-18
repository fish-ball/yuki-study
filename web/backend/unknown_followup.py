# -*- coding: utf-8 -*-
"""「不会」题目专学：按知识点拆分学习页与巩固卷。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import PRACTICE_DIR, ROOT, get_conn
from .llm_grade import generate_unknown_focus_lesson
from .mastery_policy import (
    consolidation_pass_rate,
    is_assessment_at_cap,
    level_index,
    load_policy,
)
from .sync import export_paper_json
from .tutorials import ensure_tutorial

DONT_KNOW_ANSWER = "【不会】"
FOLLOWUP_DIR = PRACTICE_DIR / "followups"


def is_dont_know_answer(user_answer: str | None) -> bool:
    s = (user_answer or "").strip()
    return s in (DONT_KNOW_ANSWER, "不会", "不知道", "不懂")


def collect_dont_know_kids(session_id: str) -> list[str]:
    """本场标记「不会」的题目所绑定的知识点（去重，保序）。"""
    items_by_kid = _missed_by_kid(session_id, dont_know_only=True)
    return list(items_by_kid.keys())


def l0_failed_knowledge_ids(
    stats: dict[str, dict[str, Any]],
    *,
    pass_rate: float,
    upgraded: dict[str, str] | None = None,
) -> list[str]:
    """本场未达合格线、且掌握度仍为 L0 的知识点。"""
    policy = load_policy()
    cfg = policy.get("unknown_followup") or {}
    only_l0 = bool(cfg.get("trigger_only_when_l0", True))
    require_below = bool(cfg.get("require_below_pass", True))
    upgraded = upgraded or {}
    out: list[str] = []
    for kid, st in stats.items():
        total = int(st.get("total") or 0)
        correct = int(st.get("correct") or 0)
        if total <= 0:
            continue
        rate = correct / total
        level = upgraded.get(kid) or st.get("current_level") or "L0"
        if only_l0 and level_index(level) > 0:
            continue
        if require_below and rate >= pass_rate:
            continue
        out.append(kid)
    return out


def _missed_by_kid(session_id: str, *, dont_know_only: bool = False) -> dict[str, list[dict[str, Any]]]:
    """本场做错或标记不会的题目，按知识点归组。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT a.question_id, a.user_answer, a.is_correct, a.attempt_no,
               q.stem, q.qtype, q.answer_key, q.explanation, q.subject_id
        FROM attempt_records a
        JOIN questions q ON q.id = a.question_id
        WHERE a.session_id = ? AND a.attempt_no = 1
        ORDER BY a.created_at
        """,
        (session_id,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if dont_know_only:
            if not is_dont_know_answer(r["user_answer"]):
                continue
        elif r["is_correct"] == 1:
            continue
        opts = [
            dict(o)
            for o in conn.execute(
                "SELECT label, content FROM question_options WHERE question_id = ? ORDER BY sort_order",
                (r["question_id"],),
            ).fetchall()
        ]
        kids = [
            x["knowledge_id"]
            for x in conn.execute(
                "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
                (r["question_id"],),
            ).fetchall()
        ]
        item = {
            "question_id": r["question_id"],
            "stem": r["stem"] or "",
            "qtype": r["qtype"] or "",
            "options": opts,
            "user_answer": r["user_answer"] or "",
            "answer_key": r["answer_key"] or "",
            "explanation": r["explanation"] or "",
            "dont_know": is_dont_know_answer(r["user_answer"]),
        }
        stem_key = re.sub(r"\s+", "", item["stem"])
        for kid in kids:
            pair = (kid, stem_key)
            if pair in seen:
                continue
            seen.add(pair)
            grouped.setdefault(kid, []).append(item)
    conn.close()
    return grouped


def canonical_pack_id(knowledge_id: str) -> str:
    return f"unknown-{_slug(knowledge_id)}"


def _qtype_label(qtype: str) -> str:
    return {"judge": "判断题", "choice": "选择题", "fill": "填空题", "short": "简答题"}.get(
        qtype or "", qtype or "题目"
    )


def _compose_focus_lesson(name: str, items: list[dict[str, Any]]) -> str:
    """根据原题写针对性讲解底稿（题型、步骤、变式）。"""
    lines = [f"## 你卡住的题（{name}）", ""]
    if not items:
        lines.append("本场该知识点未达合格线。请先把下面的考法看懂，再做巩固。")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        qtype = _qtype_label(it.get("qtype") or "")
        lines.append(f"### 第 {i} 题 · {qtype}")
        lines.append((it.get("stem") or "").strip())
        opts = it.get("options") or []
        if opts:
            for o in opts:
                lines.append(f"- {o.get('label')}. {o.get('content')}")
        ua = it.get("user_answer") or "（未作答）"
        if it.get("dont_know"):
            ua = "标记「不会」"
        lines.append("")
        lines.append(f"你的作答：{ua}")
        lines.append(f"参考答案：{it.get('answer_key') or '见解析'}")
        lines.append("")
        lines.append("## 这题在考什么")
        lines.append(f"题型是{qtype}。先定考点，再动手，不要凭感觉猜。")
        lines.append("")
        lines.append("## 一步一步怎么做")
        exp = (it.get("explanation") or "").strip()
        lines.append(exp or "对照参考答案，把每一步写成「已知 → 用哪条性质 → 得出什么」。")
        lines.append("")
        lines.append("## 同类题还会怎么变")
        if (it.get("qtype") or "") == "choice":
            lines.append("常见变式：打乱选项、改成「选错误的一项」、把正确说法改成判断题、换一组数字再问同一性质。")
        elif (it.get("qtype") or "") == "judge":
            lines.append("常见变式：改成选择题（选正确/错误说法）、把关键条件改成易错表述、改成填空默写结论。")
        elif (it.get("qtype") or "") == "fill":
            lines.append("常见变式：换数字、改问法（求另一个量）、把填空改成选择或判断。")
        else:
            lines.append("常见变式：同一考点换成选择、判断或填空，或把条件拆成两问。")
        lines.append("")
        lines.append("## 对照易错")
        lines.append("不要只记答案字母。把「这题为什么这样考」说给自己听，再做巩固卷。")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _write_focus_lesson(name: str, knowledge_id: str, items: list[dict[str, Any]], existing: str = "") -> str:
    local = _compose_focus_lesson(name, items)
    detailed = generate_unknown_focus_lesson(
        knowledge_name=name,
        knowledge_id=knowledge_id,
        questions=items,
        existing_lesson=existing or local,
    )
    return detailed or local


def _iter_followup_files() -> list[Path]:
    FOLLOWUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(FOLLOWUP_DIR.glob("unknown-*.json"), reverse=True)


def _load_pack_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
    if not data.get("knowledge_id"):
        kids = data.get("knowledge_ids") or []
        if len(kids) == 1:
            data["knowledge_id"] = kids[0]
    return data


def _save_pack(pack: dict[str, Any]) -> dict[str, Any]:
    pack_id = pack["id"]
    path = FOLLOWUP_DIR / f"{pack_id}.json"
    payload = {k: v for k, v in pack.items() if k != "path"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pack["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
    return pack


def _mastery_level(knowledge_id: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT level FROM mastery_items WHERE knowledge_id = ?", (knowledge_id,)
    ).fetchone()
    conn.close()
    return (row["level"] if row else None) or "L0"


def find_open_pack(knowledge_id: str) -> dict[str, Any] | None:
    canon = canonical_pack_id(knowledge_id)
    path = FOLLOWUP_DIR / f"{canon}.json"
    if path.exists():
        data = _load_pack_file(path)
        if data and data.get("status") in (None, "open"):
            return data
    for p in _iter_followup_files():
        data = _load_pack_file(p)
        if not data:
            continue
        if data.get("status") not in (None, "open"):
            continue
        kid = data.get("knowledge_id") or (data.get("knowledge_ids") or [None])[0]
        if kid == knowledge_id:
            return data
    return None


def close_packs_for_knowledge(knowledge_id: str) -> int:
    """巩固通过后：该知识点从不会专学列表移除。"""
    n = 0
    now = datetime.now().isoformat(timespec="seconds")
    for p in _iter_followup_files():
        data = _load_pack_file(p)
        if not data:
            continue
        kid = data.get("knowledge_id") or (data.get("knowledge_ids") or [None])[0]
        if kid != knowledge_id:
            continue
        if data.get("status") in ("done", "merged"):
            continue
        data["status"] = "done"
        data["closed_at"] = now
        data["close_reason"] = "consolidation_passed"
        p.write_text(
            json.dumps({k: v for k, v in data.items() if k != "path"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        n += 1
    return n


def _merge_question_lists(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in (old or []) + (new or []):
        key = re.sub(r"\s+", "", (it.get("stem") or "") + "|" + (it.get("qtype") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def build_unknown_followup(
    session_id: str,
    *,
    paper_id: str | None = None,
    subject_id: str | None = None,
    knowledge_ids: list[str] | None = None,
    stats: dict[str, dict[str, Any]] | None = None,
    pass_rate: float | None = None,
    upgraded: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """
    仅对「本场未达合格线且掌握度为 L0」的知识点建专学包。
    同一知识点重复不会/错题合并进一份包。
    """
    missed = _missed_by_kid(session_id, dont_know_only=False)
    if knowledge_ids is None:
        rate = pass_rate if pass_rate is not None else 0.75
        knowledge_ids = l0_failed_knowledge_ids(stats or {}, pass_rate=rate, upgraded=upgraded)
    kids = [k for k in knowledge_ids if k]
    if not kids:
        return None

    FOLLOWUP_DIR.mkdir(parents=True, exist_ok=True)
    packs: list[dict[str, Any]] = []
    for kid in kids:
        pack = _upsert_kid_pack(
            session_id=session_id,
            paper_id=paper_id,
            knowledge_id=kid,
            subject_id=subject_id,
            missed_items=missed.get(kid) or [],
        )
        if pack:
            packs.append(pack)

    if not packs:
        return None
    return {
        "packs": packs,
        "pack_count": len(packs),
        **packs[0],
        "id": packs[0]["id"],
        "all_knowledge_ids": kids,
    }


def _upsert_kid_pack(
    *,
    session_id: str,
    paper_id: str | None,
    knowledge_id: str,
    subject_id: str | None,
    missed_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        tut = ensure_tutorial(knowledge_id, force=False, target_level="L1")
    except ValueError:
        tut = None

    sid = subject_id or (tut or {}).get("subject_id") or knowledge_id.split(".", 1)[0]
    title = (tut or {}).get("title") or knowledge_id
    conn = get_conn()
    row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (knowledge_id,)).fetchone()
    conn.close()
    if row and row["name"]:
        title = row["name"]

    existing = find_open_pack(knowledge_id)
    pack_id = canonical_pack_id(knowledge_id)
    merged_items = _merge_question_lists((existing or {}).get("source_questions") or [], missed_items)
    prefer_qtypes = [it.get("qtype") for it in merged_items if it.get("qtype")]
    existing_lesson = ((existing or {}).get("focus_lesson") or {}).get("body_md") or ""
    lesson_md = _write_focus_lesson(title, knowledge_id, merged_items, existing_lesson)

    consol = create_consolidation_paper(
        knowledge_ids=[knowledge_id],
        subject_id=sid,
        source_session_id=session_id,
        pack_id=pack_id,
        knowledge_name=title,
        prefer_qtypes=prefer_qtypes,
    )

    sources = list((existing or {}).get("source_sessions") or [])
    if session_id not in sources:
        sources.append(session_id)
    pack = {
        "id": pack_id,
        "created_at": (existing or {}).get("created_at") or datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "source_paper_id": paper_id,
        "source_sessions": sources,
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
        "source_questions": merged_items,
        "focus_lesson": {
            "title": f"针对不会的题 · {title}",
            "body_md": lesson_md,
            "question_count": len(merged_items),
        },
        "consolidation_paper": consol,
        "title": f"不会专学 · {title}",
        "status": "open",
        "pass_rate": consolidation_pass_rate(),
    }
    return _save_pack(pack)


def _slug(knowledge_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", knowledge_id).strip("_")[:48]


def create_consolidation_paper(
    *,
    knowledge_ids: list[str],
    subject_id: str,
    source_session_id: str,
    pack_id: str,
    knowledge_name: str = "",
    prefer_qtypes: list[str] | None = None,
) -> dict[str, Any]:
    """为单个（或明确列表）知识点生成课后巩固卷；过滤元题目。"""
    from .day_drills import _is_meta_stem, _question_bank

    today = date.today().isoformat()
    paper_id = f"drill-{pack_id}"
    kid0 = knowledge_ids[0] if knowledge_ids else "unknown"
    name = knowledge_name or kid0
    pass_rate = consolidation_pass_rate()
    pass_pct = int(round(pass_rate * 100))
    theme = f"不会巩固 · {name}"
    note = f"单知识点巩固；通过线 {pass_pct}%；来源会话 {source_session_id}；{kid0}"

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
        prefer = [t for t in (prefer_qtypes or []) if t]
        if prefer:
            matched = [q for q in bank if (q.get("qtype") or "") in prefer]
            rest = [q for q in bank if (q.get("qtype") or "") not in prefer]
            bank = matched + rest
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
        VALUES (?, ?, ?, ?, ?, 20, 'L1', 'ready', '', ?)
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
        "label": f"巩固 · {name} · {len(questions)} 题 · 通过线 {pass_pct}%",
        "pass_rate": pass_rate,
        "pass_pct": pass_pct,
        "target_level": "L1",
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
        if str(pid).startswith("drill-unknown-"):
            continue
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
    _dedupe_open_packs()
    files = _iter_followup_files()
    out: list[dict[str, Any]] = []
    seen_kid: set[str] = set()
    for path in files:
        data = _load_pack_file(path)
        if not data:
            continue
        if data.get("status") not in (None, "open"):
            continue
        kid = data.get("knowledge_id") or (data.get("knowledge_ids") or [None])[0]
        if not kid or kid in seen_kid:
            continue
        if level_index(_mastery_level(kid)) > 0:
            continue
        if is_assessment_at_cap(_mastery_level(kid)):
            continue
        seen_kid.add(kid)
        data["pass_rate"] = data.get("pass_rate") or consolidation_pass_rate()
        data["pass_pct"] = int(round(float(data["pass_rate"]) * 100))
        out.append(data)
        if len(out) >= limit:
            break
    return out


def _dedupe_open_packs() -> None:
    """同一知识点多份不会包合并为一份。"""
    groups: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in _iter_followup_files():
        data = _load_pack_file(path)
        if not data or data.get("status") not in (None, "open"):
            continue
        kid = data.get("knowledge_id") or (data.get("knowledge_ids") or [None])[0]
        if not kid:
            continue
        groups.setdefault(kid, []).append((path, data))
    for kid, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: str(x[1].get("updated_at") or x[1].get("created_at") or ""), reverse=True)
        canon_id = canonical_pack_id(kid)
        keep_path, keep = items[0]
        merged_q = list(keep.get("source_questions") or [])
        sources = list(keep.get("source_sessions") or [])
        for path, data in items[1:]:
            merged_q = _merge_question_lists(merged_q, data.get("source_questions") or [])
            for sid in data.get("source_sessions") or []:
                if sid not in sources:
                    sources.append(sid)
            if path.resolve() != keep_path.resolve():
                data["status"] = "merged"
                data["canonical_id"] = canon_id
                path.write_text(
                    json.dumps({k: v for k, v in data.items() if k != "path"}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        keep["id"] = canon_id
        keep["source_questions"] = merged_q
        keep["source_sessions"] = sources
        keep["status"] = "open"
        keep["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_pack(keep)
        if keep_path.name != f"{canon_id}.json":
            keep_path.write_text(
                json.dumps(
                    {
                        "id": keep_path.stem,
                        "status": "merged",
                        "canonical_id": canon_id,
                        "knowledge_id": kid,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )


def get_followup(pack_id: str) -> dict[str, Any] | None:
    path = FOLLOWUP_DIR / f"{pack_id}.json"
    if not path.exists():
        path = FOLLOWUP_DIR / pack_id
        if not path.exists():
            return None
    data = _load_pack_file(path)
    if not data:
        return None
    if data.get("status") == "merged" and data.get("canonical_id"):
        canon = _load_pack_file(FOLLOWUP_DIR / f"{data['canonical_id']}.json")
        if canon:
            data = canon
    data["pass_rate"] = data.get("pass_rate") or consolidation_pass_rate()
    data["pass_pct"] = int(round(float(data["pass_rate"]) * 100))
    return data
