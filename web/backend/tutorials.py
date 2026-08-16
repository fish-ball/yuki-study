"""知识点教程：读写文件、同步 SQLite、缺失时用大模型生成。"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .db import ROOT, get_conn
from .llm_grade import load_llm_config, re_strip_fence

logger = logging.getLogger(__name__)

REQUIRED_HEADINGS = (
    "## 一句话记住",
    "## 核心概念",
    "## 例题演示",
    "## 易错点",
    "## 自测提示",
)


def tutorial_path(subject_id: str, knowledge_id: str) -> Path:
    return ROOT / "knowledge" / subject_id / "tutorials" / f"{knowledge_id}.md"


def parse_tutorial_markdown(content: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        m = re.match(r"^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$", content)
        if m:
            meta = yaml.safe_load(m.group(1)) or {}
            body = m.group(2)
    return {
        "meta": meta if isinstance(meta, dict) else {},
        "body_md": body.strip(),
        "content_md": content,
    }


def load_tutorial_file(subject_id: str, knowledge_id: str) -> dict[str, Any] | None:
    path = tutorial_path(subject_id, knowledge_id)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    parsed = parse_tutorial_markdown(raw)
    meta = parsed["meta"]
    related = meta.get("related_ids") or []
    if not isinstance(related, list):
        related = [related]
    return {
        "knowledge_id": knowledge_id,
        "subject_id": subject_id,
        "title": meta.get("title") or knowledge_id,
        "target_level": meta.get("target_level") or "L1",
        "version": int(meta.get("version") or 1),
        "source": meta.get("source") or "file",
        "updated_at": meta.get("updated_at") or date.today().isoformat(),
        "related_ids": [str(x) for x in related if str(x).strip()],
        "revision_note": meta.get("revision_note") or "",
        "content_md": parsed["content_md"],
        "body_md": parsed["body_md"],
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "has_tutorial": True,
    }


def write_tutorial_file(
    subject_id: str,
    knowledge_id: str,
    *,
    title: str,
    content_body: str,
    target_level: str = "L1",
    source: str = "agent",
    version: int = 1,
    related_ids: list[str] | None = None,
    revision_note: str = "",
) -> Path:
    path = tutorial_path(subject_id, knowledge_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content_body.strip()
    body = re.sub(r"^#\s+.*\n+", "", body, count=1).strip()
    related = [r for r in (related_ids or []) if r and r != knowledge_id]
    # 去重保序
    seen: set[str] = set()
    related_uniq: list[str] = []
    for r in related:
        if r not in seen:
            seen.add(r)
            related_uniq.append(r)
    related_yaml = "[]"
    if related_uniq:
        related_yaml = "\n" + "\n".join(f"  - {r}" for r in related_uniq)
    note = (revision_note or "").replace("\n", " ").strip()
    fm = (
        "---\n"
        f"knowledge_id: {knowledge_id}\n"
        f"subject: {subject_id}\n"
        f"title: {title}\n"
        f"target_level: {target_level}\n"
        f"version: {version}\n"
        f"source: {source}\n"
        f"updated_at: {date.today().isoformat()}\n"
        f"related_ids: {related_yaml}\n"
        f"revision_note: \"{note}\"\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
    path.write_text(fm, encoding="utf-8")
    return path


def upsert_tutorial_db(conn, data: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO tutorials
        (knowledge_id, subject_id, title, content_md, target_level, source, version, updated_at, path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(knowledge_id) DO UPDATE SET
          subject_id=excluded.subject_id,
          title=excluded.title,
          content_md=excluded.content_md,
          target_level=excluded.target_level,
          source=excluded.source,
          version=excluded.version,
          updated_at=excluded.updated_at,
          path=excluded.path
        """,
        (
            data["knowledge_id"],
            data["subject_id"],
            data["title"],
            data["content_md"],
            data.get("target_level") or "L1",
            data.get("source") or "file",
            int(data.get("version") or 1),
            data.get("updated_at") or date.today().isoformat(),
            data.get("path") or "",
        ),
    )


