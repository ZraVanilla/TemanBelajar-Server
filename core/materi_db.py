"""
Database materi pembelajaran per jenjang (TK - SMA).
Digunakan untuk RAG (Retrieval-Augmented Generation):
server mencari materi relevan dulu, baru kirim sebagai konteks ke LLM.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = "data/temanbelajar.db"
MATERI_DIR = Path("data/materi")


def init_materi():
    """Buat tabel materi + insert data awal."""
    MATERI_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS materi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jenjang TEXT NOT NULL,
            mapel TEXT NOT NULL,
            topik TEXT NOT NULL,
            sub_topik TEXT DEFAULT '',
            konten TEXT NOT NULL,
            contoh_soal TEXT DEFAULT '[]',
            level_kesulitan INTEGER DEFAULT 1,
            tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Cek apakah sudah ada data
    count = conn.execute("SELECT COUNT(*) FROM materi").fetchone()[0]
    if count > 0:
        conn.close()
        return

    # Insert data awal
    data = _get_default_materi()
    for m in data:
        conn.execute("""
            INSERT INTO materi (jenjang, mapel, topik, sub_topik, konten, contoh_soal, level_kesulitan, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m["jenjang"], m["mapel"], m["topik"], m["sub_topik"],
            m["konten"], json.dumps(m.get("contoh_soal", [])),
            m.get("level_kesulitan", 1), m.get("tags", "")
        ))
    conn.commit()
    conn.close()


def search_materi(query: str, jenjang: str = None, top_n: int = 3) -> list:
    """
    Cari materi yang relevan dengan query user.
    Pakai LIKE sederhana untuk prototype, bisa upgrade ke FTS5 nanti.
    """
    conn = sqlite3.connect(DB_PATH)
    terms = query.lower().split()

    # Bangun query dengan multiple LIKE
    conditions = []
    params = []
    for term in terms:
        if len(term) >= 2:  # abaikan kata 1 huruf
            conditions.append(
                "(LOWER(konten) LIKE ? OR LOWER(topik) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(sub_topik) LIKE ?)"
            )
            like = f"%{term}%"
            params.extend([like, like, like, like])

    if not conditions:
        conn.close()
        return []

    sql = f"SELECT jenjang, mapel, topik, sub_topik, konten, contoh_soal, tags FROM materi WHERE {' AND '.join(conditions)}"

    if jenjang:
        sql += " AND jenjang = ?"
        params.append(jenjang)

    sql += " ORDER BY level_kesulitan ASC LIMIT ?"
    params.append(top_n)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [
        {
            "jenjang": r[0], "mapel": r[1], "topik": r[2],
            "sub_topik": r[3], "konten": r[4],
            "contoh_soal": json.loads(r[5]) if r[5] else [],
            "tags": r[6],
        }
        for r in rows
    ]


def get_materi_by_jenjang(jenjang: str, mapel: str = None) -> list:
    """Ambil semua materi untuk jenjang tertentu."""
    conn = sqlite3.connect(DB_PATH)
    if mapel:
        rows = conn.execute(
            "SELECT id, jenjang, mapel, topik, sub_topik, level_kesulitan FROM materi WHERE jenjang=? AND mapel=? ORDER BY level_kesulitan",
            (jenjang, mapel)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, jenjang, mapel, topik, sub_topik, level_kesulitan FROM materi WHERE jenjang=? ORDER BY mapel, level_kesulitan",
            (jenjang,)
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "jenjang": r[1], "mapel": r[2], "topik": r[3], "sub_topik": r[4], "level": r[5]}
        for r in rows
    ]


def build_rag_context(materi_list: list) -> str:
    """Bangun konteks RAG untuk dikirim ke LLM."""
    if not materi_list:
        return ""

    ctx_parts = ["[MATERI REFERENSI DARI DATABASE]"]
    for i, m in enumerate(materi_list, 1):
        ctx_parts.append(
            f"\n{i}. [{m['jenjang']}] {m['mapel']} - {m['topik']}"
            f"{' > ' + m['sub_topik'] if m['sub_topik'] else ''}\n"
            f"{m['konten'][:600]}"
        )
        if m["contoh_soal"]:
            ctx_parts.append(f"Contoh soal: {json.dumps(m['contoh_soal'], ensure_ascii=False)[:300]}")

    return "\n".join(ctx_parts)


# ─── Data Default ───────────────────────────────────

