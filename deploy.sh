#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# EggSort AI — Deploy Script
# ใช้งาน: ./deploy.sh [local|docker|rpi|update|status|stop|logs]
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/venv"
SERVICE_NAME="eggsort"
DOCKER_IMAGE="eggsort-ai:latest"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*"; exit 1; }
info() { echo -e "   $*"; }

banner() {
  echo ""
  echo "╔══════════════════════════════════════════════════╗"
  echo "║  EggSort AI — Industrial Egg Grading System     ║"
  echo "║  Deploy: $1$(printf '%*s' $((40 - ${#1})) '')║"
  echo "╚══════════════════════════════════════════════════╝"
  echo ""
}

check_api_key() {
  if [ -f "$APP_DIR/.env" ]; then
    source "$APP_DIR/.env" 2>/dev/null || true
  fi
  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    warn "ANTHROPIC_API_KEY ไม่ได้ตั้งค่า"
    echo -n "   ใส่ API Key: "
    read -r key
    echo "ANTHROPIC_API_KEY=$key" >> "$APP_DIR/.env"
    export ANTHROPIC_API_KEY="$key"
    ok "บันทึก API Key ลง .env แล้ว"
  else
    ok "API Key พร้อมใช้งาน"
  fi
}

# ── LOCAL ─────────────────────────────────────────────────────────────────────
deploy_local() {
  banner "Local / Python venv"
  check_api_key

  # Python version check
  PY=$(python3 --version 2>&1 | awk '{print $2}')
  info "Python $PY"
  python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" \
    || err "ต้องการ Python 3.10+ (พบ $PY)"

  # OpenCV system deps (Ubuntu/Debian)
  if command -v apt-get &>/dev/null; then
    info "ติดตั้ง system dependencies..."
    sudo apt-get install -y --no-install-recommends \
      libgl1-mesa-glx libglib2.0-0 v4l-utils &>/dev/null
    ok "System dependencies พร้อม"
  fi

  # venv
  if [ ! -d "$VENV" ]; then
    info "สร้าง virtual environment..."
    python3 -m venv "$VENV"
  fi
  source "$VENV/bin/activate"
  pip install -q --upgrade pip
  pip install -q -r "$APP_DIR/requirements.txt"
  ok "Dependencies ติดตั้งแล้ว"

  mkdir -p "$APP_DIR/logs/images"
  ok "โฟลเดอร์ logs พร้อม"

  echo ""
  ok "เริ่มต้นระบบ — เปิดเบราว์เซอร์ที่ http://localhost:8080"
  echo ""
  source "$APP_DIR/.env"
  python3 "$APP_DIR/server.py"
}

# ── DOCKER ────────────────────────────────────────────────────────────────────
deploy_docker() {
  banner "Docker Compose"
  command -v docker &>/dev/null  || err "ไม่พบ Docker — https://docs.docker.com/get-docker/"
  command -v docker-compose &>/dev/null || docker compose version &>/dev/null \
    || err "ไม่พบ docker-compose"
  check_api_key

  # สร้าง .env ถ้าไม่มี
  [ -f "$APP_DIR/.env" ] || cp "$APP_DIR/.env.example" "$APP_DIR/.env"

  # ตรวจ /dev/video0
  if [ ! -e /dev/video0 ]; then
    warn "/dev/video0 ไม่พบ — ตรวจสอบการเชื่อมต่อกล้อง USB"
    warn "ใช้: ls /dev/video* แล้วแก้ docker-compose.yml"
  fi

  info "Build image..."
  docker compose -f "$APP_DIR/docker-compose.yml" build
  ok "Build เสร็จ"

  info "Start containers..."
  docker compose -f "$APP_DIR/docker-compose.yml" up -d
  ok "Container กำลังรัน"

  echo ""
  ok "Dashboard: http://localhost:8080"
  info "ดู logs: ./deploy.sh logs"
  info "หยุด:    ./deploy.sh stop"
}

