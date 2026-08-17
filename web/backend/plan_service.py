"""周/日计划、进度、负荷问卷与掌握度赋分。"""
from __future__ import annotations

import json
import math
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import ROOT, get_conn, init_db

PLANS_DIR = ROOT / "plans"
DAYS_DIR = PLANS_DIR / "days"

# 掌握度赋分：L0=0 … L4=4；满分=点数×4
LEVEL_SCORE = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
MAX_LEVEL_SCORE = 4


def ensure_plan_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS week_plan_meta (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          week_id TEXT NOT NULL,
          start_date TEXT,
          end_date TEXT,
          title TEXT NOT NULL DEFAULT '',
          content_md TEXT NOT NULL DEFAULT '',
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS day_plans (
          id TEXT PRIMARY KEY,
          week_id TEXT NOT NULL,
          plan_date TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL DEFAULT '',
          focus_text TEXT NOT NULL DEFAULT '',
          review_text TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          completed_at TEXT,
          base_item_count INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS day_plan_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day_plan_id TEXT NOT NULL,
          knowledge_id TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'main',
          sort_order INTEGER NOT NULL DEFAULT 0,
          done INTEGER NOT NULL DEFAULT 0,
          done_at TEXT,
          UNIQUE(day_plan_id, knowledge_id),
          FOREIGN KEY (day_plan_id) REFERENCES day_plans(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_surveys (
          id TEXT PRIMARY KEY,
          day_plan_id TEXT NOT NULL,
          week_id TEXT,
          plan_date TEXT,
          volume TEXT NOT NULL,
          increase_yes INTEGER,
          rescale_week INTEGER,
          items_before INTEGER,
          items_after INTEGER,
          created_at TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_day_plans_date ON day_plans(plan_date);
        CREATE INDEX IF NOT EXISTS idx_day_items_day ON day_plan_items(day_plan_id);
        """
    )
    conn.commit()


def compute_mastery_score(conn=None) -> dict[str, Any]:
    """按等级赋分汇总：已得分 / 总分（全部熟练 L4）。"""
    own = conn is None
    if own:
        conn = get_conn()
    rows = conn.execute(
        """
        SELECT m.level
        FROM mastery_items m
        JOIN knowledge_nodes k ON k.id = m.knowledge_id
        """
    ).fetchall()
    earned = 0
    n = 0
    by_level = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0}
    for r in rows:
        lv = r["level"] if r["level"] in LEVEL_SCORE else "L0"
        earned += LEVEL_SCORE[lv]
        by_level[lv] = by_level.get(lv, 0) + 1
        n += 1
    total = n * MAX_LEVEL_SCORE
    pct = round(100.0 * earned / total, 1) if total else 0.0
    if own:
        conn.close()
    return {
        "earned": earned,
        "total": total,
        "percent": pct,
        "point_count": n,
        "level_scores": LEVEL_SCORE,
        "by_level": by_level,
        "formula": "L0=0,L1=1,L2=2,L3=3,L4=4；总分=点数×4",
    }


def parse_week_markdown(content_md: str) -> dict[str, Any]:
    """从周计划 Markdown 表格解析每日知识点。"""
    start_date = None
    end_date = None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*[~～\-至]+\s*(\d{4}-\d{2}-\d{2})", content_md)
    if m:
        start_date, end_date = m.group(1), m.group(2)

    title = ""
    tm = re.search(r"^#\s+(.+)$", content_md, re.M)
    if tm:
        title = tm.group(1).strip()

    days: list[dict[str, Any]] = []
    # 表格行：| 日 08-16 | ... | `id` `id` | ... |
    year = (start_date or date.today().isoformat())[:4]
    for line in content_md.splitlines():
        if not line.strip().startswith("|"):
            continue
        if re.search(r"\|?\s*日期\s*\|", line) or re.match(r"\|\s*[-:]+", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        date_cell, focus, kids_cell = cells[0], cells[1], cells[2]
        review = cells[3] if len(cells) > 3 else ""
        # 解析日期
        dm = re.search(r"(\d{1,2})[-/.](\d{1,2})", date_cell)
        if not dm:
            continue
        month, day = int(dm.group(1)), int(dm.group(2))
        plan_date = f"{year}-{month:02d}-{day:02d}"
        # 若跨年且有 start/end，按 start 年修正
        if start_date and plan_date < start_date[:7] + "-01" and end_date:
            # 简单：若月日早于 start 的月且 end 跨年则用 end 年
            pass
        if start_date:
            try:
                sd = date.fromisoformat(start_date)
                candidate = date(sd.year, month, day)
                if end_date:
                    ed = date.fromisoformat(end_date)
                    if candidate < sd and ed.year > sd.year:
                        candidate = date(ed.year, month, day)
                plan_date = candidate.isoformat()
            except ValueError:
                pass

        knowledge_ids = re.findall(r"`([a-z][a-z0-9_.*]+)`", kids_cell)
        # 展开通配 chemistry.basic.*
        expanded: list[str] = []
        for kid in knowledge_ids:
            kid = kid.rstrip(".")
            if kid.endswith(".*"):
                expanded.extend(_expand_prefix(kid[:-2]))
            elif kid.endswith("*"):
                expanded.extend(_expand_prefix(kid[:-1].rstrip(".")))
            else:
                expanded.append(kid)
        # 去重保序
        seen = set()
        uniq = []
        for k in expanded:
            if k not in seen:
                seen.add(k)
                uniq.append(k)

        days.append(
            {
                "plan_date": plan_date,
                "title": date_cell,
                "focus_text": focus,
                "review_text": review,
                "knowledge_ids": uniq,
            }
        )

    week_id = f"week_{(start_date or (days[0]['plan_date'] if days else date.today().isoformat()))}"
    return {
        "week_id": week_id,
        "start_date": start_date or (days[0]["plan_date"] if days else None),
        "end_date": end_date or (days[-1]["plan_date"] if days else None),
        "title": title,
        "days": days,
    }


def _expand_prefix(prefix: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id FROM knowledge_nodes
        WHERE id = ? OR id LIKE ?
        ORDER BY id
        """,
        (prefix, prefix + ".%"),
    ).fetchall()
    conn.close()
    ids = [r["id"] for r in rows]
    # 优先子节点；若只有前缀本身则返回它
    children = [i for i in ids if i != prefix]
    return (children or ids)[:30]


def rebuild_days_from_week(content_md: str | None = None, force: bool = False) -> dict[str, Any]:
    """根据周计划 Markdown 重建日计划（保留已完成日的 done 状态尽量合并）。"""
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    init_db(conn)
    ensure_plan_tables(conn)

    if content_md is None:
        row = conn.execute("SELECT content_md FROM plans WHERE id = 1").fetchone()
        content_md = row["content_md"] if row else ""
        if not content_md:
            path = PLANS_DIR / "current-week.md"
            content_md = path.read_text(encoding="utf-8") if path.exists() else ""

    parsed = parse_week_markdown(content_md)
    week_id = parsed["week_id"]
    now = datetime.now().isoformat(timespec="seconds")

    # 旧进度
    old_done: dict[str, set[str]] = {}
    old_status: dict[str, str] = {}
    for d in conn.execute("SELECT id, plan_date, status FROM day_plans").fetchall():
        old_status[d["plan_date"]] = d["status"]
        done_ids = conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? AND done = 1",
            (d["id"],),
        ).fetchall()
        old_done[d["plan_date"]] = {r["knowledge_id"] for r in done_ids}

    if force:
        conn.execute("DELETE FROM day_plan_items")
        conn.execute("DELETE FROM day_plans")

    conn.execute(
        """
        INSERT INTO week_plan_meta (id, week_id, start_date, end_date, title, content_md, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          week_id=excluded.week_id,
          start_date=excluded.start_date,
          end_date=excluded.end_date,
          title=excluded.title,
          content_md=excluded.content_md,
          updated_at=excluded.updated_at
        """,
        (
            week_id,
            parsed["start_date"],
            parsed["end_date"],
            parsed["title"],
            content_md,
            now,
        ),
    )

    for day in parsed["days"]:
        did = f"day_{day['plan_date']}"
        kids = day["knowledge_ids"]
        prev_status = old_status.get(day["plan_date"], "pending")
        if prev_status == "completed" and not force:
            status = "completed"
        else:
            status = prev_status if prev_status in ("pending", "in_progress", "completed") else "pending"

        conn.execute(
            """
            INSERT INTO day_plans
            (id, week_id, plan_date, title, focus_text, review_text, status, base_item_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              week_id=excluded.week_id,
              title=excluded.title,
              focus_text=excluded.focus_text,
              review_text=excluded.review_text,
              base_item_count=CASE
                WHEN day_plans.base_item_count = 0 THEN excluded.base_item_count
                ELSE day_plans.base_item_count
              END,
              updated_at=excluded.updated_at
            """,
            (
                did,
                week_id,
                day["plan_date"],
                day["title"],
                day["focus_text"],
                day["review_text"],
                status,
                len(kids),
                now,
            ),
        )
        # 若 force 或该日尚无条目，重建条目；否则补齐周计划中新增知识点
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?", (did,)
        ).fetchone()["c"]
        if force or existing == 0:
            conn.execute("DELETE FROM day_plan_items WHERE day_plan_id = ?", (did,))
            for i, kid in enumerate(kids):
                was_done = 1 if kid in old_done.get(day["plan_date"], set()) else 0
                conn.execute(
                    """
                    INSERT INTO day_plan_items
                    (day_plan_id, knowledge_id, role, sort_order, done, done_at)
                    VALUES (?, ?, 'main', ?, ?, ?)
                    """,
                    (
                        did,
                        kid,
                        i,
                        was_done,
                        now if was_done else None,
                    ),
                )
        else:
            have = {
                r["knowledge_id"]
                for r in conn.execute(
                    "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ?",
                    (did,),
                ).fetchall()
            }
            max_ord = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m FROM day_plan_items WHERE day_plan_id = ?",
                (did,),
            ).fetchone()["m"]
            for kid in kids:
                if kid in have:
                    continue
                max_ord += 1
                conn.execute(
                    """
                    INSERT OR IGNORE INTO day_plan_items
                    (day_plan_id, knowledge_id, role, sort_order, done, done_at)
                    VALUES (?, ?, 'main', ?, 0, NULL)
                    """,
                    (did, kid, max_ord),
                )
            # 未完成的日计划：状态若误为 completed 且仍有未做条目则改回
            undone = conn.execute(
                "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ? AND done = 0",
                (did,),
            ).fetchone()["c"]
            if undone > 0 and prev_status == "completed":
                conn.execute(
                    """
                    UPDATE day_plans
                    SET status = 'pending', completed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, did),
                )
        _export_day_json(conn, did)

    conn.commit()
    result = get_plan_bundle(conn)
    conn.close()
    return result


def _export_day_json(conn, day_plan_id: str) -> None:
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    if not day:
        return
    items = conn.execute(
        """
        SELECT i.*, k.name, m.level
        FROM day_plan_items i
        LEFT JOIN knowledge_nodes k ON k.id = i.knowledge_id
        LEFT JOIN mastery_items m ON m.knowledge_id = i.knowledge_id
        WHERE i.day_plan_id = ?
        ORDER BY i.sort_order, i.id
        """,
        (day_plan_id,),
    ).fetchall()
    payload = {
        **dict(day),
        "items": [dict(x) for x in items],
    }
    path = DAYS_DIR / f"{day['plan_date']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_plan_bundle(conn=None) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = get_conn()
        ensure_plan_tables(conn)

    week = conn.execute("SELECT * FROM week_plan_meta WHERE id = 1").fetchone()
    plan_row = conn.execute("SELECT content_md, updated_at FROM plans WHERE id = 1").fetchone()

    # 若尚无日计划但有周计划正文，自动解析一次
    day_count = conn.execute("SELECT COUNT(*) AS c FROM day_plans").fetchone()["c"]
    if day_count == 0 and plan_row and plan_row["content_md"]:
        if own:
            conn.close()
        return rebuild_days_from_week(plan_row["content_md"], force=True)

    days = []
    for d in conn.execute(
        "SELECT * FROM day_plans ORDER BY plan_date"
    ).fetchall():
        items = [
            dict(r)
            for r in conn.execute(
                """
                SELECT i.id, i.knowledge_id, i.role, i.sort_order, i.done, i.done_at,
                       k.name, k.exam_weight, m.level, m.wrong_count
                FROM day_plan_items i
                LEFT JOIN knowledge_nodes k ON k.id = i.knowledge_id
                LEFT JOIN mastery_items m ON m.knowledge_id = i.knowledge_id
                WHERE i.day_plan_id = ?
                ORDER BY i.sort_order, i.id
                """,
                (d["id"],),
            ).fetchall()
        ]
        total = len(items)
        done = sum(1 for x in items if x["done"])
        days.append(
            {
                **dict(d),
                "items": items,
                "progress": {
                    "done": done,
                    "total": total,
                    "percent": round(100.0 * done / total, 1) if total else 0.0,
                },
            }
        )

    # 周进度（按知识点条目）
    all_items = conn.execute(
        "SELECT done FROM day_plan_items"
    ).fetchall()
    w_total = len(all_items)
    w_done = sum(1 for x in all_items if x["done"])

    today = date.today().isoformat()
    today_plan = next((x for x in days if x["plan_date"] == today), None)
    if today_plan is None and days:
        # 取最近未完成或最后一天
        today_plan = next((x for x in days if x["status"] != "completed"), days[0])

    pending_survey = None
    if today_plan and today_plan["status"] != "completed":
        pending_survey = _build_pending_survey(conn, today_plan)

    score = compute_mastery_score(conn)
    result = {
        "week": dict(week) if week else None,
        "content_md": plan_row["content_md"] if plan_row else "",
        "updated_at": plan_row["updated_at"] if plan_row else None,
        "days": days,
        "today": today,
        "today_plan": today_plan,
        "pending_survey": pending_survey,
        "week_progress": {
            "done": w_done,
            "total": w_total,
            "percent": round(100.0 * w_done / w_total, 1) if w_total else 0.0,
        },
        "mastery_score": score,
    }
    if own:
        conn.close()
    return result


def _build_pending_survey(conn, today_plan: dict[str, Any]) -> dict[str, Any] | None:
    """今日知识点练习全部真正完成后，才弹出负荷问卷。"""
    from .day_drills import ensure_drill_tables

    ensure_drill_tables(conn)
    day_id = today_plan["id"]
    plan_date = today_plan["plan_date"]
    item_n = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
        (day_id,),
    ).fetchone()["c"]
    if item_n <= 0:
        return None
    drill_rows = conn.execute(
        "SELECT status FROM day_drills WHERE day_plan_id = ?",
        (day_id,),
    ).fetchall()
    if len(drill_rows) < item_n:
        return None
    if any(r["status"] != "completed" for r in drill_rows):
        return None
    done_items = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ? AND done = 1",
        (day_id,),
    ).fetchone()["c"]
    if done_items < item_n:
        return None
    return {
        "day_plan_id": day_id,
        "plan_date": plan_date,
        "step": "volume",
        "question": "今日练习已全部完成。今天的内容量如何？",
        "options": [
            {"value": "too_much", "label": "过多"},
            {"value": "ok", "label": "适中"},
            {"value": "too_little", "label": "过少"},
        ],
    }


def bootstrap_plan(plan_date: str | None = None) -> dict[str, Any]:
    """
    自动就绪：同步周计划 → 生成/更新日计划 → 撤回误标完成 → 生成今日练习。
    用户无需手动点「生成」。
    """
    plan_date = plan_date or date.today().isoformat()
    conn = get_conn()
    init_db(conn)
    ensure_plan_tables(conn)

    path = PLANS_DIR / "current-week.md"
    file_md = path.read_text(encoding="utf-8") if path.exists() else ""
    row = conn.execute("SELECT content_md FROM plans WHERE id = 1").fetchone()
    db_md = row["content_md"] if row else ""
    # 以仓库周计划文件为准（避免刷新后内容丢失）
    if file_md.strip():
        now = date.today().isoformat()
        conn.execute(
            """
            INSERT INTO plans (id, content_md, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              content_md = excluded.content_md,
              updated_at = excluded.updated_at
            """,
            (file_md, now),
        )
        conn.commit()
        content_md = file_md
    else:
        content_md = db_md
    conn.close()

    if content_md.strip():
        rebuild_days_from_week(content_md, force=False)

    from .day_drills import ensure_day_drills, reopen_incomplete_drills

    reopen_incomplete_drills(plan_date)
    try:
        ensure_day_drills(plan_date)
    except ValueError:
        pass

    return get_plan_bundle()


def toggle_day_item(day_plan_id: str, knowledge_id: str, done: bool | None = None) -> dict[str, Any]:
    conn = get_conn()
    ensure_plan_tables(conn)
    row = conn.execute(
        "SELECT id, done FROM day_plan_items WHERE day_plan_id = ? AND knowledge_id = ?",
        (day_plan_id, knowledge_id),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError("日计划条目不存在")
    new_done = (1 - row["done"]) if done is None else (1 if done else 0)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE day_plan_items SET done = ?, done_at = ? WHERE id = ?",
        (new_done, now if new_done else None, row["id"]),
    )
    # 若有条目完成，日计划进入进行中
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    if day and day["status"] == "pending" and new_done:
        conn.execute(
            "UPDATE day_plans SET status = 'in_progress', updated_at = ? WHERE id = ?",
            (now, day_plan_id),
        )
    conn.commit()
    _export_day_json(conn, day_plan_id)
    conn.close()
    return get_plan_bundle()


def update_day_plan_fields(
    day_plan_id: str,
    title: str | None = None,
    focus_text: str | None = None,
    review_text: str | None = None,
) -> dict[str, Any]:
    """更新日计划文本框字段，并回写周计划 Markdown。"""
    conn = get_conn()
    ensure_plan_tables(conn)
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    if not day:
        conn.close()
        raise ValueError("日计划不存在")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE day_plans
        SET title = COALESCE(?, title),
            focus_text = COALESCE(?, focus_text),
            review_text = COALESCE(?, review_text),
            updated_at = ?
        WHERE id = ?
        """,
        (title, focus_text, review_text, now, day_plan_id),
    )
    conn.commit()
    _export_day_json(conn, day_plan_id)
    _rewrite_week_markdown_from_days(conn)
    conn.commit()
    conn.close()
    return get_plan_bundle()


def complete_day_plan(day_plan_id: str) -> dict[str, Any]:
    """完成日计划（须该日练习全部真正完成），返回需填写的问卷。"""
    conn = get_conn()
    ensure_plan_tables(conn)
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    if not day:
        conn.close()
        raise ValueError("日计划不存在")

    item_n = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
        (day_plan_id,),
    ).fetchone()["c"]
    try:
        drill_rows = conn.execute(
            "SELECT status FROM day_drills WHERE day_plan_id = ?",
            (day_plan_id,),
        ).fetchall()
    except Exception:
        drill_rows = []
    if item_n <= 0:
        conn.close()
        raise ValueError("今日没有知识点，无法完成")
    if len(drill_rows) < item_n or any(r["status"] != "completed" for r in drill_rows):
        conn.close()
        raise ValueError("今日练习尚未全部完成，请先做完「练习」页中的待练题目")

    now = datetime.now().isoformat(timespec="seconds")
    # 不在此标记 completed：等问卷提交后再标记，避免「未做完却显示已完成」
    conn.execute(
        "UPDATE day_plans SET updated_at = ? WHERE id = ?",
        (now, day_plan_id),
    )
    conn.commit()
    conn.close()
    return {
        "day_plan_id": day_plan_id,
        "plan_date": day["plan_date"],
        "week_id": day["week_id"],
        "items_count": item_n,
        "survey": {
            "step": "volume",
            "question": "今日练习已全部完成。今天的内容量如何？",
            "options": [
                {"value": "too_much", "label": "过多"},
                {"value": "ok", "label": "适中"},
                {"value": "too_little", "label": "过少"},
            ],
        },
        "bundle": get_plan_bundle(),
    }


