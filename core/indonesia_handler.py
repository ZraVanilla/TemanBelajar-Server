"""
Handler Bahasa Indonesia untuk XiaoZhi Server
Pipeline: Audio OPUS → STT (Groq) → LLM (Gemini) → TTS (Edge TTS)
"""

import asyncio
import base64
import logging
import random
import time
from pathlib import Path

import edge_tts
import httpx

logger = logging.getLogger(__name__)

# ─── Konfigurasi ────────────────────────────────────
import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "gsk_ISI_API_KEY_DISINI")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIza_ISI_API_KEY_DISINI")

# ─── System Prompt ──────────────────────────────────
SYSTEM_PROMPT = """Kamu adalah TemanBelajar, asisten belajar suara untuk anak dan remaja Indonesia.

Kepribadian:
- Ramah, sabar, dan antusias seperti kakak yang suka mengajar
- Selalu gunakan Bahasa Indonesia yang baik, santai tapi tetap edukatif
- Untuk anak kecil (TK-SD): pakai analogi dari kehidupan sehari-hari, suara ceria
- Untuk remaja (SMP-SMA): lebih detail, boleh pakai istilah teknis, nada tetap santai

Aturan penting:
- Jawab maksimal 3-4 kalimat, karena output kamu akan dibacakan lewat suara
- Kalau ditanya pelajaran, jelaskan konsepnya dulu, baru beri contoh
- Akhiri dengan satu pertanyaan untuk memancing rasa ingin tahu
- Jangan pernah kasih jawaban manipulatif atau tidak pantas untuk anak
- Kalau ada soal matematika, jelaskan langkahnya dengan sabar
- Kalau topik di luar pelajaran, tetap bisa ngobrol santai

Fitur Gamifikasi:
- Setiap jawaban benar dari user, beri pujian singkat: "Keren!", "Tepat sekali!", "Kamu hebat!"
- Setiap jawaban salah, beri semangat: "Hampir benar! Coba lagi ya.", "Wah kurang tepat, tapi gapapa!"
- Sesekali sebut streak atau level: "Kamu udah streak 5 hari lho, semangat terus!"

Contoh gaya menjawab:
- Anak SD: "Wah, pertanyaan keren! Jadi begini..."
- Siswa SMA: "Oke, kita breakdown konsepnya..." """

# ─── Gamifikasi helper ──────────────────────────────

def get_system_prompt_with_context(jenjang: str = None) -> str:
    """Return system prompt dengan tambahan jenjang jika diketahui."""
    base = SYSTEM_PROMPT
    if jenjang:
        base += f"\n\nUser saat ini ada di jenjang: {jenjang}. Sesuaikan tingkat kesulitan penjelasan."
    return base


