"""Unit tests for BioScriptLibrary (availability gating, folders, files on disk)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from octop.infra.bioinformatics.library import BioScriptLibrary, script_available
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.bio_script_folders import BioScriptFolderRepo
from octop.infra.db.repos.bio_scripts import BioScriptRepo
from octop.infra.errors import ErrorCode, OctopError


@pytest.fixture
def lib(tmp_path: Path) -> BioScriptLibrary:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return BioScriptLibrary(
        script_repo=BioScriptRepo(pool),
        folder_repo=BioScriptFolderRepo(pool),
        library_root=tmp_path / "bio_library",
    )


def _verified_active(lib: BioScriptLibrary, script_id: str) -> None:
    lib.scripts.set_verified(script_id, verified=True, verified_by=1)


def test_availability_gating(lib: BioScriptLibrary) -> None:
    row = lib.create_script(name="fastp", script_file=("fastp.py", b"print(1)"))
    # unverified -> unavailable
    assert not script_available(lib.require_script(row.id))
    _verified_active(lib, row.id)
    assert script_available(lib.require_script(row.id))
    # inactive -> unavailable
    lib.scripts.set_active(row.id, is_active=False)
    assert not script_available(lib.require_script(row.id))
    lib.scripts.set_active(row.id, is_active=True)
    # outside validity window -> unavailable
    now = int(time.time())
    lib.scripts.update(row.id, valid_from=now + 1000)
    assert not script_available(lib.require_script(row.id))
    lib.scripts.update(row.id, valid_from=now - 1000, valid_until=now - 10)
    assert not script_available(lib.require_script(row.id))
    lib.scripts.update(row.id, valid_until=now + 1000)
    assert script_available(lib.require_script(row.id))


def test_require_available_raises(lib: BioScriptLibrary) -> None:
    row = lib.create_script(name="x", script_file=("x.py", b"pass"))
    with pytest.raises(OctopError) as excinfo:
        lib.require_available(row.id)
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_UNAVAILABLE
    with pytest.raises(OctopError) as excinfo:
        lib.require_available("missing")
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_NOT_FOUND


def test_script_files_on_disk(lib: BioScriptLibrary, tmp_path: Path) -> None:
    row = lib.create_script(
        name="qc",
        script_file=("qc.py", b"print('qc')"),
        md_file=("qc.md", "# QC\n去接头".encode()),
    )
    script_dir = tmp_path / "bio_library" / row.id
    assert (script_dir / "qc.py").read_bytes() == b"print('qc')"
    assert row.file_path == "qc.py"
    assert row.md_content.startswith("# QC")

    lib.delete_script(row.id)
    assert lib.scripts.get(row.id) is None
    assert not script_dir.exists()


def test_folder_rules(lib: BioScriptLibrary) -> None:
    root = lib.create_folder(name="扩增子")
    child = lib.create_folder(name="16S", parent_id=root.id)
    with pytest.raises(OctopError) as excinfo:
        lib.create_folder(name="bad", parent_id="missing")
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND

    # non-empty folder cannot be deleted
    with pytest.raises(OctopError) as excinfo:
        lib.delete_folder(root.id)
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_FOLDER_NOT_EMPTY

    lib.delete_folder(child.id)
    lib.delete_folder(root.id)
    assert lib.folders.list_all() == []


def test_catalog_for_planner(lib: BioScriptLibrary) -> None:
    available = lib.create_script(
        name="fastp",
        category="qc",
        script_file=("fastp.py", b"pass"),
        md_file=("doc.md", ("x" * 600).encode()),
    )
    _verified_active(lib, available.id)
    lib.create_script(name="draft", script_file=("d.py", b"pass"))  # unverified -> excluded

    catalog = lib.catalog_for_planner()
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["id"] == available.id
    assert entry["name"] == "fastp"
    assert len(str(entry["md_content"])) <= 501  # 500 chars + ellipsis
