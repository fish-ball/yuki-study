# -*- coding: utf-8 -*-
"""计算专题：同一题型换数据反复练（仿可汗学院）。答案一律现算。"""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime
from typing import Any

from .db import get_conn
from .sync import export_paper_json

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
    (20, 21, 29),
]


def _topic_defs() -> list[dict[str, Any]]:
    return [
        {
            "id": "math.pythagorean",
            "subject_id": "math",
            "knowledge_id": "math.integrated.geometry_hard",
            "title": "勾股定理",
            "blurb": "直角边一变，斜边重算。练准 a²+b²=c²。",
        },
        {
            "id": "math.radical",
            "subject_id": "math",
            "knowledge_id": "math.number.radical",
            "title": "二次根式化简",
            "blurb": "把 √(n²·m) 化成 n√m，数字每次不同。",
        },
        {
            "id": "math.quadratic",
            "subject_id": "math",
            "knowledge_id": "math.equation.quadratic",
            "title": "解一元二次方程",
            "blurb": "因式分解求两根，系数换一批再算。",
        },
        {
            "id": "math.linear",
            "subject_id": "math",
            "knowledge_id": "math.function.linear",
            "title": "一次函数求值",
            "blurb": "代入 x 求 y，斜率截距每次换。",
        },
        {
            "id": "physics.speed",
            "subject_id": "physics",
            "knowledge_id": "physics.mech.motion",
            "title": "速度计算",
            "blurb": "v = s / t，换不同路程与时间。",
        },
    ]


def list_calc_topics() -> list[dict[str, Any]]:
    """专题列表，附带从第一次练习起的准确率与练习记录（报错题已排除）。"""
    stats = _load_topic_stats()
    out = []
    for t in _topic_defs():
        item = dict(t)
        st = stats.get(t["id"]) or {}
        item["correct"] = int(st.get("correct") or 0)
        item["total"] = int(st.get("total") or 0)
        item["voided_count"] = int(st.get("voided_count") or 0)
        item["rate"] = st.get("rate")
        item["records"] = st.get("records") or []
        out.append(item)
    return out


def start_calc_drill(topic_id: str, count: int = 8) -> dict[str, Any]:
    from .practice import start_session

    topic = next((t for t in _topic_defs() if t["id"] == topic_id), None)
    if not topic:
        raise ValueError("没有这个计算专题")
    n = max(5, min(int(count or 8), 12))
    rng = random.Random()
    questions = _generate(topic_id, n, rng)
    if not questions:
        raise ValueError("该专题暂无题目")
    paper_id = _write_paper(topic, questions)
    session = start_session(paper_id, force_new=True)
    session["calc_topic"] = topic
    session["theme"] = f"计算专题 · {topic['title']}"
    return session


def _generate(topic_id: str, count: int, rng: random.Random) -> list[dict[str, Any]]:
    makers = {
        "math.pythagorean": _gen_pythagorean,
        "math.radical": _gen_radical,
        "math.quadratic": _gen_quadratic,
        "math.linear": _gen_linear,
        "physics.speed": _gen_speed,
    }
    fn = makers.get(topic_id)
    if not fn:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    guard = 0
    while len(out) < count and guard < 80:
        guard += 1
        q = fn(rng)
        key = (q.get("stem") or "").replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _q(
    stem: str,
    answer: str,
    *,
    accept: list[str] | None = None,
    explanation: str = "",
    qtype: str = "fill",
    score: int = 2,
) -> dict[str, Any]:
    acc = accept or [answer]
    return {
        "qtype": qtype,
        "stem": stem,
        "score": score,
        "options": [],
        "answer_key": answer,
        "answer_accept": acc,
        "explanation": explanation,
    }