def submit_plan_survey(
    day_plan_id: str,
    volume: str,
    increase_yes: bool | None = None,
    rescale_week: bool | None = None,
) -> dict[str, Any]:
    """
    简化问卷：
    1) volume: too_much|ok|too_little —— 过多自动减、过少自动加，适中不改
    2) 若内容有变 → 询问是否整周按今日体量调整（再次提交时带 rescale_week）
    """
    if volume not in ("too_much", "ok", "too_little"):
        raise ValueError("volume 无效")

    conn = get_conn()
    ensure_plan_tables(conn)
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    if not day:
        conn.close()
        raise ValueError("日计划不存在")

    items_before = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
        (day_plan_id,),
    ).fetchone()["c"]
    now = datetime.now().isoformat(timespec="seconds")
    changed = False
    items_after = items_before
    want_increase = volume == "too_little"

    def _as_yes(v) -> bool:
        if v is True:
            return True
        if v is False or v is None:
            return False
        return str(v).lower() in ("yes", "true", "1")

    if volume == "ok":
        conn.execute(
            """
            UPDATE day_plans
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, day_plan_id),
        )
        _save_survey(conn, day, volume, None, None, items_before, items_before, now)
        conn.commit()
        _export_day_json(conn, day_plan_id)
        conn.close()
        return {
            "done": True,
            "message": "已记录：内容适中，日计划不做增减。",
            "bundle": get_plan_bundle(),
        }

    # 第一轮：自动调整体量；第二轮（已带 rescale_week）不再重复增减
    if rescale_week is None:
        if volume == "too_little":
            added = _increase_day_by_two_fifths(conn, day_plan_id)
            changed = added > 0
        else:
            removed = _decrease_day_by_two_fifths(conn, day_plan_id)
            changed = removed > 0

        items_after = conn.execute(
            "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
            (day_plan_id,),
        ).fetchone()["c"]
        _export_day_json(conn, day_plan_id)

        if changed:
            conn.commit()
            conn.close()
            try:
                from .day_drills import ensure_day_drills

                ensure_day_drills(day["plan_date"])
            except ValueError:
                pass
            return {
                "done": False,
                "step": "rescale_week",
                "question": "今日内容量已自动调整。是否按现在这份「今日体量」同步调整本周其余日子？",
                "options": [
                    {"value": "yes", "label": "是，整周一起调"},
                    {"value": "no", "label": "否，只改今天"},
                ],
                "volume": volume,
                "increase_yes": want_increase,
                "day_plan_id": day_plan_id,
                "items_before": items_before,
                "items_after": items_after,
            }

        # 未能实际增减：仍标记完成
        conn.execute(
            """
            UPDATE day_plans
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, day_plan_id),
        )
        _save_survey(conn, day, volume, 0, None, items_before, items_after, now)
        conn.commit()
        _export_day_json(conn, day_plan_id)
        conn.close()
        return {
            "done": True,
            "message": "已记录负荷反馈；今日知识点数量暂无可用调整空间。",
            "bundle": get_plan_bundle(),
        }

    # 第二轮：是否整周缩放
    changed = True
    want_rescale = _as_yes(rescale_week)
    if want_rescale:
        _rescale_week_to_day(conn, day_plan_id)

    items_after = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
        (day_plan_id,),
    ).fetchone()["c"]

    conn.execute(
        """
        UPDATE day_plans
        SET status = 'in_progress', completed_at = NULL, updated_at = ?
        WHERE id = ?
        """,
        (now, day_plan_id),
    )
    _save_survey(
        conn,
        day,
        volume,
        1 if want_increase else 0,
        1 if want_rescale else 0,
        items_before,
        items_after,
        now,
    )
    conn.commit()
    _rewrite_week_markdown_from_days(conn)
    conn.close()

    try:
        from .day_drills import ensure_day_drills

        ensure_day_drills(day["plan_date"])
    except ValueError:
        pass

    msg = "问卷已保存，今日内容已按你的反馈调整"
    if want_rescale:
        msg += "，并已同步本周其余日子"
    msg += "。请继续完成「练习」中的待练题目。"
    return {
        "done": True,
        "message": msg,
        "items_before": items_before,
        "items_after": items_after,
        "bundle": get_plan_bundle(),
    }


