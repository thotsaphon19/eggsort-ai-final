"""
EggSort AI — Edge Client
กล้อง: CR5M03 · MI5100 Aptina · USB500W05G-SFV(2.8-12) · 5MP

รันที่โรงงาน — จับภาพจากกล้อง USB แล้วส่งขึ้น Render cloud

ตั้งค่า:
  CLOUD_URL        https://eggsort-ai.onrender.com
  EDGE_SECRET      รหัสลับ (ตรงกับ Render Dashboard)
  CAMERA_INDEX     0  (CR5M03 มักเป็น index 0)
  CAPTURE_INTERVAL 2.0
"""

import logging, os, time, threading
import cv2
import requests

# ─── Config ───────────────────────────────────────────────────────────────────
CLOUD_URL        = os.environ.get("CLOUD_URL", "https://eggsort-ai.onrender.com")
EDGE_SECRET      = os.environ.get("EDGE_SECRET", "")
CAMERA_INDEX     = int(os.environ.get("CAMERA_INDEX", "0"))
CAPTURE_INTERVAL = float(os.environ.get("CAPTURE_INTERVAL", "2.0"))
KEEPALIVE_INTERVAL = float(os.environ.get("KEEPALIVE_INTERVAL", "60.0"))  # ping ทุก 60s

# CR5M03 · USB500W05G-SFV(2.8-12) · 5MP — ความละเอียดสูงสุด
CAPTURE_WIDTH  = int(os.environ.get("CAPTURE_WIDTH",  "2592"))
CAPTURE_HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", "1944"))

FRAME_ENDPOINT     = f"{CLOUD_URL.rstrip('/')}/api/frame"
KEEPALIVE_ENDPOINT = f"{CLOUD_URL.rstrip('/')}/api/ping"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("edge.log"),
    ],
)
log = logging.getLogger("edge")

# ─── Camera ───────────────────────────────────────────────────────────────────
def open_camera() -> cv2.VideoCapture:
    """เปิดกล้อง CR5M03 USB500W05G-SFV และตั้งค่า 5MP"""
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)   # Linux
    if not cap.isOpened():
        cap = cv2.VideoCapture(CAMERA_INDEX)              # fallback (Windows/macOS)
    if not cap.isOpened():
        raise RuntimeError(f"ไม่สามารถเปิดกล้อง index={CAMERA_INDEX} (CR5M03)")

    # ตั้งค่าเฉพาะ CR5M03 · MI5100 Aptina · 5MP
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)           # ลด latency buffer
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # MJPEG ไว
    cap.set(cv2.CAP_PROP_FPS, 15)                 # MI5100 Aptina รองรับ 15fps@5MP

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log.info(f"CR5M03 USB500W05G-SFV เปิดแล้ว — {w}×{h} @ index={CAMERA_INDEX}")
    return cap

def capture_frame(cap: cv2.VideoCapture) -> bytes | None:
    """จับภาพ 1 เฟรม คืน JPEG bytes"""
    for _ in range(2):          # flush buffer เก่า
        cap.grab()
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    # ปรับความสว่าง/contrast เล็กน้อยสำหรับ MI5100 Aptina sensor
    frame = cv2.convertScaleAbs(frame, alpha=1.1, beta=8)
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes()

# ─── HTTP helpers ─────────────────────────────────────────────────────────────
def _headers() -> dict:
    h = {"User-Agent": "EggSort-Edge/1.0 (CR5M03-USB500W05G-SFV)"}
    if EDGE_SECRET:
        h["X-Edge-Secret"] = EDGE_SECRET
    return h

def send_frame(img_bytes: bytes) -> dict | None:
    try:
        resp = requests.post(
            FRAME_ENDPOINT,
            files={"frame": ("frame.jpg", img_bytes, "image/jpeg")},
            headers=_headers(),
            timeout=20,
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"Server {resp.status_code}: {resp.text[:100]}")
    except requests.exceptions.ConnectionError:
        log.warning("เชื่อมต่อ cloud ไม่ได้ — อาจกำลัง wake up")
    except requests.exceptions.Timeout:
        log.warning("Request timeout (>20s)")
    except Exception as e:
        log.error(f"send_frame error: {e}")
    return None

def keepalive_ping():
    """ping /api/ping ทุก 60 วินาที เพื่อกัน Render sleep"""
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        try:
            resp = requests.get(KEEPALIVE_ENDPOINT, headers=_headers(), timeout=10)
            log.debug(f"keepalive ping → {resp.status_code}")
        except Exception:
            pass   # ไม่สำคัญ — capture loop ทำงานต่อ

# ─── Main loop ────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("  EggSort AI — Edge Client")
    log.info(f"  กล้อง : CR5M03 · USB500W05G-SFV(2.8-12) · 5MP")
    log.info(f"  Server: {CLOUD_URL}")
    log.info(f"  ช่วงสแกน: {CAPTURE_INTERVAL}s")
    log.info("=" * 55)

    # เริ่ม keepalive thread แยก
    t = threading.Thread(target=keepalive_ping, daemon=True)
    t.start()
    log.info(f"keepalive thread เริ่มแล้ว (ทุก {KEEPALIVE_INTERVAL}s)")

    cap       = None
    retry_cam = 0

    while True:
        # ── เปิดกล้อง ──────────────────────────────────────────────────────
        if cap is None or not cap.isOpened():
            try:
                cap = open_camera()
                retry_cam = 0
            except RuntimeError as e:
                retry_cam += 1
                wait = min(60, 5 * retry_cam)
                log.error(f"{e} — retry ใน {wait}s (ครั้งที่ {retry_cam})")
                time.sleep(wait)
                continue

        # ── จับภาพ ─────────────────────────────────────────────────────────
        img_bytes = capture_frame(cap)
        if img_bytes is None:
            log.warning("capture ล้มเหลว — reopen camera")
            cap.release()
            cap = None
            time.sleep(1)
            continue

        # ── ส่ง ────────────────────────────────────────────────────────────
        result = send_frame(img_bytes)
        if result:
            status = result.get("status", "")
            if status == "paused":
                log.info("⏸  Server หยุดชั่วคราว — ไม่วิเคราะห์")
            elif status == "ok":
                grade = result.get("grade", "?")
                conf  = result.get("confidence", "?")
                log.info(f"✓  เกรด={grade}  ความมั่นใจ={conf}%")

        time.sleep(CAPTURE_INTERVAL)

if __name__ == "__main__":
    main()
