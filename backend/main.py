"""FastAPI app — routes, middleware, async dispatch"""
import asyncio
import json
import concurrent.futures
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("resonant")

# relative import for docker/package mode; fallback for local dev
try:
    from .processor import ProcessParams, get_audio_info, process_multi, process_single
except ImportError:
    from processor import ProcessParams, get_audio_info, process_multi, process_single

# ── constants ────────────────────────────────────────────────────────────────

# upload size cap is enforced by nginx (client_max_body_size 210m)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── thread pool (librosa releases GIL, threads are sufficient) ───────────────

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

IDLE_TIMEOUT = 300  # seconds before process exits when no requests are active
_last_request: float = time.monotonic()


async def _idle_watcher():
    while True:
        await asyncio.sleep(60)
        if time.monotonic() - _last_request > IDLE_TIMEOUT:
            os.kill(os.getpid(), signal.SIGTERM)  # clean exit → systemd won't restart


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_idle_watcher())
    yield
    task.cancel()
    executor.shutdown(wait=False)


# ── app ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Resonant Averages", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://resonant.grivtdynamics.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def track_request_activity(request: Request, call_next):
    global _last_request
    _last_request = time.monotonic()
    return await call_next(request)


# serve frontend at root — api routes take priority over mount
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ── param model ──────────────────────────────────────────────────────────────

class RawParams(BaseModel):
    mode: str = "single"
    n_fft: int = Field(default=2048, ge=512, le=65536)
    hop_pct: float = Field(default=25.0, ge=10.0, le=75.0)
    output_duration: float = Field(default=10.0, ge=1.0, le=120.0)
    sample_rate: int = Field(default=22050, ge=8000, le=48000)
    contrast_enable: bool = True
    contrast_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    boost_power: float = Field(default=2.0, ge=0.1, le=10.0)
    suppress_power: float = Field(default=2.0, ge=0.1, le=10.0)
    griffinlim_iters: int = Field(default=32, ge=4, le=128)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("single", "multi"):
            raise ValueError("mode must be 'single' or 'multi'")
        return v

    def to_process_params(self) -> ProcessParams:
        hop_length = max(1, int(self.n_fft * self.hop_pct / 100))
        return ProcessParams(
            mode=self.mode,
            n_fft=self.n_fft,
            hop_length=hop_length,
            output_duration=self.output_duration,
            sample_rate=self.sample_rate,
            contrast_enable=self.contrast_enable,
            contrast_threshold=self.contrast_threshold,
            boost_power=self.boost_power,
            suppress_power=self.suppress_power,
            griffinlim_iters=self.griffinlim_iters,
        )


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/info")
async def file_info(file: UploadFile = File(...)):
    """return lightweight metadata for a single audio file"""
    raw = await file.read()
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(executor, get_audio_info, raw)
    except Exception:
        log.exception("audio info read failed")
        raise HTTPException(status_code=422, detail="could not read file")
    return JSONResponse(info)


@app.post("/api/process")
async def process(
    files: List[UploadFile] = File(...),
    params: str = Form(...),  # JSON string — FastAPI 0.104 can't mix JSON body + multipart
):
    """average spectral content of uploaded files and return synthesized WAV"""
    # parse + validate params
    try:
        raw_params = RawParams.model_validate(json.loads(params))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid params: {e}")

    params_obj = raw_params.to_process_params()

    # validate file count vs mode
    if raw_params.mode == "single" and len(files) != 1:
        raise HTTPException(status_code=422, detail="single mode requires exactly one file")
    if raw_params.mode == "multi" and len(files) < 2:
        raise HTTPException(status_code=422, detail="multi mode requires at least two files")

    # read all file bytes (async, non-blocking)
    files_bytes: list[bytes] = []
    for f in files:
        files_bytes.append(await f.read())

    # dispatch CPU work to thread pool
    loop = asyncio.get_event_loop()
    try:
        if raw_params.mode == "single":
            wav_bytes = await loop.run_in_executor(
                executor, process_single, files_bytes[0], params_obj
            )
        else:
            wav_bytes = await loop.run_in_executor(
                executor, process_multi, files_bytes, params_obj
            )
    except Exception:
        log.exception("processing failed")
        raise HTTPException(status_code=500, detail="processing failed")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="resonant-average.wav"'},
    )


# ── entrypoint ───────────────────────────────────────────────────────────────
# local dev: run from project root with `python -m uvicorn backend.main:app --reload`
# or: `cd backend && python -m uvicorn main:app --reload` (uses non-relative import fallback)
