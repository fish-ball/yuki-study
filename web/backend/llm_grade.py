"""大模型主观题判分（OpenAI 兼容接口）。"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "config" / "llm.local.json",
    Path(__file__).resolve().parents[2] / "profile" / "llm.local.json",
]


def load_llm_config() -> dict[str, Any] | None:
    """读取本地凭据。优先环境变量，其次 llm.local.json。"""
    api_key = (os.environ.get("STUDY_LLM_API_KEY") or "").strip()
    base_url = (os.environ.get("STUDY_LLM_BASE_URL") or "").strip()
    model = (os.environ.get("STUDY_LLM_MODEL") or "").strip()

    file_cfg: dict[str, Any] = {}
    for path in _CONFIG_CANDIDATES:
        if path.exists():
            try:
                file_cfg = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.exception("读取 LLM 配置失败: %s", path)
                file_cfg = {}
            break

    api_key = api_key or str(file_cfg.get("api_key") or "").strip()
    base_url = base_url or str(file_cfg.get("base_url") or "").strip()
    model = model or str(file_cfg.get("model") or "").strip()
    if not api_key or not base_url or not model:
        return None

    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout_sec": int(file_cfg.get("timeout_sec") or os.environ.get("STUDY_LLM_TIMEOUT") or 60),
        "temperature": float(file_cfg.get("temperature") or 0.1),
    }


def llm_configured() -> bool:
    return load_llm_config() is not None


def grade_short_with_llm(
    *,
    stem: str,
    explanation: str,
    answer_key: str,
    user_answer: str,
    subject_id: str | None = None,
    knowledge_ids: list[str] | None = None,
) -> tuple[bool | None, str]:
    """
    用大模型批改简答题。
    返回 (是否正确, 反馈)。配置缺失或调用失败时返回 (None, 说明)。
    """
    cfg = load_llm_config()
    if not cfg:
        return None, "尚未配置大模型凭据，简答题暂无法自动批改。请在 web/config/llm.local.json 填写 api_key / base_url / model。"

    system = (
        "你是广东佛山顺德初三化学/数学等中考辅导老师，负责批改简答题。"
        "只根据评分要点判断学生作答是否达到要求，允许表述不同但要点正确。"
        "必须只输出一个 JSON 对象，不要 Markdown，不要其它文字。"
        '格式：{"correct": true或false, "feedback": "给学生的中文反馈（1-3句）", "score_ratio": 0到1的小数}'
    )
    user_payload = {
        "subject_id": subject_id,
        "knowledge_ids": knowledge_ids or [],
        "stem": stem,
        "rubric_or_explanation": explanation or answer_key,
        "reference_answer": answer_key,
        "student_answer": user_answer,
    }
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请批改下列作答，并严格输出 JSON：\n"
            + json.dumps(user_payload, ensure_ascii=False),
        },
    ]

    try:
        raw = _chat_completion(cfg, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        logger.exception("LLM 判题请求失败")
        return None, f"大模型批改失败：{e}"
    except ValueError as e:
        logger.exception("LLM 判题响应解析失败")
        return None, f"大模型返回无法解析：{e}"

    correct = bool(raw.get("correct"))
    ratio = raw.get("score_ratio")
    try:
        if ratio is not None and float(ratio) >= 0.8:
            correct = True
        elif ratio is not None and float(ratio) < 0.5:
            correct = False
    except (TypeError, ValueError):
        pass

    feedback = str(raw.get("feedback") or "").strip()
    if not feedback:
        feedback = "回答正确。" if correct else "要点不完整，请对照解析订正。"
    if correct:
        feedback = feedback if "正确" in feedback or "可以" in feedback else f"要点达标。{feedback}"
    return correct, feedback


def needs_detailed_explanation(explanation: str | None, answer_key: str | None) -> bool:
    """答案只有字母/对错或一两句时，需要补详细解析。"""
    exp = (explanation or "").strip()
    key = (answer_key or "").strip()
    if not exp:
        return True
    body = re.sub(r"^[A-D对错][.。、．]?\s*", "", exp).strip()
    if not body or body == key:
        return True
    if "【考点】" in exp and "【思路】" in exp and len(exp) >= 40:
        return False
    if "解析" in exp and len(body) >= 48:
        return False
    return len(body) < 48


def explain_question(
    *,
    stem: str,
    qtype: str,
    answer_key: str,
    options: list[dict[str, str]] | None = None,
    existing_explanation: str = "",
    subject_id: str | None = None,
    knowledge_ids: list[str] | None = None,
) -> str | None:
    """为参考答案生成详细解析（考点/思路/易错）。配置缺失或调用失败时返回 None。"""
    cfg = load_llm_config()
    if not cfg:
        return None

    system = (
        "你是广东佛山顺德初三中考辅导老师，负责给参考答案配详细解析。"
        "必须只输出一个 JSON 对象，不要 Markdown，不要其它文字。"
        '格式：{"explanation": "中文解析正文"}。'
        "explanation 必须含三部分，用换行分隔："
        "【考点】……"
        "【思路】……（写出关键步骤或公式；选择题说明为何该项对，其余项错在哪）"
        "【易错】……"
        "面向初三学生，简体中文，不要 emoji，不要只重复答案字母或「对/错」。"
        "解析必须与给定参考答案一致。"
    )
    user_payload = {
        "subject_id": subject_id,
        "knowledge_ids": knowledge_ids or [],
        "qtype": qtype,
        "stem": stem,
        "options": options or [],
        "reference_answer": answer_key,
        "existing_hint": existing_explanation or "",
    }
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请为下列题目的参考答案写详细解析，并严格输出 JSON：\n"
            + json.dumps(user_payload, ensure_ascii=False),
        },
    ]
    try:
        raw = _chat_completion(cfg, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        logger.exception("LLM 生成解析失败")
        return None

    text = str(raw.get("explanation") or "").strip()
    return text or None


def generate_unknown_focus_lesson(
    *,
    knowledge_name: str,
    knowledge_id: str,
    questions: list[dict[str, Any]],
    existing_lesson: str = "",
) -> str | None:
    """针对不会/做错的原题写更细的专学讲解（题型、考法、变式）。"""
    cfg = load_llm_config()
    if not cfg or not questions:
        return None
    system = (
        "你是广东佛山顺德初三中考辅导老师，正在写「不会专学」针对性讲解。"
        "必须只输出一个 JSON 对象，不要 Markdown 围栏，不要其它文字。"
        '格式：{"lesson_md": "完整中文 Markdown"}。'
        "lesson_md 必须比普通教程更细，紧扣学生不会的那几道题："
        "包含二级标题：## 你卡住的题、## 这题在考什么、## 一步一步怎么做、## 同类题还会怎么变、## 对照易错。"
        "要讲清题型（判断/选择/填空）、其它常见考法与变式，步骤写全，面向初三，不要 emoji。"
        "不要只复述参考答案字母。"
    )
    payload = {
        "knowledge_id": knowledge_id,
        "knowledge_name": knowledge_name,
        "missed_questions": questions,
        "existing_lesson": existing_lesson or "",
    }
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请根据下列错题写针对性专学讲解，并严格输出 JSON：\n"
            + json.dumps(payload, ensure_ascii=False),
        },
    ]
    try:
        raw = _chat_completion(cfg, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        logger.exception("LLM 生成不会专学讲解失败")
        return None
    text = str(raw.get("lesson_md") or raw.get("explanation") or "").strip()
    return text or None


def _chat_completion(cfg: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    url = cfg["base_url"]
    if url.endswith("/chat/completions"):
        endpoint = url
    else:
        endpoint = f"{url}/chat/completions"

    body = {
        "model": cfg["model"],
        "temperature": cfg["temperature"],
        "messages": messages,
        "response_format": {"type": "json_object"},
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
    with urllib.request.urlopen(req, timeout=cfg["timeout_sec"]) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    content = (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if not content:
        raise ValueError("空响应")
    content = content.strip()
    # 容错：偶发 ```json 包裹
    if content.startswith("```"):
        content = re_strip_fence(content)
    return json.loads(content)


def re_strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