def _decrease_day_by_two_fifths(conn, day_plan_id: str) -> int:
    """减少当前日计划条目数的 2/5（向上取整，至少留 1 条）。优先移除未完成项。"""
    items = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, knowledge_id, sort_order, done
            FROM day_plan_items WHERE day_plan_id = ?
            ORDER BY done ASC, sort_order DESC
            """,
            (day_plan_id,),
        ).fetchall()
    ]
    n = len(items)
    if n <= 1:
        return 0
    remove_n = min(n - 1, max(1, int(math.ceil(n * 2 / 5))))
    to_remove = items[:remove_n]
    for it in to_remove:
        conn.execute("DELETE FROM day_plan_items WHERE id = ?", (it["id"],))
        conn.execute(
            """
            DELETE FROM day_drills
            WHERE day_plan_id = ? AND knowledge_id = ? AND status != 'completed'
            """,
            (day_plan_id, it["knowledge_id"]),
        )
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE day_plans SET status = 'in_progress', completed_at = NULL, updated_at = ? WHERE id = ?",
        (now, day_plan_id),
    )
    return len(to_remove)


def _save_survey(conn, day, volume, increase_yes, rescale_week, before, after, now) -> None:
    sid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO plan_surveys
        (id, day_plan_id, week_id, plan_date, volume, increase_yes, rescale_week,
         items_before, items_after, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            day["id"],
            day["week_id"],
            day["plan_date"],
            volume,
            increase_yes,
            rescale_week,
            before,
            after,
            now,
        ),
    )


def _increase_day_by_two_fifths(conn, day_plan_id: str) -> int:
    """增加当前日计划条目数的 2/5（向上取整，至少 1）。"""
    items = conn.execute(
        "SELECT knowledge_id, sort_order FROM day_plan_items WHERE day_plan_id = ? ORDER BY sort_order",
        (day_plan_id,),
    ).fetchall()
    n = len(items)
    add_n = max(1, int(math.ceil(n * 2 / 5))) if n else 2
    existing = {r["knowledge_id"] for r in items}
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()

    # 候选：同周后续日计划中的知识点，再补薄弱高权重
    candidates: list[str] = []
    for d in conn.execute(
        """
        SELECT id FROM day_plans
        WHERE week_id = ? AND plan_date > ?
        ORDER BY plan_date
        """,
        (day["week_id"], day["plan_date"]),
    ).fetchall():
        for r in conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? ORDER BY sort_order",
            (d["id"],),
        ).fetchall():
            if r["knowledge_id"] not in existing and r["knowledge_id"] not in candidates:
                candidates.append(r["knowledge_id"])

    if len(candidates) < add_n:
        for r in conn.execute(
            """
            SELECT m.knowledge_id
            FROM mastery_items m
            JOIN knowledge_nodes k ON k.id = m.knowledge_id
            WHERE m.level IN ('L0', 'L1') AND k.exam_weight = 'high'
              AND k.parent_id IS NOT NULL
            ORDER BY m.wrong_count DESC, k.id
            LIMIT 50
            """
        ).fetchall():
            if r["knowledge_id"] not in existing and r["knowledge_id"] not in candidates:
                candidates.append(r["knowledge_id"])

    to_add = candidates[:add_n]
    now = datetime.now().isoformat(timespec="seconds")
    base_order = max((r["sort_order"] for r in items), default=-1) + 1
    for i, kid in enumerate(to_add):
        conn.execute(
            """
            INSERT OR IGNORE INTO day_plan_items
            (day_plan_id, knowledge_id, role, sort_order, done, done_at)
            VALUES (?, ?, 'main', ?, 0, NULL)
            """,
            (day_plan_id, kid, base_order + i),
        )
    conn.execute(
        "UPDATE day_plans SET status = 'in_progress', completed_at = NULL, updated_at = ? WHERE id = ?",
        (now, day_plan_id),
    )
    return len(to_add)


def _rescale_week_to_day(conn, day_plan_id: str) -> None:
    """按选定日计划的条目数，调整本周其余未完成日的内容量。"""
    day = conn.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,)).fetchone()
    target_n = conn.execute(
        "SELECT COUNT(*) AS c FROM day_plan_items WHERE day_plan_id = ?",
        (day_plan_id,),
    ).fetchone()["c"]
    if target_n <= 0:
        return

    # 收集本周全部知识点池（保序）
    pool: list[str] = []
    seen = set()
    for d in conn.execute(
        "SELECT id FROM day_plans WHERE week_id = ? ORDER BY plan_date",
        (day["week_id"],),
    ).fetchall():
        for r in conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? ORDER BY sort_order",
            (d["id"],),
        ).fetchall():
            if r["knowledge_id"] not in seen:
                seen.add(r["knowledge_id"])
                pool.append(r["knowledge_id"])

    # 补池
    for r in conn.execute(
        """
        SELECT knowledge_id FROM mastery_items
        ORDER BY knowledge_id LIMIT 200
        """
    ).fetchall():
        if r["knowledge_id"] not in seen:
            seen.add(r["knowledge_id"])
            pool.append(r["knowledge_id"])

    now = datetime.now().isoformat(timespec="seconds")
    others = conn.execute(
        """
        SELECT * FROM day_plans
        WHERE week_id = ? AND id != ? AND status != 'completed'
        ORDER BY plan_date
        """,
        (day["week_id"], day_plan_id),
    ).fetchall()

    # 从池中切片分配，尽量不与今日完全相同集合
    cursor = 0
    today_ids = {
        r["knowledge_id"]
        for r in conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ?",
            (day_plan_id,),
        ).fetchall()
    }
    for od in others:
        chunk: list[str] = []
        guard = 0
        while len(chunk) < target_n and guard < len(pool) * 2:
            kid = pool[cursor % len(pool)]
            cursor += 1
            guard += 1
            if kid in chunk:
                continue
            # 允许与今日有重叠，但优先新点
            if kid in today_ids and len(chunk) < target_n - 1 and guard < len(pool):
                continue
            chunk.append(kid)
        # 若不够，放宽
        while len(chunk) < target_n and pool:
            for kid in pool:
                if kid not in chunk:
                    chunk.append(kid)
                if len(chunk) >= target_n:
                    break
            break

        # 保留已 done 的标记
        old_done = {
            r["knowledge_id"]
            for r in conn.execute(
                "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? AND done = 1",
                (od["id"],),
            ).fetchall()
        }
        conn.execute("DELETE FROM day_plan_items WHERE day_plan_id = ?", (od["id"],))
        for i, kid in enumerate(chunk[:target_n]):
            was = 1 if kid in old_done else 0
            conn.execute(
                """
                INSERT INTO day_plan_items
                (day_plan_id, knowledge_id, role, sort_order, done, done_at)
                VALUES (?, ?, 'main', ?, ?, ?)
                """,
                (od["id"], kid, i, was, now if was else None),
            )
        conn.execute(
            "UPDATE day_plans SET updated_at = ? WHERE id = ?",
            (now, od["id"]),
        )
        _export_day_json(conn, od["id"])


def _rewrite_week_markdown_from_days(conn) -> None:
    """根据 day_plans 回写周计划表格，保持目标说明区尽量不动。"""
    week = conn.execute("SELECT * FROM week_plan_meta WHERE id = 1").fetchone()
    plan = conn.execute("SELECT content_md FROM plans WHERE id = 1").fetchone()
    if not plan:
        return
    content = plan["content_md"] or ""
    days = conn.execute(
        "SELECT * FROM day_plans ORDER BY plan_date"
    ).fetchall()
    if not days:
        return

    lines = [
        "| 日期 | 主攻 | 知识点 | 复习/巩固 |",
        "|------|------|--------|-----------|",
    ]
    for d in days:
        kids = conn.execute(
            "SELECT knowledge_id FROM day_plan_items WHERE day_plan_id = ? ORDER BY sort_order",
            (d["id"],),
        ).fetchall()
        kid_str = " ".join(f"`{k['knowledge_id']}`" for k in kids)
        lines.append(
            f"| {d['title'] or d['plan_date']} | {d['focus_text'] or '—'} | {kid_str} | {d['review_text'] or '—'} |"
        )
    table = "\n".join(lines)

    if re.search(r"\|?\s*日期\s*\|", content):
        # 替换原表格块
        new_content = re.sub(
            r"(\|?\s*日期\s*\|[\s\S]*?)(?=\n## |\n# |\Z)",
            table + "\n\n",
            content,
            count=1,
        )
    else:
        new_content = content.rstrip() + "\n\n## 每日安排\n\n" + table + "\n"

    now = date.today().isoformat()
    conn.execute(
        "UPDATE plans SET content_md = ?, updated_at = ? WHERE id = 1",
        (new_content, now),
    )
    if week:
        conn.execute(
            "UPDATE week_plan_meta SET content_md = ?, updated_at = ? WHERE id = 1",
            (new_content, now),
        )
    path = PLANS_DIR / "current-week.md"
    path.write_text(new_content, encoding="utf-8")
