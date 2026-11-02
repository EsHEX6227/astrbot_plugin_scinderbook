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


def test_import_files_accepts_good_and_rejects_bad() -> None:
    cat, root = _vault()
    result = cat.import_files(
        [
            {"name": "导入好.md", "text": "# 导入好\n\n好词 导入\n正文内容。\n"},
            {"name": "导入坏.md", "text": "没有召回词的一整句话。\n"},
            {"name": "空.md", "text": "# 空\n\n只有 词\n"},
            {"name": "不是md.txt", "text": "x"},
            {"name": "", "text": "x"},
        ],
        folder="导入",
    )
    imported = result["imported"]
    rejected = result["rejected"]
    assert [item["path"] for item in imported] == ["导入/导入好.md"]
    assert imported[0]["keywords"] == 2
    assert len(rejected) == 4
    reasons = " ".join(item["reason"] for item in rejected)
    assert "召回词" in reasons and "正文为空" in reasons and ".md" in reasons
    saved = (root / "导入" / "导入好.md").read_text(encoding="utf-8")
    assert saved.startswith("# 导入好\n")
    assert "好词 导入" in saved
    # 同名再导入一次，不能覆盖
    again = cat.import_files([{"name": "导入好.md", "text": "# 导入好\n\n好词 导入\n新正文。\n"}], folder="导入")
    assert again["imported"][0]["path"] == "导入/导入好-2.md"
    assert "新正文。" in (root / "导入" / "导入好-2.md").read_text(encoding="utf-8")
    assert "正文内容。" in (root / "导入" / "导入好.md").read_text(encoding="utf-8")


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
