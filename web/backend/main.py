"""中考辅导 Harness 管理 API。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import get_conn, init_db
from .knowledge_topo import build_lattice, build_ordered_tree, compute_topo_depths
from .plan_service import (
    bootstrap_plan,
    complete_day_plan,
    compute_mastery_score,
    get_plan_bundle,
    rebuild_days_from_week,
    submit_plan_survey,
    toggle_day_item,
    update_day_plan_fields,
)
from .practice import (
    finish_session,
    get_session,
    get_session_question,
    list_active_sessions,
    list_history,
    list_papers,
    list_sessions,
    start_session,
    submit_answer,
)
from .sync import export_mastery_to_yaml, refresh_summary_yaml, sync_from_files

STATIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"

app = FastAPI(title="中考辅导 Harness 管理台", version="1.0.0")


class MasteryUpdate(BaseModel):
    level: str = Field(..., pattern=r"^L[0-4]$")
    notes: str = ""
    wrong_count: int | None = None
    last_assessed: str | None = None
    write_back_yaml: bool = True


class PlanUpdate(BaseModel):
    content_md: str
    write_back_file: bool = True
    rebuild_days: bool = True


class DayItemToggle(BaseModel):
    knowledge_id: str
    done: bool | None = None


class DayPlanUpdate(BaseModel):
    title: str | None = None
    focus_text: str | None = None
    review_text: str | None = None


class PlanSurveyBody(BaseModel):
    day_plan_id: str
    volume: str
    increase_yes: bool | None = None
    rescale_week: bool | None = None


class SkipPracticeBody(BaseModel):
    knowledge_ids: list[str] = Field(default_factory=list)
    day_plan_id: str | None = None


class StartCalcBody(BaseModel):
    topic_id: str
    count: int = 8


class StartSessionBody(BaseModel):
    paper_id: str
    force_new: bool = False


class SubmitAnswerBody(BaseModel):
    question_id: str
    user_answer: str | None = None
    user_answers: list[str] | None = None
    elapsed_ms: int | None = None
    update_mastery: bool = True
    dont_know: bool = False


class ReportErrorBody(BaseModel):
    question_id: str


@app.on_event("startup")
def _startup() -> None:
    conn = get_conn()
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
    conn.close()
    if count == 0:
        sync_from_files()
    try:
        from .unknown_followup import retire_meta_question_papers
        from .assessment_progress import repair_computed_fill_answers

        retire_meta_question_papers()
        repair_computed_fill_answers()
    except (OSError, RuntimeError, ValueError):
        pass


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sync")
def api_sync() -> dict[str, Any]:
    stats = sync_from_files()
    # 同步后自动重建日计划并生成今日练习，避免刷新后空白
    try:
        bundle = bootstrap_plan()
        stats["plan_days"] = len(bundle.get("days") or [])
        stats["today"] = bundle.get("today")
    except (OSError, RuntimeError, ValueError) as e:
        stats["plan_bootstrap_error"] = str(e)
    return stats


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    conn = get_conn()
    subjects = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name_zh, phase, paper_full, admit_score, sort_order FROM subjects ORDER BY sort_order, id"
        ).fetchall()
    ]
    levels = {}
    for s in subjects:
        rows = conn.execute(
            """
            SELECT level, COUNT(*) AS cnt
            FROM mastery_items WHERE subject_id = ?
            GROUP BY level
            """,
            (s["id"],),
        ).fetchall()
        bucket = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0, "total": 0}
        for r in rows:
            bucket[r["level"]] = r["cnt"]
            bucket["total"] += r["cnt"]
        levels[s["id"]] = bucket

    weak = [
        dict(r)
        for r in conn.execute(
            """
            SELECT m.knowledge_id, m.level, m.wrong_count, m.notes,
                   k.name, k.exam_weight, k.subject_id
            FROM mastery_items m
            JOIN knowledge_nodes k ON k.id = m.knowledge_id
            WHERE m.wrong_count > 0
               OR (m.level IN ('L0', 'L1') AND k.exam_weight = 'high' AND k.parent_id IS NOT NULL)
            ORDER BY m.wrong_count DESC,
                     CASE k.exam_weight WHEN 'high' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END
            LIMIT 30
            """
        ).fetchall()
    ]

    assessments = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
    knowledge = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
    questions = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    attempts = conn.execute("SELECT COUNT(*) FROM attempt_records").fetchone()[0]
    tutorials = conn.execute("SELECT COUNT(*) FROM tutorials").fetchone()[0]
    last_sync = conn.execute(
        "SELECT value FROM meta WHERE key = 'last_sync'"
    ).fetchone()
    profile_row = conn.execute("SELECT data_json FROM profile WHERE id = 1").fetchone()
    plan_row = conn.execute("SELECT content_md, updated_at FROM plans WHERE id = 1").fetchone()
    mastery_score = compute_mastery_score(conn)
    conn.close()

    profile = json.loads(profile_row["data_json"]) if profile_row else {}
    return {
        "subjects": subjects,
        "levels": levels,
        "weak_points": weak,
        "mastery_score": mastery_score,
        "counts": {
            "knowledge": knowledge,
            "assessments": assessments,
            "questions": questions,
            "attempts": attempts,
            "tutorials": tutorials,
        },
        "last_sync": last_sync["value"] if last_sync else None,
        "student": profile.get("student"),
        "exam_policy": profile.get("exam_policy"),
        "plan_preview": (plan_row["content_md"][:400] if plan_row else ""),
        "plan_updated_at": plan_row["updated_at"] if plan_row else None,
    }


@app.get("/api/subjects")
def list_subjects() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name_zh, phase, paper_full, admit_score, sort_order FROM subjects ORDER BY sort_order, id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_knowledge_rows(
    conn: Any,
    subject: str | None = None,
    weight: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT k.id, k.subject_id, k.name, k.parent_id, k.exam_weight,
               k.prerequisites_json, k.sort_index, k.children_json,
               m.level, m.wrong_count, m.last_assessed, m.notes,
               CASE WHEN t.knowledge_id IS NOT NULL THEN 1 ELSE 0 END AS has_tutorial
        FROM knowledge_nodes k
        LEFT JOIN mastery_items m ON m.knowledge_id = k.id
        LEFT JOIN tutorials t ON t.knowledge_id = k.id
        WHERE 1=1
    """
    params: list[Any] = []
    if subject:
        sql += " AND k.subject_id = ?"
        params.append(subject)
    if weight:
        sql += " AND k.exam_weight = ?"
        params.append(weight)
    if q:
        sql += " AND (k.id LIKE ? OR k.name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY k.subject_id, k.sort_index, k.id"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["prerequisites"] = json.loads(item.pop("prerequisites_json") or "[]")
        item["children_ids"] = json.loads(item.pop("children_json") or "[]")
        item["has_tutorial"] = bool(item.get("has_tutorial"))
        result.append(item)
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for item in result:
        by_subject.setdefault(item["subject_id"], []).append(item)
    for group in by_subject.values():
        depths = compute_topo_depths(group)
        for item in group:
            item["topo_depth"] = depths.get(item["id"], 0)
    result.sort(
        key=lambda x: (
            x.get("subject_id") or "",
            int(x.get("topo_depth") or 0),
            int(x.get("sort_index") or 0),
            x["id"],
        )
    )
    return result


