"""从考核 Markdown 解析结构化题目。"""
from __future__ import annotations

import re
import math
from typing import Any


def parse_assessment_markdown(paper_id: str, content: str, meta: dict | None = None) -> list[dict[str, Any]]:
    """解析卷面 Markdown，返回题目列表（含选项与答案）。"""
    meta = meta or {}
    subject_id = meta.get("subject")

    # 切分参考答案
    parts = re.split(r"\n##\s*参考答案\s*\n", content, maxsplit=1)
    body = parts[0]
    answer_section = parts[1] if len(parts) > 1 else ""

    # 去掉 front matter 与一级标题
    body = re.sub(r"^---[\s\S]*?---\s*", "", body, count=1)
    body = re.sub(r"^#\s+.*\n+", "", body, count=1)

    answers = _parse_answers(answer_section)
    questions: list[dict[str, Any]] = []

    # 按题号切分：行首数字.
    chunks = re.split(r"\n(?=\d+\.\s)", "\n" + body)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(\d+)\.\s+([\s\S]+)$", chunk)
        if not m:
            continue
        num = int(m.group(1))
        rest = m.group(2).strip()

        knowledge_ids = _extract_knowledge_ids(rest)
        # 去掉题干开头的 （knowledge...）
        stem_raw = re.sub(r"^（[^）]+）\s*", "", rest)
        stem_raw = re.sub(r"^\([^)]+\)\s*", "", stem_raw)

        options = _extract_options(stem_raw)
        stem = _strip_options(stem_raw).strip()
        # 去掉分数标注行首（分数已单独提取）
        score_m = re.search(r"^（(\d+)\s*分）\s*", stem)
        if not score_m:
            score_m = re.search(r"^\((\d+)\s*分\)\s*", stem)
        explicit_score = float(score_m.group(1)) if score_m else None
        stem = re.sub(r"^（\d+\s*分）\s*", "", stem)
        stem = re.sub(r"^\(\d+\s*分\)\s*", "", stem)
        # 练习架构：去掉误吸入题干的章节标题与分隔线
        stem = _clean_stem(stem)

        qtype = _infer_qtype(stem, options, answers.get(num))
        ans = answers.get(num) or {}
        answer_key = _strip_answer_meta(str(ans.get("key", "") or ""))
        explanation = _strip_answer_meta(str(ans.get("explanation", "") or ""))
        accept = [
            _strip_answer_meta(str(x))
            for x in (ans.get("accept") or ([] if not answer_key else [answer_key]))
            if str(x).strip()
        ]
        if not accept and answer_key:
            accept = [answer_key]

        auto = 1
        if qtype == "short":
            auto = 0
        if qtype == "fill" and len(accept) == 0 and not answer_key:
            auto = 0

        score = explicit_score if explicit_score is not None else _default_score(qtype)

        qid = f"{paper_id}__q{num:02d}"
        questions.append(
            {
                "id": qid,
                "paper_id": paper_id,
                "subject_id": subject_id,
                "qtype": qtype,
                "stem": stem,
                "score": score,
                "sort_order": num,
                "explanation": explanation,
                "answer_key": answer_key if isinstance(answer_key, str) else str(answer_key),
                "answer_accept": accept,
                "auto_gradable": auto,
                "options": options,
                "knowledge_ids": knowledge_ids,
            }
        )
    return questions


def _extract_knowledge_ids(text: str) -> list[str]:
    ids: list[str] = []
    for m in re.finditer(r"（([^）]+)）", text):
        blob = m.group(1)
        # 跳过「10 分」之类
        if re.fullmatch(r"\d+\s*分", blob.strip()):
            continue
        for part in re.split(r"[/\s,，、]+", blob):
            part = part.strip()
            if re.match(r"^[a-z]+\.[a-z0-9_.]+$", part):
                ids.append(part)
        if ids:
            break
    return ids


def _extract_options(text: str) -> list[dict[str, str]]:
    opts = []
    for m in re.finditer(r"(?m)^\s*-\s*([A-D])\.\s*(.+?)\s*$", text):
        opts.append({"label": m.group(1), "content": m.group(2).strip()})
    return opts


def _strip_options(text: str) -> str:
    return re.sub(r"(?m)^\s*-\s*[A-D]\.\s*.+\s*$", "", text).strip()


