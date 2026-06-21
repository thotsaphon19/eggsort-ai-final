"""
EggSort AI — Cloud Server (Render)
รับภาพจาก Edge client → วิเคราะห์ด้วย Claude Vision → broadcast ผลให้ Dashboard

โหมดการทำงาน:
  RENDER=true  → Cloud mode: รอรับภาพจาก edge client ผ่าน HTTP POST /api/frame
  RENDER=false → Local mode: จับภาพเองจากกล้อง USB (ใช้สำหรับ dev)
"""

import asyncio
import base64
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

import anthropic
from aiohttp import web

from database import (
    count_scans,
    daily_summary,
    end_session,
    get_scans,
    get_sessions,
    grade_distribution,
    hourly_summary,
    init_db,
    insert_scan,
    shell_condition_summary,
    start_session,
)

# ─── Config ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IS_RENDER         = os.environ.get("RENDER", "false").lower() == "true"
CAPTURE_INTERVAL  = float(os.environ.get("CAPTURE_INTERVAL", "2.0"))
WS_HOST           = os.environ.get("WS_HOST", "0.0.0.0")
HTTP_PORT         = int(os.environ.get("PORT", os.environ.get("HTTP_PORT", "8080")))
LOG_DIR           = Path("logs")
DB_PATH           = LOG_DIR / "eggsort.db"
SAVE_IMAGES       = os.environ.get("SAVE_IMAGES", "true").lower() == "true"
EDGE_SECRET       = os.environ.get("EDGE_SECRET", "")

# ─── Local camera (Local mode เท่านั้น) ──────────────────────────────────────
if not IS_RENDER:
    import cv2
    CAMERA_INDEX   = int(os.environ.get("CAMERA_INDEX", "0"))
    CAPTURE_WIDTH  = int(os.environ.get("CAPTURE_WIDTH", "2592"))
    CAPTURE_HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", "1944"))

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "system.log"),
    ],
)
log = logging.getLogger("eggsort")

# ─── State ───────────────────────────────────────────────────────────────────
state = {
    "running":            False,
    "camera_ok":          IS_RENDER,
    "counts":             {"AA": 0, "A": 0, "B": 0, "C": 0},
    "history":            [],
    "total":              0,
    "last_frame_b64":     None,
    "mode":               "cloud" if IS_RENDER else "local",
    "current_session_id": None,
    "edge_connected":     False,
}

# WebSocket clients เก็บเป็น aiohttp WebSocketResponse
connected_clients: set = set()

# ─── Anthropic ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """คุณคือระบบ AI คัดแยกขนาดไข่สำหรับสายพานอุตสาหกรรม
วิเคราะห์ภาพไข่และจำแนกเกรดตามมาตรฐานไทย:
- AA: ≥70g   A: 60-69g   B: 50-59g   C: <50g

ตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น:
{
  "grade": "AA|A|B|C",
  "estimatedWeight": "เช่น 72g",
  "confidence": 0-100,
  "shellCondition": "ปกติ|รอยร้าว|สกปรก|แตก",
  "color": "ขาว|น้ำตาล|ครีม",
  "shape": "ปกติ|ผิดรูป",
  "recommendation": "ผ่าน|ตรวจสอบ|ปฏิเสธ",
  "notes": "ข้อสังเกตสั้น ๆ ไม่เกิน 30 ตัวอักษร",
  "eggCount": 1
}"""


def analyze_image(image_bytes: bytes) -> dict:
    key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ยังไม่ได้ตั้งค่า ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    b64 = base64.standard_b64encode(image_bytes).decode()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": "วิเคราะห์ไข่และส่งผลเป็น JSON"},
                ],
            }
        ],
    )
    raw = (
        msg.content[0].text.strip()
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )
    return json.loads(raw)


# ─── Local camera (Local mode) ───────────────────────────────────────────────
class CameraCapture:
    def __init__(self):
        self.cap  = None
        self.lock = threading.Lock()

    def open(self, index: int = 0) -> bool:
        with self.lock:
            if self.cap and self.cap.isOpened():
                self.cap.release()
            self.cap = cv2.VideoCapture(index)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                log.info(f"กล้อง index={index} เปิดแล้ว")
                return True
            log.error(f"ไม่สามารถเปิดกล้อง index={index}")
            return False

    def grab(self) -> bytes | None:
        with self.lock:
            if not self.cap or not self.cap.isOpened():
                return None
            for _ in range(2):
                self.cap.grab()
            ret, frame = self.cap.read()
            if not ret:
                return None
            frame = cv2.convertScaleAbs(frame, alpha=1.1, beta=10)
            _, buf = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
            )
            return buf.tobytes()

    def list_cameras(self) -> list:
        found = []
        for i in range(6):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                found.append(i)
                cap.release()
        return found


camera = CameraCapture() if not IS_RENDER else None

# ─── Helpers ─────────────────────────────────────────────────────────────────
async def broadcast(msg: dict):
    if not connected_clients:
        return
    data = json.dumps(msg, ensure_ascii=False)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_str(data)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


def record_result(result: dict, img_bytes: bytes | None = None) -> dict:
    now   = datetime.now()
    grade = result.get("grade", "B")
    state["counts"][grade] = state["counts"].get(grade, 0) + 1
    state["total"] += 1
    entry = {
        "id":   state["total"],
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        **result,
    }
    state["history"].insert(0, entry)
    if len(state["history"]) > 200:
        state["history"].pop()

    # CSV
    csv_path = LOG_DIR / f"log_{now.strftime('%Y%m%d')}.csv"
    if not csv_path.exists():
        csv_path.write_text(
            "id,time,grade,estimatedWeight,confidence,"
            "shellCondition,color,recommendation,notes\n"
        )
    with open(csv_path, "a", encoding="utf-8") as f:
        f.write(
            f"{entry['id']},{entry['time']},{grade},"
            f"{result.get('estimatedWeight','')},"
            f"{result.get('confidence','')},"
            f"{result.get('shellCondition','')},"
            f"{result.get('color','')},"
            f"{result.get('recommendation','')},"
            f"{result.get('notes','')}\n"
        )

    # FIX: กำหนด img_path ก่อนใช้งาน — ถ้าไม่ save ให้เป็น None
    img_path = None
    if SAVE_IMAGES and img_bytes:
        d = LOG_DIR / "images" / now.strftime("%Y%m%d")
        d.mkdir(parents=True, exist_ok=True)
        img_path = d / f"{now.strftime('%H%M%S')}_{grade}_{state['total']:05d}.jpg"
        img_path.write_bytes(img_bytes)

    log.info(
        f"[{state['total']:05d}] grade={grade} "
        f"weight={result.get('estimatedWeight')} "
        f"conf={result.get('confidence')}%"
    )

    try:
        insert_scan(
            result,
            session_id=state.get("current_session_id"),
            image_path=str(img_path) if img_path else None,
        )
    except Exception as e:
        log.warning(f"DB insert error: {e}")

    return entry


# ─── Cloud mode: HTTP POST /api/frame ────────────────────────────────────────
async def handle_frame_upload(request: web.Request):
    if EDGE_SECRET:
        secret = request.headers.get("X-Edge-Secret", "")
        if secret != EDGE_SECRET:
            return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data        = await request.post()
        frame_field = data.get("frame")
        if not frame_field:
            img_bytes = await request.read()
        else:
            img_bytes = frame_field.file.read()  # type: ignore[union-attr]
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

    if not img_bytes:
        return web.json_response({"error": "no image"}, status=400)

    b64 = base64.b64encode(img_bytes).decode()
    state["last_frame_b64"] = b64
    state["edge_connected"] = True
    await broadcast({"type": "frame", "data": b64})

    if not state["running"]:
        return web.json_response({"status": "paused"})

    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_image, img_bytes)
    except Exception as e:
        log.error(f"AI error: {e}")
        await broadcast({"type": "error", "message": str(e)})
        return web.json_response({"error": str(e)}, status=500)

    entry  = record_result(result, img_bytes if SAVE_IMAGES else None)
    total  = state["total"]
    pass_c = state["counts"].get("AA", 0) + state["counts"].get("A", 0)

    await broadcast({
        "type":   "result",
        "entry":  entry,
        "counts": state["counts"],
        "total":  total,
        "pass":   pass_c,
        "rate":   round(pass_c / total * 100) if total else 0,
    })
    return web.json_response({
        "status":     "ok",
        "grade":      result.get("grade"),
        "confidence": result.get("confidence"),
    })


# ─── Local mode: conveyor loop ───────────────────────────────────────────────
async def conveyor_loop():
    log.info("conveyor loop เริ่มต้น (local mode)")
    while True:
        if not state["running"]:
            await asyncio.sleep(0.5)
            continue

        img_bytes = await asyncio.get_event_loop().run_in_executor(
            None, camera.grab  # type: ignore[union-attr]
        )
        if img_bytes is None:
            await broadcast({"type": "error", "message": "ไม่ได้รับภาพจากกล้อง"})
            await asyncio.sleep(1)
            continue

        b64 = base64.b64encode(img_bytes).decode()
        state["last_frame_b64"] = b64
        await broadcast({"type": "frame", "data": b64})

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, analyze_image, img_bytes
            )
        except Exception as e:
            log.error(f"AI error: {e}")
            await broadcast({"type": "error", "message": str(e)})
            await asyncio.sleep(CAPTURE_INTERVAL)
            continue

        entry  = record_result(result, img_bytes if SAVE_IMAGES else None)
        total  = state["total"]
        pass_c = state["counts"].get("AA", 0) + state["counts"].get("A", 0)
        await broadcast({
            "type":   "result",
            "entry":  entry,
            "counts": state["counts"],
            "total":  total,
            "pass":   pass_c,
            "rate":   round(pass_c / total * 100) if total else 0,
        })
        await asyncio.sleep(CAPTURE_INTERVAL)


# ─── WebSocket handler (aiohttp — ใช้ port เดียวกับ HTTP) ────────────────────
async def ws_handler(request: web.Request):
    """
    FIX: เปลี่ยนจาก websockets library (port แยก) มาเป็น aiohttp WebSocket
    เพื่อให้ทำงานบน Render ที่เปิดได้แค่ port เดียว
    Dashboard เชื่อมที่  wss://your-app.onrender.com/ws  (ไม่ต้องระบุ port)
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    log.info(f"WS client connected ({len(connected_clients)})")

    total  = state["total"]
    pass_c = state["counts"].get("AA", 0) + state["counts"].get("A", 0)
    await ws.send_str(json.dumps({
        "type":         "init",
        "running":      state["running"],
        "camera_ok":    state["camera_ok"],
        "counts":       state["counts"],
        "total":        total,
        "pass":         pass_c,
        "rate":         round(pass_c / total * 100) if total else 0,
        "history":      state["history"][:50],
        "mode":         state["mode"],
        "edge_connected": state["edge_connected"],
        "cameras":      camera.list_cameras() if camera else [],
    }, ensure_ascii=False))

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:  # type: ignore[attr-defined]
                data = json.loads(msg.data)
                cmd  = data.get("cmd")

                if cmd == "start":
                    if IS_RENDER:
                        state["running"] = True
                        try:
                            state["current_session_id"] = start_session()
                        except Exception:
                            pass
                        await broadcast({
                            "type": "status", "running": True, "camera_ok": True
                        })
                    else:
                        idx = data.get("cameraIndex", CAMERA_INDEX)
                        ok  = await asyncio.get_event_loop().run_in_executor(
                            None, camera.open, idx  # type: ignore[union-attr]
                        )
                        state["camera_ok"] = ok
                        if ok:
                            state["running"] = True
                            await broadcast({
                                "type": "status", "running": True, "camera_ok": True
                            })
                        else:
                            await ws.send_str(json.dumps({
                                "type":    "error",
                                "message": f"เปิดกล้อง index={idx} ไม่ได้",
                            }))

                elif cmd == "stop":
                    state["running"] = False
                    if state.get("current_session_id"):
                        try:
                            end_session(state["current_session_id"])
                        except Exception:
                            pass
                        state["current_session_id"] = None
                    await broadcast({
                        "type": "status",
                        "running":   False,
                        "camera_ok": state["camera_ok"],
                    })

                elif cmd == "reset":
                    state.update({
                        "counts": {"AA": 0, "A": 0, "B": 0, "C": 0},
                        "history": [],
                        "total": 0,
                    })
                    await broadcast({"type": "reset"})

                elif cmd == "set_interval":
                    global CAPTURE_INTERVAL
                    CAPTURE_INTERVAL = max(0.5, float(data.get("value", 2.0)))
                    await broadcast({"type": "config", "interval": CAPTURE_INTERVAL})

                elif cmd == "scan_cameras" and camera:
                    cams = await asyncio.get_event_loop().run_in_executor(
                        None, camera.list_cameras
                    )
                    await ws.send_str(json.dumps({"type": "cameras", "cameras": cams}))

            elif msg.type == web.WSMsgType.ERROR:  # type: ignore[attr-defined]
                log.warning(f"WS error: {ws.exception()}")
                break

    finally:
        connected_clients.discard(ws)
        log.info(f"WS client disconnected ({len(connected_clients)})")

    return ws