# ── RASPBERRY PI ──────────────────────────────────────────────────────────────
deploy_rpi() {
  banner "Raspberry Pi (systemd)"
  [ "$(id -u)" -eq 0 ] || err "ต้องรันด้วย sudo: sudo ./deploy.sh rpi"
  check_api_key

  INSTALL_DIR="/home/pi/egg-sorter"
  RPI_USER="pi"

  # ตรวจ Raspberry Pi
  grep -q "Raspberry" /proc/cpuinfo 2>/dev/null || \
  grep -q "Raspberry" /sys/firmware/devicetree/base/model 2>/dev/null || \
    warn "ไม่ใช่ Raspberry Pi จริง — ดำเนินการต่อ..."

  # System deps
  info "ติดตั้ง system packages..."
  apt-get update -q
  apt-get install -y --no-install-recommends \
    python3-pip python3-venv python3-opencv \
    libgl1-mesa-glx libglib2.0-0 v4l-utils nginx &>/dev/null
  ok "System packages พร้อม"

  # user video group
  usermod -aG video "$RPI_USER"
  ok "เพิ่ม $RPI_USER เข้า video group"

  # copy app
  mkdir -p "$INSTALL_DIR"
  cp -r "$APP_DIR"/* "$INSTALL_DIR/"
  chown -R "$RPI_USER:$RPI_USER" "$INSTALL_DIR"
  ok "Copy ไฟล์ไป $INSTALL_DIR"

  # venv
  sudo -u "$RPI_USER" python3 -m venv "$INSTALL_DIR/venv"
  sudo -u "$RPI_USER" "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
  ok "Python venv พร้อม"

  # systemd service
  sed "s|/home/pi|/home/$RPI_USER|g" "$APP_DIR/eggsort.service" \
    > /etc/systemd/system/eggsort.service
  systemctl daemon-reload
  systemctl enable eggsort
  systemctl restart eggsort
  ok "systemd service เปิดใช้งานแล้ว (autostart)"

  # nginx
  cp "$APP_DIR/eggsort-nginx.conf" /etc/nginx/sites-available/eggsort
  ln -sf /etc/nginx/sites-available/eggsort /etc/nginx/sites-enabled/eggsort
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  ok "Nginx reverse proxy พร้อม"

  # แสดง IP
  IP=$(hostname -I | awk '{print $1}')
  echo ""
  ok "Deploy สำเร็จ!"
  info "Dashboard: http://$IP"
  info "ดู logs:   journalctl -u eggsort -f"
  info "รีสตาร์ท: sudo systemctl restart eggsort"
}

# ── UPDATE ────────────────────────────────────────────────────────────────────
deploy_update() {
  banner "Update"
  if systemctl is-active --quiet eggsort 2>/dev/null; then
    info "หยุด service ชั่วคราว..."
    sudo systemctl stop eggsort
    source "$VENV/bin/activate" 2>/dev/null || true
    pip install -q -r "$APP_DIR/requirements.txt"
    sudo systemctl start eggsort
    ok "อัปเดตและรีสตาร์ทแล้ว"
  elif docker compose -f "$APP_DIR/docker-compose.yml" ps 2>/dev/null | grep -q "Up"; then
    docker compose -f "$APP_DIR/docker-compose.yml" pull
    docker compose -f "$APP_DIR/docker-compose.yml" up -d --build
    ok "Docker อัปเดตแล้ว"
  else
    source "$VENV/bin/activate" 2>/dev/null || true
    pip install -q -r "$APP_DIR/requirements.txt"
    ok "Dependencies อัปเดตแล้ว"
  fi
}

# ── STATUS ────────────────────────────────────────────────────────────────────
show_status() {
  banner "Status"
  echo "── Process ──────────────────────────────────"
  if systemctl is-active --quiet eggsort 2>/dev/null; then
    ok "systemd service: RUNNING"
    systemctl status eggsort --no-pager -l | tail -8
  elif docker compose -f "$APP_DIR/docker-compose.yml" ps 2>/dev/null | grep -q "Up"; then
    ok "Docker: RUNNING"
    docker compose -f "$APP_DIR/docker-compose.yml" ps
  else
    warn "ระบบยังไม่รัน"
  fi
  echo ""
  echo "── Camera ───────────────────────────────────"
  ls /dev/video* 2>/dev/null && ok "พบกล้อง" || warn "ไม่พบ /dev/video*"
  echo ""
  echo "── API stats ────────────────────────────────"
  curl -s http://localhost:8080/api/stats 2>/dev/null \
    | python3 -m json.tool 2>/dev/null || warn "ไม่สามารถเชื่อมต่อ server"
}

# ── STOP ──────────────────────────────────────────────────────────────────────
do_stop() {
  banner "Stop"
  if systemctl is-active --quiet eggsort 2>/dev/null; then
    sudo systemctl stop eggsort; ok "systemd service หยุดแล้ว"
  fi
  if docker compose -f "$APP_DIR/docker-compose.yml" ps 2>/dev/null | grep -q "Up"; then
    docker compose -f "$APP_DIR/docker-compose.yml" down; ok "Docker หยุดแล้ว"
  fi
  pkill -f "server.py" 2>/dev/null && ok "Process หยุดแล้ว" || true
}

# ── LOGS ──────────────────────────────────────────────────────────────────────
do_logs() {
  if systemctl is-active --quiet eggsort 2>/dev/null; then
    journalctl -u eggsort -f --no-pager
  elif docker compose -f "$APP_DIR/docker-compose.yml" ps 2>/dev/null | grep -q "eggsort"; then
    docker compose -f "$APP_DIR/docker-compose.yml" logs -f
  else
    tail -f "$APP_DIR/logs/system.log" 2>/dev/null || warn "ไม่พบ log file"
  fi
}

# ── ROUTER ────────────────────────────────────────────────────────────────────
CMD="${1:-help}"
case "$CMD" in
  local)  deploy_local  ;;
  docker) deploy_docker ;;
  rpi)    deploy_rpi    ;;
  update) deploy_update ;;
  status) show_status   ;;
  stop)   do_stop       ;;
  logs)   do_logs       ;;
  *)
    echo "การใช้งาน: ./deploy.sh <คำสั่ง>"
    echo ""
    echo "  local    — รันบน machine ปัจจุบัน (Python venv)"
    echo "  docker   — รันผ่าน Docker Compose"
    echo "  rpi      — ติดตั้งบน Raspberry Pi + systemd + Nginx  [sudo]"
    echo "  update   — อัปเดต dependencies และรีสตาร์ท"
    echo "  status   — ตรวจสอบสถานะระบบ"
    echo "  stop     — หยุดระบบ"
    echo "  logs     — ดู log แบบ real-time"
    ;;
esac