def _clean_stem(stem: str) -> str:
    """去掉题干末尾误吸入的 Markdown 小节标题与分隔线。"""
    stem = re.sub(r"\n+##\s+[^\n]+", "", stem)
    stem = re.sub(r"\n+---+\s*$", "", stem)
    stem = re.sub(r"\n{3,}", "\n\n", stem)
    return stem.strip()


def _strip_knowledge_tail(text: str) -> str:
    """去掉答案末尾的 （knowledge.id） 标注，避免进入可接受答案。"""
    return re.sub(r"（[a-z]+\.[a-z0-9_.]+）\s*$", "", text.strip()).strip()


_ANSWER_META_TAIL = re.compile(
    r"(?:（冲L[0-4][^）]*）|（L[0-4]）|【L[0-4]】|\(冲L[0-4][^)]*\)|\(L[0-4]\))\s*$",
    re.IGNORECASE,
)


def _strip_answer_meta(text: str) -> str:
    """去掉考核卷答案末尾的「（冲L3）」等标注，只保留真正可判的答案。"""
    s = _strip_knowledge_tail(text or "")
    prev = None
    while prev != s:
        prev = s
        s = _ANSWER_META_TAIL.sub("", s).strip()
        s = re.sub(r"[。、，,\s]+$", "", s)
    return s


def _infer_qtype(stem: str, options: list, ans: dict | None) -> str:
    explanation = str((ans or {}).get("explanation", "") or "")
    # 简答优先：参考答案以「评分要点/示例」开头
    if explanation.startswith("评分") or explanation.startswith("示例"):
        return "short"
    if options:
        return "choice"
    key = (ans or {}).get("key", "")
    if key in ("对", "错", "true", "false", "T", "F"):
        return "judge"
    # 仅当明确是判断题指令时，避免「说明判断依据」误伤
    if re.search(r"(对的写|判断题|对错判断)", stem[:40]):
        return "judge"
    if "______" in stem or "____" in stem or stem.lstrip().startswith("填"):
        return "fill"
    if key and len(str(key)) <= 40 and "评分" not in explanation:
        if options:
            return "choice"
        return "fill"
    return "short"


def _default_score(qtype: str) -> float:
    return {"choice": 4.0, "judge": 2.0, "fill": 4.0, "short": 8.0}.get(qtype, 2.0)


def _infer_score(stem_raw: str, qtype: str) -> float:
    """兼容旧调用：只在文本前部查找（N 分）。"""
    head = re.split(r"\n##\s+", stem_raw, maxsplit=1)[0]
    head = "\n".join(head.splitlines()[:4])
    m = re.search(r"（(\d+)\s*分）", head)
    if m:
        return float(m.group(1))
    return _default_score(qtype)


def _parse_answers(section: str) -> dict[int, dict[str, Any]]:
    """解析参考答案区。"""
    result: dict[int, dict[str, Any]] = {}
    if not section.strip():
        return result
    # 去掉提示行
    section = re.sub(r"^（[^）]*）\s*", "", section.strip())
    chunks = re.split(r"\n(?=\d+\.\s)", "\n" + section)
    for chunk in chunks:
        chunk = chunk.strip()
        m = re.match(r"^(\d+)\.\s+([\s\S]+)$", chunk)
        if not m:
            continue
        num = int(m.group(1))
        text = m.group(2).strip()
        key = ""
        accept: list[str] = []
        text_core = _strip_answer_meta(text)
        key_src = text_core
        explanation = text_core
        exp_m = re.search(r"(?:^|\n)\s*解析[:：]\s*", text_core)
        if exp_m:
            key_src = text_core[: exp_m.start()].strip()
            explanation = text_core[exp_m.end() :].strip()
        else:
            parts = re.split(r"解析[:：]\s*", text_core, maxsplit=1)
            if len(parts) == 2:
                key_src = parts[0].strip()
                explanation = parts[1].strip()

        # 选择题：开头字母（先去掉末尾「冲L3」标注再取字母）
        cm = re.match(r"^([A-D])[.。、．]?\s*(.*)$", key_src, re.S)
        if cm:
            key = cm.group(1)
            accept = [key]
        else:
            # 判断题
            jm = re.match(r"^(对|错)[.。]?\s*(.*)$", key_src, re.S)
            if jm:
                key = jm.group(1)
                accept = [key]
            else:
                # 填空：取第一句到句号/换行前；多空用分号拆（句中顿号不拆，避免「钠、镁」误切）
                first = re.split(r"[。\n]", key_src, maxsplit=1)[0].strip()
                first = _strip_answer_meta(first)
                # 去掉「评分要点」类整段作为 short
                if first.startswith("评分") or first.startswith("示例"):
                    key = ""
                    accept = []
                else:
                    # 多空：优先按中文/英文分号拆
                    if re.search(r"[；;]", first):
                        parts = re.split(r"[；;]", first)
                    else:
                        parts = [first]
                    accept = [_strip_answer_meta(p.strip()) for p in parts if p.strip()]
                    if not accept and first:
                        accept = [first]
                    key = "；".join(accept) if accept else first

        if not explanation:
            explanation = text_core
        result[num] = {"key": key, "accept": accept, "explanation": explanation}
    return result


