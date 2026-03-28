"""SQLite-backed metadata store for simple graph queries."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional


class GraphMetadataStore:
    """Persist and query graph metadata using SQLite."""

    def __init__(self, db_path: str = "metadata.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                label TEXT,
                properties TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                target TEXT,
                label TEXT,
                properties TEXT
            )
            """
        )
        self.conn.commit()

    # ------------------------------- nodes -------------------------------
    def create_node(
        self,
        node_id: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            (
                "INSERT OR REPLACE INTO nodes (id, label, properties) "
                "VALUES (?, ?, ?)"
            ),
            (node_id, label, json.dumps(properties or {})),
        )
        self.conn.commit()

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, label, properties FROM nodes WHERE id=?",
            (node_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "label": row[1],
            "properties": json.loads(row[2] or "{}"),
        }

    def update_node(
        self,
        node_id: str,
        label: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        node = self.get_node(node_id)
        if not node:
            return
        new_label = label or node["label"]
        new_props = node["properties"]
        if properties:
            new_props.update(properties)
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE nodes SET label=?, properties=? WHERE id=?",
            (new_label, json.dumps(new_props), node_id),
        )
        self.conn.commit()

    def delete_node(self, node_id: str) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        cur.execute(
            "DELETE FROM edges WHERE source=? OR target=?",
            (node_id, node_id),
        )
        self.conn.commit()

    # ------------------------------- edges -------------------------------
    def create_edge(
        self,
        source: str,
        target: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO edges (source, target, label, properties) "
            "VALUES (?, ?, ?, ?)",
            (source, target, label, json.dumps(properties or {})),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_neighbors(self, node_id: str) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, source, target, label, properties "
            "FROM edges WHERE source=? OR target=?",
            (node_id, node_id),
        )
        rows = cur.fetchall()
        neighbors: List[Dict[str, Any]] = []
        for row in rows:
            neighbors.append(
                {
                    "id": row[0],
                    "source": row[1],
                    "target": row[2],
                    "label": row[3],
                    "properties": json.loads(row[4] or "{}"),
                }
            )
        return neighbors

    def delete_edge(self, edge_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM edges WHERE id=?", (edge_id,))
        self.conn.commit()
