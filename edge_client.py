import os
import sys
import time
import logging
import threading
import platform

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

import cv2  # type: ignore[import-untyped]
import requests  # type: ignore[import-untyped]

CLOUD_URL = os.getenv(
    "CLOUD_URL",
    "https://eggsort-ai.onrender.com",
)

EDGE_SECRET = os.getenv("EDGE_SECRET", "")

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))

CAPTURE_INTERVAL = float(os.getenv("CAPTURE_INTERVAL", "2.0"))

KEEPALIVE_INTERVAL = float(os.getenv("KEEPALIVE_INTERVAL", "60.0"))

CAPTURE_WIDTH = int(os.getenv("CAPTURE_WIDTH", "2592"))

CAPTURE_HEIGHT = int(os.getenv("CAPTURE_HEIGHT", "1944"))

FRAME_ENDPOINT = f"{CLOUD_URL.rstrip('/')}/api/frame"
KEEPALIVE_ENDPOINT = f"{CLOUD_URL.rstrip('/')}/api/ping"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("edge.log", encoding="utf-8"),
    ],
)

log = logging.getLogger("edge")


def open_camera():
    if platform.system() == "Windows":
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)

    if not cap.isOpened():
        cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        raise RuntimeError(
            f"Cannot open camera index={CAMERA_INDEX}"
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FPS, 15)

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    log.info(
        f"Camera opened {width}x{height} "
        f"(index={CAMERA_INDEX})"
    )

    return cap


def capture_frame(cap):
    for _ in range(2):
        cap.grab()

    ret, frame = cap.read()

    if not ret or frame is None:
        return None

    frame = cv2.convertScaleAbs(frame, alpha=1.1, beta=8)

    success, buffer = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, 88],
    )

    if not success:
        return None

    return buffer.tobytes()


def build_headers():
    headers = {"User-Agent": "EggSort-Edge/1.0"}
    if EDGE_SECRET:
        headers["X-Edge-Secret"] = EDGE_SECRET
    return headers


def send_frame(image_bytes):
    try:
        response = requests.post(
            FRAME_ENDPOINT,
            files={
                "frame": (
                    "frame.jpg",
                    image_bytes,
                    "image/jpeg",
                )
            },
            headers=build_headers(),
            timeout=20,
        )

        if response.status_code == 200:
            return response.json()

        log.warning(
            f"Server {response.status_code}: "
            f"{response.text[:100]}"
        )

    except requests.exceptions.ConnectionError:
        log.warning("Cloud connection failed")

    except requests.exceptions.Timeout:
        log.warning("Request timeout")

    except Exception as exc:
        log.exception(f"send_frame error: {exc}")

    return None


def keepalive_worker():
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        try:
            response = requests.get(
                KEEPALIVE_ENDPOINT,
                headers=build_headers(),
                timeout=10,
            )
            log.debug(f"KeepAlive {response.status_code}")
        except Exception:
            pass


def main():
    log.info("=" * 60)
    log.info("EggSort AI Edge Client")
    log.info(f"Server   : {CLOUD_URL}")
    log.info("Camera   : CR5M03")
    log.info(f"Interval : {CAPTURE_INTERVAL}s")
    log.info("=" * 60)

    thread = threading.Thread(
        target=keepalive_worker,
        daemon=True,
    )
    thread.start()

    cap = None
    retry_count = 0

    while True:
        if cap is None or not cap.isOpened():
            try:
                cap = open_camera()
                retry_count = 0
            except RuntimeError as exc:
                retry_count += 1
                wait_time = min(60, retry_count * 5)
                log.error(f"{exc} (retry in {wait_time}s)")
                time.sleep(wait_time)
                continue

        image_bytes = capture_frame(cap)

        if image_bytes is None:
            log.warning("Capture failed. Reopen camera.")
            try:
                cap.release()
            except Exception:
                pass
            cap = None
            time.sleep(1)
            continue

        result = send_frame(image_bytes)

        if result:
            status = result.get("status", "")

            if status == "paused":
                log.info("[PAUSE] Server paused")

            elif status == "ok":
                grade = result.get("grade", "?")
                confidence = result.get("confidence", "?")
                log.info(
                    f"[OK] Grade={grade} "
                    f"Confidence={confidence}%"
                )

        time.sleep(CAPTURE_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Application stopped")
