"""Bulk-import planning for a script-library directory upload.

Real-world layout (see bioinformatics-backend/scripts/Amplicon)::

    Amplicon/
      scripts/    step3_dada2.sh ...        ← executable scripts (.sh/.py/.pl/.r)
      reference/  step3_dada2.md ...        ← one doc per script, paired by stem
      examples/   step3_dada2/D1.fastq.gz   ← sample data (NOT imported)

`plan_import` turns the flat ``(relative_path, bytes)`` list the browser
produces for a directory upload into a list of script+doc pairs with parsed
metadata. Pairing rules:

- scripts and docs are matched by file stem (``step3_dada2``);
- a doc under a ``reference/`` (or ``docs/``/``doc/``) directory wins over a
  same-stem doc sitting next to the script;
- a script with no doc is still imported (metadata falls back to defaults);
- ``README*`` docs, anything under ``examples/``/``example/``/``data/``/
  ``test*/`` directories, and non script/doc files are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from octop.infra.bioinformatics.mdmeta import ScriptMdMeta, parse_script_md

_SCRIPT_EXTS = {".sh", ".py", ".pl", ".r"}
_DOC_EXT = ".md"
_DOC_DIR_HINTS = {"reference", "doc", "docs"}
_DATA_DIR_HINTS = {"examples", "example", "data", "test", "tests"}


@dataclass(frozen=True)
class PlannedScript:
    """One script (+ its paired doc + parsed metadata) ready for create_script."""

    rel_path: str
    script: tuple[str, bytes]
    md: tuple[str, bytes] | None
    meta: ScriptMdMeta


@dataclass(frozen=True)
class ImportPlan:
    folder_name: str
    scripts: list[PlannedScript] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _sanitize(rel: str) -> PurePosixPath | None:
    """Normalize a browser-supplied relative path; None = reject (unsafe)."""
    path = PurePosixPath(rel.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.name:
        return None
    return path


def _under_dir(path: PurePosixPath, hints: set[str]) -> bool:
    return any(part.lower() in hints for part in path.parts[:-1])


def plan_import(files: list[tuple[str, bytes]], folder_name: str = "") -> ImportPlan:
    scripts: dict[str, tuple[PurePosixPath, bytes]] = {}
    docs: dict[str, list[tuple[PurePosixPath, bytes]]] = {}
    skipped: list[str] = []

    for rel, content in files:
        path = _sanitize(rel)
        if path is None:
            skipped.append(rel)
            continue
        ext = path.suffix.lower()
        if path.name.lower().startswith("readme") or _under_dir(path, _DATA_DIR_HINTS):
            skipped.append(rel)
        elif ext in _SCRIPT_EXTS:
            scripts[str(path)] = (path, content)
        elif ext == _DOC_EXT:
            docs.setdefault(path.stem, []).append((path, content))
        else:
            skipped.append(rel)

    planned: list[PlannedScript] = []
    used_docs: set[str] = set()
    for rel_path in sorted(scripts):
        path, content = scripts[rel_path]
        candidates = docs.get(path.stem, [])
        # prefer a doc from a reference/docs directory over a sibling doc
        pick = next(
            ((p, b) for p, b in candidates if _under_dir(p, _DOC_DIR_HINTS)),
            candidates[0] if candidates else None,
        )
        md: tuple[str, bytes] | None = None
        meta = ScriptMdMeta()
        if pick is not None:
            doc_path, doc_bytes = pick
            used_docs.add(str(doc_path))
            md = (doc_path.name, doc_bytes)
            meta = parse_script_md(doc_bytes.decode("utf-8", errors="replace"))
        planned.append(
            PlannedScript(rel_path=rel_path, script=(path.name, content), md=md, meta=meta)
        )

    for candidates in docs.values():
        for p, _ in candidates:
            if str(p) not in used_docs:
                skipped.append(str(p))

    if not folder_name:
        roots = {p.parts[0] for p, _ in scripts.values() if len(p.parts) > 1}
        folder_name = roots.pop() if len(roots) == 1 else ""
    return ImportPlan(folder_name=folder_name, scripts=planned, skipped=sorted(skipped))


__all__ = ["ImportPlan", "PlannedScript", "plan_import"]