@app.get("/api/knowledge")
def list_knowledge(
    subject: str | None = None,
    weight: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    conn = get_conn()
    result = _fetch_knowledge_rows(conn, subject=subject, weight=weight, q=q)
    conn.close()
    for item in result:
        item.pop("children_ids", None)
    return result


@app.get("/api/knowledge/tree")
def knowledge_tree(subject: str = Query(...)) -> dict[str, Any]:
    conn = get_conn()
    nodes = _fetch_knowledge_rows(conn, subject=subject)
    conn.close()
    for n in nodes:
        n["children_json"] = json.dumps(n.get("children_ids") or [], ensure_ascii=False)
    roots = build_ordered_tree(nodes)
    return {"subject": subject, "roots": roots}


@app.get("/api/knowledge/lattice")
def knowledge_lattice(subject: str = Query(...)) -> dict[str, Any]:
    conn = get_conn()
    nodes = _fetch_knowledge_rows(conn, subject=subject)
    conn.close()
    for n in nodes:
        n["children_json"] = json.dumps(n.get("children_ids") or [], ensure_ascii=False)
    payload = build_lattice(nodes)
    return {"subject": subject, **payload}


@app.get("/api/mastery")
def list_mastery(
    subject: str | None = None,
    level: str | None = None,
) -> list[dict[str, Any]]:
    conn = get_conn()
    sql = """
        SELECT m.knowledge_id, m.subject_id, m.level, m.last_assessed,
               m.wrong_count, m.notes, k.name, k.exam_weight, k.parent_id
        FROM mastery_items m
        JOIN knowledge_nodes k ON k.id = m.knowledge_id
        WHERE 1=1
    """
    params: list[Any] = []
    if subject:
        sql += " AND m.subject_id = ?"
        params.append(subject)
    if level:
        sql += " AND m.level = ?"
        params.append(level)
    sql += " ORDER BY m.subject_id, k.sort_index, m.knowledge_id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.patch("/api/mastery/{knowledge_id}")
def update_mastery(knowledge_id: str, body: MasteryUpdate) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT subject_id FROM mastery_items WHERE knowledge_id = ?",
        (knowledge_id,),
    ).fetchone()
    if not row:
        # 若仅有知识点无掌握度，则创建
        k = conn.execute(
            "SELECT subject_id FROM knowledge_nodes WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        if not k:
            conn.close()
            raise HTTPException(404, "知识点不存在")
        conn.execute(
            """
            INSERT INTO mastery_items
            (knowledge_id, subject_id, level, last_assessed, wrong_count, notes)
            VALUES (?, ?, 'L0', NULL, 0, '')
            """,
            (knowledge_id, k["subject_id"]),
        )
        subject_id = k["subject_id"]
    else:
        subject_id = row["subject_id"]

    last_assessed = body.last_assessed
    if last_assessed is None:
        last_assessed = date.today().isoformat()

    if body.wrong_count is None:
        conn.execute(
            """
            UPDATE mastery_items
            SET level = ?, notes = ?, last_assessed = ?
            WHERE knowledge_id = ?
            """,
            (body.level, body.notes, last_assessed, knowledge_id),
        )
    else:
        conn.execute(
            """
            UPDATE mastery_items
            SET level = ?, notes = ?, last_assessed = ?, wrong_count = ?
            WHERE knowledge_id = ?
            """,
            (body.level, body.notes, last_assessed, body.wrong_count, knowledge_id),
        )
    conn.commit()
    updated = conn.execute(
        """
        SELECT m.*, k.name, k.exam_weight
        FROM mastery_items m
        JOIN knowledge_nodes k ON k.id = m.knowledge_id
        WHERE m.knowledge_id = ?
        """,
        (knowledge_id,),
    ).fetchone()
    conn.close()

    if body.write_back_yaml:
        export_mastery_to_yaml(subject_id)
        refresh_summary_yaml()

    return dict(updated)


@app.get("/api/assessments")
def list_assessments(subject: str | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    sql = """
        SELECT id, path, subject_id, theme, date, minutes, target_level, status, note
        FROM assessments
        WHERE 1=1
    """
    params: list[Any] = []
    if subject:
        sql += " AND subject_id = ?"
        params.append(subject)
    sql += " ORDER BY date DESC, id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows:
        kids = conn.execute(
            "SELECT knowledge_id FROM assessment_knowledge WHERE assessment_id = ?",
            (row["id"],),
        ).fetchall()
        row["knowledge_ids"] = [k["knowledge_id"] for k in kids]
    conn.close()
    return rows


@app.post("/api/assessments/skip-practice")
def api_skip_practice(body: SkipPracticeBody) -> dict[str, Any]:
    from .assessment_progress import skip_practice_to_assessment

    try:
        return skip_practice_to_assessment(
            body.knowledge_ids or [],
            day_plan_id=body.day_plan_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/assessments/{assessment_id}")
def get_assessment(assessment_id: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "考核不存在")
    kids = conn.execute(
        "SELECT knowledge_id FROM assessment_knowledge WHERE assessment_id = ?",
        (assessment_id,),
    ).fetchall()
    conn.close()
    data = dict(row)
    data["knowledge_ids"] = [k["knowledge_id"] for k in kids]
    return data


@app.get("/api/plan")
def get_plan() -> dict[str, Any]:
    """自动就绪周/日计划与今日练习，返回进度与待填问卷。"""
    return bootstrap_plan()


@app.post("/api/plan/bootstrap")
def api_bootstrap_plan() -> dict[str, Any]:
    return bootstrap_plan()


@app.put("/api/plan")
def update_plan(body: PlanUpdate) -> dict[str, Any]:
    conn = get_conn()
    now = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO plans (id, content_md, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET content_md = excluded.content_md, updated_at = excluded.updated_at
        """,
        (body.content_md, now),
    )
    conn.commit()
    conn.close()
    if body.write_back_file:
        from .db import ROOT

        path = ROOT / "plans" / "current-week.md"
        path.write_text(body.content_md, encoding="utf-8")
    if body.rebuild_days:
        return rebuild_days_from_week(body.content_md, force=True)
    return bootstrap_plan()


@app.post("/api/plan/rebuild-days")
def api_rebuild_days(force: bool = True) -> dict[str, Any]:
    return rebuild_days_from_week(force=force)


@app.post("/api/plan/days/{day_plan_id}/toggle")
def api_toggle_day_item(day_plan_id: str, body: DayItemToggle) -> dict[str, Any]:
    try:
        return toggle_day_item(day_plan_id, body.knowledge_id, body.done)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/plan/days/{day_plan_id}")
def api_update_day_plan(day_plan_id: str, body: DayPlanUpdate) -> dict[str, Any]:
    try:
        return update_day_plan_fields(
            day_plan_id,
            title=body.title,
            focus_text=body.focus_text,
            review_text=body.review_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/plan/days/{day_plan_id}/complete")
def api_complete_day(day_plan_id: str) -> dict[str, Any]:
    try:
        return complete_day_plan(day_plan_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/plan/survey")
def api_plan_survey(body: PlanSurveyBody) -> dict[str, Any]:
    try:
        return submit_plan_survey(
            body.day_plan_id,
            body.volume,
            body.increase_yes,
            body.rescale_week,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/mastery-score")
def api_mastery_score() -> dict[str, Any]:
    return compute_mastery_score()


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM profile WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {}
    return json.loads(row["data_json"])


class EnsureTutorialBody(BaseModel):
    force: bool = False
    target_level: str = "L1"


class ReviseTutorialBody(BaseModel):
    mode: str = "patch"  # patch | correct | integrate
    notes: str = ""
    related_ids: list[str] | None = None
    student_mistake: str | None = None
    target_level: str | None = None


@app.get("/api/tutorials")
def api_list_tutorials(
    subject: str | None = None,
    hide_capped: bool = False,
) -> list[dict[str, Any]]:
    from .tutorials import list_tutorials

    return list_tutorials(subject, hide_capped=hide_capped)


@app.get("/api/tutorials/{knowledge_id}")
def api_get_tutorial(knowledge_id: str) -> dict[str, Any]:
    from .tutorials import get_tutorial

    data = get_tutorial(knowledge_id)
    if not data:
        raise HTTPException(404, "知识点不存在")
    return data


@app.post("/api/tutorials/{knowledge_id}/ensure")
def api_ensure_tutorial(
    knowledge_id: str, body: EnsureTutorialBody = EnsureTutorialBody()
) -> dict[str, Any]:
    from .tutorials import ensure_tutorial

    try:
        return ensure_tutorial(
            knowledge_id,
            force=body.force,
            target_level=body.target_level,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/tutorials/{knowledge_id}/revise")
def api_revise_tutorial(knowledge_id: str, body: ReviseTutorialBody) -> dict[str, Any]:
    from .tutorials import revise_tutorial

    try:
        return revise_tutorial(
            knowledge_id,
            mode=body.mode,
            notes=body.notes,
            related_ids=body.related_ids,
            student_mistake=body.student_mistake,
            target_level=body.target_level,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- 练习 / 做题 ----------


@app.get("/api/practice/papers")
def api_practice_papers(subject: str | None = None) -> list[dict[str, Any]]:
    return list_papers(subject)


@app.get("/api/practice/papers/{paper_id}")
def api_practice_paper(paper_id: str) -> dict[str, Any]:
    from .practice import get_paper

    paper = get_paper(paper_id, include_answers=False)
    if not paper:
        raise HTTPException(404, "试卷不存在")
    return paper


@app.get("/api/mastery-policy")
def api_mastery_policy() -> dict[str, Any]:
    from .mastery_policy import load_policy, practice_max_level, assessment_pass_rate, consolidation_pass_rate

    p = load_policy()
    return {
        "policy": p,
        "practice_max_level": practice_max_level(p),
        "assessment_pass_rate": assessment_pass_rate(p),
        "practice_pass_rate": float((p.get("practice_level_up") or {}).get("pass_rate") or 0.75),
        "consolidation_pass_rate": consolidation_pass_rate(p),
        "allow_skip_practice": bool((p.get("assessment") or {}).get("allow_skip_practice", True)),
    }


@app.get("/api/basics")
def api_basics() -> list[dict[str, Any]]:
    from .mastery_policy import list_basics

    return list_basics()


@app.get("/api/basics/{item_id}")
def api_basic_item(item_id: str) -> dict[str, Any]:
    from .mastery_policy import list_basics

    for it in list_basics():
        if it.get("id") == item_id:
            return it
    raise HTTPException(404, "基础资料不存在")


@app.post("/api/practice/sessions")
def api_start_session(body: StartSessionBody) -> dict[str, Any]:
    from .practice import get_paper, is_calc_drill_paper, is_unknown_consolidation_paper
    from .mastery_policy import is_practice_exempt, load_policy

    try:
        paper = get_paper(body.paper_id, include_answers=False)
        if not paper:
            raise ValueError("试卷不存在")
        consol = is_unknown_consolidation_paper(paper.get("id"), paper.get("theme"))
        calc = is_calc_drill_paper(paper.get("id"), paper.get("theme"))
        special = consol or calc
        st = (paper.get("status") or "ready").lower()
        # 不会巩固 / 计算专题：即使已达练习上限、或曾被误标退役，仍允许开卷
        if special and st == "retired":
            conn = get_conn()
            conn.execute(
                "UPDATE assessments SET status = 'ready' WHERE id = ?",
                (body.paper_id,),
            )
            conn.commit()
            conn.close()
            st = "ready"
        if not special and st in ("completed", "retired", "exempt", "archived"):
            raise ValueError("该卷已完成或已取消，请到「做题记录」查看；列表中仅保留待练卷。")
        if (
            not special
            and paper
            and (str(body.paper_id).startswith("drill-") or "drill" in (paper.get("theme") or ""))
        ):
            policy = load_policy()
            conn = get_conn()
            kids = [
                r["knowledge_id"]
                for r in conn.execute(
                    """
                    SELECT DISTINCT qk.knowledge_id
                    FROM question_knowledge qk
                    JOIN questions q ON q.id = qk.question_id
                    WHERE q.paper_id = ?
                    """,
                    (body.paper_id,),
                ).fetchall()
            ]
            levels = []
            for kid in kids:
                m = conn.execute(
                    "SELECT level FROM mastery_items WHERE knowledge_id = ?", (kid,)
                ).fetchone()
                levels.append(m["level"] if m else "L0")
            conn.close()
            if kids and all(is_practice_exempt(lv, policy) for lv in levels):
                raise ValueError(
                    "该知识点已超过练习上限，请改做「考核」卷晋级。"
                )
        return start_session(body.paper_id, force_new=body.force_new)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/practice/sessions/active")
def api_active_sessions(limit: int = 20) -> list[dict[str, Any]]:
    return list_active_sessions(limit)


@app.get("/api/practice/sessions")
def api_list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    return list_sessions(limit)


@app.get("/api/practice/sessions/{session_id}")
def api_get_session(session_id: str) -> dict[str, Any]:
    data = get_session(session_id)
    if not data:
        raise HTTPException(404, "会话不存在")
    return data


@app.get("/api/practice/sessions/{session_id}/question")
def api_session_question(session_id: str, index: int = Query(0, ge=0)) -> dict[str, Any]:
    try:
        return get_session_question(session_id, index)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/practice/sessions/{session_id}/submit")
def api_submit(session_id: str, body: SubmitAnswerBody) -> dict[str, Any]:
    try:
        return submit_answer(
            session_id,
            body.question_id,
            body.user_answer,
            body.elapsed_ms,
            body.update_mastery,
            body.user_answers,
            dont_know=body.dont_know,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/practice/sessions/{session_id}/report-error")
def api_report_error(session_id: str, body: ReportErrorBody) -> dict[str, Any]:
    from .calc_drill import report_answer_error

    try:
        return report_answer_error(session_id, body.question_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/practice/llm-status")
def api_llm_status() -> dict[str, Any]:
    from .llm_grade import llm_configured, load_llm_config

    cfg = load_llm_config()
    return {
        "configured": llm_configured(),
        "model": (cfg or {}).get("model"),
        "base_url": (cfg or {}).get("base_url"),
        "hint": None
        if cfg
        else "请复制 web/config/llm.example.json 为 llm.local.json 并填写凭据，或设置环境变量 STUDY_LLM_API_KEY / STUDY_LLM_BASE_URL / STUDY_LLM_MODEL",
    }


@app.post("/api/practice/sessions/{session_id}/finish")
def api_finish(session_id: str) -> dict[str, Any]:
    data = finish_session(session_id)
    if not data:
        raise HTTPException(404, "会话不存在")
    return data


@app.get("/api/practice/history")
def api_history(limit: int = 50) -> list[dict[str, Any]]:
    return list_history(limit)


@app.get("/api/calc-drills")
def api_list_calc_drills() -> list[dict[str, Any]]:
    from .calc_drill import list_calc_topics

    return list_calc_topics()


@app.post("/api/calc-drills/start")
def api_start_calc_drill(body: StartCalcBody) -> dict[str, Any]:
    from .calc_drill import start_calc_drill

    try:
        return start_calc_drill(body.topic_id, body.count)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/achievements")
def api_achievements() -> dict[str, Any]:
    from .achievements import list_achievements

    return list_achievements()


@app.get("/api/learn/unknown-followups")
def api_list_unknown_followups(limit: int = 30) -> list[dict[str, Any]]:
    from .unknown_followup import list_followups

    return list_followups(limit)


@app.get("/api/learn/unknown-followups/{pack_id}")
def api_get_unknown_followup(pack_id: str) -> dict[str, Any]:
    from .unknown_followup import get_followup

    data = get_followup(pack_id)
    if not data:
        raise HTTPException(404, "不会专学包不存在")
    return data


# 静态前端（history 路由：刷新 /practice 等路径仍返回 index.html）
if STATIC_DIR.exists():
    def index_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.add_api_route("/", index_page, methods=["GET"], include_in_schema=False)
    for _page in (
        "practice",
        "basics",
        "learn",
        "history",
        "knowledge",
        "mastery",
        "assessments",
        "plan",
        "profile",
        "calc",
    ):
        app.add_api_route(f"/{_page}", index_page, methods=["GET"], include_in_schema=False)

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