def get_tutorial(knowledge_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tutorials WHERE knowledge_id = ?", (knowledge_id,)
    ).fetchone()
    node = conn.execute(
        "SELECT id, name, subject_id, exam_weight, parent_id FROM knowledge_nodes WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    if not node:
        conn.close()
        return None

    # 文件优先刷新
    file_data = load_tutorial_file(node["subject_id"], knowledge_id)
    if file_data:
        upsert_tutorial_db(conn, file_data)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tutorials WHERE knowledge_id = ?", (knowledge_id,)
        ).fetchone()

    mastery = conn.execute(
        "SELECT level, wrong_count FROM mastery_items WHERE knowledge_id = ?",
        (knowledge_id,),
    ).fetchone()
    conn.close()

    if not row and not file_data:
        return {
            "knowledge_id": knowledge_id,
            "subject_id": node["subject_id"],
            "name": node["name"],
            "exam_weight": node["exam_weight"],
            "parent_id": node["parent_id"],
            "has_tutorial": False,
            "level": mastery["level"] if mastery else "L0",
            "wrong_count": mastery["wrong_count"] if mastery else 0,
        }

    data = dict(row) if row else file_data
    data["name"] = node["name"]
    data["exam_weight"] = node["exam_weight"]
    data["parent_id"] = node["parent_id"]
    data["has_tutorial"] = True
    data["level"] = mastery["level"] if mastery else "L0"
    data["wrong_count"] = mastery["wrong_count"] if mastery else 0
    if file_data:
        data["related_ids"] = file_data.get("related_ids") or []
        data["revision_note"] = file_data.get("revision_note") or ""
        data["body_md"] = file_data.get("body_md") or ""
    else:
        data["related_ids"] = data.get("related_ids") or []
        data["revision_note"] = data.get("revision_note") or ""
    return data


