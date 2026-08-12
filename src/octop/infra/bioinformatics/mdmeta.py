"""Parse script metadata out of the companion Markdown doc.

Admins document a script once in its .md file; neither the dashboard form
nor the API should make them retype it. Two documentation styles are
supported (all fields optional, zh or en):

Style A — key-value headings and bullet lists::

    # <title>                        → name fallback
    ## 名称: xxx  / ## Name: xxx     → name
    ## 版本: 1.2.0 / ## Version:     → version
    ## 描述: ...  / ## Description:  → description (inline or following paragraph)
    ## 分类: xxx  / ## Category:     → category
    ## 有效期开始: 2026-01-01         → valid_from (date or unix ts)
    ## 有效期结束: 2027-01-01         → valid_until
    ## 输入 / ## Inputs              → inputs JSON (list items, see below)
    ## 输出 / ## Outputs             → outputs JSON

    - reads: fastq, fq
    - samplesheet: csv, tsv, multiple

become ``[{"label": "reads", "extensions": ["fastq", "fq"], "multiple": false}]``.

Style B — Markdown tables (common in real script-library docs)::

    ## 基本信息
    | 字段 | 值 |
    | **Tool Name** | dada2 |        → name
    | **Description** | ... |        → description
    | 估算机时 | 8.0 小时 |            → runtime_min (hours → minutes)
    | 报价分数 | 14.4 |               → cost

    ## 输入
    | 文件名 | 描述 | 格式 | ... |
    | manifest.tsv | ... | TSV |     → {"label": "manifest.tsv", "extensions": ["tsv"]}
    | ${sample}.fastq | ... | FASTQ |→ extensions from the file suffix;
                                       a ${...} or * placeholder ⇒ multiple=true

Lenient fallbacks for docs that follow neither style exactly:

- the first free-text paragraph doubles as ``description``;
- a bare list item (``- paired-end reads``) under 输入/输出 becomes a slot
  that accepts any extension.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_HEADING_KV = re.compile(r"^#{1,3}\s*(?P<key>[^:：|]+)[:：]\s*(?P<val>.+?)\s*$")
_HEADING = re.compile(r"^(#{1,3})\s*(?P<key>[^:：|]+?)\s*$")
_LIST_ITEM = re.compile(r"^[-*]\s*(?P<label>[^:：|]+)[:：]\s*(?P<rest>.+?)\s*$")
_BARE_ITEM = re.compile(r"^[-*]\s*(?P<label>\S.*?)\s*$")
_HRULE = re.compile(r"^([-*_]\s*){3,}$")
_TABLE_SEP_CELL = re.compile(r"^:?-{2,}:?$")
_FILE_SUFFIX = re.compile(r"\.([A-Za-z0-9]+(?:\.[Gg][Zz])?)$")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_RUNTIME = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(小时|hours?|hrs?|h|分钟|mins?|m)?\b", re.IGNORECASE)

_KEY_ALIASES = {
    "名称": "name",
    "name": "name",
    "tool name": "name",
    "工具名": "name",
    "工具名称": "name",
    "版本": "version",
    "version": "version",
    "描述": "description",
    "description": "description",
    "分类": "category",
    "category": "category",
    "有效期开始": "valid_from",
    "valid_from": "valid_from",
    "valid from": "valid_from",
    "有效期结束": "valid_until",
    "valid_until": "valid_until",
    "valid until": "valid_until",
    "估算机时": "runtime_min",
    "机时": "runtime_min",
    "estimated runtime": "runtime_min",
    "报价分数": "cost",
    "报价": "cost",
    "cost": "cost",
}
_SECTION_ALIASES = {
    "输入": "inputs",
    "inputs": "inputs",
    "input": "inputs",
    "输出": "outputs",
    "outputs": "outputs",
    "output": "outputs",
}
_SLOT_HEADER_CELLS = {"文件名", "filename", "file name", "file", "文件"}
_FMT_HEADER_CELLS = {"格式", "format"}
_KV_HEADER_CELLS = {"字段", "字段名", "field", "key"}


@dataclass(frozen=True)
class ScriptMdMeta:
    """Metadata extracted from a script's Markdown doc (empty = not found)."""

    name: str = ""
    version: str = ""
    description: str = ""
    category: str = ""
    valid_from: int | None = None
    valid_until: int | None = None
    runtime_min: float | None = None
    cost: float | None = None
    inputs: str = "[]"
    outputs: str = "[]"


