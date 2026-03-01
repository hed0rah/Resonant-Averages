FROM python:3.12-slim

WORKDIR /app

# install system deps for soundfile/librosa
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# install python deps first (layer cache)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# copy project files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# railway.toml [deploy].startCommand handles startup
# for local docker: docker run -p 8000:8000 -e PORT=8000 <image> uvicorn backend.main:app --host 0.0.0.0 --port 8000
