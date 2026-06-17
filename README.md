# 🥚 ระบบ AI คัดแยกขนาดไข่บนสายพานลำเลียง
**Industrial Egg Grading System** — กล้อง CR5M03 USB500W05G-SFV(2.8-12) · Claude Vision AI

---

## โครงสร้างไฟล์

```
egg-sorter/
├── server.py          ← โปรแกรมหลัก (backend + WebSocket + HTTP)
├── requirements.txt   ← dependencies
├── run.sh             ← สคริปต์ติดตั้งและรัน (Linux/macOS)
├── templates/
│   └── index.html     ← Web Dashboard
└── logs/              ← บันทึกข้อมูล (สร้างอัตโนมัติ)
    ├── system.log
    ├── log_YYYYMMDD.csv
    └── images/        ← ภาพที่บันทึก (ถ้าเปิดใช้งาน)
```

---

## การติดตั้ง

### 1. ติดตั้ง Python 3.10+
```bash
# Ubuntu/Debian
sudo apt install python3 python3-pip python3-venv

# Windows — ดาวน์โหลดจาก python.org
```

### 2. ติดตั้ง OpenCV dependencies (Linux)
```bash
sudo apt install libgl1-mesa-glx libglib2.0-0
```

### 3. ตั้งค่า API Key
```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxxxxxx"
```

### 4. รันระบบ
```bash
chmod +x run.sh
./run.sh
```
หรือบน Windows:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-xxxx
python server.py
```

### 5. เปิด Dashboard
เปิดเบราว์เซอร์: **http://localhost:8080**

---

## การใช้งาน

1. **เสียบกล้อง** CR5M03 ผ่าน USB
2. **ค้นหากล้อง** — กดปุ่ม 🔍 เพื่อสแกนหากล้องที่เชื่อมต่อ
3. **เลือกกล้อง** — เลือกจากรายการ (ปกติคือ Camera 0)
4. **ตั้งความถี่** — เลื่อน Slider ตามความเร็วสายพาน (แนะนำ 1-3 วินาที)
5. **กด ▶ เริ่มสายพาน** — ระบบจะจับภาพและวิเคราะห์อัตโนมัติ
6. **ดูผลลัพธ์** ผ่าน Dashboard แบบ real-time

---

## การตั้งค่าผ่าน Environment Variables

| ตัวแปร | ค่าเริ่มต้น | คำอธิบาย |
|--------|------------|---------|
| `ANTHROPIC_API_KEY` | — | **จำเป็น** — API Key จาก console.anthropic.com |
| `CAMERA_INDEX` | `0` | ดัชนีกล้อง (0 = แรก) |
| `CAPTURE_WIDTH` | `2592` | ความกว้างภาพ (pixel) |
| `CAPTURE_HEIGHT` | `1944` | ความสูงภาพ (pixel) |
| `CAPTURE_INTERVAL` | `2.0` | วินาทีต่อการสแกน |
| `HTTP_PORT` | `8080` | พอร์ต Web Dashboard |
| `WS_PORT` | `8765` | พอร์ต WebSocket |
| `SAVE_IMAGES` | `true` | บันทึกภาพทุกครั้ง |

ตัวอย่าง:
```bash
CAMERA_INDEX=1 CAPTURE_INTERVAL=1.5 python server.py
```

---

## เกณฑ์การจัดเกรด (มาตรฐานไทย)

| เกรด | น้ำหนัก | คำแนะนำ |
|------|---------|--------|
| **AA** | ≥70g | ผ่าน — ส่งออก / ซูเปอร์มาร์เก็ต |
| **A**  | 60–69g | ผ่าน — ตลาดทั่วไป |
| **B**  | 50–59g | ผ่าน — โรงงานแปรรูป |
| **C**  | <50g | ตรวจสอบ — อาจไม่ผ่านมาตรฐาน |

ระบบยังวิเคราะห์: สภาพเปลือก · รอยร้าว · ความสกปรก · รูปร่าง

---

## ดาวน์โหลดข้อมูล

- **CSV ประจำวัน**: http://localhost:8080/export
- **API stats**: http://localhost:8080/api/stats
- **บันทึกภาพ**: `logs/images/YYYYMMDD/`

---

## แก้ไขปัญหา

**กล้องไม่พบ**
```bash
# ตรวจสอบกล้อง Linux
ls /dev/video*
# ตรวจสอบ permission
sudo usermod -aG video $USER
```

**ภาพมืดหรือไม่คมชัด**
- ปรับ aperture บน Lens 2.8-12mm
- แก้ไข `alpha` และ `beta` ใน `camera.grab()` ใน server.py

**AI วิเคราะห์ช้า**
- เพิ่ม `CAPTURE_INTERVAL` ให้มากขึ้น
- ลดความละเอียดภาพ (`CAPTURE_WIDTH`, `CAPTURE_HEIGHT`)
