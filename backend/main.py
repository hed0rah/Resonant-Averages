"""FastAPI app — routes, middleware, async dispatch"""
import asyncio
import json
import concurrent.futures
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from processor import ProcessParams, get_audio_info, process_multi, process_single

# ── constants ────────────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB total
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── thread pool (librosa releases GIL, threads are sufficient) ───────────────

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    executor.shutdown(wait=False)


# ── app ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Resonant Averages", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # public tool, safe to allow all origins
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    if request.method == "POST":
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_UPLOAD_BYTES:
            return JSONResponse({"detail": "upload too large (max 200 MB)"}, status_code=413)
    return await call_next(request)


# serve frontend from same process (optional — also works via live server)
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


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
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"could not read file: {e}")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"processing failed: {e}")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="resonant-average.wav"'},
    )


# ── entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
