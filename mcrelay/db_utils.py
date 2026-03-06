"""Database utilities - simplified for MCRelay (uses pubkey_prefix as node id)."""
import sqlite3
import os

config = None


def get_db_path():
    base = os.path.expanduser("~/.mcrelay/data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "meshcore.sqlite")


def get_longname(node_id):
    """Get long name for node (pubkey_prefix or node id)."""
    try:
        conn = sqlite3.connect(get_db_path())
        cur = conn.execute(
            "SELECT longname FROM nodes WHERE node_id = ?", (str(node_id),)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def get_shortname(node_id):
    """Get short name for node."""
    try:
        conn = sqlite3.connect(get_db_path())
        cur = conn.execute(
            "SELECT shortname FROM nodes WHERE node_id = ?", (str(node_id),)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def save_longname(node_id, longname):
    try:
        conn = sqlite3.connect(get_db_path())
        short = longname[:8] if longname else str(node_id)[:8]
        conn.execute(
            """INSERT INTO nodes (node_id, longname, shortname) VALUES (?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET longname=excluded.longname, shortname=COALESCE(excluded.shortname, shortname)""",
            (str(node_id), longname, short),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def save_shortname(node_id, shortname):
    try:
        conn = sqlite3.connect(get_db_path())
        conn.execute(
            """INSERT INTO nodes (node_id, longname, shortname) VALUES (?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET shortname=excluded.shortname""",
            (str(node_id), str(node_id), shortname or str(node_id)[:8]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def initialize_database():
    conn = sqlite3.connect(get_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            longname TEXT,
            shortname TEXT
        )"""
    )
    conn.commit()
    conn.close()