def _parse_date(raw: str) -> int | None:
    """'2026-01-01' / '2026/1/1' / unix ts → unix ts; None when unparseable."""
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    m = re.match(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
    if m:
        import datetime as dt

        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return int(dt.datetime(y, mo, d, tzinfo=dt.UTC).timestamp())
        except ValueError:
            return None
    return None


def _parse_runtime(raw: str) -> float | None:
    """'8.0 小时' → 480.0 minutes; '30' / '30 min' → 30.0; None when unparseable."""
    m = _RUNTIME.match(raw)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("小时", "h", "hr", "hrs", "hour", "hours"):
        return value * 60
    return value


def _parse_float(raw: str) -> float | None:
    m = _NUMBER.search(raw)
    return float(m.group(0)) if m else None


def _apply_kv(out: dict[str, Any], key: str, val: str) -> None:
    """Route one 'key: value' pair (heading or table row) into the result dict."""
    canonical = _KEY_ALIASES.get(key.strip().lower())
    if canonical is None:
        return
    if canonical in ("valid_from", "valid_until"):
        out[canonical] = _parse_date(val)
    elif canonical == "runtime_min":
        out[canonical] = _parse_runtime(val)
    elif canonical == "cost":
        out[canonical] = _parse_float(val)
    else:
        out[canonical] = val


def _slot_list(items: list[tuple[str, str]]) -> str:
    slots = []
    for label, rest in items:
        parts = [p.strip() for p in rest.split(",") if p.strip()]
        multiple = any(p.lower() in ("multiple", "多个", "可多选") for p in parts)
        extensions = [
            p.lstrip(".").lower() for p in parts if p.lower() not in ("multiple", "多个", "可多选")
        ]
        slots.append({"label": label.strip(), "extensions": extensions, "multiple": multiple})
    return json.dumps(slots, ensure_ascii=False)


def _table_cells(line: str) -> list[str]:
    return [c.strip().strip("*`").strip() for c in line.strip().strip("|").split("|")]


def _slot_from_row(label: str, fmt: str) -> tuple[str, str]:
    """Table row → (label, 'ext1, ext2[, multiple]') for _slot_list.

    Extensions come from the file suffix when the label has one
    ('feature-table.tsv' → tsv), else from the 格式 column. A ${...} or '*'
    placeholder in the label means the slot takes multiple files.
    """
    extensions = ""
    m = _FILE_SUFFIX.search(label)
    if m:
        extensions = m.group(1).lower().replace(".gz", "")
    elif fmt:
        extensions = ", ".join(
            p.lstrip(".").lower() for p in re.split(r"[/,、，]", fmt) if p.strip()
        )
    if "${" in label or "*" in label:
        extensions = f"{extensions}, multiple" if extensions else "multiple"
    return label, extensions


def parse_script_md(content: str) -> ScriptMdMeta:
    out: dict[str, Any] = {
        "name": "",
        "version": "",
        "description": "",
        "category": "",
        "valid_from": None,
        "valid_until": None,
        "runtime_min": None,
        "cost": None,
    }
    title = ""
    sections: dict[str, list[tuple[str, str]]] = {"inputs": [], "outputs": []}
    current_section: str | None = None
    description_lines: list[str] = []
    in_description = False
    prose_fallback = ""
    # table state: None = not in a table; otherwise the table kind
    table_kind: str | None = None  # "kv" | "slots" | "ignore"
    table_open = False
    file_idx = -1
    fmt_idx = -1

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("|"):
            cells = _table_cells(stripped)
            if cells and all(_TABLE_SEP_CELL.match(c) for c in cells):
                continue  # header separator row
            if not table_open:
                # first row of a table: classify by its header cells
                table_open = True
                norm = [c.lower() for c in cells]
                slot_cols = [i for i, c in enumerate(norm) if c in _SLOT_HEADER_CELLS]
                if slot_cols:
                    table_kind = "slots"
                    file_idx = slot_cols[0]
                    fmt_idx = next((i for i, c in enumerate(norm) if c in _FMT_HEADER_CELLS), -1)
                elif len(cells) >= 2 and norm[0] in _KV_HEADER_CELLS:
                    table_kind = "kv"
                else:
                    table_kind = "ignore"
                continue
            if table_kind == "kv" and len(cells) >= 2 and cells[0]:
                _apply_kv(out, cells[0], cells[1])
            elif table_kind == "slots" and current_section is not None and file_idx < len(cells):
                label = cells[file_idx]
                if label:
                    fmt = cells[fmt_idx] if 0 <= fmt_idx < len(cells) else ""
                    sections[current_section].append(_slot_from_row(label, fmt))
            continue

        # non-table line: any open table ends here
        table_open = False
        table_kind = None

        if not stripped or _HRULE.match(stripped):
            continue

        kv = _HEADING_KV.match(stripped)
        if kv:
            key = kv.group("key").strip()
            val = kv.group("val").strip()
            _apply_kv(out, key, val)
            in_description = key.lower() in ("描述", "description")
            current_section = None
            continue
        heading = _HEADING.match(stripped)
        if heading:
            key = heading.group("key").strip().lower()
            if len(heading.group(1)) == 1:
                # level-1 heading is the doc title (name fallback), not a section
                if not title:
                    title = heading.group("key").strip()
                current_section = None
                in_description = False
                continue
            current_section = _SECTION_ALIASES.get(key)
            in_description = key in ("描述", "description")
            continue
        item = _LIST_ITEM.match(stripped)
        if item and current_section is not None:
            sections[current_section].append((item.group("label"), item.group("rest")))
            continue
        bare = _BARE_ITEM.match(stripped)
        if bare and current_section is not None:
            # "- <text>" with no "label: exts" — slot that accepts any extension
            sections[current_section].append((bare.group("label"), ""))
            continue
        if in_description and stripped:
            description_lines.append(stripped)
        elif (
            stripped and current_section is None and not prose_fallback and stripped[0] not in "-*"
        ):
            # lenient fallback: first free-text paragraph doubles as description
            prose_fallback = stripped

    description = out["description"]
    if not description and description_lines:
        description = " ".join(description_lines)[:500]
    if not description:
        description = prose_fallback[:500]
    return ScriptMdMeta(
        name=out["name"] or title,
        version=out["version"],
        description=description,
        category=out["category"],
        valid_from=out["valid_from"],
        valid_until=out["valid_until"],
        runtime_min=out["runtime_min"],
        cost=out["cost"],
        inputs=_slot_list(sections["inputs"]),
        outputs=_slot_list(sections["outputs"]),
    )


__all__ = ["ScriptMdMeta", "parse_script_md"]
