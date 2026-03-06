"""Database utilities - simplified for MCMRelay (uses pubkey_prefix as node id)."""
import sqlite3
import os

from mcmrelay.config import get_base_dir
from mcmrelay.log_utils import get_logger

logger = get_logger(name="db")


def get_db_path():
    base = os.path.join(get_base_dir(), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "meshcore.sqlite")


def get_longname(node_id):
    """Get long name for node (pubkey_prefix or node id)."""
    conn = None
    try:
        conn = sqlite3.connect(get_db_path())
        cur = conn.execute(
            "SELECT longname FROM nodes WHERE node_id = ?", (str(node_id),)
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.debug(f"get_longname error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_shortname(node_id):
    """Get short name for node."""
    conn = None
    try:
        conn = sqlite3.connect(get_db_path())
        cur = conn.execute(
            "SELECT shortname FROM nodes WHERE node_id = ?", (str(node_id),)
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.debug(f"get_shortname error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def save_longname(node_id, longname):
    conn = None
    try:
        conn = sqlite3.connect(get_db_path())
        short = longname[:8] if longname else str(node_id)[:8]
        conn.execute(
            """INSERT INTO nodes (node_id, longname, shortname) VALUES (?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET longname=excluded.longname, shortname=COALESCE(excluded.shortname, shortname)""",
            (str(node_id), longname, short),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"save_longname error: {e}")
    finally:
        if conn:
            conn.close()


def save_shortname(node_id, shortname):
    conn = None
    try:
        conn = sqlite3.connect(get_db_path())
        longname = get_longname(node_id) or str(node_id)
        conn.execute(
            """INSERT INTO nodes (node_id, longname, shortname) VALUES (?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET shortname=excluded.shortname""",
            (str(node_id), longname, shortname or str(node_id)[:8]),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"save_shortname error: {e}")
    finally:
        if conn:
            conn.close()


def initialize_database():
    conn = None
    try:
        conn = sqlite3.connect(get_db_path())
        conn.execute(
            """CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                longname TEXT,
                shortname TEXT
            )"""
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"initialize_database error: {e}")
    finally:
        if conn:
            conn.close()
