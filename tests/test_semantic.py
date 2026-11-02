"""语义兜底的单元测试（用合成向量，不依赖任何嵌入服务）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scinderbook.entry import scan_dir  # noqa: E402
from scinderbook.index import ScinderBookIndex  # noqa: E402
from scinderbook.semantic import cosine, pack_vector, unpack_vector  # noqa: E402

FILES = {
    "Kn视力.md": "# Kn视力\n看 观看 视觉 视力 看见 视\n你默认没有视觉能力，除非调用视觉工具。\n",
    "Kn触觉.md": "# Kn触觉\n触觉 摸 感觉到\n你没有触觉，亲热时才可以表述。\n",
    "Kn署名.md": "# Kn署名\n署名 落款 签名\n落款写 YuJie.ctr。\n",
}


def _vault() -> Path:
    root = Path(tempfile.mkdtemp(prefix="scb_"))
    for name, text in FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


def test_pack_unpack_roundtrip() -> None:
    vec = [0.1, -0.25, 3.5]
    assert unpack_vector(pack_vector(vec)) == vec or all(
        abs(a - b) < 1e-6 for a, b in zip(unpack_vector(pack_vector(vec)), vec)
    )


def test_cosine_bounds() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) > 0.99
    assert cosine([1.0, 0.0], [0.0, 1.0]) < 0.01
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embedding_text_excludes_keywords() -> None:
    root = _vault()
    entries, _ = scan_dir(root)
    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scb_idx_")) / "i.sqlite")
    index.rebuild(entries)
    candidates = index.embedding_candidates(max_chars=512)
    assert len(candidates) == 3
    joined = " ".join(text for _, text in candidates)
    assert "看 观看 视觉" not in joined, "检索词不能参与 embedding"
    assert "你默认没有视觉能力" in joined


def test_semantic_search_ranks_by_similarity() -> None:
    root = _vault()
    entries, _ = scan_dir(root)
    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scb_idx_")) / "i.sqlite")
    index.rebuild(entries)

    ids = {rel: entry_id for entry_id, rel in index._conn.execute(  # noqa: SLF001
        "SELECT id, rel_path FROM entries"
    )}
    # 让「视力」条目落在 +x 轴，「触觉」落在 +y 轴
    index.set_embeddings([
        (ids["Kn视力.md"], [1.0, 0.0, 0.0]),
        (ids["Kn触觉.md"], [0.0, 1.0, 0.0]),
        (ids["Kn署名.md"], [0.0, 0.0, 1.0]),
    ])
    assert index.embeddings_count() == 3
    assert index.get_vector_dim() == 3

    hits = index.semantic_search([0.95, 0.05, 0.0], top_n=2, min_sim=0.5)
    assert hits and hits[0][1].title == "Kn视力", hits

    # 阈值过滤：与三个轴向量都不接近的查询，高分阈值下应当为空
    assert index.semantic_search([1.0, 1.0, 1.0], top_n=1, min_sim=0.99) == []


def test_rebuild_clears_vectors() -> None:
    root = _vault()
    entries, _ = scan_dir(root)
    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scb_idx_")) / "i.sqlite")
    index.rebuild(entries)
    ids = [row[0] for row in index._conn.execute("SELECT id FROM entries")]  # noqa: SLF001
    index.set_embeddings([(ids[0], [1.0, 0.0])])
    assert index.embeddings_count() == 1
    index.rebuild(entries)
    assert index.embeddings_count() == 0, "重建索引后旧向量必须清掉"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("全部通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)