def _gen_pythagorean(rng: random.Random) -> dict[str, Any]:
    a, b, c = rng.choice(_PYTHAG_TRIPLES)
    if rng.random() < 0.5:
        a, b = b, a
    mode = rng.choice(["hyp", "leg"])
    if mode == "hyp":
        stem = f"直角三角形中，两直角边分别为 {a} 和 {b}，斜边长是 ______。"
        ans = str(c)
        exp = (
            f"【考点】勾股定理。\n"
            f"【思路】斜边² = {a}² + {b}² = {a * a} + {b * b} = {a * a + b * b} = {c}²，斜边 = {c}。\n"
            f"【易错】数字换了必须重算，不能看见直角三角形就写 5。"
        )
    else:
        # 已知斜边与一条直角边，求另一直角边
        stem = f"直角三角形斜边是 {c}，一条直角边是 {a}，另一条直角边是 ______。"
        ans = str(b)
        exp = (
            f"【考点】勾股定理求直角边。\n"
            f"【思路】另一直角边² = {c}² - {a}² = {c * c} - {a * a} = {b * b}，所以是 {b}。\n"
            f"【易错】是平方差，不要做成 {c}-{a}。"
        )
    return _q(stem, ans, explanation=exp, score=3)


def _gen_radical(rng: random.Random) -> dict[str, Any]:
    n = rng.choice([2, 3, 4, 5, 6])
    m = rng.choice([2, 3, 5, 6, 7])
    inside = n * n * m
    stem = f"化简：√{inside} = ______。"
    if n == 1:
        ans = f"√{m}"
        acc = [ans, f"sqrt{m}"]
    else:
        ans = f"{n}√{m}"
        acc = [ans, f"{n}sqrt{m}", f"{n}*√{m}"]
    exp = (
        f"【考点】二次根式化简 √(k²·m)=|k|√m。\n"
        f"【思路】{inside} = {n}² × {m}，所以 √{inside} = {n}√{m}。\n"
        f"【易错】不要把根号里的数直接开成小数交差；先拆完全平方因数。"
    )
    return _q(stem, ans, accept=acc, explanation=exp, score=3)


def _gen_quadratic(rng: random.Random) -> dict[str, Any]:
    p = rng.choice([-5, -4, -3, -2, 2, 3, 4, 5])
    q = rng.choice([-6, -4, -1, 1, 2, 3, 6])
    if p == q:
        q = p + 1 if p > 0 else p - 1
    s = p + q
    prod = p * q
    mid_coef = -s
    if mid_coef == 0:
        mid = ""
    elif mid_coef == 1:
        mid = "+x"
    elif mid_coef == -1:
        mid = "-x"
    elif mid_coef > 0:
        mid = f"+{mid_coef}x"
    else:
        mid = f"{mid_coef}x"
    const = f"+{prod}" if prod >= 0 else str(prod)
    stem = f"方程 x²{mid}{const}=0 的两根是 ______ 和 ______（从小到大）。"
    lo, hi = sorted([p, q])
    ans = f"{lo}；{hi}"
    exp = (
        f"【考点】因式分解解一元二次方程。\n"
        f"【思路】(x-({p}))(x-({q}))=0，两根为 {p} 和 {q}。从小到大：{lo}、{hi}。\n"
        f"【易错】一次项系数等于两根之和的相反数。"
    )
    return _q(stem, ans, accept=[str(lo), str(hi)], explanation=exp, score=4)


def _gen_linear(rng: random.Random) -> dict[str, Any]:
    k = rng.choice([-4, -3, -2, -1, 2, 3, 4, 5])
    b = rng.choice([-6, -3, -1, 0, 1, 2, 5, 8])
    x = rng.choice([-3, -2, -1, 0, 1, 2, 4, 5])
    y = k * x + b
    kb = f"{k}x" if k not in (1, -1) else ("x" if k == 1 else "-x")
    if b > 0:
        expr = f"{kb}+{b}"
    elif b < 0:
        expr = f"{kb}{b}"
    else:
        expr = kb
    stem = f"一次函数 y={expr}。当 x={x} 时，y 的值是 ______。"
    exp = (
        f"【考点】一次函数代入求值。\n"
        f"【思路】y={k}×({x})+({b})={k * x}+({b})={y}。\n"
        f"【易错】负号：k 或 x 为负时先带括号再乘。"
    )
    return _q(stem, str(y), explanation=exp, score=2)