def _get_default_materi() -> list:
    """Materi bawaan untuk demo. Bisa ditambah sendiri."""
    return [
        # TK
        {
            "jenjang": "TK", "mapel": "Kognitif", "topik": "Mengenal Angka 1-10",
            "sub_topik": "Berhitung", "level_kesulitan": 1,
            "tags": "angka, berhitung, matematika dasar, tk",
            "konten": "Angka 1 sampai 10 adalah dasar berhitung. Satu (1) seperti satu buah apel. Dua (2) seperti dua mata. Tiga (3) seperti roda becak. Empat (4) seperti kaki meja. Lima (5) seperti jari tangan. Enam (6), Tujuh (7), Delapan (8), Sembilan (9), Sepuluh (10). Ajak anak menghitung benda di sekitar: 'Coba hitung ada berapa sendok di meja?'",
            "contoh_soal": [
                {"tanya": "Ada 3 apel, ditambah 2 apel. Jadi berapa?", "jawab": "5 apel"},
                {"tanya": "Sebutkan angka setelah 7!", "jawab": "8"},
            ]
        },
        {
            "jenjang": "TK", "mapel": "Kognitif", "topik": "Mengenal Warna",
            "sub_topik": "Warna Dasar", "level_kesulitan": 1,
            "tags": "warna, pelangi, merah, biru, kuning, tk",
            "konten": "Warna dasar ada merah, kuning, dan biru. Merah seperti apel dan stroberi. Kuning seperti matahari dan pisang. Biru seperti langit dan laut. Kalau campur kuning dan biru jadi hijau seperti daun. Ajak anak: 'Coba cari benda warna merah di sekitar kamu!'",
            "contoh_soal": [
                {"tanya": "Warna apa daun pepaya?", "jawab": "Hijau"},
                {"tanya": "Campuran warna merah dan kuning jadi apa?", "jawab": "Oranye / Jingga"},
            ]
        },
        # SD Kelas 1-3
        {
            "jenjang": "SD", "mapel": "Matematika", "topik": "Penjumlahan dan Pengurangan",
            "sub_topik": "Penjumlahan 1-20", "level_kesulitan": 2,
            "tags": "tambah, kurang, matematika, sd, penjumlahan, pengurangan",
            "konten": "Penjumlahan adalah menggabungkan dua bilangan. Contoh: 5 + 3 = 8. Bisa dibayangkan: punya 5 permen, dikasih 3 permen lagi, jadi 8 permen. Pengurangan adalah mengambil sebagian. Contoh: 10 - 4 = 6. Seperti punya 10 kelereng, hilang 4, sisa 6. Gunakan jari atau benda sekitar untuk membantu menghitung.",
            "contoh_soal": [
                {"tanya": "7 + 6 = ?", "jawab": "13"},
                {"tanya": "15 - 8 = ?", "jawab": "7"},
                {"tanya": "Ibu punya 12 telur, dipakai 5. Sisa berapa?", "jawab": "7 telur"},
            ]
        },
        {
            "jenjang": "SD", "mapel": "IPA", "topik": "Bagian Tubuh dan Fungsinya",
            "sub_topik": "Panca Indera", "level_kesulitan": 2,
            "tags": "tubuh, mata, telinga, hidung, lidah, kulit, sd, ipa",
            "konten": "Manusia punya lima indera (panca indera): 1. Mata untuk melihat. 2. Telinga untuk mendengar. 3. Hidung untuk mencium bau. 4. Lidah untuk mengecap rasa (manis, asin, asam, pahit). 5. Kulit untuk meraba (panas, dingin, kasar, halus). Otak mengolah semua informasi dari panca indera.",
            "contoh_soal": [
                {"tanya": "Apa fungsi telinga?", "jawab": "Untuk mendengar suara"},
                {"tanya": "Berapa jumlah panca indera manusia?", "jawab": "Lima"},
            ]
        },
        # SD Kelas 4-6
        {
            "jenjang": "SD", "mapel": "IPA", "topik": "Tata Surya",
            "sub_topik": "Planet-planet", "level_kesulitan": 3,
            "tags": "planet, matahari, bumi, tata surya, merkurius, venus, mars, jupiter, saturnus, sd, ipa",
            "konten": "Tata surya terdiri dari Matahari sebagai pusat dan 8 planet yang mengelilinginya. Urutan dari terdekat: Merkurius, Venus, Bumi, Mars, Jupiter, Saturnus, Uranus, Neptunus. Jupiter adalah planet terbesar. Saturnus punya cincin indah. Bumi adalah satu-satunya planet yang diketahui punya kehidupan. Planet berotasi (berputar pada sumbunya) dan berevolusi (mengelilingi matahari).",
            "contoh_soal": [
                {"tanya": "Planet apa yang paling dekat dengan Matahari?", "jawab": "Merkurius"},
                {"tanya": "Planet apa yang punya cincin?", "jawab": "Saturnus"},
                {"tanya": "Kenapa Bumi bisa dihuni makhluk hidup?", "jawab": "Karena punya air, udara, dan suhu yang cocok"},
            ]
        },
        {
            "jenjang": "SD", "mapel": "Bahasa Indonesia", "topik": "Jenis-Jenis Puisi",
            "sub_topik": "Pantun", "level_kesulitan": 3,
            "tags": "pantun, puisi, sajak, rima, sd, bahasa",
            "konten": "Pantun adalah puisi lama Melayu yang terdiri dari 4 baris. Baris 1 dan 2 disebut sampiran (pembayang), baris 3 dan 4 disebut isi. Pola rima pantun adalah a-b-a-b (baris 1 dan 3 sama bunyi akhir, baris 2 dan 4 sama). Contoh: 'Jalan-jalan ke kota Medan / Jangan lupa beli durian / Kalau kamu rajin belajar / Pasti jadi anak teladan'. Pantun biasanya 8-12 suku kata per baris.",
            "contoh_soal": [
                {"tanya": "Buatlah satu bait pantun dengan tema persahabatan!", "jawab": "Contoh: Burung merpati terbang tinggi / Hinggap sebentar di pohon cemara / Sahabat sejati tak akan pergi / Selalu ada dalam suka dan duka"},
            ]
        },
        # SMP
        {
            "jenjang": "SMP", "mapel": "IPA", "topik": "Fotosintesis",
            "sub_topik": "Proses dan Reaksi", "level_kesulitan": 3,
            "tags": "fotosintesis, tumbuhan, klorofil, glukosa, oksigen, karbon dioksida, smp, ipa, biologi",
            "konten": "Fotosintesis adalah proses tumbuhan mengubah karbon dioksida (CO2) dan air (H2O) menjadi glukosa (C6H12O6) dan oksigen (O2) menggunakan energi cahaya matahari. Reaksi: 6CO2 + 6H2O + cahaya → C6H12O6 + 6O2. Terjadi di kloroplas yang mengandung klorofil (zat hijau daun). Faktor yang mempengaruhi: intensitas cahaya, konsentrasi CO2, suhu, ketersediaan air. Fotosintesis penting karena menghasilkan oksigen yang kita hirup dan menjadi dasar rantai makanan.",
            "contoh_soal": [
                {"tanya": "Apa hasil dari fotosintesis?", "jawab": "Glukosa (C6H12O6) dan Oksigen (O2)"},
                {"tanya": "Di bagian tumbuhan mana fotosintesis terjadi?", "jawab": "Di kloroplas pada daun, yang mengandung klorofil"},
            ]
        },
        {
            "jenjang": "SMP", "mapel": "Matematika", "topik": "Persamaan Linear Satu Variabel",
            "sub_topik": "Penyelesaian PLSV", "level_kesulitan": 3,
            "tags": "aljabar, persamaan, variabel, x, smp, matematika",
            "konten": "Persamaan Linear Satu Variabel (PLSV) adalah kalimat terbuka yang memuat satu variabel berpangkat 1 dan dihubungkan tanda '='. Bentuk umum: ax + b = 0. Cara menyelesaikan: 1. Kumpulkan suku yang memuat variabel di satu ruas. 2. Kumpulkan konstanta di ruas lain. 3. Bagi kedua ruas dengan koefisien variabel. Contoh: 3x + 5 = 14 → 3x = 14 - 5 → 3x = 9 → x = 3.",
            "contoh_soal": [
                {"tanya": "Tentukan x dari: 2x - 4 = 10", "jawab": "x = 7. Langkah: 2x - 4 = 10 → 2x = 14 → x = 7"},
                {"tanya": "Jika 5(x-2) = 3x + 4, berapa x?", "jawab": "x = 7. Langkah: 5x - 10 = 3x + 4 → 5x - 3x = 4 + 10 → 2x = 14 → x = 7"},
            ]
        },
        # SMA
        {
            "jenjang": "SMA", "mapel": "Fisika", "topik": "Hukum Newton",
            "sub_topik": "Hukum Newton 1, 2, dan 3", "level_kesulitan": 4,
            "tags": "newton, gaya, inersia, aksi reaksi, fisika, sma, mekanika",
            "konten": "Hukum Newton ada tiga: 1. Hukum Kelembaman (Inersia): ΣF = 0 → benda diam tetap diam, bergerak tetap bergerak lurus beraturan. 2. Hukum Gerak: ΣF = m × a. Percepatan benda berbanding lurus dengan resultan gaya dan berbanding terbalik dengan massa. 3. Hukum Aksi-Reaksi: F_aksi = -F_reaksi. Setiap aksi menimbulkan reaksi yang sama besar tapi berlawanan arah, bekerja pada benda yang berbeda. Contoh: saat mendorong dinding, dinding mendorong balik dengan gaya sama besar. Aplikasi: roket (gas ke bawah → roket ke atas), berjalan (kaki dorong tanah ke belakang → tanah dorong ke depan).",
            "contoh_soal": [
                {"tanya": "Benda 5 kg didorong gaya 20 N di lantai licin. Berapa percepatannya?", "jawab": "4 m/s². Rumus: a = F/m = 20/5"},
                {"tanya": "Jelaskan Hukum Newton 3 dengan contoh sehari-hari!", "jawab": "Saat kita berenang, tangan mendorong air ke belakang (aksi), air mendorong tubuh kita ke depan (reaksi)"},
            ]
        },
    ]