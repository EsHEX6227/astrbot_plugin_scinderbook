"""词条管理层的单元测试（不依赖 AstrBot，直接跑：python tests/test_manager.py）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scinderbook.manager import Catalog  # noqa: E402


def _vault() -> tuple[Catalog, Path]:
    tmp = tempfile.mkdtemp(prefix="scinderbook-")
    root = Path(tmp)
    (root / "Kn视力.md").write_text(
        "# Kn视力\n\n看 观看 视觉 视力\n你默认没有视觉能力。\n",
        encoding="utf-8",
    )
    (root / "Kn坏词条.md").write_text(
        "作为语言模型的电子猫娘，你默认没有视觉能力。\n",
        encoding="utf-8",
    )
    (root / "Kn空.md").write_text("# Kn空\n\n只有 召回词\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "嵌套.md").write_text(
        "# 嵌套\n\nalways 常驻\n嵌套正文。\n", encoding="utf-8"
    )
    return Catalog(root, mode="auto", max_chars=1500), root


def test_list_detects_all_entries() -> None:
    cat, _ = _vault()
    items = {item["path"]: item for item in cat.list_entries()}
    assert set(items) == {"Kn视力.md", "Kn坏词条.md", "Kn空.md", "sub/嵌套.md"}
    assert items["Kn视力.md"]["title"] == "Kn视力"
    assert items["Kn视力.md"]["keywords"] == ["看", "观看", "视觉", "视力"]
    assert not items["Kn视力.md"]["issues"]


def test_flags_missing_heading_and_keywords() -> None:
    cat, _ = _vault()
    items = {item["path"]: item for item in cat.list_entries()}
    bad = {i["code"] for i in items["Kn坏词条.md"]["issues"]}
    assert "no_heading" in bad
    assert "keyword_line_skipped" in bad
    assert "no_keywords" in bad


def test_flags_empty_body() -> None:
    cat, _ = _vault()
    items = {item["path"]: item for item in cat.list_entries()}
    codes = {i["code"] for i in items["Kn空.md"]["issues"]}
    assert "empty_body" in codes


def test_save_writes_canonical_format() -> None:
    cat, root = _vault()
    saved = cat.save_entry(
        "Kn视力.md",
        title="Kn视力",
        keywords=["看", "观看", "[[视觉]]"],
        body="你默认没有视觉能力。\n第二行。",
        always=False,
    )
    assert saved["keywords"] == ["看", "观看", "视觉"]
    assert not saved["issues"]
    text = (root / "Kn视力.md").read_text(encoding="utf-8")
    assert text.startswith("# Kn视力\n")
    assert "看 观看 视觉\n" in text
    assert "你默认没有视觉能力。" in text
    # 写回后必须还能被解析回来
    again = cat.read_entry("Kn视力.md")
    assert again["body"] == "你默认没有视觉能力。\n第二行。"


def test_save_with_always_uses_frontmatter() -> None:
    cat, root = _vault()
    cat.save_entry("sub/嵌套.md", title="嵌套", keywords=["常驻"], body="正文。", always=True)
    text = (root / "sub" / "嵌套.md").read_text(encoding="utf-8")
    assert text.startswith("---\nalways: true\n---\n")
    assert cat.read_entry("sub/嵌套.md")["always"] is True


def test_path_traversal_rejected() -> None:
    cat, _ = _vault()
    for bad in ("../evil.md", "/etc/passwd.md", "a/../../b.md", "noext", ""):
        try:
            cat.read_entry(bad)
        except (ValueError, FileNotFoundError):
            continue
        raise AssertionError(f"应当拒绝：{bad!r}")


def test_create_entry_is_unique() -> None:
    cat, root = _vault()
    first = cat.create_entry("新词条")
    second = cat.create_entry("新词条")
    assert first["path"] == "新词条.md"
    assert second["path"] == "新词条-2.md"
    assert (root / "新词条.md").is_file()


def test_create_entry_in_folder() -> None:
    cat, root = _vault()
    created = cat.create_entry("分类词条", folder="新分类")
    assert created["path"] == "新分类/分类词条.md"
    assert (root / "新分类" / "分类词条.md").is_file()
    try:
        cat.create_entry("坏目录", folder="../外面")
    except ValueError:
        pass
    else:
        raise AssertionError("目录越界应当被拒绝")


def test_import_keeps_malformed_but_marks_blocked() -> None:
    cat, root = _vault()
    result = cat.import_files(
        [
            {"name": "导入好.md", "text": "# 导入好\n\n好词 导入\n正文内容。\n"},
            {"name": "导入坏.md", "text": "没有召回词的一整句话。\n第二行也还在。\n"},
            {"name": "空.md", "text": "# 空\n\n只有 词\n"},
            {"name": "不是md.txt", "text": "x"},
            {"name": "", "text": "x"},
        ],
        folder="导入",
    )
    imported = {item["path"]: item for item in result["imported"]}
    rejected = result["rejected"]

    # 格式不对的也导入，只是标成 blocked；只有非 md / 空文件才退回
    assert set(imported) == {"导入/导入好.md", "导入/导入坏.md", "导入/空.md"}
    assert imported["导入/导入好.md"]["blocked"] is False
    assert imported["导入/导入坏.md"]["blocked"] is True
    assert "no_keywords" in imported["导入/导入坏.md"]["problems"]
    assert imported["导入/空.md"]["blocked"] is True  # 正文为空
    assert len(rejected) == 2

    # 缺召回词的文件：整篇内容都留在「内容」里，一行都没被吃掉
    bad = cat.read_entry("导入/导入坏.md")
    assert bad["keywords"] == []
    assert bad["blocked"] is True
    assert "没有召回词的一整句话。" in bad["body"]
    assert "第二行也还在。" in bad["body"]

    # 同名再导入一次，不能覆盖
    again = cat.import_files([{"name": "导入好.md", "text": "# 导入好\n\n好词 导入\n新正文。\n"}], folder="导入")
    assert again["imported"][0]["path"] == "导入/导入好-2.md"
    assert "新正文。" in (root / "导入" / "导入好-2.md").read_text(encoding="utf-8")
    assert "正文内容。" in (root / "导入" / "导入好.md").read_text(encoding="utf-8")


def test_fixing_keywords_unblocks() -> None:
    cat, _ = _vault()
    assert cat.read_entry("Kn坏词条.md")["blocked"] is True
    cat.save_entry(
        "Kn坏词条.md",
        title="Kn坏词条",
        keywords=["视觉", "猫娘"],
        body="补好召回词之后的正文。",
    )
    after = cat.read_entry("Kn坏词条.md")
    assert after["blocked"] is False
    assert after["keywords"] == ["视觉", "猫娘"]


def test_entry_blockers_rule() -> None:
    from scinderbook.entry import Entry, entry_blockers

    assert entry_blockers(Entry("a.md", "A", ["看"], "正文")) == []
    assert entry_blockers(Entry("a.md", "A", [], "正文")) == ["no_keywords"]
    assert entry_blockers(Entry("a.md", "A", ["看"], "  ")) == ["empty_body"]
    assert entry_blockers(Entry("a.md", "A", [], "")) == ["no_keywords", "empty_body"]


def test_delete_entry() -> None:
    cat, root = _vault()
    assert cat.delete_entry("Kn空.md") is True
    assert not (root / "Kn空.md").exists()
    assert cat.delete_entry("Kn空.md") is False


def test_suggest_keywords_falls_back() -> None:
    cat, _ = _vault()
    words = cat.suggest_keywords("视觉能力与观看行为的关系", topk=5)
    assert words
    assert all(len(w) >= 1 for w in words)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("全部通过")