def list_tutorials(subject_id: str | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    if subject_id:
        rows = conn.execute(
            """
            SELECT t.knowledge_id, t.subject_id, t.title, t.target_level, t.source,
                   t.updated_at, t.path, k.name, k.exam_weight
            FROM tutorials t
            JOIN knowledge_nodes k ON k.id = t.knowledge_id
            WHERE t.subject_id = ?
            ORDER BY t.knowledge_id
            """,
            (subject_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT t.knowledge_id, t.subject_id, t.title, t.target_level, t.source,
                   t.updated_at, t.path, k.name, k.exam_weight
            FROM tutorials t
            JOIN knowledge_nodes k ON k.id = t.knowledge_id
            ORDER BY t.subject_id, t.knowledge_id
            """
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ensure_tutorial(
    knowledge_id: str,
    *,
    force: bool = False,
    target_level: str = "L1",
) -> dict[str, Any]:
    """若教程不存在则生成；force=True 时用 LLM 覆盖重生（保留 version+1）。"""
    conn = get_conn()
    node = conn.execute(
        "SELECT id, name, subject_id, exam_weight, parent_id FROM knowledge_nodes WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    if not node:
        conn.close()
        raise ValueError(f"知识点不存在: {knowledge_id}")

    existing = load_tutorial_file(node["subject_id"], knowledge_id)
    if existing and not force:
        upsert_tutorial_db(conn, existing)
        conn.commit()
        conn.close()
        data = get_tutorial(knowledge_id)
        assert data is not None
        data["generated"] = False
        return data

    prereq_rows = conn.execute(
        "SELECT prerequisites_json FROM knowledge_nodes WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    prereqs: list[str] = []
    if prereq_rows:
        try:
            prereqs = json.loads(prereq_rows["prerequisites_json"] or "[]")
        except json.JSONDecodeError:
            prereqs = []

    parent_name = None
    if node["parent_id"]:
        p = conn.execute(
            "SELECT name FROM knowledge_nodes WHERE id = ?", (node["parent_id"],)
        ).fetchone()
        parent_name = p["name"] if p else None
    conn.close()

    body = _generate_tutorial_body(
        knowledge_id=knowledge_id,
        name=node["name"],
        subject_id=node["subject_id"],
        exam_weight=node["exam_weight"],
        prerequisites=prereqs,
        parent_name=parent_name,
        target_level=target_level,
    )
    version = int((existing or {}).get("version") or 0) + 1 if force else 1
    related0 = list(prereqs)
    write_tutorial_file(
        node["subject_id"],
        knowledge_id,
        title=node["name"],
        content_body=body,
        target_level=target_level,
        source="llm",
        version=version,
        related_ids=related0,
        revision_note="初次生成" if not existing else "强制重生",
    )
    # 新建后尝试把关联写回已有教程（无 LLM 时用规则拼接）
    _ensure_related_section_local(node["subject_id"], knowledge_id, related0)
    for rid in related0:
        other = load_tutorial_file(node["subject_id"], rid)
        if other:
            ids = list(other.get("related_ids") or [])
            if knowledge_id not in ids:
                ids.append(knowledge_id)
            _ensure_related_section_local(node["subject_id"], rid, ids, peer_id=knowledge_id)

    data = get_tutorial(knowledge_id)
    assert data is not None
    data["generated"] = True
    return data


def collect_related_ids(knowledge_id: str, extra: list[str] | None = None) -> list[str]:
    """先修 + 同父兄弟中已有教程的点 + 额外指定。"""
    conn = get_conn()
    node = conn.execute(
        "SELECT subject_id, parent_id, prerequisites_json FROM knowledge_nodes WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    if not node:
        conn.close()
        return list(extra or [])
    related: list[str] = []
    try:
        related.extend(json.loads(node["prerequisites_json"] or "[]"))
    except json.JSONDecodeError:
        pass
    if node["parent_id"]:
        siblings = conn.execute(
            """
            SELECT k.id FROM knowledge_nodes k
            JOIN tutorials t ON t.knowledge_id = k.id
            WHERE k.parent_id = ? AND k.id != ?
            """,
            (node["parent_id"], knowledge_id),
        ).fetchall()
        related.extend(r["id"] for r in siblings)
    conn.close()
    related.extend(extra or [])
    seen: set[str] = set()
    out: list[str] = []
    for r in related:
        if r and r != knowledge_id and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def revise_tutorial(
    knowledge_id: str,
    *,
    mode: str = "patch",
    notes: str = "",
    related_ids: list[str] | None = None,
    student_mistake: str | None = None,
    target_level: str | None = None,
) -> dict[str, Any]:
    """
    按需修订教程。
    mode:
      - patch: 补充例子/易错/说明
      - correct: 纠正错误表述
      - integrate: 整合关联知识点
    """
    if mode not in ("patch", "correct", "integrate"):
        raise ValueError("mode 须为 patch / correct / integrate")

    # 先确保存在
    ensure_tutorial(knowledge_id, force=False)

    conn = get_conn()
    node = conn.execute(
        "SELECT id, name, subject_id FROM knowledge_nodes WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    conn.close()
    if not node:
        raise ValueError(f"知识点不存在: {knowledge_id}")

    existing = load_tutorial_file(node["subject_id"], knowledge_id)
    if not existing:
        raise ValueError("教程仍不存在")

    merged_related = collect_related_ids(knowledge_id, related_ids)
    for r in existing.get("related_ids") or []:
        if r not in merged_related:
            merged_related.append(r)

    related_briefs = _load_related_briefs(node["subject_id"], merged_related)

    body = _revise_tutorial_body(
        mode=mode,
        knowledge_id=knowledge_id,
        name=node["name"],
        existing_body=existing.get("body_md") or "",
        notes=notes,
        student_mistake=student_mistake or "",
        related_briefs=related_briefs,
    )

    note_bits = [mode]
    if notes:
        note_bits.append(notes[:80])
    if student_mistake:
        note_bits.append("订正学生错因")
    revision_note = "；".join(note_bits)

    write_tutorial_file(
        node["subject_id"],
        knowledge_id,
        title=existing.get("title") or node["name"],
        content_body=body,
        target_level=target_level or existing.get("target_level") or "L1",
        source="llm" if load_llm_config() else "agent",
        version=int(existing.get("version") or 1) + 1,
        related_ids=merged_related,
        revision_note=revision_note,
    )

    # integrate：双向写入关联
    if mode == "integrate" or merged_related:
        for rid in merged_related:
            other = load_tutorial_file(node["subject_id"], rid)
            if not other:
                continue
            oids = list(other.get("related_ids") or [])
            if knowledge_id not in oids:
                oids.append(knowledge_id)
                _ensure_related_section_local(
                    node["subject_id"], rid, oids, peer_id=knowledge_id
                )

    data = get_tutorial(knowledge_id)
    assert data is not None
    data["revised"] = True
    data["revise_mode"] = mode
    return data


def _load_related_briefs(subject_id: str, related_ids: list[str]) -> list[dict[str, str]]:
    briefs = []
    conn = get_conn()
    for rid in related_ids:
        name_row = conn.execute(
            "SELECT name FROM knowledge_nodes WHERE id = ?", (rid,)
        ).fetchone()
        tut = load_tutorial_file(subject_id, rid)
        one_liner = ""
        if tut and tut.get("body_md"):
            m = re.search(r"##\s*一句话记住\s*\n+([^\n#]+)", tut["body_md"])
            if m:
                one_liner = m.group(1).strip()
        briefs.append(
            {
                "knowledge_id": rid,
                "name": name_row["name"] if name_row else rid,
                "one_liner": one_liner,
            }
        )
    conn.close()
    return briefs


def _ensure_related_section_local(
    subject_id: str,
    knowledge_id: str,
    related_ids: list[str],
    peer_id: str | None = None,
) -> None:
    """无 LLM 时：更新 related_ids，并规则拼接「关联知识点」小节。"""
    existing = load_tutorial_file(subject_id, knowledge_id)
    if not existing:
        return
    body = existing.get("body_md") or ""
    # 去掉一级标题
    body = re.sub(r"^#\s+.*\n+", "", body.strip(), count=1).strip()
    briefs = _load_related_briefs(subject_id, related_ids)
    lines = ["## 关联知识点", ""]
    for b in briefs:
        tip = b["one_liner"] or "学习本点时可对照复习。"
        lines.append(f"- `{b['knowledge_id']}`（{b['name']}）：{tip}")
    if peer_id:
        # 强调刚并入的点
        pass
    section = "\n".join(lines) + "\n"
    if re.search(r"##\s*关联知识点\s*\n", body):
        body = re.sub(
            r"##\s*关联知识点\s*\n[\s\S]*?(?=\n##\s|\Z)",
            section + "\n",
            body,
            count=1,
        )
    else:
        # 插在自测提示前，否则文末
        if re.search(r"##\s*自测提示", body):
            body = re.sub(r"(##\s*自测提示)", section + r"\n\1", body, count=1)
        else:
            body = body.rstrip() + "\n\n" + section

    write_tutorial_file(
        subject_id,
        knowledge_id,
        title=existing.get("title") or knowledge_id,
        content_body=body,
        target_level=existing.get("target_level") or "L1",
        source=existing.get("source") or "agent",
        version=int(existing.get("version") or 1) + 1,
        related_ids=related_ids,
        revision_note=existing.get("revision_note") or "整合关联知识点",
    )


def _revise_tutorial_body(
    *,
    mode: str,
    knowledge_id: str,
    name: str,
    existing_body: str,
    notes: str,
    student_mistake: str,
    related_briefs: list[dict[str, str]],
) -> str:
    cfg = load_llm_config()
    if not cfg:
        return _revise_body_without_llm(
            existing_body, notes, student_mistake, related_briefs, mode
        )

    system = (
        "你是广东佛山顺德初三辅导老师，正在修订已有知识点教程。"
        "必须保留原文中正确的内容，在原基础上补充或纠正，禁止无故删光重写。"
        "只输出完整 Markdown 正文（不要 YAML，不要代码围栏）。"
        "必须仍包含这些二级标题："
        + "、".join(REQUIRED_HEADINGS)
        + "。建议包含 ## 关联知识点。可保留 ## 自测参考。"
    )
    user = json.dumps(
        {
            "mode": mode,
            "knowledge_id": knowledge_id,
            "name": name,
            "revision_notes": notes,
            "student_mistake": student_mistake,
            "related_briefs": related_briefs,
            "existing_body": existing_body,
            "instructions": {
                "patch": "补充例子、说明或易错点",
                "correct": "纠正错误或过时表述，可在易错点注明曾易混成什么",
                "integrate": "把关联知识点整合进正文，写清对照/先后/易混",
            }.get(mode, "修订"),
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"请修订下列教程并输出完整正文：\n{user}"},
    ]
    try:
        text = _chat_text(cfg, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
        logger.warning("教程修订 LLM 失败，使用本地规则修订: %s", e)
        return _revise_body_without_llm(
            existing_body, notes, student_mistake, related_briefs, mode
        )
    text = text.strip()
    if text.startswith("```"):
        text = re_strip_fence(text)
    return text


def _revise_body_without_llm(
    existing_body: str,
    notes: str,
    student_mistake: str,
    related_briefs: list[dict[str, str]],
    mode: str,
) -> str:
    body = re.sub(r"^#\s+.*\n+", "", existing_body.strip(), count=1).strip()
    extras: list[str] = []
    if student_mistake:
        extras.append(f"- 易错（练习暴露）：{student_mistake.strip()} → 对照本课定义订正。")
    if notes and mode in ("patch", "correct"):
        extras.append(f"- 补充：{notes.strip()}")
    if extras:
        block = "\n".join(extras) + "\n"
        if re.search(r"##\s*易错点\s*\n", body):
            body = re.sub(
                r"(##\s*易错点\s*\n)",
                r"\1\n" + block,
                body,
                count=1,
            )
        else:
            body += "\n\n## 易错点\n\n" + block
    if related_briefs:
        lines = ["## 关联知识点", ""]
        for b in related_briefs:
            tip = b.get("one_liner") or "对照复习。"
            lines.append(f"- `{b['knowledge_id']}`（{b['name']}）：{tip}")
        section = "\n".join(lines) + "\n"
        if re.search(r"##\s*关联知识点\s*\n", body):
            body = re.sub(
                r"##\s*关联知识点\s*\n[\s\S]*?(?=\n##\s|\Z)",
                section + "\n",
                body,
                count=1,
            )
        elif re.search(r"##\s*自测提示", body):
            body = re.sub(r"(##\s*自测提示)", section + r"\n\1", body, count=1)
        else:
            body += "\n\n" + section
    return body


def _generate_tutorial_body(
    *,
    knowledge_id: str,
    name: str,
    subject_id: str,
    exam_weight: str,
    prerequisites: list[str],
    parent_name: str | None,
    target_level: str,
) -> str:
    cfg = load_llm_config()
    if not cfg:
        return _fallback_stub_body(name, knowledge_id)

    system = (
        "你是广东佛山顺德初三中考辅导老师，正在写知识点教程讲义。"
        "只输出 Markdown 正文（不要 YAML front matter，不要外包代码块）。"
        "必须包含且仅用这些二级标题："
        + "、".join(REQUIRED_HEADINGS)
        + "。另可在末尾加 ## 自测参考（含简要答案）。"
        "面向初二升初三暑假学生，语言简洁，可用 $...$ 写公式。"
    )
    user = json.dumps(
        {
            "knowledge_id": knowledge_id,
            "name": name,
            "subject_id": subject_id,
            "exam_weight": exam_weight,
            "parent_name": parent_name,
            "prerequisites": prerequisites,
            "target_level": target_level,
            "region": "广东佛山顺德",
            "exam_year": 2027,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"请为下列知识点生成完整教程正文：\n{user}"},
    ]
    try:
        text = _chat_text(cfg, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        logger.exception("教程 LLM 生成失败，使用占位稿")
        return _fallback_stub_body(name, knowledge_id)

    text = text.strip()
    if text.startswith("```"):
        text = re_strip_fence(text)
    # 若模型漏标题，仍返回，前端可学；Agent 可再润色
    return text


def _fallback_stub_body(name: str, knowledge_id: str) -> str:
    return (
        f"## 一句话记住\n\n先抓住「{name}」的定义与判别标准。\n\n"
        f"## 核心概念\n\n- 本知识点 id：`{knowledge_id}`\n"
        "- （占位）请配置大模型后重新生成完整教程，或由老师补充。\n\n"
        "## 例题演示\n\n**例 1** 待补充。\n\n解：待补充。\n\n"
        "## 易错点\n\n- 易错：概念混淆 → 正确：对照定义逐条核对。\n\n"
        "## 自测提示\n\n1. 用自己的话复述本点定义。\n2. 举一个正例和一个反例。\n"
    )


def _chat_text(cfg: dict[str, Any], messages: list[dict[str, str]]) -> str:
    url = cfg["base_url"]
    endpoint = url if url.endswith("/chat/completions") else f"{url}/chat/completions"
    body = {
        "model": cfg["model"],
        "temperature": cfg.get("temperature", 0.2),
        "messages": messages,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.get("timeout_sec", 90)) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise ValueError("空响应")
    return content


def sync_tutorials_from_files(conn) -> int:
    """扫描 knowledge/*/tutorials/*.md 写入 SQLite。"""
    count = 0
    base = ROOT / "knowledge"
    if not base.exists():
        return 0
    for subject_dir in base.iterdir():
        if not subject_dir.is_dir():
            continue
        tut_dir = subject_dir / "tutorials"
        if not tut_dir.exists():
            continue
        subject_id = subject_dir.name
        for md in tut_dir.glob("*.md"):
            kid = md.stem
            data = load_tutorial_file(subject_id, kid)
            if not data:
                continue
            # 仅当知识点存在时入库
            exists = conn.execute(
                "SELECT 1 FROM knowledge_nodes WHERE id = ?", (kid,)
            ).fetchone()
            if not exists:
                continue
            upsert_tutorial_db(conn, data)
            count += 1
    return count