def _gen_speed(rng: random.Random) -> dict[str, Any]:
    t = rng.choice([2, 4, 5, 8, 10])
    v = rng.choice([3, 4, 5, 6, 8, 10, 12])
    s = v * t
    mode = rng.choice(["v", "s", "t"])
    if mode == "v":
        stem = f"一物体 {t} 秒内通过 {s} 米，平均速度是 ______ m/s。"
        ans = str(v)
        exp = f"【考点】v=s/t。\n【思路】{s}÷{t}={v}（m/s）。\n【易错】单位要统一；不要做成 {s}×{t}。"
    elif mode == "s":
        stem = f"平均速度 {v} m/s，运动 {t} 秒，路程是 ______ 米。"
        ans = str(s)
        exp = f"【考点】s=vt。\n【思路】{v}×{t}={s} 米。\n【易错】时间单位已是秒，不必再换。"
    else:
        stem = f"路程 {s} 米，平均速度 {v} m/s，所用时间是 ______ 秒。"
        ans = str(t)
        exp = f"【考点】t=s/v。\n【思路】{s}÷{v}={t} 秒。\n【易错】不要写成 {v}÷{s}。"
    return _q(stem, ans, explanation=exp, score=2)


def _write_paper(topic: dict[str, Any], questions: list[dict[str, Any]]) -> str:
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H%M%S")
    paper_id = f"drill-calc-{topic['id'].replace('.', '_')}-{today}-{stamp}"
    theme = f"计算专题 · {topic['title']}"
    kid = topic["knowledge_id"]
    sid = topic["subject_id"]
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO assessments
        (id, path, subject_id, theme, date, minutes, target_level, status, content_md, note)
        VALUES (?, ?, ?, ?, ?, 20, 'L2', 'ready', '', ?)
        """,
        (
            paper_id,
            f"practice/papers/{paper_id}.json",
            sid,
            theme,
            today,
            f"计算专题；topic={topic['id']}；同一题型换数据；{kid}",
        ),
    )
    now = datetime.now().isoformat(timespec="seconds")
    for i, t in enumerate(questions, 1):
        qid = f"{paper_id}__q{i:02d}"
        conn.execute(
            """
            INSERT INTO questions
            (id, paper_id, subject_id, qtype, stem, score, sort_order, explanation,
             answer_key, answer_accept, auto_gradable, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'calc_drill', ?, ?)
            """,
            (
                qid,
                paper_id,
                sid,
                t.get("qtype") or "fill",
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
    return paper_id


def topic_id_from_paper(paper_id: str | None, note: str | None = None, theme: str | None = None) -> str | None:
    pid = str(paper_id or "")
    note_s = str(note or "")
    for t in _topic_defs():
        token = "drill-calc-" + t["id"].replace(".", "_") + "-"
        if pid.startswith(token):
            return t["id"]
        if f"topic={t['id']}" in note_s:
            return t["id"]
    return None


def _load_topic_stats() -> dict[str, dict[str, Any]]:
    """按专题汇总首次作答（voided 不计入），并从第一次练习开始算。"""
    conn = get_conn()
    papers = conn.execute(
        """
        SELECT id, note, theme FROM assessments
        WHERE id LIKE 'drill-calc-%' OR theme LIKE '计算专题%'
        """
    ).fetchall()
    paper_topic: dict[str, str] = {}
    for p in papers:
        tid = topic_id_from_paper(p["id"], p["note"], p["theme"])
        if tid:
            paper_topic[p["id"]] = tid

    attempts = conn.execute(
        """
        SELECT a.session_id, a.paper_id, a.question_id, a.is_correct,
               COALESCE(a.voided, 0) AS voided, a.attempt_no, a.created_at
        FROM attempt_records a
        WHERE a.attempt_no = 1 AND a.paper_id LIKE 'drill-calc-%'
        ORDER BY a.created_at
        """
    ).fetchall()
    sessions = conn.execute(
        """
        SELECT id, paper_id, started_at, finished_at, status
        FROM practice_sessions
        WHERE paper_id LIKE 'drill-calc-%'
        ORDER BY started_at DESC
        """
    ).fetchall()
    conn.close()

    acc: dict[str, dict[str, Any]] = {
        t["id"]: {"correct": 0, "total": 0, "voided_count": 0, "records": []}
        for t in _topic_defs()
    }
    session_rows: dict[str, dict[str, int]] = {}
    for a in attempts:
        tid = paper_topic.get(a["paper_id"])
        if not tid:
            continue
        sid = a["session_id"]
        bucket = session_rows.setdefault(sid, {"correct": 0, "total": 0, "voided": 0})
        if int(a["voided"] or 0) == 1:
            acc[tid]["voided_count"] += 1
            bucket["voided"] += 1
            continue
        if a["is_correct"] is None:
            continue
        acc[tid]["total"] += 1
        bucket["total"] += 1
        if a["is_correct"] == 1:
            acc[tid]["correct"] += 1
            bucket["correct"] += 1

    for s in sessions:
        tid = paper_topic.get(s["paper_id"])
        if not tid:
            continue
        sc = session_rows.get(s["id"]) or {"correct": 0, "total": 0, "voided": 0}
        if sc["total"] == 0 and sc["voided"] == 0 and s["status"] != "completed":
            continue
        acc[tid]["records"].append(
            {
                "session_id": s["id"],
                "paper_id": s["paper_id"],
                "started_at": s["started_at"],
                "finished_at": s["finished_at"],
                "status": s["status"],
                "correct": sc["correct"],
                "total": sc["total"],
                "voided": sc["voided"],
            }
        )

    for tid, st in acc.items():
        st["records"] = st["records"][:8]
        tot = st["total"]
        st["rate"] = round(st["correct"] / tot, 4) if tot else None
    return acc


def session_scored_stats(session_id: str) -> dict[str, int]:
    """本场计入正确率的首次作答（报错作废题排除）。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT is_correct, COALESCE(voided, 0) AS voided
        FROM attempt_records
        WHERE session_id = ? AND attempt_no = 1
        """,
        (session_id,),
    ).fetchall()
    conn.close()
    correct = total = voided = 0
    for r in rows:
        if int(r["voided"] or 0) == 1:
            voided += 1
            continue
        if r["is_correct"] is None:
            continue
        total += 1
        if r["is_correct"] == 1:
            correct += 1
    return {"correct": correct, "total": total, "voided": voided}


def recompute_calc_answer(stem: str) -> dict[str, Any] | None:
    """根据题面重算填空答案。无法解析则返回 None。"""
    from .assessment_progress import _PYTHAG_LEGS, _hyp_from_legs, _simplify_sqrt

    text = stem or ""
    m = _PYTHAG_LEGS.search(text)
    if m and "斜边" in text and "另一条直角边" not in text:
        a, b = int(m.group(1)), int(m.group(2))
        key, accept, exp = _hyp_from_legs(a, b)
        return {"answer_key": key, "answer_accept": accept, "explanation": exp}

    m = re.search(
        r"斜边是\s*(\d+).*一条直角边是\s*(\d+).*另一条直角边",
        text,
    )
    if m:
        c, a = int(m.group(1)), int(m.group(2))
        diff = c * c - a * a
        if diff <= 0:
            return None
        root = int(round(diff ** 0.5))
        if root * root == diff:
            key = str(root)
            accept = [key]
            exp = (
                f"【考点】勾股定理求直角边。\n"
                f"【思路】另一直角边² = {c}² - {a}² = {diff} = {root}²，所以是 {root}。"
            )
        else:
            coeff, rest = _simplify_sqrt(diff)
            key = str(coeff) if rest == 1 else (f"√{rest}" if coeff == 1 else f"{coeff}√{rest}")
            accept = [key, f"sqrt{diff}"]
            exp = (
                f"【考点】勾股定理求直角边。\n"
                f"【思路】另一直角边² = {c}² - {a}² = {diff}，所以是 {key}。"
            )
        return {"answer_key": key, "answer_accept": accept, "explanation": exp}

    m = re.search(r"化简[：:]\s*√\s*(\d+)", text)
    if m:
        inside = int(m.group(1))
        coeff, rest = _simplify_sqrt(inside)
        if rest == 1:
            key = str(coeff)
            accept = [key]
        elif coeff == 1:
            key = f"√{rest}"
            accept = [key, f"sqrt{rest}"]
        else:
            key = f"{coeff}√{rest}"
            accept = [key, f"{coeff}sqrt{rest}", f"{coeff}*√{rest}", f"√{inside}"]
        exp = (
            f"【考点】二次根式化简。\n"
            f"【思路】{inside} = {coeff}² × {rest}，所以 √{inside} = {key}。"
        )
        return {"answer_key": key, "answer_accept": accept, "explanation": exp}

    m = re.search(r"一次函数\s*y\s*=\s*(.+?)。当\s*x\s*=\s*(-?\d+)", text)
    if m:
        y = _eval_linear_expr(m.group(1), int(m.group(2)))
        if y is None:
            return None
        x = int(m.group(2))
        key = str(y)
        exp = f"【考点】一次函数代入求值。\n【思路】把 x={x} 代入，得 y={y}。"
        return {"answer_key": key, "answer_accept": [key], "explanation": exp}

    m = re.search(r"(\d+)\s*秒内通过\s*(\d+)\s*米，平均速度", text)
    if m:
        t, s = int(m.group(1)), int(m.group(2))
        if t == 0 or s % t != 0:
            return None
        v = s // t
        key = str(v)
        exp = f"【考点】v=s/t。\n【思路】{s}÷{t}={v}（m/s）。"
        return {"answer_key": key, "answer_accept": [key], "explanation": exp}
    m = re.search(r"平均速度\s*(\d+)\s*m/s，运动\s*(\d+)\s*秒，路程", text)
    if m:
        v, t = int(m.group(1)), int(m.group(2))
        s = v * t
        key = str(s)
        exp = f"【考点】s=vt。\n【思路】{v}×{t}={s} 米。"
        return {"answer_key": key, "answer_accept": [key], "explanation": exp}
    m = re.search(r"路程\s*(\d+)\s*米，平均速度\s*(\d+)\s*m/s，所用时间", text)
    if m:
        s, v = int(m.group(1)), int(m.group(2))
        if v == 0 or s % v != 0:
            return None
        t = s // v
        key = str(t)
        exp = f"【考点】t=s/v。\n【思路】{s}÷{v}={t} 秒。"
        return {"answer_key": key, "answer_accept": [key], "explanation": exp}

    m = re.search(r"方程\s*x[²2](.*)=0", text)
    if m:
        roots = _solve_monic_quadratic(m.group(1))
        if not roots:
            return None
        lo, hi = roots
        key = f"{lo}；{hi}"
        exp = (
            f"【考点】解一元二次方程。\n"
            f"【思路】两根从小到大是 {lo}、{hi}。"
        )
        return {
            "answer_key": key,
            "answer_accept": [str(lo), str(hi)],
            "explanation": exp,
        }
    return None


def _eval_linear_expr(expr: str, x: int) -> int | None:
    s = (expr or "").replace(" ", "").replace("−", "-")
    m = re.fullmatch(r"([+-]?)(\d*)x([+-]\d+)?", s)
    if not m:
        return None
    sign, coef, b_s = m.group(1), m.group(2), m.group(3)
    k = 1 if coef == "" else int(coef)
    if sign == "-":
        k = -k
    b = int(b_s) if b_s else 0
    return k * x + b


def _solve_monic_quadratic(mid_const: str) -> tuple[int, int] | None:
    """解析 x² 后面的一次项与常数项，求整数根。"""
    rest = (mid_const or "").replace(" ", "").replace("−", "-")
    b, c = 0, 0
    cm = re.search(r"([+-]\d+)$", rest)
    if cm:
        c = int(cm.group(1))
        rest = rest[: cm.start()]
    elif rest.endswith("+") or rest.endswith("-"):
        return None
    rest = rest.replace("x", "")
    if rest in ("", "+"):
        b = 1
    elif rest == "-":
        b = -1
    elif rest:
        try:
            b = int(rest)
        except ValueError:
            return None
    disc = b * b - 4 * c
    if disc < 0:
        return None
    root = int(round(disc ** 0.5))
    if root * root != disc:
        return None
    if ( -b + root) % 2 != 0:
        return None
    r1 = (-b + root) // 2
    r2 = (-b - root) // 2
    return (min(r1, r2), max(r1, r2))


def question_allows_report(paper_id: str | None, subject_id: str | None, qtype: str | None) -> bool:
    pid = str(paper_id or "")
    theme_ok = pid.startswith("drill-calc-")
    if theme_ok:
        return True
    qt = qtype or ""
    sub = str(subject_id or "")
    if qt in ("fill", "short") and (
        sub in ("math", "physics") or sub.startswith("math.") or sub.startswith("physics.")
    ):
        return True
    return False


def report_answer_error(session_id: str, question_id: str) -> dict[str, Any]:
    """报错：立刻按题面重算；若标准答案确实错了则修正，本题不计入正确率。"""
    import uuid

    from .question_parse import _norm_fill

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
    if not question_allows_report(q["paper_id"], q["subject_id"], q["qtype"]):
        conn.close()
        raise ValueError("本题不支持报错")

    old = (q["answer_key"] or "").strip()
    recomputed = recompute_calc_answer(q["stem"] or "")
    now = datetime.now().isoformat(timespec="seconds")
    fixed = False
    excluded = False
    new_key = old
    new_exp = q["explanation"] or ""
    new_accept = json.loads(q["answer_accept"] or "[]")

    if recomputed:
        new_key = recomputed["answer_key"]
        new_accept = recomputed["answer_accept"]
        new_exp = recomputed["explanation"]
        same = _norm_fill(old) == _norm_fill(new_key)
        if same:
            detail = (
                f"系统按题面重算，标准答案仍是 {new_key}，没有发现出错。"
                "本题继续计入正确率。请再核对你的计算步骤。"
            )
        else:
            fixed = True
            excluded = True
            conn.execute(
                """
                UPDATE questions
                SET answer_key = ?, answer_accept = ?, explanation = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_key,
                    json.dumps(new_accept, ensure_ascii=False),
                    new_exp,
                    now,
                    question_id,
                ),
            )
            export_paper_json(conn, q["paper_id"])
            detail = (
                f"已修正：原标准答案「{old}」按题面重算应为「{new_key}」。"
                "本题不计入正确率。刷新后可按新答案再做一遍（仍不计入）。"
            )
    else:
        excluded = True
        detail = (
            "已报错。系统暂时无法从题面自动重算，本题不计入正确率。"
            "请把题干发给老师继续查。"
        )

    if excluded:
        first = conn.execute(
            """
            SELECT is_correct FROM attempt_records
            WHERE session_id = ? AND question_id = ? AND attempt_no = 1
            """,
            (session_id, question_id),
        ).fetchone()
        conn.execute(
            """
            UPDATE attempt_records
            SET voided = 1, void_reason = 'report_error'
            WHERE session_id = ? AND question_id = ?
            """,
            (session_id, question_id),
        )
        if not first:
            conn.execute(
                """
                INSERT INTO attempt_records
                (id, session_id, question_id, paper_id, user_answer, is_correct, feedback,
                 attempt_no, created_at, elapsed_ms, voided, void_reason)
                VALUES (?, ?, ?, ?, ?, NULL, ?, 1, ?, NULL, 1, 'report_error')
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    question_id,
                    q["paper_id"],
                    "[报错]",
                    "已报错，本题不计入正确率。",
                    now,
                ),
            )
        if first and first["is_correct"] == 1:
            conn.execute(
                """
                UPDATE practice_sessions
                SET correct_count = CASE WHEN correct_count > 0 THEN correct_count - 1 ELSE 0 END
                WHERE id = ?
                """,
                (session_id,),
            )
        if first and first["is_correct"] == 0:
            kids = conn.execute(
                "SELECT knowledge_id FROM question_knowledge WHERE question_id = ?",
                (question_id,),
            ).fetchall()
            for kid in kids:
                conn.execute(
                    """
                    UPDATE mastery_items
                    SET wrong_count = CASE WHEN wrong_count > 0 THEN wrong_count - 1 ELSE 0 END
                    WHERE knowledge_id = ?
                    """,
                    (kid["knowledge_id"],),
                )

    rid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO calc_answer_reports
        (id, session_id, question_id, paper_id, old_answer, new_answer, fixed, excluded, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            session_id,
            question_id,
            q["paper_id"],
            old,
            new_key,
            1 if fixed else 0,
            1 if excluded else 0,
            detail,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return {
        "report_id": rid,
        "fixed": fixed,
        "excluded": excluded,
        "old_answer": old,
        "new_answer": new_key,
        "explanation": new_exp if fixed else "",
        "detail": detail,
        "can_retry": excluded,
    }

