"""
TemanBelajar Server
- WebSocket untuk ESP32 (protokol XiaoZhi)
- Dashboard web untuk monitoring
- Deploy ke Render / Railway / lokal
"""

import asyncio
import json
import logging
import os
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from core.indonesia_handler import process_audio_pipeline
from dashboard import init_db, save_sesi, get_overview, get_history, get_profiles, create_profile
from core.materi_db import init_materi, search_materi, get_materi_by_jenjang
from core.gamifikasi import (
    init_gamifikasi, get_player_stats, get_daily_mission,
    claim_mission_reward, generate_kuis
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("temanbelajar")

app = FastAPI(title="TemanBelajar Server")

# ─── Inisialisasi ───────────────────────────────────
init_db()
init_materi()
init_gamifikasi()
logger.info("TemanBelajar ready: DB + Materi + Gamifikasi loaded")

# ─── History per sesi WebSocket ─────────────────────
session_history: dict[str, list] = {}


# ══════════════════════════════════════════════════════
#  WebSocket untuk ESP32
# ══════════════════════════════════════════════════════

@app.websocket("/ws/device")
async def device_websocket(ws: WebSocket):
    await ws.accept()
    session_id = str(id(ws))[-8:]
    logger.info(f"[WS] ESP32 connected: {session_id}")

    keepalive_running = True

    async def keepalive():
        while keepalive_running:
            try:
                await asyncio.sleep(30)
                await ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    keepalive_task = asyncio.create_task(keepalive())

    current_profile = 1   # default profile
    current_jenjang = None  # auto-detect

    try:
        while True:
            opus_data = await ws.receive_bytes()

            if len(opus_data) < 200:
                continue

            logger.info(f"[WS] Audio received: {len(opus_data)} bytes")

            history = session_history.get(session_id, [])
            result = await process_audio_pipeline(
                opus_data, history,
                jenjang=current_jenjang,
                profile_id=current_profile
            )

            if result.get("error"):
                await ws.send_text(json.dumps({
                    "type": "error",
                    "message": result["error"],
                }))
                continue

            # Simpan history
            history.append({
                "user": result["user_text"],
                "assistant": result["text"],
            })
            session_history[session_id] = history[-10:]

            # Simpan ke database
            save_sesi(result["user_text"], result["text"], result["total_time"])

            # Kirim audio ke ESP32
            if result.get("audio"):
                await ws.send_bytes(result["audio"])

            # Kirim teks + gamifikasi untuk display di ESP32
            await ws.send_text(json.dumps({
                "type": "response",
                "text": result["text"],
                "user_text": result["user_text"],
                "total_time": round(result["total_time"], 2),
                "xp_gained": result.get("xp_gained", 0),
                "is_kuis": result.get("is_kuis", False),
                "gamifikasi": result.get("gamifikasi", {}),
            }))

    except WebSocketDisconnect:
        logger.info(f"[WS] ESP32 disconnected: {session_id}")
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
    finally:
        keepalive_running = False
        keepalive_task.cancel()
        session_history.pop(session_id, None)


# ══════════════════════════════════════════════════════
#  Dashboard API
# ══════════════════════════════════════════════════════

@app.get("/api/overview")
def api_overview():
    overview = get_overview()
    # Merge dengan gamifikasi stats
    try:
        stats = get_player_stats()
        overview.update({
            "xp": stats["xp"],
            "level": stats["level"],
            "streak": stats["streak"],
            "badges": stats["badges"],
            "jawaban_benar": stats["jawaban_benar"],
            "total_jawaban": stats["total_jawaban"],
        })
    except:
        pass
    return JSONResponse(overview)


@app.get("/api/history")
def api_history(limit: int = 30, profile_id: int = None):
    return JSONResponse(get_history(limit=limit, profile_id=profile_id))


@app.get("/api/profiles")
def api_profiles():
    return JSONResponse(get_profiles())


# ─── Gamifikasi API ─────────────────────────────────

@app.get("/api/gamifikasi")
def api_gamifikasi(profile_id: int = 1):
    """Statistik gamifikasi lengkap."""
    try:
        stats = get_player_stats(profile_id)
        mission = get_daily_mission(profile_id)
        return JSONResponse({
            "profile_id": profile_id,
            "stats": stats,
            "daily_mission": mission,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily-mission")
def api_daily_mission(profile_id: int = 1):
    """Misi harian hari ini."""
    return JSONResponse(get_daily_mission(profile_id))


@app.post("/api/claim-mission")
def api_claim_mission(profile_id: int = 1):
    """Klaim reward misi harian."""
    result = claim_mission_reward(profile_id)
    return JSONResponse(result)


# ─── Materi & Kuis API ──────────────────────────────

@app.get("/api/materi")
def api_materi(jenjang: str = None, mapel: str = None):
    """Daftar materi per jenjang."""
    if jenjang:
        return JSONResponse(get_materi_by_jenjang(jenjang, mapel))
    return JSONResponse({"jenjang_tersedia": ["TK", "SD", "SMP", "SMA"], "info": "Gunakan ?jenjang=TK untuk lihat materi"})


@app.get("/api/kuis")
def api_kuis(jenjang: str = "SD", mapel: str = None, jumlah: int = 5):
    """Generate kuis berbasis suara."""
    result = generate_kuis(jenjang, mapel, jumlah)
    return JSONResponse(result)


# ══════════════════════════════════════════════════════
#  Health Check
# ══════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok", "server": "TemanBelajar"}


# ══════════════════════════════════════════════════════
#  Dashboard HTML
# ══════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TemanBelajar Dashboard</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0b1120;color:#e2e8f0;min-height:100vh}
nav{background:#1a2332;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e3a5f}
nav h1{font-size:18px;font-weight:700;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
#status{font-size:12px;padding:4px 10px;border-radius:12px;background:#052e16;color:#22c55e}
main{max-width:1000px;margin:24px auto;padding:0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:24px}
.card{background:#1a2332;border-radius:14px;padding:18px;border:1px solid #1e3a5f;transition:border-color .2s}
.card:hover{border-color:#38bdf8}
.card h3{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:8px}
.card .value{font-size:30px;font-weight:700;color:#38bdf8}
.card .sub{font-size:12px;color:#475569;margin-top:4px}
.card.gold{border-color:#eab308;background:linear-gradient(135deg,#1a2332,#1a2a0a)}
.card.gold .value{color:#eab308}
.mission-bar{background:#1a2332;border-radius:14px;padding:16px 20px;margin-bottom:20px;border:1px solid #1e3a5f;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.mission-bar .m-icon{font-size:28px}
.mission-bar .m-info{flex:1;min-width:200px}
.mission-bar .m-info h3{font-size:14px;margin-bottom:4px}
.mission-bar .m-info p{font-size:12px;color:#64748b}
.mission-bar .progress-wrap{flex:1;min-width:180px}
.progress-bar{height:8px;background:#1e293b;border-radius:4px;overflow:hidden;margin-bottom:4px}
.progress-fill{height:100%;background:linear-gradient(90deg,#38bdf8,#818cf8);border-radius:4px;transition:width .3s}
.mission-bar .claim-btn{padding:8px 18px;background:#22c55e;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:13px}
.mission-bar .claim-btn:disabled{background:#1e293b;color:#475569;cursor:default}
.badge-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.badge-item{background:#1e3a5f;padding:4px 10px;border-radius:20px;font-size:12px;white-space:nowrap}
.section-title{font-size:16px;font-weight:600;margin-bottom:16px;color:#94a3b8}
.history-item{background:#1a2332;border-radius:10px;padding:14px 18px;margin-bottom:10px;border:1px solid #1e3a5f}
.history-item .q{color:#f1f5f9;font-weight:500;margin-bottom:6px}
.history-item .q::before{content:'Q: ';color:#818cf8;font-size:12px}
.history-item .a{color:#94a3b8;font-size:14px;line-height:1.5}
.history-item .a::before{content:'A: ';color:#22c55e;font-size:12px}
.history-item .meta{margin-top:8px;font-size:11px;color:#475569}
.empty{text-align:center;padding:48px 20px;color:#475569}
.empty p{font-size:15px;margin-bottom:6px}
.tabs{display:flex;gap:4px;margin-bottom:20px;flex-wrap:wrap}
.tab{padding:8px 18px;border-radius:8px;background:#1a2332;border:1px solid #1e3a5f;color:#94a3b8;cursor:pointer;font-size:13px;transition:all .2s}
.tab.active{background:#1e3a5f;border-color:#38bdf8;color:#38bdf8}
@media(max-width:540px){.cards{grid-template-columns:repeat(2,1fr)}.card .value{font-size:24px}}
</style>
</head>
<body>
<nav>
<h1>TemanBelajar</h1>
<span id="status">Online</span>
</nav>
<main>

<!-- Cards -->
<div class="cards">
<div class="card gold"><h3>XP Total</h3><div class="value" id="v-xp">0</div><div class="sub">Level <span id="v-level">1</span></div></div>
<div class="card"><h3>Streak</h3><div class="value" id="v-streak">0</div><div class="sub">hari berturut-turut</div></div>
<div class="card"><h3>Sesi Belajar</h3><div class="value" id="v-total">0</div><div class="sub"><span id="v-hariini">0</span> hari ini</div></div>
<div class="card"><h3>Akurasi</h3><div class="value" id="v-akurasi">-</div><div class="sub" id="v-akurasi-sub"></div></div>
</div>

<!-- Badges -->
<div id="badge-area" style="margin-bottom:20px">
<span style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.5px">Lencana</span>
<div class="badge-row" id="badge-list"><span style="color:#475569;font-size:12px">Belum ada lencana</span></div>
</div>

<!-- Misi Harian -->
<div id="mission-container"></div>

<!-- Tabs -->
<div class="tabs">
<div class="tab active" onclick="switchTab('riwayat')">Riwayat</div>
<div class="tab" onclick="switchTab('materi')">Materi</div>
</div>

<!-- Tab: Riwayat -->
<div id="tab-riwayat">
<div id="history-container"><div class="empty"><p>Belum ada sesi belajar.</p><p>Nyalakan TemanBelajar dan mulai ngobrol!</p></div></div>
</div>

<!-- Tab: Materi -->
<div id="tab-materi" style="display:none">
<select id="sel-jenjang" onchange="loadMateri()" style="background:#1a2332;color:#e2e8f0;border:1px solid #1e3a5f;border-radius:8px;padding:8px 14px;margin-bottom:16px;font-size:14px">
<option value="">Semua Jenjang</option><option value="TK">TK</option><option value="SD" selected>SD</option><option value="SMP">SMP</option><option value="SMA">SMA</option>
</select>
<div id="materi-container"><div class="empty"><p>Memuat materi...</p></div></div>
</div>

</main>
<script>
const H=document.getElementById('history-container');
const MC=document.getElementById('materi-container');

async function load(){
// Overview
const o=await fetch('/api/overview').then(r=>r.json());
document.getElementById('v-total').textContent=o.total_sesi;
document.getElementById('v-hariini').textContent=o.sesi_hari_ini;
document.getElementById('v-xp').textContent=o.xp||0;
document.getElementById('v-level').textContent=o.level||1;
document.getElementById('v-streak').textContent=o.streak||0;
if(o.total_jawaban>0 && o.jawaban_benar!==undefined){
  const acc=Math.round(o.jawaban_benar/o.total_jawaban*100);
  document.getElementById('v-akurasi').textContent=acc+'%';
  document.getElementById('v-akurasi-sub').textContent=o.jawaban_benar+'/'+o.total_jawaban+' benar';
}

// Badges
if(o.badges && o.badges.length){
  document.getElementById('badge-list').innerHTML=o.badges.map(b=>`<span class="badge-item">${b.icon||''} ${b.name}</span>`).join('');
}

// History
const h=await fetch('/api/history?limit=40').then(r=>r.json());
if(!h.length){H.innerHTML='<div class="empty"><p>Belum ada sesi belajar.</p><p>Nyalakan TemanBelajar dan mulai ngobrol!</p></div>'}
else{H.innerHTML=h.map(s=>`<div class="history-item">
<div class="q">${esc(s.pertanyaan||'')}</div>
<div class="a">${esc((s.jawaban||'').substring(0,200))}${(s.jawaban||'').length>200?'...':''}</div>
<div class="meta">${s.waktu||''} · ${((s.durasi_ms||0)/1000).toFixed(1)}s</div>
</div>`).join('');}

// Misi harian
const m=await fetch('/api/daily-mission').then(r=>r.json());
const pct=Math.min(100,Math.round(m.progress/m.target*100));
const done=m.completed;
const claimed=m.claimed;
let btn='';
if(claimed)btn='<button class="claim-btn" disabled>Diklaim</button>';
else if(done)btn=`<button class="claim-btn" onclick="claimMission()">Klaim +80 XP!</button>`;
else btn='';
document.getElementById('mission-container').innerHTML=`<div class="mission-bar">
<div class="m-icon">🎯</div><div class="m-info"><h3>${m.name||'Misi Harian'}</h3><p>${m.desc||''}</p></div>
<div class="progress-wrap"><div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
<span style="font-size:11px;color:#64748b">${m.progress}/${m.target} ${done?'✅ Selesai!':''}</span></div>
${btn}</div>`;
}

async function claimMission(){
const r=await fetch('/api/claim-mission',{method:'POST'}).then(r=>r.json());
if(r.claimed){alert(r.message||'+80 XP! Misi selesai!');load();}
else{alert(r.reason||'Gagal klaim');}
}

async function loadMateri(){
const j=document.getElementById('sel-jenjang').value;
const params=j?'?jenjang='+j:'';
const d=await fetch('/api/materi'+params).then(r=>r.json());
if(!d.length){MC.innerHTML='<div class="empty"><p>Belum ada materi untuk jenjang ini.</p></div>';return}
MC.innerHTML='<div style="display:grid;gap:10px">'+d.map(m=>`<div style="background:#1a2332;border-radius:10px;padding:14px 18px;border:1px solid #1e3a5f">
<span style="font-size:10px;color:#818cf8;text-transform:uppercase">${m.mapel} · Level ${m.level}</span>
<div style="font-weight:600;margin:4px 0">${m.topik}${m.sub_topik?' > '+m.sub_topik:''}</div>
</div>`).join('')+'</div>';
}

function switchTab(t){
document.querySelectorAll('.tab').forEach(el=>el.classList.remove('active'));
event.target.classList.add('active');
document.getElementById('tab-riwayat').style.display=t==='riwayat'?'block':'none';
document.getElementById('tab-materi').style.display=t==='materi'?'block':'none';
if(t==='materi')loadMateri();
}

function esc(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}
load();setInterval(load,10000);
</script>
</body>
</html>""")


# ══════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting TemanBelajar on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)