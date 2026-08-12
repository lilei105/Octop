"""tests/integration/test_bio_admin_api.py — bio script library admin endpoints."""

from __future__ import annotations


async def test_script_folder_crud(env):
    c, srv, auth = env
    r = await c.post("/api/admin/bio/script-folders", headers=auth, json={"name": "扩增子"})
    assert r.status_code == 201
    folder = r.json()
    assert folder["name"] == "扩增子"

    r = await c.get("/api/admin/bio/script-folders", headers=auth)
    assert any(f["id"] == folder["id"] for f in r.json())

    r = await c.put(
        f"/api/admin/bio/script-folders/{folder['id']}",
        headers=auth,
        json={"name": "扩增子分析", "sort_order": 2},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "扩增子分析"

    r = await c.delete(f"/api/admin/bio/script-folders/{folder['id']}", headers=auth)
    assert r.status_code == 204
    r = await c.get("/api/admin/bio/script-folders", headers=auth)
    assert not any(f["id"] == folder["id"] for f in r.json())


async def test_script_lifecycle(env):
    c, srv, auth = env
    folder = (
        await c.post("/api/admin/bio/script-folders", headers=auth, json={"name": "qc"})
    ).json()

    r = await c.post(
        "/api/admin/bio/scripts",
        headers=auth,
        data={
            "name": "fastp 质控",
            "folder_id": folder["id"],
            "category": "qc",
            "weight": "10",
        },
        files={
            "script_file": ("fastp.py", b"print('qc')", "text/x-python"),
            "md_file": ("fastp.md", "# fastp\n去接头+质控".encode(), "text/markdown"),
        },
    )
    assert r.status_code == 201
    script = r.json()
    assert script["file_path"] == "fastp.py"
    assert script["verified"] is False
    assert script["is_active"] is True
    assert script["md_content"].startswith("# fastp")

    # folder now non-empty -> delete blocked
    r = await c.delete(f"/api/admin/bio/script-folders/{folder['id']}", headers=auth)
    assert r.status_code == 409

    r = await c.get(f"/api/admin/bio/scripts/{script['id']}", headers=auth)
    assert r.json()["name"] == "fastp 质控"

    r = await c.put(
        f"/api/admin/bio/scripts/{script['id']}",
        headers=auth,
        json={"description": "去接头", "runtime_min": 15.5},
    )
    assert r.status_code == 200
    assert r.json()["runtime_min"] == 15.5

    r = await c.patch(f"/api/admin/bio/scripts/{script['id']}/verify", headers=auth)
    assert r.json()["verified"] is True

    r = await c.patch(f"/api/admin/bio/scripts/{script['id']}/toggle", headers=auth)
    assert r.json()["is_active"] is False

    r = await c.get("/api/admin/bio/scripts", headers=auth, params={"folder_id": folder["id"]})
    assert [s["id"] for s in r.json()] == [script["id"]]

    r = await c.delete(f"/api/admin/bio/scripts/{script['id']}", headers=auth)
    assert r.status_code == 204
    r = await c.get(f"/api/admin/bio/scripts/{script['id']}", headers=auth)
    assert r.status_code == 404


async def test_non_admin_forbidden(env):
    c, srv, admin_auth = env
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "bob", "password": "pw", "role": "user"},
    )
    tok = (await c.post("/api/auth/login", json={"username": "bob", "password": "pw"})).json()[
        "access_token"
    ]
    user_auth = {"Authorization": f"Bearer {tok}"}
    r = await c.get("/api/admin/bio/scripts", headers=user_auth)
    assert r.status_code == 403


async def test_create_script_metadata_from_md(env):
    """Blank form fields are back-filled from the Markdown doc."""
    c, srv, auth = env
    md = (
        "# read counter\n"
        "## 版本: 3.2.1\n"
        "## 分类: qc\n"
        "## 描述: 统计 FASTQ reads 数\n"
        "## 输入\n"
        "- reads: fastq, fq\n"
    ).encode()
    r = await c.post(
        "/api/admin/bio/scripts",
        headers=auth,
        files={
            "script_file": ("counter.py", b"print(2)", "text/x-python"),
            "md_file": ("counter.md", md, "text/markdown"),
        },
    )
    assert r.status_code == 201, r.text
    script = r.json()
    assert script["name"] == "read counter"  # from the doc title
    assert script["version"] == "3.2.1"
    assert script["category"] == "qc"
    assert "reads" in script["description"]
    import json

    assert json.loads(script["inputs"])[0]["label"] == "reads"


async def test_parse_md_endpoint(env):
    c, srv, auth = env
    md = "## 名称: bwa 比对\n## Version: 0.7.17\n## 输入\n- ref: fasta\n".encode()
    r = await c.post(
        "/api/admin/bio/scripts/parse-md",
        headers=auth,
        files={"md_file": ("bwa.md", md, "text/markdown")},
    )
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["name"] == "bwa 比对"
    assert meta["version"] == "0.7.17"
    import json

    assert json.loads(meta["inputs"])[0]["label"] == "ref"


async def test_import_directory(env):
    """Bulk import: scripts paired with reference docs, folder auto-created, dedup on re-import."""
    c, srv, auth = env
    md = (
        "# x\n## 基本信息\n\n| 字段 | 值 |\n|---|---|\n"
        "| **Tool Name** | cutadapt |\n| 估算机时 | 2 小时 |\n\n"
        "## 输入\n\n| 文件名 | 描述 | 格式 |\n|---|---|---|\n| reads.fastq | 原始序列 | FASTQ |\n"
    ).encode()
    files = [
        ("files", ("Amplicon/scripts/step1_cutadapt.sh", b"#!/bin/bash\ntrue\n", "text/x-sh")),
        ("files", ("Amplicon/reference/step1_cutadapt.md", md, "text/markdown")),
        ("files", ("Amplicon/scripts/step2_orphan.sh", b"#!/bin/bash\ntrue\n", "text/x-sh")),
        ("files", ("Amplicon/examples/D1.fastq.gz", b"\x1f\x8b", "application/gzip")),
        ("files", ("Amplicon/reference/README.md", b"# readme", "text/markdown")),
    ]
    r = await c.post("/api/admin/bio/scripts/import", headers=auth, files=files)
    assert r.status_code == 201, r.text
    result = r.json()
    assert sorted(result["created"]) == ["cutadapt", "step2_orphan"]
    assert len(result["skipped"]) == 2  # example data + README

    folder_id = result["folder_id"]
    r = await c.get("/api/admin/bio/scripts", headers=auth, params={"folder_id": folder_id})
    scripts = {s["name"]: s for s in r.json()}
    assert scripts["cutadapt"]["runtime_min"] == 120.0
    import json

    assert json.loads(scripts["cutadapt"]["inputs"])[0]["label"] == "reads.fastq"
    assert scripts["step2_orphan"]["version"] == "1.0.0"  # no doc → defaults

    # re-import: same names are skipped, not duplicated
    r = await c.post("/api/admin/bio/scripts/import", headers=auth, files=files[:2])
    assert r.status_code == 201
    assert r.json()["created"] == []
    assert r.json()["skipped"][0]["reason"].startswith("name exists")


async def test_import_empty_rejected(env):
    c, srv, auth = env
    r = await c.post(
        "/api/admin/bio/scripts/import",
        headers=auth,
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BIO_IMPORT_EMPTY"