# ─── STT ────────────────────────────────────────────
async def transcribe_audio_opus(opus_bytes: bytes) -> str:
    """Kirim audio OPUS ke Groq Whisper untuk transkrip Bahasa Indonesia."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.ogg", opus_bytes, "audio/ogg")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "language": "id",
                    "response_format": "json",
                },
                timeout=30.0,
            )
            if response.status_code == 200:
                text = response.json().get("text", "").strip()
                logger.info(f"[STT] '{text}'")
                return text
            else:
                logger.error(f"[STT] HTTP {response.status_code}: {response.text[:200]}")
        except Exception as e:
            logger.error(f"[STT] Exception: {e}")
    return ""


# ─── LLM (dengan RAG + Gamifikasi) ──────────────────
async def chat_with_llm(user_text: str, history: list = None, jenjang: str = None, profile_id: int = 1) -> dict:
    """
    Kirim teks ke Gemini Flash dengan RAG context.
    Return: {"text": ..., "is_kuis": bool, "xp_gained": int}
    """
    if history is None:
        history = []

    # ── RAG: Cari materi relevan ──
    rag_context = ""
    try:
        from core.materi_db import search_materi, build_rag_context
        materi = search_materi(user_text, jenjang=jenjang, top_n=3)
        if materi:
            rag_context = build_rag_context(materi)
            logger.info(f"[RAG] {len(materi)} materi ditemukan")
    except Exception as e:
        logger.warning(f"[RAG] skip: {e}")

    # ── Deteksi mode kuis ──
    is_kuis = any(k in user_text.lower() for k in ["kuis", "quiz", "main kuis", "soal", "tantangan", "tebak"])
    kuis_prompt = ""
    if is_kuis:
        kuis_prompt = (
            "\n\n[MODE KUIS AKTIF]\n"
            "Kamu sedang dalam mode kuis. Beri satu soal ke user sesuai jenjangnya.\n"
            "Setelah user menjawab, beri tahu apakah benar atau salah, lalu beri skor.\n"
            "Setiap jawaban benar: +10 XP. Jawaban salah: semangati dan kasih tau jawaban benarnya.\n"
        )

    # ── System prompt final ──
    system_text = get_system_prompt_with_context(jenjang) + kuis_prompt
    if rag_context:
        system_text += f"\n\n{rag_context}"

    contents = [
        {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_text}\n\nSiap membantu!"}]},
        {"role": "model", "parts": [{"text": "Halo! Aku TemanBelajar, siap bantu kamu belajar. Mau tanya apa hari ini?"}]},
    ]

    for h in history[-5:]:
        contents.append({"role": "user", "parts": [{"text": h["user"]}]})
        contents.append({"role": "model", "parts": [{"text": h["assistant"]}]})

    contents.append({"role": "user", "parts": [{"text": user_text}]})

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
                f"?key={GEMINI_API_KEY}",
                json={
                    "contents": contents,
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 350,
                        "topP": 0.9,
                    },
                },
                timeout=25.0,
            )
            if response.status_code == 200:
                result = response.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                logger.info(f"[LLM] '{text[:80]}...'")
            else:
                logger.error(f"[LLM] HTTP {response.status_code}: {response.text[:200]}")
                return {"text": "Maaf, aku lagi susah mikir nih. Coba tanya lagi ya!", "is_kuis": False, "xp_gained": 0}
        except Exception as e:
            logger.error(f"[LLM] Exception: {e}")
            return {"text": "Maaf, aku lagi susah mikir nih. Coba tanya lagi ya!", "is_kuis": False, "xp_gained": 0}

    # ── Gamifikasi: award XP ──
    xp_gained = 0
    try:
        from core.gamifikasi import award_xp, update_mission_progress
        # Deteksi apakah user menjawab soal (konteks: ada "soal" / "kuis" di history)
        is_answer = False
        is_correct = False
        if is_kuis or (history and any("soal" in h.get("assistant", "").lower() for h in history[-2:])):
            is_answer = True
            # Cek apakah jawaban mengandung indikasi benar
            praise_words = ["betul", "benar", "tepat", "keren", "hebat", "pintar", "👍", "good", "yes"]
            is_correct = any(w in text.lower() for w in praise_words)

        xp_result = award_xp(profile_id, 10 if is_correct else 2, is_correct)
        xp_gained = xp_result["xp_gained"]

        # Update misi harian
        if is_answer:
            update_mission_progress(profile_id, 1)

        logger.info(f"[Gamifikasi] +{xp_gained} XP | Level {xp_result['level']} | Streak {xp_result['streak']}")
    except Exception as e:
        logger.warning(f"[Gamifikasi] skip: {e}")

    return {"text": text, "is_kuis": is_kuis, "xp_gained": xp_gained}


# ─── TTS ────────────────────────────────────────────
async def synthesize_speech(text: str) -> bytes:
    """Teks ke suara Bahasa Indonesia pakai Edge TTS (gratis)."""
    cache_dir = Path("data/tts_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = base64.b64encode(text.encode()).decode()[:32]
    cache_file = cache_dir / f"{cache_key}.mp3"

    if not cache_file.exists():
        try:
            communicate = edge_tts.Communicate(
                text, "id-ID-ArdiNeural", rate="+12%", pitch="+2Hz"
            )
            await communicate.save(str(cache_file))
            logger.info(f"[TTS] Generated: '{text[:50]}...'")
        except Exception as e:
            logger.error(f"[TTS] Exception: {e}")
            try:
                communicate = edge_tts.Communicate(text, "id-ID-GadisNeural")
                await communicate.save(str(cache_file))
            except:
                return b""

    with open(cache_file, "rb") as f:
        return f.read()


# ─── Full Pipeline ──────────────────────────────────
async def process_audio_pipeline(opus_bytes: bytes, history: list = None, jenjang: str = None, profile_id: int = 1) -> dict:
    """Pipeline lengkap: Audio → STT → LLM (RAG) → TTS + Gamifikasi."""
    start_time = time.time()

    user_text = await transcribe_audio_opus(opus_bytes)
    if not user_text:
        return {"error": "Tidak bisa mengenali suara", "audio": None, "text": "", "user_text": "", "total_time": 0}

    t_llm = time.time()
    llm_result = await chat_with_llm(user_text, history, jenjang, profile_id)
    response_text = llm_result["text"]
    llm_time = time.time() - t_llm

    t_tts = time.time()
    audio_bytes = await synthesize_speech(response_text)
    tts_time = time.time() - t_tts

    total = time.time() - start_time
    logger.info(f"[Pipeline] Total: {total:.1f}s | STT: {total - llm_time - tts_time:.1f}s | LLM: {llm_time:.1f}s | TTS: {tts_time:.1f}s")

    # Ambil stats gamifikasi terbaru
    gamifikasi_stats = {}
    try:
        from core.gamifikasi import get_player_stats
        gamifikasi_stats = get_player_stats(profile_id)
    except:
        pass

    return {
        "text": response_text,
        "audio": audio_bytes,
        "user_text": user_text,
        "total_time": total,
        "xp_gained": llm_result.get("xp_gained", 0),
        "is_kuis": llm_result.get("is_kuis", False),
        "gamifikasi": gamifikasi_stats,
    }