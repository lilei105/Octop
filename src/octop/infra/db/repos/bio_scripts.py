"""Bio script library entries — one row per analysis script (BioFlow integration)."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts, optional_updates


@dataclass(frozen=True)
class BioScriptRow:
    id: str
    folder_id: str
    name: str
    description: str
    tool_id: str
    category: str
    version: str
    file_path: str
    md_content: str
    inputs: str
    outputs: str
    runtime_min: float
    cost: float
    weight: float
    verified: bool
    is_active: bool
    valid_from: int | None
    valid_until: int | None
    uploaded_by: int
    verified_by: int | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, r: DbRow) -> BioScriptRow:
        return cls(
            id=r["id"],
            folder_id=r["folder_id"],
            name=r["name"],
            description=r["description"],
            tool_id=r["tool_id"],
            category=r["category"],
            version=r["version"],
            file_path=r["file_path"],
            md_content=r["md_content"],
            inputs=r["inputs"],
            outputs=r["outputs"],
            runtime_min=float(r["runtime_min"]),
            cost=float(r["cost"]),
            weight=float(r["weight"]),
            verified=bool(r["verified"]),
            is_active=bool(r["is_active"]),
            valid_from=int(r["valid_from"]) if r["valid_from"] is not None else None,
            valid_until=int(r["valid_until"]) if r["valid_until"] is not None else None,
            uploaded_by=int(r["uploaded_by"]),
            verified_by=int(r["verified_by"]) if r["verified_by"] is not None else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


class BioScriptRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_all(self, *, folder_id: str | None = None) -> list[BioScriptRow]:
        with self._db.connect() as conn:
            if folder_id is None:
                rows = conn.execute(
                    "SELECT * FROM bio_scripts ORDER BY weight DESC, name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bio_scripts WHERE folder_id = ? ORDER BY weight DESC, name",
                    (folder_id,),
                ).fetchall()
        return map_rows(rows, BioScriptRow)

    def get(self, script_id: str) -> BioScriptRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM bio_scripts WHERE id = ?", (script_id,)).fetchone()
        return BioScriptRow.from_row(r) if r else None

    def create(
        self,
        *,
        id: str,
        name: str,
        folder_id: str = "",
        description: str = "",
        tool_id: str = "",
        category: str = "",
        version: str = "1.0.0",
        file_path: str = "",
        md_content: str = "",
        inputs: str = "[]",
        outputs: str = "[]",
        runtime_min: float = 0,
        cost: float = 0,
        weight: float = 0,
        valid_from: int | None = None,
        valid_until: int | None = None,
        uploaded_by: int = 0,
    ) -> BioScriptRow:
        ts = str(now_ts())
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO bio_scripts("
                "id, folder_id, name, description, tool_id, category, version, file_path,"
                " md_content, inputs, outputs, runtime_min, cost, weight, verified, is_active,"
                " valid_from, valid_until, uploaded_by, verified_by, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, NULL, ?, ?)",
                (
                    id,
                    folder_id,
                    name,
                    description,
                    tool_id,
                    category,
                    version,
                    file_path,
                    md_content,
                    inputs,
                    outputs,
                    runtime_min,
                    cost,
                    weight,
                    valid_from,
                    valid_until,
                    uploaded_by,
                    ts,
                    ts,
                ),
            )
        row = self.get(id)
        if row is None:
            raise RuntimeError(f"bio script insert failed: {id}")
        return row

    def update(self, script_id: str, **changes: object) -> None:
        """Patch arbitrary columns; keys must be bio_scripts column names."""
        allowed = {
            "folder_id",
            "name",
            "description",
            "tool_id",
            "category",
            "version",
            "file_path",
            "md_content",
            "inputs",
            "outputs",
            "runtime_min",
            "cost",
            "weight",
            "valid_from",
            "valid_until",
        }
        fields, params = optional_updates([(k, v) for k, v in changes.items() if k in allowed])
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(str(now_ts()))
        params.append(script_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE bio_scripts SET {', '.join(fields)} WHERE id = ?",
                params,
            )

    def set_verified(self, script_id: str, *, verified: bool, verified_by: int | None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bio_scripts SET verified = ?, verified_by = ?, updated_at = ? WHERE id = ?",
                (bool_int(verified), verified_by, str(now_ts()), script_id),
            )

    def set_active(self, script_id: str, *, is_active: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bio_scripts SET is_active = ?, updated_at = ? WHERE id = ?",
                (bool_int(is_active), str(now_ts()), script_id),
            )

    def delete(self, script_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM bio_scripts WHERE id = ?", (script_id,))
