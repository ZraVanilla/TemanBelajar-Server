"""
Dashboard web untuk TemanBelajar.
Database + API endpoint untuk monitoring sesi belajar.
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = "data/temanbelajar.db"


def init_db():
    """Inisialisasi database SQLite."""
    Path("data").mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            level TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sesi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER DEFAULT 1,
            pertanyaan TEXT,
            jawaban TEXT,
            durasi_ms REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Insert default profile if none
    count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    if count == 0:
        conn.execute("INSERT INTO profiles (nama, level) VALUES (?, ?)", ("Anak", "SD"))
    conn.commit()
    conn.close()


def save_sesi(pertanyaan: str, jawaban: str, durasi: float):
    """Simpan sesi belajar ke database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO sesi (pertanyaan, jawaban, durasi_ms) VALUES (?, ?, ?)",
        (pertanyaan, jawaban, int(durasi * 1000)),
    )
    conn.commit()
    conn.close()


def get_overview() -> dict:
    """Ambil ringkasan statistik."""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM sesi").fetchone()[0]
    total_durasi = conn.execute(
        "SELECT COALESCE(SUM(durasi_ms), 0) FROM sesi"
    ).fetchone()[0]
    hari_ini = conn.execute(
        "SELECT COUNT(*) FROM sesi WHERE date(created_at) = date('now')"
    ).fetchone()[0]
    conn.close()
    return {
        "total_sesi": total,
        "sesi_hari_ini": hari_ini,
        "total_durasi_menit": round(total_durasi / 60000, 1),
    }


def get_history(limit: int = 30, profile_id: int = None) -> list:
    """Ambil riwayat sesi belajar."""
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT s.id, p.nama, p.level, s.pertanyaan, s.jawaban,
               s.durasi_ms, s.created_at
        FROM sesi s JOIN profiles p ON s.profile_id = p.id
    """
    params = []
    if profile_id:
        query += " WHERE s.profile_id = ?"
        params.append(profile_id)
    query += " ORDER BY s.id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "nama": r[1],
            "level": r[2],
            "pertanyaan": r[3],
            "jawaban": r[4],
            "durasi_ms": r[5],
            "waktu": r[6],
        }
        for r in rows
    ]


def get_profiles() -> list:
    """Ambil semua profil."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, nama, level, created_at FROM profiles ORDER BY id").fetchall()
    conn.close()
    return [{"id": r[0], "nama": r[1], "level": r[2], "created_at": r[3]} for r in rows]


def create_profile(nama: str, level: str) -> int:
    """Buat profil baru."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO profiles (nama, level) VALUES (?, ?)", (nama, level))
    conn.commit()
    profile_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return profile_id