# ─── HTTP routes ─────────────────────────────────────────────────────────────
async def handle_index(request: web.Request):
    return web.FileResponse(Path(__file__).parent / "templates" / "index.html")


async def handle_export(request: web.Request):
    today    = datetime.now().strftime("%Y%m%d")
    csv_path = LOG_DIR / f"log_{today}.csv"
    if not csv_path.exists():
        return web.Response(text="ยังไม่มีข้อมูลวันนี้", status=404)
    return web.FileResponse(
        csv_path,
        headers={"Content-Disposition": f"attachment; filename=egg_log_{today}.csv"},
    )


async def handle_stats(request: web.Request):
    total  = state["total"]
    pass_c = state["counts"].get("AA", 0) + state["counts"].get("A", 0)
    return web.json_response({
        "counts":         state["counts"],
        "total":          total,
        "pass":           pass_c,
        "rate":           round(pass_c / total * 100) if total else 0,
        "running":        state["running"],
        "mode":           state["mode"],
        "edge_connected": state["edge_connected"],
    })


async def handle_history(request: web.Request):
    q         = request.rel_url.query
    date_from = q.get("from")
    date_to   = q.get("to")
    grade     = q.get("grade")
    limit     = min(int(q.get("limit", 200)), 500)
    page      = max(int(q.get("page", 1)), 1)
    offset    = (page - 1) * limit
    try:
        rows  = get_scans(date_from, date_to, grade, limit, offset)
        total = count_scans(date_from, date_to, grade)
        return web.json_response({
            "data": rows, "total": total, "page": page, "limit": limit
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_analytics(request: web.Request):
    days = int(request.rel_url.query.get("days", 30))
    try:
        return web.json_response({
            "daily":    daily_summary(days),
            "overall":  grade_distribution(),
            "shell":    shell_condition_summary(),
            "sessions": get_sessions(20),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_hourly(request: web.Request):
    day = request.rel_url.query.get("date")
    try:
        return web.json_response({"data": hourly_summary(day)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_ping(request: web.Request):
    return web.json_response({"ok": True, "ts": datetime.now().isoformat()})


# ─── Self-ping anti-sleep (Render Free tier) ─────────────────────────────────
async def self_ping_loop():
    if not IS_RENDER:
        return
    import aiohttp
    url = f"http://127.0.0.1:{HTTP_PORT}/api/ping"
    await asyncio.sleep(30)
    log.info("self-ping anti-sleep เริ่มแล้ว (ทุก 10 นาที)")
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    log.debug(f"self-ping → {resp.status}")
        except Exception as e:
            log.debug(f"self-ping skip: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / "images").mkdir(exist_ok=True)
    init_db()
    log.info(f"Database: {DB_PATH}")

    if not ANTHROPIC_API_KEY:
        log.warning("⚠  ANTHROPIC_API_KEY ยังไม่ได้ตั้งค่า")

    mode_label = "☁  Cloud (Render)" if IS_RENDER else "💻 Local"
    log.info(f"โหมด: {mode_label}")

    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_get( "/",              handle_index)
    app.router.add_get( "/export",        handle_export)
    app.router.add_get( "/api/stats",     handle_stats)
    app.router.add_get( "/api/ping",      handle_ping)
    app.router.add_get( "/api/history",   handle_history)
    app.router.add_get( "/api/analytics", handle_analytics)
    app.router.add_get( "/api/hourly",    handle_hourly)
    app.router.add_post("/api/frame",     handle_frame_upload)
    # FIX: WebSocket บน path /ws — ใช้ port เดียวกับ HTTP ไม่ต้องเปิด port แยก
    app.router.add_get( "/ws",            ws_handler)
    app.router.add_get(
        "/history",
        lambda r: web.FileResponse(
            Path(__file__).parent / "templates" / "history.html"
        ),
    )
    try:
        app.router.add_static("/static", Path(__file__).parent / "static")
    except Exception:
        pass

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WS_HOST, HTTP_PORT).start()
    log.info(f"HTTP + WS → http://0.0.0.0:{HTTP_PORT}")
    log.info("WebSocket  → wss://<your-app>.onrender.com/ws")

    if not IS_RENDER:
        asyncio.create_task(conveyor_loop())

    asyncio.create_task(self_ping_loop())

    log.info("✅ พร้อมใช้งาน")
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
