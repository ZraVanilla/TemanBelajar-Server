"""
Sistem gamifikasi TemanBelajar.
XP, Level, Streak, Badge, Daily Mission.
"""

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path

DB_PATH = "data/temanbelajar.db"


def init_gamifikasi():
    """Buat tabel gamifikasi."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gamifikasi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            last_seen DATE,
            badges TEXT DEFAULT '[]',
            total_jawaban INTEGER DEFAULT 0,
            jawaban_benar INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_mission (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER DEFAULT 1,
            mission_date DATE,
            mission_type TEXT,
            target INTEGER DEFAULT 5,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0
        )
    """)
    # Init default
    count = conn.execute("SELECT COUNT(*) FROM gamifikasi").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO gamifikasi (profile_id, xp, level, streak, last_seen) VALUES (1, 0, 1, 0, ?)",
            (date.today().isoformat(),)
        )
    conn.commit()
    conn.close()


def get_player_stats(profile_id: int = 1) -> dict:
    """Ambil statistik pemain."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT xp, level, streak, last_seen, badges, total_jawaban, jawaban_benar FROM gamifikasi WHERE profile_id=?",
        (profile_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"xp": 0, "level": 1, "streak": 0, "badges": [], "total_jawaban": 0, "jawaban_benar": 0}
    return {
        "xp": row[0],
        "level": row[1],
        "streak": row[2],
        "last_seen": row[3],
        "badges": json.loads(row[4]) if row[4] else [],
        "total_jawaban": row[5],
        "jawaban_benar": row[6],
        "xp_to_next_level": _xp_for_level(row[1] + 1) - row[0],
    }


def _xp_for_level(level: int) -> int:
    """XP yang dibutuhkan untuk mencapai level tertentu."""
    return 50 * level * level


def award_xp(profile_id: int = 1, amount: int = 10, is_correct: bool = True):
    """
    Tambah XP dan update level. Return dict update info.
    Dipanggil setiap kali anak selesai menjawab.
    """
    conn = sqlite3.connect(DB_PATH)

    # Ambil data sekarang
    row = conn.execute(
        "SELECT xp, level, streak, last_seen, total_jawaban, jawaban_benar FROM gamifikasi WHERE profile_id=?",
        (profile_id,)
    ).fetchone()

    xp, level, streak, last_seen, total_jwb, jwb_benar = row

    # Update streak
    today = date.today()
    if last_seen:
        last = datetime.strptime(last_seen, "%Y-%m-%d").date()
        diff = (today - last).days
        if diff == 1:
            streak += 1
        elif diff == 0:
            pass  # same day
        else:
            streak = 1
    else:
        streak = 1

    # Bonus XP untuk streak
    streak_bonus = min(streak * 2, 20)  # max +20 XP streak bonus
    total_xp = amount + (streak_bonus if is_correct else 0)

    # Update stats
    new_xp = xp + total_xp
    total_jwb += 1
    if is_correct:
        jwb_benar += 1

    # Cek level up
    new_level = level
    leveled_up = False
    while new_xp >= _xp_for_level(new_level + 1):
        new_level += 1
        leveled_up = True

    conn.execute(
        "UPDATE gamifikasi SET xp=?, level=?, streak=?, last_seen=?, total_jawaban=?, jawaban_benar=? WHERE profile_id=?",
        (new_xp, new_level, streak, today.isoformat(), total_jwb, jwb_benar, profile_id)
    )

    # Cek badge baru
    new_badges = _check_new_badges(conn, profile_id, new_level, streak, total_jwb, jwb_benar)

    conn.commit()
    conn.close()

    return {
        "xp_gained": total_xp,
        "total_xp": new_xp,
        "level": new_level,
        "leveled_up": leveled_up,
        "streak": streak,
        "streak_bonus": streak_bonus,
        "new_badges": new_badges,
        "xp_to_next": _xp_for_level(new_level + 1) - new_xp,
    }


# ─── Badge System ───────────────────────────────────

BADGE_DEFINITIONS = [
    {"id": "first_step", "name": "Langkah Pertama", "icon": "👣", "desc": "Sesi belajar pertama", "condition": "total_sesi >= 1"},
    {"id": "ten_sessions", "name": "Rajin Belajar", "icon": "📚", "desc": "10 sesi belajar", "condition": "total_sesi >= 10"},
    {"id": "fifty_sessions", "name": "Pelajar Sejati", "icon": "🎓", "desc": "50 sesi belajar", "condition": "total_sesi >= 50"},
    {"id": "level_5", "name": "Naik Kelas", "icon": "⬆️", "desc": "Mencapai level 5", "condition": "level >= 5"},
    {"id": "level_10", "name": "Master Cilik", "icon": "⭐", "desc": "Mencapai level 10", "condition": "level >= 10"},
    {"id": "streak_3", "name": "Semangat 3 Hari", "icon": "🔥", "desc": "Streak 3 hari", "condition": "streak >= 3"},
    {"id": "streak_7", "name": "Minggu Produktif", "icon": "💪", "desc": "Streak 7 hari", "condition": "streak >= 7"},
    {"id": "streak_30", "name": "Sebulan Penuh", "icon": "🏆", "desc": "Streak 30 hari", "condition": "streak >= 30"},
    {"id": "accuracy_80", "name": "Jago Jawab", "icon": "🎯", "desc": "Akurasi 80%+ (min 20 jawaban)", "condition": "accuracy >= 0.8"},
]


def _check_new_badges(conn, profile_id: int, level: int, streak: int, total: int, benar: int) -> list:
    """Cek badge baru yang unlocked."""
    row = conn.execute("SELECT badges FROM gamifikasi WHERE profile_id=?", (profile_id,)).fetchone()
    owned = set(json.loads(row[0]) if row[0] else [])
    new_badges = []

    total_sesi = conn.execute("SELECT COUNT(*) FROM sesi WHERE profile_id=?", (profile_id,)).fetchone()[0]
    accuracy = benar / total if total >= 20 else 0

    context = {
        "total_sesi": total_sesi, "level": level, "streak": streak,
        "total": total, "benar": benar, "accuracy": accuracy,
    }

    for badge in BADGE_DEFINITIONS:
        if badge["id"] in owned:
            continue
        try:
            if eval(badge["condition"], {"__builtins__": {}}, context):
                owned.add(badge["id"])
                new_badges.append(badge)
        except:
            pass

    if new_badges:
        conn.execute(
            "UPDATE gamifikasi SET badges=? WHERE profile_id=?",
            (json.dumps(list(owned)), profile_id)
        )

    return new_badges


# ─── Daily Mission ──────────────────────────────────

MISSION_TYPES = [
    {"type": "tanya_soal", "name": "Tanya Soal", "desc": "Ajukan {} pertanyaan ke TemanBelajar", "target": 3},
    {"type": "kuis_cepat", "name": "Kuis Cepat", "desc": "Selesaikan kuis {} soal dengan benar", "target": 5},
    {"type": "topik_baru", "name": "Jelajah Ilmu", "desc": "Tanyakan {} topik yang berbeda", "target": 3},
    {"type": "review", "name": "Ulang Kaji", "desc": "Ulangi {} soal dari sesi sebelumnya", "target": 3},
]


def get_daily_mission(profile_id: int = 1) -> dict:
    """Ambil atau generate misi harian."""
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT mission_type, target, progress, completed, claimed FROM daily_mission WHERE profile_id=? AND mission_date=?",
        (profile_id, today)
    ).fetchone()

    if row:
        conn.close()
        # Cari nama misi
        mtype = row[0]
        name = "Misi Harian"
        desc = f"Selesaikan {row[1]} tantangan"
        for mt in MISSION_TYPES:
            if mt["type"] == mtype:
                name = mt["name"]
                desc = mt["desc"].format(row[1])
                break
        return {
            "type": mtype, "name": name, "desc": desc,
            "target": row[1], "progress": row[2],
            "completed": bool(row[3]), "claimed": bool(row[4]),
        }

    # Generate misi baru
    import random
    mission = random.choice(MISSION_TYPES)
    conn.execute(
        "INSERT INTO daily_mission (profile_id, mission_date, mission_type, target) VALUES (?, ?, ?, ?)",
        (profile_id, today, mission["type"], mission["target"])
    )
    conn.commit()
    conn.close()

    return {
        "type": mission["type"], "name": mission["name"],
        "desc": mission["desc"].format(mission["target"]),
        "target": mission["target"], "progress": 0,
        "completed": False, "claimed": False,
    }


def update_mission_progress(profile_id: int = 1, increment: int = 1):
    """Tambah progress misi harian."""
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE daily_mission SET progress = progress + ? WHERE profile_id=? AND mission_date=? AND completed=0",
        (increment, profile_id, today)
    )
    # Auto-complete kalau progress >= target
    conn.execute(
        "UPDATE daily_mission SET completed=1 WHERE profile_id=? AND mission_date=? AND progress >= target",
        (profile_id, today)
    )
    conn.commit()
    conn.close()


def claim_mission_reward(profile_id: int = 1) -> dict:
    """Klaim reward misi harian yang sudah selesai."""
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT completed, claimed FROM daily_mission WHERE profile_id=? AND mission_date=?",
        (profile_id, today)
    ).fetchone()

    if not row or not row[0] or row[1]:
        conn.close()
        return {"claimed": False, "reason": "Misi belum selesai atau sudah diklaim"}

    # Tandai claimed dan kasih XP bonus
    conn.execute(
        "UPDATE daily_mission SET claimed=1 WHERE profile_id=? AND mission_date=?",
        (profile_id, today)
    )
    conn.execute(
        "UPDATE gamifikasi SET xp = xp + 80 WHERE profile_id=?",
        (profile_id,)
    )
    conn.commit()
    conn.close()

    return {"claimed": True, "xp_reward": 80, "message": "Misi selesai! +80 XP bonus!"}


# ─── Generate Quiz Voice ─────────────────────────────

def generate_kuis(jenjang: str, mapel: str = None, jumlah: int = 5) -> dict:
    """
    Generate kuis interaktif berbasis suara.
    Return format yang bisa langsung dipake di flow percakapan.
    """
    from core.materi_db import get_materi_by_jenjang

    materi = get_materi_by_jenjang(jenjang, mapel)
    if not materi:
        return {"intro": f"Maaf, belum ada materi untuk jenjang {jenjang}.", "soal": []}

    # Ambil soal dari materi
    all_soal = []
    conn = sqlite3.connect(DB_PATH)
    for m in materi[:10]:
        row = conn.execute("SELECT contoh_soal FROM materi WHERE id=?", (m["id"],)).fetchone()
        if row and row[0]:
            soal_list = json.loads(row[0])
            for s in soal_list:
                s["mapel"] = m["mapel"]
                s["topik"] = m["topik"]
                all_soal.append(s)
    conn.close()

    if not all_soal:
        return {"intro": "Belum ada soal untuk materi ini. Coba tanya materi bebas dulu ya!", "soal": []}

    import random
    selected = random.sample(all_soal, min(jumlah, len(all_soal)))

    intro_templates = [
        "Oke! Ayo main kuis {mapel}! Aku kasih {n} pertanyaan ya. Siap?",
        "Waktunya kuis {mapel} nih! {n} soal, kamu pasti bisa! Mulai ya!",
        "Tantangan {mapel} dimulai! Jawab {n} pertanyaan ini. Semangat!",
    ]
    intro = random.choice(intro_templates).format(
        mapel=mapel or "campuran", n=len(selected)
    )

    return {"intro": intro, "soal": selected}