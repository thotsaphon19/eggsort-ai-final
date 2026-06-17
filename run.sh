#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# ติดตั้งและเริ่มต้นระบบ AI คัดแยกขนาดไข่
# ใช้งาน: chmod +x run.sh && ./run.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "╔══════════════════════════════════════════════════════╗"
echo "║    ระบบ AI คัดแยกขนาดไข่บนสายพานลำเลียง            ║"
echo "║    Industrial Egg Grading — CR5M03 USB Camera 5MP   ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ตรวจสอบ Python
if ! command -v python3 &>/dev/null; then
  echo "❌ ไม่พบ Python 3 กรุณาติดตั้งก่อน: https://python.org"
  exit 1
fi
echo "✓ Python $(python3 --version)"

# ตรวจสอบ API Key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo ""
  echo "⚠️  ยังไม่ได้ตั้งค่า ANTHROPIC_API_KEY"
  echo "   ตั้งค่าด้วยคำสั่ง:"
  echo "   export ANTHROPIC_API_KEY='your-key-here'"
  echo "   หรือสร้างไฟล์ .env แล้วรัน: source .env && ./run.sh"
  echo ""
  read -p "   ใส่ API Key ตอนนี้เลย (Enter เพื่อข้าม): " key
  [ -n "$key" ] && export ANTHROPIC_API_KEY="$key"
fi

# สร้าง virtualenv (ถ้ายังไม่มี)
if [ ! -d "venv" ]; then
  echo ""
  echo "📦 สร้าง virtual environment..."
  python3 -m venv venv
fi

# ติดตั้ง dependencies
echo "📦 ติดตั้ง/อัปเดต dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

# สร้างโฟลเดอร์ logs
mkdir -p logs/images

echo ""
echo "🚀 เริ่มต้นระบบ..."
echo "   🌐 Web Dashboard : http://localhost:8080"
echo "   🔌 WebSocket     : ws://localhost:8765"
echo "   📁 บันทึก log    : ./logs/"
echo ""
echo "   กด Ctrl+C เพื่อหยุดระบบ"
echo ""

# ตั้งค่าตัวแปรสภาพแวดล้อม (แก้ไขได้)
export CAMERA_INDEX=${CAMERA_INDEX:-0}
export CAPTURE_INTERVAL=${CAPTURE_INTERVAL:-2.0}
export SAVE_IMAGES=${SAVE_IMAGES:-true}

python3 server.py
