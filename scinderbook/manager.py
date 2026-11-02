"""词条文件的管理层：列表、读写、体检、写回。

这一层只碰文件系统，不碰索引和数据库，保证「md 是唯一真源」：
管理页保存后按规范重写 md，索引/向量随后由插件重建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .entry import _looks_like_keyword_line, _normalize_keyword, _split_frontmatter, parse_entry

_SAFE_REL = re.compile(r"^[\w\u4e00-\u9fff\-./ ]+\.md$", re.UNICODE)
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


@dataclass
class Issue:
    code: str
    message: str

    @property
    def level(self) -> str:
        return ISSUE_LEVELS.get(self.code, "warn")

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "level": self.level}


# error：这条词条不会参与召回/注入（不可用）；warn：能用但大概不是你想要的；info：只是提示
ISSUE_LEVELS = {
    "unreadable": "error",
    "empty_body": "error",
    "no_keywords": "error",
    "keyword_line_skipped": "error",
    "too_long": "warn",
    "title_mismatch": "warn",
    "no_heading": "info",
    "filename_mismatch": "info",
}

# 出现这些问题的词条会被索引跳过（等于「导进来了但不启用」）
BLOCKING_CODES = frozenset(
    {"unreadable", "empty_body", "no_keywords", "keyword_line_skipped"}
)


@dataclass
class EntryInfo:
    """列表用的轻量视图（不含正文）。"""

    rel_path: str
    title: str
    keywords: list[str]
    always: bool
    body_len: int
    mtime: float
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.rel_path,
            "title": self.title,
            "keywords": self.keywords,
            "always": self.always,
            "body_len": self.body_len,
            "mtime": self.mtime,
            "issues": [i.to_dict() for i in self.issues],
            "level": worst_level(self.issues),
            "blocked": is_blocked(self.issues),
        }


def _slug(title: str) -> str:
    name = _FILENAME_BAD.sub("_", title.strip()) or "未命名词条"
    return name[:60]


def worst_level(issues: list[Issue]) -> str:
    order = {"info": 0, "warn": 1, "error": 2}
    level = "ok"
    for issue in issues:
        if order.get(issue.level, 1) > order.get(level, -1):
            level = issue.level
    return level


def is_blocked(issues: list[Issue]) -> bool:
    """这条词条是否应该被索引跳过（导进来了，但不启用）。"""
    return any(issue.code in BLOCKING_CODES for issue in issues)


class Catalog:
    """一个词条目录。"""

    def __init__(
        self,
        root: Path | str,
        mode: str = "auto",
        always_files: list[str] | None = None,
        max_chars: int = 1500,
    ) -> None:
        self.root = Path(root)
        self.mode = mode or "auto"
        self.always_files = [str(x) for x in (always_files or [])]
        self.max_chars = max(1, int(max_chars))

    # ---------- 路径安全 ----------

    def _safe_path(self, rel_path: str) -> Path:
        rel_path = (rel_path or "").strip().replace("\\", "/")
        if not rel_path or not _SAFE_REL.match(rel_path):
            raise ValueError(f"非法的词条路径：{rel_path!r}")
        if rel_path.startswith("/") or ".." in rel_path.split("/"):
            raise ValueError(f"非法的词条路径：{rel_path!r}")
        path = (self.root / rel_path).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"路径越界：{rel_path!r}")
        return path

    # ---------- 体检 ----------

    def _audit(self, rel_path: str, raw: str, title: str) -> list[Issue]:
        issues: list[Issue] = []
        meta, rest = _split_frontmatter(raw)
        entry = parse_entry(raw, rel_path, mode=self.mode)

        has_heading = any(
            line.strip().startswith("# ") for line in rest.splitlines()[:5]
        )
        if not has_heading:
            issues.append(Issue("no_heading", "首行不是 `# 标题`，条目名取自文件名"))

        if not entry.keywords:
            first = next(
                (line.strip() for line in entry.body.splitlines() if line.strip()), ""
            )
            if first and any(ch in first for ch in "。！？，、；："):
                issues.append(
                    Issue(
                        "keyword_line_skipped",
                        "第一行是带标点的句子，不会被识别成召回词",
                    )
                )
            issues.append(
                Issue("no_keywords", "没有召回词，只能靠正文分词命中")
            )

        if not entry.body.strip():
            issues.append(Issue("empty_body", "正文为空"))

        if len(entry.body) > self.max_chars:
            issues.append(
                Issue(
                    "too_long",
                    f"正文 {len(entry.body)} 字，超过单轮注入上限 {self.max_chars}",
                )
            )

        if meta.get("title") and entry.title != meta.get("title"):
            issues.append(Issue("title_mismatch", "frontmatter 标题与正文标题不一致"))

        if Path(rel_path).stem != entry.title:
            issues.append(Issue("filename_mismatch", "文件名与条目名不一致（仅提示）"))

        return issues

    # ---------- 列表 / 读取 ----------

    def list_entries(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        items: list[EntryInfo] = []
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root).as_posix()
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                entry = parse_entry(raw, rel, mode=self.mode)
                mtime = path.stat().st_mtime
                issues = self._audit(rel, raw, entry.title)
                items.append(
                    EntryInfo(
                        rel_path=rel,
                        title=entry.title,
                        keywords=entry.keywords,
                        always=entry.always or path.name in self.always_files,
                        body_len=len(entry.body),
                        mtime=mtime,
                        issues=issues,
                    )
                )
            except Exception:  # noqa: BLE001
                items.append(
                    EntryInfo(
                        rel_path=rel,
                        title=path.stem,
                        keywords=[],
                        always=False,
                        body_len=0,
                        mtime=0.0,
                        issues=[Issue("unreadable", "文件读取或解析失败")],
                    )
                )
        return [item.to_dict() for item in items]

    def read_entry(self, rel_path: str, include_raw: bool = False) -> dict:
        path = self._safe_path(rel_path)
        if not path.is_file():
            raise FileNotFoundError(rel_path)
        raw = path.read_text(encoding="utf-8", errors="replace")
        entry = parse_entry(raw, rel_path, mode=self.mode)
        issues = self._audit(rel_path, raw, entry.title)
        data = {
            "path": rel_path,
            "title": entry.title,
            "keywords": entry.keywords,
            "body": entry.body,
            "always": entry.always or path.name in self.always_files,
            "mtime": path.stat().st_mtime,
            "issues": [i.to_dict() for i in issues],
            "level": worst_level(issues),
            "blocked": is_blocked(issues),
        }
        if include_raw:
            data["raw"] = raw
        return data

    # ---------- 写入 ----------

    @staticmethod
    def _render(title: str, keywords: list[str], body: str, always: bool) -> str:
        title = (title or "").strip().replace("\n", " ")
        kws = " ".join(
            kw for kw in (_normalize_keyword(k) for k in keywords) if kw
        )
        body = body.replace("\r\n", "\n").strip()
        parts: list[str] = []
        if always:
            parts.append("---\nalways: true\n---\n")
        parts.append(f"# {title}\n")
        if kws:
            parts.append(kws + "\n")
        if body:
            parts.append("\n" + body + "\n")
        return "\n".join(parts).rstrip("\n") + "\n"

    def save_entry(
        self,
        rel_path: str,
        title: str,
        keywords: list[str],
        body: str,
        always: bool = False,
    ) -> dict:
        if not (title or "").strip():
            raise ValueError("标题不能为空")
        path = self._safe_path(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self._render(title, keywords, body, always)
        path.write_text(text, encoding="utf-8", newline="\n")
        return self.read_entry(rel_path, include_raw=True)

    def create_entry(self, title: str, folder: str = "") -> dict:
        if not (title or "").strip():
            raise ValueError("标题不能为空")
        base = self._safe_folder(folder)
        stem = _slug(title)
        candidate = base / f"{stem}.md"
        index = 2
        while candidate.exists():
            candidate = base / f"{stem}-{index}.md"
            index += 1
        candidate.parent.mkdir(parents=True, exist_ok=True)
        rel = candidate.relative_to(self.root).as_posix()
        candidate.write_text(self._render(title, [], "", False), encoding="utf-8")
        return self.read_entry(rel, include_raw=True)

    def import_files(self, files: list[dict], folder: str = "") -> dict:
        """拖进来的 .md：格式不对也照收，只是标成不可用（不参与召回），等用户改好。

        只有「不是 md / 文件为空 / 写不进去」才退回。
        """
        base = self._safe_folder(folder)
        imported: list[dict] = []
        rejected: list[dict] = []
        for item in files or []:
            raw_name = str(item.get("name") or "").strip()
            name = Path(raw_name.replace("\\", "/")).name
            if not name or name.startswith("."):
                rejected.append({"name": raw_name, "reason": "文件名不可用"})
                continue
            if not name.lower().endswith(".md"):
                rejected.append({"name": name, "reason": "不是 .md 文件"})
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                rejected.append({"name": name, "reason": "文件是空的"})
                continue
            try:
                entry = parse_entry(text, name, mode=self.mode)
            except Exception:  # noqa: BLE001
                rejected.append({"name": name, "reason": "解析失败"})
                continue

            stem = _slug(entry.title or Path(name).stem)
            target = base / f"{stem}.md"
            index = 2
            while target.exists():
                target = base / f"{stem}-{index}.md"
                index += 1
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    self._render(entry.title, entry.keywords, entry.body, entry.always),
                    encoding="utf-8",
                    newline="\n",
                )
            except OSError as exc:
                rejected.append({"name": name, "reason": f"写入失败：{exc}"})
                continue

            rel = target.relative_to(self.root).as_posix()
            issues = self._audit(rel, target.read_text(encoding="utf-8"), entry.title)
            imported.append(
                {
                    "path": rel,
                    "title": entry.title,
                    "keywords": len(entry.keywords),
                    "blocked": is_blocked(issues),
                    "problems": [i.code for i in issues if i.code in BLOCKING_CODES],
                }
            )
        return {"imported": imported, "rejected": rejected}

    def delete_entry(self, rel_path: str) -> bool:
        path = self._safe_path(rel_path)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _safe_folder(self, folder: str) -> Path:
        """导入/新建的目标目录：只允许 root 下的相对子目录。"""
        folder = (folder or "").strip().replace("\\", "/").strip("/")
        root = self.root.resolve()
        if not folder or folder == ".":
            return root
        if ".." in folder.split("/"):
            raise ValueError(f"非法的目录：{folder!r}")
        target = (root / folder).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"目录越界：{folder!r}")
        return target

    # ---------- 辅助 ----------

    def suggest_keywords(self, text: str, topk: int = 8) -> list[str]:
        """从正文里抽候选召回词（有 jieba 用 TF-IDF，没有就用字符二元组频次）。"""
        text = (text or "").strip()
        if not text:
            return []
        try:
            import jieba.analyse

            tags = jieba.analyse.extract_tags(text, topK=topk)
            return [t for t in tags if t and len(t) >= 2]
        except Exception:  # noqa: BLE001
            from collections import Counter

            from .tokenize import tokenize

            freq = Counter(tokenize(text))
            return [word for word, _ in freq.most_common(topk)]

    def first_line_looks_like_body(self, raw: str) -> bool:
        meta, rest = _split_frontmatter(raw)
        lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        body_lines = [ln for ln in lines if not ln.startswith("# ")]
        if not body_lines:
            return False
        return not _looks_like_keyword_line(body_lines[0])
