"""Bio script folder tree — one row per folder (BioFlow integration)."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, partial_updates


@dataclass(frozen=True)
class BioScriptFolderRow:
    id: str
    name: str
    parent_id: str
    sort_order: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, r: DbRow) -> BioScriptFolderRow:
        return cls(
            id=r["id"],
            name=r["name"],
            parent_id=r["parent_id"],
            sort_order=int(r["sort_order"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


class BioScriptFolderRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_all(self) -> list[BioScriptFolderRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bio_script_folders ORDER BY sort_order, name"
            ).fetchall()
        return map_rows(rows, BioScriptFolderRow)

    def get(self, folder_id: str) -> BioScriptFolderRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM bio_script_folders WHERE id = ?", (folder_id,)
            ).fetchone()
        return BioScriptFolderRow.from_row(r) if r else None

    def create(
        self,
        *,
        id: str,
        name: str,
        parent_id: str = "",
        sort_order: int = 0,
    ) -> BioScriptFolderRow:
        ts = str(now_ts())
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO bio_script_folders("
                "id, name, parent_id, sort_order, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (id, name, parent_id, sort_order, ts, ts),
            )
        row = self.get(id)
        if row is None:
            raise RuntimeError(f"bio script folder insert failed: {id}")
        return row

    def update(
        self,
        folder_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        sort_order: int | None = None,
    ) -> None:
        fields, params = partial_updates(
            [
                ("name", name),
                ("parent_id", parent_id),
                ("sort_order", sort_order),
            ]
        )
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(str(now_ts()))
        params.append(folder_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE bio_script_folders SET {', '.join(fields)} WHERE id = ?",
                params,
            )

    def delete(self, folder_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM bio_script_folders WHERE id = ?", (folder_id,))
