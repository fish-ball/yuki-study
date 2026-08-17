"""考核结束后：按通过线晋级，并刷新更高难度去重新卷。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import ROOT, get_conn
from .mastery_policy import (
    apply_assessment_pass,
    assessment_pass_rate,
    format_assessment_label,
    is_assessment_at_cap,
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
    conn = get_conn()
    paper = conn.execute("SELECT * FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    conn.close()
    if not paper:
        return {"ok": False, "reason": "paper_missing"}

    theme = (paper["theme"] or "") + " " + (paper["note"] or "")
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

    next_paper = None
    cfg = policy.get("assessment") or {}
    if cfg.get("auto_refresh_next", True) and any(r.get("passed") for r in results):
        next_lv = next_level(target, int(cfg.get("next_level_step") or 1))
        # 已在最高档（L4）：不再刷新下一考核卷
        if level_index(next_lv) > level_index(target):
            kids = list(stats.keys()) or _paper_knowledge_ids(paper_id)
            # 晋级后仍未到 L4 的点才出新卷
            keep = []
            for k in kids:
                row = next((r for r in results if r.get("knowledge_id") == k), None)
                lv = (row.get("to") if row else None) or (row.get("from") if row else None) or "L0"
                if not is_assessment_at_cap(lv):
                    keep.append(k)
            if keep:
                next_paper = generate_next_assessment_paper(
                    subject_id=paper["subject_id"] or (keep[0].split(".", 1)[0]),
                    knowledge_ids=keep,
                    from_level=target,
                    to_level=next_lv,
                    exclude_paper_id=paper_id,
                )

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
) -> dict[str, Any]:
    """生成更高难度考核卷 Markdown + 入库（题干去重）。"""
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
    """按目标等级组装题；优先化学基础点题库，其它点用通用模板。"""
    from .day_drills import _question_bank

    out: list[dict[str, Any]] = []
    for kid in knowledge_ids:
        name = kid
        conn = get_conn()
        row = conn.execute("SELECT name FROM knowledge_nodes WHERE id = ?", (kid,)).fetchone()
        conn.close()
        if row:
            name = row["name"]
        bank = _question_bank(kid, name)
        # 等级越高取更后段/更综合题，并追加变式后缀避免与旧卷完全重复
        if level in ("L3", "L4"):
            ordered = list(reversed(bank)) + bank
        elif level == "L2":
            ordered = bank[2:] + bank[:2] if len(bank) > 2 else bank
        else:
            ordered = bank
        taken = 0
        need = 6 if level in ("L3", "L4") else 5
        for q in ordered:
            stem_raw = q.get("stem") or ""
            from .day_drills import _is_meta_stem

            if _is_meta_stem(stem_raw):
                continue
            # 高难度卷在题干加层次标记，降低与旧卷完全撞车概率
            if level in ("L3", "L4"):
                stem_raw = stem_raw.replace("（　　）", f"（冲{level}·选）（　　）")
                if "______" in stem_raw and f"冲{level}" not in stem_raw:
                    stem_raw = f"【{level}】" + stem_raw
            if _is_meta_stem(stem_raw):
                continue
            stem_key = re.sub(r"\s+", "", stem_raw)
            if stem_key in avoid:
                continue
            qq = dict(q)
            qq["stem"] = stem_raw
            qq["knowledge_ids"] = [kid]
            if level in ("L3", "L4"):
                qq["explanation"] = (qq.get("explanation") or "") + f"（冲{level}）"
            out.append(qq)
            avoid.add(stem_key)
            taken += 1
            if taken >= need:
                break
    return out[:18]


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
        exp = q.get("explanation") or key
        if qtype == "short":
            answers.append(f"{i}. 评分要点：{exp}")
        else:
            answers.append(f"{i}. {exp if exp.startswith(key) or not key else key + '。' + exp}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(answers)
    lines.append("")
    return "\n".join(lines)
