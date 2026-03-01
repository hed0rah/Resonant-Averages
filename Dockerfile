FROM python:3.12-slim

WORKDIR /app

# copy project files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# install dependencies
RUN pip install --no-cache-dir \
    fastapi==0.104.1 \
    uvicorn==0.24.0 \
    librosa==0.11.0 \
    numpy==1.26.2 \
    scipy==1.16.2 \
    soundfile==0.13.1 \
    pydantic==2.5.0 \
    python-multipart==0.0.6 \
    audioread==3.0.1

# start app
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