def count_blanks(stem: str) -> int:
    """统计题干中的填空个数（连续下划线）。"""
    return len(re.findall(r"_{2,}", stem or ""))


def grade_answer(
    qtype: str,
    answer_key: str,
    answer_accept: list[str],
    user_answer: str,
    *,
    blank_count: int | None = None,
) -> tuple[bool | None, str]:
    """自动判分。返回 (是否正确, 反馈)。short 题返回 None 表示需 LLM/人工。"""
    ua = (user_answer or "").strip()
    if qtype == "short":
        return None, "简答题待大模型批改。"

    if qtype == "choice":
        key = _strip_answer_meta(answer_key or "")
        um = re.match(r"^\s*([A-D])\b", ua, re.I)
        km = re.match(r"^\s*([A-D])\b", key, re.I)
        if um and km:
            ok = um.group(1).upper() == km.group(1).upper()
        else:
            ok = ua.upper() == key.upper()
        show = km.group(1).upper() if km else key
        return ok, "回答正确。" if ok else f"不正确。正确答案是 {show}。"

    if qtype == "judge":
        norm = _norm_judge(ua)
        key = _norm_judge(_strip_answer_meta(answer_key))
        ok = norm == key and norm in ("对", "错")
        show = key if key in ("对", "错") else _strip_answer_meta(answer_key)
        return ok, "回答正确。" if ok else f"不正确。正确答案是 {show}。"

    if qtype == "fill":
        return _grade_fill(answer_key, answer_accept, ua, blank_count=blank_count)

    return None, "已记录作答。"


def _grade_fill(
    answer_key: str,
    answer_accept: list[str],
    user_answer: str,
    *,
    blank_count: int | None = None,
) -> tuple[bool, str]:
    accept_list = [a for a in (answer_accept or []) if str(a).strip()]
    if not accept_list and answer_key:
        # 答案键也可能是多空串
        accept_list = _split_fill_parts(answer_key, expected_n=0)

    expected_n = blank_count or len(accept_list) or 1
    user_parts = _split_fill_parts(user_answer, expected_n=expected_n)
    if not user_parts:
        return False, "请填写答案。"

    accept_norms = [_norm_fill(a) for a in accept_list]
    # 单空：用户整段与任一可接受答案比对；也允许可接受列表只有一项
    if expected_n <= 1 or len(accept_norms) <= 1:
        candidates = accept_norms or ([_norm_fill(answer_key)] if answer_key else [])
        ok = any(_fill_equals(user_parts[0] if len(user_parts) == 1 else _norm_fill(user_answer), c) for c in candidates)
        # 若用户误用多分隔但只有一空，合并后再比
        if not ok and len(user_parts) > 1:
            joined = _norm_fill("".join(user_parts))
            ok = any(_fill_equals(joined, c) for c in candidates)
        return ok, "回答正确。" if ok else "不正确，请对照解析再试。"

    # 多空：按序比对；空数不一致时尝试宽松匹配
    if len(user_parts) != len(accept_norms):
        # 若用户少写/多写，仍逐空尽力比对已填部分
        if len(user_parts) < len(accept_norms):
            return False, f"请填完所有空（共 {len(accept_norms)} 空）。"
        # 多出来的忽略尾部空内容过严；优先按前 N 空
        user_parts = user_parts[: len(accept_norms)]

    matched = [_fill_equals(u, a) for u, a in zip(user_parts, accept_norms)]
    if all(matched):
        return True, "回答正确。"
    wrong_idx = [str(i + 1) for i, ok in enumerate(matched) if not ok]
    return False, f"第 {('、'.join(wrong_idx))} 空不正确，请对照解析再试。"


