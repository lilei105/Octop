"""Unit tests for directory-upload import planning (importer)."""

from __future__ import annotations

from octop.infra.bioinformatics.importer import plan_import

MD = "## 名称: fastp 质控\n## 输入\n- reads: fastq\n".encode()


def _files() -> list[tuple[str, bytes]]:
    return [
        ("Amplicon/scripts/step1_fastp.sh", b"#!/bin/bash\nfastp\n"),
        ("Amplicon/reference/step1_fastp.md", MD),
        ("Amplicon/scripts/step2_orphan.sh", b"#!/bin/bash\ntrue\n"),
        ("Amplicon/reference/README.md", b"# docs readme\n"),
        ("Amplicon/reference/step9_unused.md", MD),
        ("Amplicon/examples/step1_fastp/D1.fastq.gz", b"\x1f\x8b data"),
        ("Amplicon/scripts/example/demo.txt", b"not a script"),
    ]


def test_pairs_script_with_reference_doc() -> None:
    plan = plan_import(_files())
    by_rel = {p.rel_path: p for p in plan.scripts}
    paired = by_rel["Amplicon/scripts/step1_fastp.sh"]
    assert paired.md is not None and paired.md[0] == "step1_fastp.md"
    assert paired.meta.name == "fastp 质控"


def test_script_without_doc_still_imported() -> None:
    plan = plan_import(_files())
    by_rel = {p.rel_path: p for p in plan.scripts}
    orphan = by_rel["Amplicon/scripts/step2_orphan.sh"]
    assert orphan.md is None
    assert orphan.meta.name == ""  # falls back to filename stem at create time


def test_readme_examples_and_junk_skipped() -> None:
    plan = plan_import(_files())
    assert "Amplicon/reference/README.md" in plan.skipped
    assert "Amplicon/examples/step1_fastp/D1.fastq.gz" in plan.skipped
    assert "Amplicon/scripts/example/demo.txt" in plan.skipped
    # doc with no matching script is reported too
    assert "Amplicon/reference/step9_unused.md" in plan.skipped
    assert len(plan.scripts) == 2


def test_folder_name_inferred_from_common_root() -> None:
    assert plan_import(_files()).folder_name == "Amplicon"
    flat = plan_import([("a.sh", b"x"), ("b.sh", b"y")])
    assert flat.folder_name == ""


def test_explicit_folder_name_wins() -> None:
    assert plan_import(_files(), "扩增子").folder_name == "扩增子"


def test_path_sanitization() -> None:
    plan = plan_import(
        [
            ("../evil.sh", b"x"),
            ("/abs/path.sh", b"x"),
            ("ok\\scripts\\win_style.sh", b"x"),  # backslashes from Windows browsers
        ]
    )
    rels = [p.rel_path for p in plan.scripts]
    assert "../evil.sh" not in rels and "/abs/path.sh" not in rels
    assert "ok/scripts/win_style.sh" in rels
    assert plan.folder_name == "ok"


def test_reference_dir_preferred_over_sibling_doc() -> None:
    sibling = "## 名称: sibling\n".encode()
    reference = "## 名称: reference\n".encode()
    plan = plan_import(
        [
            ("lib/scripts/x.sh", b"x"),
            ("lib/scripts/x.md", sibling),
            ("lib/reference/x.md", reference),
        ]
    )
    assert plan.scripts[0].meta.name == "reference"
    assert "lib/scripts/x.md" in plan.skipped
