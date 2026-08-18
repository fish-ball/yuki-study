# -*- coding: utf-8 -*-
"""汇总指定日期的做题时长、Cursor 对话与作答统计，供 daily-summary Skill 使用。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DB = ROOT / "web" / "data" / "study.db"
JSONL_DIR = ROOT / "practice" / "attempts"

CAP_MS = 10 * 60 * 1000  # 单题有效作答上限 10 分钟
GAP_MERGE_MIN = 15  # 做题：间隔不超过 15 分钟视为同一段
CHAT_GAP_MERGE_MIN = 30  # 对话：Agent 实现往往更久，30 分钟内算同一段
CHAT_TAIL_MIN = 3  # 最后一条用户消息后补的等待回复时长
SKIP_QUERY_MARKERS = (
    "Briefly inform the user about the task result",
    "Execute the selected diff-tab",
)
TS_RE = re.compile(r"<timestamp>([^<]+)</timestamp>", re.I)
QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S | re.I)
TS_FMT = "%A, %b %d, %Y, %I:%M %p (UTC+8)"

SYSTEM_KEYS = (
    "harness",
    "界面",
    "路由",
    "vue",
    "git",
    "github",
    "填空",
    "考核题目",
    "进度条",
    "计划页面",
    "练习架构",
    "教程",
    "拓扑",
    "点阵",
    "sqlite",
    "前端",
    "管理台",
    "免练",
    "通过线",
    "负荷",
    "元素周期表",
    "古诗",
    "文言文",
    "推送",
    "提交",
    "刷新不能",
    "每日小结",
    "学习总结",
)
STUDY_KEYS = (
    "预习",
    "化学变化",
    "元素符号",
    "分子原子",
    "这题",
    "交卷",
    "讲解一下",
    "帮我讲",
)
ANSWER_RE = re.compile(
    r"^(?:\d+\s*[.、:：]?\s*)?(?:对|错|[A-Da-d]|新物质|[A-Za-z]{1,3}(?:\s+[A-Za-z]{1,3}){1,8})$"
)


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26] if "." in fmt else s[:19], fmt)
        except ValueError:
            continue
    return None


def parse_chat_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, TS_FMT)
    except ValueError:
        pass
    m = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M)", raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%b %d, %Y, %I:%M %p")
        except ValueError:
            return None
    return None


def fmt_ms(ms: int) -> str:
    total_s = max(0, int(round(ms / 1000)))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def split_hourly(start: datetime, ms: int) -> dict[int, int]:
    hourly: dict[int, int] = defaultdict(int)
    cur = start
    remain = max(ms, 0)
    while remain > 0:
        hour_end = cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        chunk = min(remain, int((hour_end - cur).total_seconds() * 1000))
        hourly[cur.hour] += chunk
        remain -= chunk
        cur = hour_end
    return hourly


def merge_intervals(
    intervals: list[tuple[datetime, datetime]], gap_min: int = GAP_MERGE_MIN
) -> list[list[datetime]]:
    pts = sorted(intervals, key=lambda x: x[0])
    merged: list[list[datetime]] = []
    for start, end in pts:
        if end <= start:
            continue
        if not merged:
            merged.append([start, end])
            continue
        if start <= merged[-1][1] + timedelta(minutes=gap_min):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def intervals_to_blocks(merged: list[list[datetime]]) -> tuple[int, list[dict], dict[int, int]]:
    total = 0
    blocks = []
    hourly: dict[int, int] = defaultdict(int)
    for a, b in merged:
        span = int((b - a).total_seconds() * 1000)
        total += span
        blocks.append(
            {
                "start": a.strftime("%H:%M:%S"),
                "end": b.strftime("%H:%M:%S"),
                "ms": span,
                "label": fmt_ms(span),
            }
        )
        for h, chunk in split_hourly(a, span).items():
            hourly[h] += chunk
    return total, blocks, hourly


def subject_of(paper_id: str | None, kids: list[str]) -> str:
    pid = paper_id or ""
    if "chemistry" in pid or any(k.startswith("chemistry.") for k in kids):
        return "chemistry"
    if "math" in pid or any(k.startswith("math.") for k in kids):
        return "math"
    if "chinese" in pid or any(k.startswith("chinese.") for k in kids):
        return "chinese"
    if "english" in pid or any(k.startswith("english.") for k in kids):
        return "english"
    if "physics" in pid or any(k.startswith("physics.") for k in kids):
        return "physics"
    return "other"


def find_transcripts_dir() -> Path | None:
    env = os.environ.get("CURSOR_TRANSCRIPTS")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    home = Path.home()
    projects = home / ".cursor" / "projects"
    prefer = []
    if projects.is_dir():
        for child in projects.iterdir():
            tdir = child / "agent-transcripts"
            if not tdir.is_dir():
                continue
            name = child.name.lower()
            if "yuki-study" in name:
                prefer.append(tdir)
            else:
                prefer.append(tdir)
    # yuki-study 优先
    prefer.sort(key=lambda p: (0 if "yuki-study" in p.parent.name.lower() else 1, str(p)))
    return prefer[0] if prefer else None


def extract_text(obj) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(extract_text(x) for x in obj)
    if isinstance(obj, dict):
        if "text" in obj:
            return str(obj.get("text") or "")
        if "content" in obj:
            return extract_text(obj["content"])
        if "message" in obj:
            return extract_text(obj["message"])
    return ""


def classify_query(q: str) -> str:
    t = (q or "").strip()
    low = t.lower()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if lines and all(ANSWER_RE.match(ln) or re.match(r"^\d+\s", ln) for ln in lines[:8]):
        return "study"
    if any(k in t or k in low for k in SYSTEM_KEYS):
        return "system"
    if len(t) > 60 and ("请" in t or "帮我" in t):
        return "system"
    if any(k in t for k in STUDY_KEYS):
        return "study"
    if len(t) < 40 and re.search(r"[对错ABCD]", t):
        return "study"
    return "system"


SECRET_RE = re.compile(r"(?i)(api[_\s-]?key|token\s*plan|sk-|ghp_|Bearer\s)")


def title_from_query(q: str, kind: str) -> str:
    t = re.sub(r"\s+", " ", (q or "").strip())
    if SECRET_RE.search(t):
        if "git" in t.lower():
            return "Git 初始化与推送"
        return "系统改动（凭据类消息已跳过）"
    if not t:
        return "系统改动" if kind == "system" else "学科对话"
    return t[:28]


def collect_chats(day: str) -> dict:
    tdir = find_transcripts_dir()
    empty = {
        "found": False,
        "dir": str(tdir) if tdir else "",
        "user_messages": 0,
        "sessions": [],
        "kind_counts": {"study": 0, "system": 0},
        "totals": {"chat_ms": 0, "chat_label": "0秒", "blocks": 0},
        "blocks": [],
        "hourly": {},
        "intervals": [],
    }
    if not tdir:
        return empty
    empty["found"] = True

    sessions_out = []
    all_intervals: list[tuple[datetime, datetime]] = []
    kind_counts = {"study": 0, "system": 0}
    user_n = 0

    for jsonl in sorted(tdir.rglob("*.jsonl")):
        sid = jsonl.stem
        points: list[tuple[datetime, str, str]] = []
        try:
            lines = jsonl.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("role") != "user":
                continue
            text = extract_text(row)
            ts_m = TS_RE.search(text)
            if not ts_m:
                continue
            ts = parse_chat_ts(ts_m.group(1))
            if not ts or ts.strftime("%Y-%m-%d") != day:
                continue
            qm = QUERY_RE.search(text)
            query = (qm.group(1) if qm else "").strip()
            if any(m in query or m in text for m in SKIP_QUERY_MARKERS):
                continue
            kind = classify_query(query)
            points.append((ts, kind, query))
        if not points:
            continue
        points.sort(key=lambda x: x[0])
        user_n += len(points)
        for _ts, kind, _q in points:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

        # 同一对话内：相邻用户消息间隔 ≤30 分钟计入；末条补 3 分钟或到文件修改时间
        mtime = datetime.fromtimestamp(jsonl.stat().st_mtime)
        sess_intervals = []
        for i, (ts, kind, _q) in enumerate(points):
            if i + 1 < len(points):
                nxt = points[i + 1][0]
                end = (
                    nxt
                    if (nxt - ts) <= timedelta(minutes=CHAT_GAP_MERGE_MIN)
                    else ts + timedelta(minutes=CHAT_TAIL_MIN)
                )
            else:
                end = ts + timedelta(minutes=CHAT_TAIL_MIN)
                if mtime.strftime("%Y-%m-%d") == day and ts < mtime <= ts + timedelta(
                    minutes=CHAT_GAP_MERGE_MIN
                ):
                    end = mtime
            sess_intervals.append((ts, end))
            all_intervals.append((ts, end))

        merged = merge_intervals(sess_intervals, CHAT_GAP_MERGE_MIN)
        span = sum(int((b - a).total_seconds() * 1000) for a, b in merged)
        kinds = {k for _t, k, _q in points}
        kind = "mixed" if len(kinds) > 1 else next(iter(kinds))
        first_q = next((q for _t, _k, q in points if not SECRET_RE.search(q or "")), points[0][2])
        sessions_out.append(
            {
                "id": sid,
                "title": title_from_query(first_q, kind if kind != "mixed" else "system"),
                "kind": kind,
                "user_messages": len(points),
                "start": points[0][0].strftime("%H:%M"),
                "end": merged[-1][1].strftime("%H:%M") if merged else points[-1][0].strftime("%H:%M"),
                "ms": span,
                "label": fmt_ms(span),
            }
        )

    merged_all = merge_intervals(all_intervals, CHAT_GAP_MERGE_MIN)
    chat_ms, blocks, hourly = intervals_to_blocks(merged_all)
    return {
        "found": True,
        "dir": str(tdir),
        "user_messages": user_n,
        "sessions": sessions_out,
        "kind_counts": kind_counts,
        "totals": {
            "chat_ms": chat_ms,
            "chat_label": fmt_ms(chat_ms),
            "blocks": len(merged_all),
        },
        "blocks": blocks,
        "hourly": {str(h): v for h, v in hourly.items()},
        "intervals": [(a.isoformat(), b.isoformat()) for a, b in merged_all],
    }


def load_from_db(day: str) -> tuple[list[dict], list[dict], dict[str, list[str]], dict[str, str]]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sessions = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, paper_id, mode, subject_id, knowledge_id, status,
                   started_at, finished_at, total_questions, correct_count, attempt_count
            FROM practice_sessions
            WHERE started_at LIKE ? OR finished_at LIKE ?
            ORDER BY started_at
            """,
            (day + "%", day + "%"),
        )
    ]
    attempts = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, session_id, paper_id, question_id, user_answer, is_correct,
                   attempt_no, created_at, elapsed_ms
            FROM attempt_records
            WHERE created_at LIKE ?
            ORDER BY created_at
            """,
            (day + "%",),
        )
    ]
    qk: dict[str, list[str]] = {}
    for r in conn.execute("SELECT question_id, knowledge_id FROM question_knowledge"):
        qk.setdefault(r["question_id"], []).append(r["knowledge_id"])
    names = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM knowledge_nodes")
    }
    conn.close()
    return sessions, attempts, qk, names


def load_from_jsonl(day: str) -> list[dict]:
    path = JSONL_DIR / f"{day}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def collect(day: str) -> dict:
    sessions, attempts, qk, names = load_from_db(day)
    if not attempts:
        raw = load_from_jsonl(day)
        attempts = []
        for a in raw:
            attempts.append(
                {
                    "id": a.get("id"),
                    "session_id": a.get("session_id"),
                    "paper_id": a.get("paper_id"),
                    "question_id": a.get("question_id"),
                    "user_answer": a.get("user_answer"),
                    "is_correct": 1
                    if a.get("is_correct") is True
                    else (0 if a.get("is_correct") is False else None),
                    "attempt_no": a.get("attempt_no") or 1,
                    "created_at": a.get("created_at"),
                    "elapsed_ms": a.get("elapsed_ms"),
                }
            )
            for kid in a.get("knowledge_ids") or []:
                qk.setdefault(a.get("question_id") or "", []).append(kid)

    by_kid: dict[str, dict] = defaultdict(
        lambda: {"first_n": 0, "first_ok": 0, "wrong": 0, "unknown": 0}
    )
    by_sub: dict[str, dict] = defaultdict(lambda: {"n": 0, "ok": 0})
    first = [a for a in attempts if (a.get("attempt_no") or 1) == 1]
    for a in first:
        kids = qk.get(a.get("question_id") or "") or []
        sub = subject_of(a.get("paper_id"), kids)
        by_sub[sub]["n"] += 1
        if a.get("is_correct") == 1:
            by_sub[sub]["ok"] += 1
        for kid in kids:
            by_kid[kid]["first_n"] += 1
            if a.get("is_correct") == 1:
                by_kid[kid]["first_ok"] += 1
            else:
                by_kid[kid]["wrong"] += 1
                if a.get("user_answer") == "【不会】":
                    by_kid[kid]["unknown"] += 1

    kid_rows = []
    for kid, st in sorted(by_kid.items()):
        n = st["first_n"]
        kid_rows.append(
            {
                "knowledge_id": kid,
                "name": names.get(kid, kid),
                "first_n": n,
                "first_ok": st["first_ok"],
                "wrong": st["wrong"],
                "unknown": st["unknown"],
                "rate": round(st["first_ok"] / n, 3) if n else 0,
            }
        )

    sub_rows = []
    for sub, st in sorted(by_sub.items()):
        n = st["n"]
        sub_rows.append(
            {
                "subject": sub,
                "first_n": n,
                "first_ok": st["ok"],
                "rate": round(st["ok"] / n, 3) if n else 0,
            }
        )

    practice_intervals = []
    prev_t = None
    active_ms = 0
    hourly_active: dict[int, int] = defaultdict(int)
    for a in attempts:
        t = parse_ts(a.get("created_at"))
        if not t:
            continue
        el = a.get("elapsed_ms")
        if el is None or el < 0:
            el = int((t - prev_t).total_seconds() * 1000) if prev_t and t > prev_t else 0
        el = min(int(el), CAP_MS)
        active_ms += el
        start = t - timedelta(milliseconds=el)
        practice_intervals.append((start, t))
        for h, chunk in split_hourly(start, el).items():
            hourly_active[h] += chunk
        prev_t = t

    practice_merged = merge_intervals(practice_intervals)
    study_ms, blocks, hourly_merged = intervals_to_blocks(practice_merged)

    chats = collect_chats(day)
    chat_intervals = []
    for a_s, b_s in chats.get("intervals") or []:
        a = datetime.fromisoformat(a_s)
        b = datetime.fromisoformat(b_s)
        chat_intervals.append((a, b))
    combined = merge_intervals(practice_intervals + chat_intervals)
    combined_ms, combined_blocks, hourly_combined = intervals_to_blocks(combined)

    chat_hourly = {int(k): v for k, v in (chats.get("hourly") or {}).items()}
    hours = []
    for h in range(24):
        if hourly_active[h] or hourly_merged[h] or chat_hourly.get(h) or hourly_combined[h]:
            hours.append(
                {
                    "hour": h,
                    "range": f"{h:02d}:00-{h:02d}:59",
                    "active_ms": hourly_active[h],
                    "active_label": fmt_ms(hourly_active[h]),
                    "study_ms": hourly_merged[h],
                    "study_label": fmt_ms(hourly_merged[h]),
                    "chat_ms": chat_hourly.get(h, 0),
                    "chat_label": fmt_ms(chat_hourly.get(h, 0)),
                    "combined_ms": hourly_combined[h],
                    "combined_label": fmt_ms(hourly_combined[h]),
                }
            )

    real_sessions = [
        s
        for s in sessions
        if (s.get("attempt_count") or 0) > 0 or (s.get("correct_count") or 0) > 0
    ]

    chats_public = {k: v for k, v in chats.items() if k != "intervals"}

    return {
        "date": day,
        "cap_minutes_per_question": 10,
        "gap_merge_minutes": GAP_MERGE_MIN,
        "totals": {
            "study_ms": study_ms,
            "study_label": fmt_ms(study_ms),
            "active_ms": active_ms,
            "active_label": fmt_ms(active_ms),
            "chat_ms": chats["totals"]["chat_ms"],
            "chat_label": chats["totals"]["chat_label"],
            "combined_ms": combined_ms,
            "combined_label": fmt_ms(combined_ms),
            "attempts": len(attempts),
            "first_attempts": len(first),
            "first_ok": sum(1 for a in first if a.get("is_correct") == 1),
            "first_wrong": sum(1 for a in first if a.get("is_correct") == 0),
            "sessions_with_attempts": len(real_sessions),
            "blocks": len(practice_merged),
            "chat_blocks": chats["totals"]["blocks"],
            "combined_blocks": len(combined),
        },
        "blocks": blocks,
        "combined_blocks": combined_blocks,
        "hourly": hours,
        "subjects": sub_rows,
        "knowledge": kid_rows,
        "chats": chats_public,
    }


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    data = collect(day)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
