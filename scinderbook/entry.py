"""词条解析：把一个 .md 拆成「条目名 + 检索词 + 正文」。

文件格式（两种都支持）：

1. 极简格式（推荐，写起来最省事）

    # Kn视力

    看 观看 视觉 视力 看见 视
    作为语言模型的电子猫娘，你默认没有视觉能力。
    当且仅当有具体通知或者你能调用视觉工具时，你可以使用「我看见……」这类表达。

   标题下、正文前的第一行是检索词（空格分隔），其余全部是正文。

2. YAML frontmatter（适合要带常驻标记、或者正文开头本身就是列表/空行的情况）

    ---
    keywords: [看, 观看, 视觉, 视力]
    always: false
    ---
    正文……

识别顺序由 mode 控制：
- auto：优先 frontmatter，没有就把标题后的第一行当检索词
- frontmatter_only：只认 frontmatter，其余内容全部算正文
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HEADING_RE = re.compile(r"^#\s+(.+?)\s*$")

# 出现这些字符的行不可能是检索词行（句子标点）
_SENTENCE_PUNCT = set("。！？，、；：…—")

MAX_KEYWORD_LINE = 240
MAX_KEYWORD_LEN = 24
MAX_KEYWORDS = 60


@dataclass
class Entry:
    """一个词条。"""

    rel_path: str
    title: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""
    always: bool = False

    @property
    def keyword_text(self) -> str:
        return " ".join(self.keywords)

    def render(self) -> str:
        """注入用的片段：只有条目名和正文，不含检索词。"""
        return f"【{self.title}】\n{self.body}".strip()


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """切出开头的 YAML frontmatter，只做够用的极简解析（不引入 PyYAML）。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text

    meta: dict[str, str] = {}
    key = None
    buf: list[str] = []
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key:
            buf.append(stripped[2:].strip())
            continue
        if ":" in stripped:
            if key and buf:
                meta[key] = " ".join(buf)
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()
            buf = [value] if value else []
    if key and buf:
        meta[key] = " ".join(buf)

    rest = "\n".join(lines[end + 1 :])
    return meta, rest


def _parse_list(value: str) -> list[str]:
    """把 `[a, b]`、`a, b`、`a b` 都解析成列表。"""
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    for sep in ("，", ",", "、", "|"):
        value = value.replace(sep, " ")
    out: list[str] = []
    for tok in value.split():
        kw = _normalize_keyword(tok)
        if kw:
            out.append(kw)
    return out


def _normalize_keyword(token: str) -> str:
    """去掉 Obsidian 双链、引号、括号之类的包装，只留关键词本身。"""
    token = token.strip()
    for left, right in (("[[", "]]"), ("**", "**"), ("`", "`"), ("“", "”"), ("‘", "’"), ("《", "》")):
        if token.startswith(left) and token.endswith(right) and len(token) > len(left) + len(right):
            token = token[len(left) : -len(right)].strip()
            break
    token = token.replace("[[", "").replace("]]", "")
    return token.strip("[]\"'“”‘’()（）<>。，、；：！？… ")


def _looks_like_keyword_line(line: str) -> bool:
    """启发式判断：这一行是不是检索词行。"""
    if not line or line.startswith("#"):
        return False
    if len(line) > MAX_KEYWORD_LINE:
        return False
    if any(ch in _SENTENCE_PUNCT for ch in line):
        return False
    tokens = line.split()
    if len(tokens) < 2 or len(tokens) > MAX_KEYWORDS:
        return False
    return all(1 <= len(tok) <= MAX_KEYWORD_LEN for tok in tokens)


def _extract_title_and_body(rest: str, fallback_title: str) -> tuple[str, str]:
    """从正文里取出标题行，返回 (title, 去掉标题行后的内容)。"""
    lines = rest.splitlines()
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line.strip())
        if m:
            title = m.group(1).strip()
            body = "\n".join(lines[i + 1 :]).strip()
            return title or fallback_title, body
    return fallback_title, rest.strip()


def parse_entry(raw: str, rel_path: str, mode: str = "auto") -> Entry:
    """解析单个词条文本。"""
    fallback = Path(rel_path).stem
    meta, rest = _split_frontmatter(raw)

    always = str(meta.get("always", "")).strip().lower() in {"true", "1", "yes", "on"}
    keywords: list[str] = []
    for key in ("keywords", "aliases", "alias", "keys"):
        if meta.get(key):
            keywords = _parse_list(meta[key])
            break

    title, body = _extract_title_and_body(rest, meta.get("title", fallback))

    if not keywords and mode != "frontmatter_only":
        lines = body.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if _looks_like_keyword_line(stripped):
                keywords = [
                    kw for kw in (_normalize_keyword(tok) for tok in stripped.split()) if kw
                ]
                body = "\n".join(lines[i + 1 :]).strip()
            break

    # 去重但保留顺序
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return Entry(
        rel_path=rel_path,
        title=title or fallback,
        keywords=unique,
        body=body,
        always=always,
    )


def scan_dir(
    root: Path,
    mode: str = "auto",
    always_files: list[str] | None = None,
) -> tuple[list[Entry], list[str]]:
    """递归扫描目录下所有 .md，返回 (词条列表, 解析失败的相对路径)。"""
    always_set = {str(name).strip() for name in (always_files or []) if str(name).strip()}
    entries: list[Entry] = []
    failed: list[str] = []
    root = Path(root)
    if not root.is_dir():
        return entries, [str(root)]

    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            failed.append(rel)
            continue
        try:
            entry = parse_entry(raw, rel, mode=mode)
        except Exception:  # noqa: BLE001 - 单个文件坏掉不能拖垮整次扫描
            failed.append(rel)
            continue
        entry.always = entry.always or rel in always_set or path.name in always_set
        if entry.body:
            entries.append(entry)
        else:
            failed.append(rel)
    return entries, failed
