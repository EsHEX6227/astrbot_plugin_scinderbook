"""分词：优先 jieba，缺失时退化成「CJK 二元组 + 英文单词」。

索引和查询必须用同一套分词，否则 FTS 永远对不上。
"""

from __future__ import annotations

import re

_CJK = r"\u4e00-\u9fff"
_CJK_RE = re.compile(f"[{_CJK}]+")
_ASCII_RE = re.compile(r"[A-Za-z0-9_]+")

try:  # jieba 是可选依赖
    import jieba

    _HAS_JIEBA = True
except Exception:  # noqa: BLE001
    jieba = None  # type: ignore[assignment]
    _HAS_JIEBA = False

STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "这", "那", "个", "吗", "呢",
    "啊", "吧", "就", "都", "也", "很", "在", "有", "和", "与", "及", "等", "把", "被",
    "对", "给", "让", "而", "但", "还", "又", "再", "要", "会", "能", "可以", "什么",
    "怎么", "为什么", "一个", "不", "没", "没有", "如果", "因为", "所以", "但是",
    "and", "or", "the", "a", "an", "is", "are", "to", "of", "in", "on", "for",
}


def _cjk_bigrams(text: str) -> list[str]:
    tokens: list[str] = []
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def tokenize(text: str) -> list[str]:
    """把一段文本切成检索 token（已去停用词、去重保序）。"""
    text = (text or "").strip()
    if not text:
        return []

    raw: list[str]
    if _HAS_JIEBA:
        raw = [tok.strip() for tok in jieba.cut(text)]
    else:
        raw = _cjk_bigrams(text) + _ASCII_RE.findall(text)

    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        tok = tok.strip()
        if not tok or tok in STOPWORDS:
            continue
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", tok):
            continue
        lowered = tok.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(tok)
    return out


def fts5_query(text: str) -> str:
    """把文本转成 FTS5 的 OR 查询串；没有可用 token 时返回空串。"""
    tokens = tokenize(text)
    if not tokens:
        return ""
    return " OR ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)