def _split_fill_parts(text: str, *, expected_n: int) -> list[str]:
    """把用户/标准答案拆成各空。兼容 JSON 数组、分号、顿号、逗号、空格。"""
    raw = (text or "").strip()
    if not raw:
        return []
    # JSON 数组：["H","C"]
    if raw.startswith("["):
        try:
            import json

            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if re.search(r"[；;]", raw):
        parts = re.split(r"[；;]", raw)
    elif expected_n > 1 and re.search(r"[、，,]", raw):
        parts = re.split(r"[、，,]+", raw)
    elif expected_n > 1 and re.search(r"\s+", raw):
        parts = re.split(r"\s+", raw)
    else:
        parts = [raw]
    return [p.strip() for p in parts if p.strip()]


def _fill_equals(user_raw: str, accept_raw: str) -> bool:
    """在规范化后比较；兼容公式脚标与轻微多余字。"""
    u = _norm_fill(str(user_raw))
    a = _norm_fill(str(accept_raw))
    if not a:
        return False
    if u == a:
        return True
    # 公式：去掉非字母数字后再比（H₂O / H2O / h_2o）
    fu, fa = _norm_formula(u), _norm_formula(a)
    if fa and fu == fa:
        return True
    # 分数：1/2 与 frac{1}{2}
    if _norm_frac_slash(u) and _norm_frac_slash(u) == _norm_frac_slash(a):
        return True
    # 用户多写了「是/为」等
    for prefix in ("是", "为", "等于", "="):
        if u.startswith(prefix) and _fill_equals(u[len(prefix) :], a):
            return True
    return False

def _norm_judge(s: str) -> str:
    s = (s or "").strip().lower()
    if s in ("对", "正确", "true", "t", "√", "yes", "y", "✓"):
        return "对"
    if s in ("错", "错误", "false", "f", "×", "no", "n", "✗", "x"):
        return "错"
    return s


_SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
_FULLWIDTH = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
)


def _norm_fill(s: str) -> str:
    s = _strip_answer_meta(s or "")
    s = s.translate(_SUB).translate(_FULLWIDTH)
    s = s.replace(" ", "").replace("\u3000", "").replace("＄", "$")
    s = s.replace("×", "*").replace("÷", "/").replace("·", "*")
    s = s.replace("π", "pi")
    s = s.replace("√", "sqrt")
    s = re.sub(r"sqrt\{(\d+)\}", r"sqrt\1", s)
    s = re.sub(r"sqrt\((\d+)\)", r"sqrt\1", s)
    s = re.sub(r"(\d)\*sqrt", r"\1sqrt", s)
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("【", "[").replace("】", "]")
    s = s.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    s = s.replace("，", ",").replace("；", ";").replace("。", "")
    s = s.replace("＿", "_").replace("–", "-").replace("—", "-")
    s = s.replace("＜", "<").replace("＞", ">").replace("≦", "<=").replace("≧", ">=")
    s = s.replace("≤", "<=").replace("≥", ">=")
    # 去掉外层 $ 或 $$
    s = re.sub(r"^\$+|\$+$", "", s)
    # 分数与正体
    s = re.sub(r"\\dfrac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", s)
    s = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", s)
    s = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", s)
    s = s.replace("\\", "")
    # 下标写法 H_2O -> H2O
    s = re.sub(r"_\{([^}]*)\}", r"\1", s)
    s = re.sub(r"_(\d+)", r"\1", s)
    # 上标 (x+1)^2 与 (x+1)² 对齐
    s = re.sub(r"\^(\d+)", r"\1", s)
    # 再剥一次标注（全角括号已转半角）
    s = re.sub(r"\(冲l[0-4][^)]*\)$", "", s, flags=re.I)
    s = re.sub(r"\(l[0-4]\)$", "", s, flags=re.I)
    return s.lower()


def _norm_frac_slash(s: str) -> str:
    m = re.fullmatch(r"(-?\d+)/(-?\d+)", s or "")
    if not m:
        m = re.fullmatch(r"frac\{(-?\d+)\}\{(-?\d+)\}", s or "")
    if not m:
        return ""
    a, b = int(m.group(1)), int(m.group(2))
    if b == 0:
        return ""
    g = math.gcd(a, b)
    return f"{a // g}/{b // g}"


def _norm_formula(s: str) -> str:
    s = _norm_fill(s)
    return re.sub(r"[^a-z0-9+]", "", s)