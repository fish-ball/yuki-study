"""从仓库 YAML/Markdown 导入到 SQLite。"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

from .db import PRACTICE_DIR, ROOT, get_conn, init_db
from .question_parse import parse_assessment_markdown
from .tutorials import sync_tutorials_from_files

SUBJECT_NAMES = {
    "chinese": "语文",
    "math": "数学",
    "english": "英语",
    "physics": "物理",
    "chemistry": "化学",
    "morality": "道德与法治",
    "history": "历史",
    "pe": "体育与健康",
}

SUBJECT_ORDER = {
    "math": 1,
    "chinese": 2,
    "english": 3,
    "physics": 4,
    "chemistry": 5,
}


def _load_yaml(path: Path) -> dict | list:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def sync_from_files() -> dict:
    """全量同步：以仓库文件为准覆盖导入。"""
    conn = get_conn()
    init_db(conn)

    # 重建课程/档案数据时短暂关闭外键，避免 assessments 等引用阻塞
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in (
        "assessment_knowledge",
        "mastery_items",
        "knowledge_nodes",
        "subjects",
        "plans",
        "profile",
        "meta",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("PRAGMA foreign_keys = ON")

    profile = _load_yaml(ROOT / "profile" / "student.yaml")
    policy = _load_yaml(ROOT / "profile" / "exam-policy-foshan-2027.yaml")
    conn.execute(
        "INSERT INTO profile (id, data_json) VALUES (1, ?)",
        (json.dumps({"student": profile, "exam_policy": policy}, ensure_ascii=False),),
    )

    phase1 = set(profile.get("phase1_subjects") or [])
    policy_subjects = (policy.get("subjects") or {}) if isinstance(policy, dict) else {}

    for sid, name in SUBJECT_NAMES.items():
        phase = 1 if sid in phase1 else 2
        info = policy_subjects.get(sid) or {}
        conn.execute(
            """
            INSERT INTO subjects (id, name_zh, phase, paper_full, admit_score, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                info.get("name") or name,
                phase,
                info.get("paper_full"),
                info.get("admit_score"),
                SUBJECT_ORDER.get(sid, 99),
            ),
        )

    # 知识点
    for sid in SUBJECT_NAMES:
        tree_path = ROOT / "knowledge" / sid / "tree.yaml"
        if not tree_path.exists():
            continue
        tree = _load_yaml(tree_path)
        nodes = tree.get("nodes") or []
        # 先插无父节点，再插子节点：按 parent 深度两轮不够时多轮
        remaining = [
            (idx, node) for idx, node in enumerate(nodes) if isinstance(node, dict)
        ]
        inserted: set[str] = set()
        guard = 0

        def _insert_node(sort_index: int, node: dict, parent_override=...) -> None:
            parent = node.get("parent") if parent_override is ... else parent_override
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_nodes
                (id, subject_id, name, parent_id, exam_weight, prerequisites_json,
                 status_default, sort_index, children_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node["id"],
                    sid,
                    node["name"],
                    parent,
                    node.get("exam_weight") or "mid",
                    json.dumps(node.get("prerequisites") or [], ensure_ascii=False),
                    node.get("status_default") or "unlearned",
                    sort_index,
                    json.dumps(node.get("children") or [], ensure_ascii=False),
                ),
            )

        while remaining and guard < 20:
            guard += 1
            next_remaining = []
            for sort_index, node in remaining:
                parent = node.get("parent")
                if parent and parent not in inserted:
                    next_remaining.append((sort_index, node))
                    continue
                _insert_node(sort_index, node)
                inserted.add(node["id"])
            if len(next_remaining) == len(remaining):
                # 残留节点强制插入，避免死循环
                for sort_index, node in next_remaining:
                    parent = node.get("parent")
                    _insert_node(
                        sort_index,
                        node,
                        None if parent not in inserted else parent,
                    )
                    inserted.add(node["id"])
                break
            remaining = next_remaining

    # 掌握度
    for sid in SUBJECT_NAMES:
        mastery_path = ROOT / "mastery" / f"{sid}.yaml"
        if not mastery_path.exists():
            continue
        data = _load_yaml(mastery_path)
        for item in data.get("items") or []:
            kid = item["knowledge_id"]
            # 若知识点缺失则跳过
            exists = conn.execute(
                "SELECT 1 FROM knowledge_nodes WHERE id = ?", (kid,)
            ).fetchone()
            if not exists:
                continue
            conn.execute(
                """
                INSERT INTO mastery_items
                (knowledge_id, subject_id, level, last_assessed, wrong_count, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kid,
                    sid,
                    item.get("level") or "L0",
                    item.get("last_assessed"),
                    int(item.get("wrong_count") or 0),
                    item.get("notes") or "",
                ),
            )

    # 考核：upsert + 解析结构化题目（不做题记录删除）
    seen_papers: set[str] = set()
    index_path = ROOT / "assessments" / "_index.yaml"
    if index_path.exists():
        index_data = _load_yaml(index_path)
        for item in index_data.get("items") or []:
            rel = item.get("path") or ""
            abs_path = ROOT / rel
            content = abs_path.read_text(encoding="utf-8") if abs_path.exists() else ""
            aid = item.get("id") or Path(rel).stem
            meta = {
                "subject": item.get("subject"),
                "theme": item.get("theme"),
                "date": item.get("date") or _guess_date(aid),
                "minutes": item.get("minutes"),
                "target_level": item.get("target_level"),
                "knowledge_ids": item.get("knowledge_ids") or [],
            }
            if content:
                fm = _parse_front_matter(content)
                for k, v in fm.items():
                    if meta.get(k) in (None, "", []):
                        meta[k] = v
            _upsert_assessment(
                conn,
                aid,
                rel,
                meta,
                content,
                item.get("status") or "ready",
                item.get("note") or "",
            )
            seen_papers.add(aid)

    assessments_dir = ROOT / "assessments"
    if assessments_dir.exists():
        for md in assessments_dir.glob("*.md"):
            aid = md.stem
            if aid in seen_papers:
                # 已从索引写入；若正文更新仍再解析题目
                content = md.read_text(encoding="utf-8")
                row = conn.execute(
                    "SELECT subject_id, theme, date, minutes, target_level FROM assessments WHERE id = ?",
                    (aid,),
                ).fetchone()
                meta = {
                    "subject": row["subject_id"] if row else None,
                    "theme": row["theme"] if row else None,
                    "date": row["date"] if row else None,
                    "minutes": row["minutes"] if row else None,
                    "target_level": row["target_level"] if row else None,
                }
                fm = _parse_front_matter(content)
                meta.update({k: v for k, v in fm.items() if v is not None})
                conn.execute(
                    "UPDATE assessments SET content_md = ?, subject_id = ?, theme = ?, date = ?, minutes = ?, target_level = ? WHERE id = ?",
                    (
                        content,
                        meta.get("subject"),
                        meta.get("theme"),
                        str(meta.get("date") or "") or None,
                        meta.get("minutes"),
                        meta.get("target_level"),
                        aid,
                    ),
                )
                upsert_questions_from_markdown(conn, aid, content, meta)
                continue
            content = md.read_text(encoding="utf-8")
            meta = _parse_front_matter(content)
            _upsert_assessment(
                conn,
                aid,
                f"assessments/{md.name}",
                meta,
                content,
                "ready",
                "",
            )
            seen_papers.add(aid)

    plan_path = ROOT / "plans" / "current-week.md"
    plan_md = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    conn.execute(
        "INSERT INTO plans (id, content_md, updated_at) VALUES (1, ?, ?)",
        (plan_md, date.today().isoformat()),
    )

    tutorial_count = sync_tutorials_from_files(conn)

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('last_sync', ?)",
        (date.today().isoformat(),),
    )
    conn.commit()

    stats = {
        "subjects": conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
        "knowledge": conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0],
        "mastery": conn.execute("SELECT COUNT(*) FROM mastery_items").fetchone()[0],
        "assessments": conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0],
        "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "attempts": conn.execute("SELECT COUNT(*) FROM attempt_records").fetchone()[0],
        "tutorials": conn.execute("SELECT COUNT(*) FROM tutorials").fetchone()[0],
        "tutorials_synced": tutorial_count,
        "last_sync": date.today().isoformat(),
    }
    conn.close()
    return stats


def _upsert_assessment(
    conn,
    aid: str,
    rel: str,
    meta: dict,
    content: str,
    status: str,
    note: str,
) -> None:
    conn.execute(
        """
        INSERT INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          path=excluded.path,
          subject_id=excluded.subject_id,
          theme=excluded.theme,
          date=excluded.date,
          minutes=excluded.minutes,
          target_level=excluded.target_level,
          status=excluded.status,
          content_md=excluded.content_md,
          note=excluded.note
        """,
        (
            aid,
            rel,
            meta.get("subject"),
            meta.get("theme"),
            str(meta.get("date") or _guess_date(aid) or "") or None,
            meta.get("minutes"),
            meta.get("target_level"),
            status,
            content,
            note,
        ),
    )
    conn.execute("DELETE FROM assessment_knowledge WHERE assessment_id = ?", (aid,))
    kids = meta.get("knowledge_ids") or []
    if isinstance(kids, list):
        for kid in kids:
            conn.execute(
                "INSERT OR IGNORE INTO assessment_knowledge (assessment_id, knowledge_id) VALUES (?, ?)",
                (aid, kid),
            )
    if content.strip():
        upsert_questions_from_markdown(conn, aid, content, meta)


def upsert_questions_from_markdown(conn, paper_id: str, content: str, meta: dict) -> int:
    """解析 Markdown 并 upsert 题目，同时镜像到 practice/papers。"""
    questions = parse_assessment_markdown(paper_id, content, meta)
    now = date.today().isoformat()
    for q in questions:
        conn.execute(
            """
            INSERT INTO questions
            (id, paper_id, subject_id, qtype, stem, score, sort_order, explanation,
             answer_key, answer_accept, auto_gradable, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'markdown', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              subject_id=excluded.subject_id,
              qtype=excluded.qtype,
              stem=excluded.stem,
              score=excluded.score,
              sort_order=excluded.sort_order,
              explanation=excluded.explanation,
              answer_key=excluded.answer_key,
              answer_accept=excluded.answer_accept,
              auto_gradable=excluded.auto_gradable,
              updated_at=excluded.updated_at
            """,
            (
                q["id"],
                paper_id,
                q.get("subject_id"),
                q["qtype"],
                q["stem"],
                q["score"],
                q["sort_order"],
                q.get("explanation") or "",
                q.get("answer_key") or "",
                json.dumps(q.get("answer_accept") or [], ensure_ascii=False),
                int(q.get("auto_gradable", 1)),
                now,
                now,
            ),
        )
        conn.execute("DELETE FROM question_options WHERE question_id = ?", (q["id"],))
        for i, opt in enumerate(q.get("options") or []):
            conn.execute(
                """
                INSERT INTO question_options (question_id, label, content, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (q["id"], opt["label"], opt["content"], i),
            )
        conn.execute("DELETE FROM question_knowledge WHERE question_id = ?", (q["id"],))
        for kid in q.get("knowledge_ids") or []:
            conn.execute(
                "INSERT OR IGNORE INTO question_knowledge (question_id, knowledge_id) VALUES (?, ?)",
                (q["id"], kid),
            )

    export_paper_json(conn, paper_id)
    return len(questions)


def export_paper_json(conn, paper_id: str) -> None:
    """把试卷与题目结构写入 practice/papers/<id>.json（永久文件镜像）。"""
    paper = conn.execute("SELECT * FROM assessments WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return
    qs = conn.execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY sort_order",
        (paper_id,),
    ).fetchall()
    payload = {
        "id": paper_id,
        "path": paper["path"],
        "subject_id": paper["subject_id"],
        "theme": paper["theme"],
        "date": paper["date"],
        "minutes": paper["minutes"],
        "target_level": paper["target_level"],
        "status": paper["status"],
        "note": paper["note"],
        "questions": [],
    }
    for q in qs:
        opts = conn.execute(
            "SELECT label, content, sort_order FROM question_options WHERE question_id = ? ORDER BY sort_order",
            (q["id"],),
        ).fetchall()
        kids = conn.execute(
            "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
            (q["id"],),
        ).fetchall()
        payload["questions"].append(
            {
                "id": q["id"],
                "qtype": q["qtype"],
                "stem": q["stem"],
                "score": q["score"],
                "sort_order": q["sort_order"],
                "explanation": q["explanation"],
                "answer_key": q["answer_key"],
                "answer_accept": json.loads(q["answer_accept"] or "[]"),
                "auto_gradable": bool(q["auto_gradable"]),
                "options": [dict(o) for o in opts],
                "knowledge_ids": [k["knowledge_id"] for k in kids],
            }
        )
    out = PRACTICE_DIR / "papers" / f"{paper_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _guess_date(aid: str) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", aid)
    return m.group(1) if m else None


def _parse_front_matter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def export_mastery_to_yaml(subject_id: str) -> None:
    """把 SQLite 中某科掌握度写回 mastery/<subject>.yaml。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT knowledge_id, level, last_assessed, wrong_count, notes
        FROM mastery_items
        WHERE subject_id = ?
        ORDER BY knowledge_id
        """,
        (subject_id,),
    ).fetchall()
    conn.close()

    payload = {
        "subject": subject_id,
        "updated_at": date.today().isoformat(),
        "items": [
            {
                "knowledge_id": r["knowledge_id"],
                "level": r["level"],
                "last_assessed": r["last_assessed"],
                "wrong_count": r["wrong_count"],
                "notes": r["notes"],
            }
            for r in rows
        ],
    }
    out = ROOT / "mastery" / f"{subject_id}.yaml"
    with out.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def refresh_summary_yaml() -> None:
    """根据 mastery_items 刷新 mastery/summary.yaml。"""
    conn = get_conn()
    subjects = conn.execute(
        "SELECT id FROM subjects WHERE phase = 1 ORDER BY sort_order"
    ).fetchall()
    summary: dict = {
        "updated_at": date.today().isoformat(),
        "phase": 1,
        "subjects": {},
        "weak_points": [],
        "next_focus": "math",
        "notes": "由管理后台根据 SQLite 掌握度汇总生成。",
    }
    weak = []
    for s in subjects:
        sid = s["id"]
        counts = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0, "total": 0}
        rows = conn.execute(
            """
            SELECT m.knowledge_id, m.level, m.wrong_count, k.exam_weight, k.name
            FROM mastery_items m
            JOIN knowledge_nodes k ON k.id = m.knowledge_id
            WHERE m.subject_id = ?
            """,
            (sid,),
        ).fetchall()
        for r in rows:
            lv = r["level"]
            if lv in counts:
                counts[lv] += 1
            counts["total"] += 1
            if r["wrong_count"] > 0 or (lv in ("L0", "L1") and r["exam_weight"] == "high"):
                weak.append(
                    {
                        "knowledge_id": r["knowledge_id"],
                        "name": r["name"],
                        "subject": sid,
                        "level": lv,
                        "wrong_count": r["wrong_count"],
                        "exam_weight": r["exam_weight"],
                    }
                )
        summary["subjects"][sid] = counts

    weak.sort(key=lambda x: (-x["wrong_count"], 0 if x["exam_weight"] == "high" else 1))
    summary["weak_points"] = [w["knowledge_id"] for w in weak[:20]]
    if weak:
        summary["next_focus"] = weak[0]["subject"]

    conn.close()
    out = ROOT / "mastery" / "summary.yaml"
    with out.open("w", encoding="utf-8") as f:
        yaml.dump(summary, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
