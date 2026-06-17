FROM python:3.11-slim

# libgl1-mesa-glx ถูกเปลี่ยนชื่อเป็น libgl1 ใน Debian 12 (Bookworm)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY database.py .
COPY templates/ templates/

RUN mkdir -p logs/images
VOLUME ["/app/logs"]

ENV RENDER=true \
    CAPTURE_INTERVAL=2.0 \
    SAVE_IMAGES=false \
    WS_HOST=0.0.0.0

EXPOSE 8080 8765

CMD ["python", "server.py"]
