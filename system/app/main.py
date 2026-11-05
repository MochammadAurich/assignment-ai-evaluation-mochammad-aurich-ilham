"""
Entry point aplikasi FastAPI — IT Helpdesk Assistant.

Alur endpoint utama:
  1. Menerima pertanyaan/permintaan dari user
  2. Meneruskan ke router (router.py) untuk menentukan intent
  3. Router memanggil RAG (rag.py) atau tool (tools.py) sesuai intent
  4. Mengembalikan response terstruktur ke client
"""

import json
import logging
import time

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import metrics
from app.router import handle_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="IT Helpdesk Assistant API")

# Perbaikan Isu C: batas panjang query. Query evaluasi terpanjang hanya 148 karakter; pada baseline,
# query ~20.000 karakter diterima dan memakai 7.256 token (sekitar 15,6 kali rata-rata per request).
MAX_QUERY_LENGTH = 500


class AskRequest(BaseModel):
    query: str


class AskResponse(BaseModel):
    status: str
    intent: str
    data: str


@app.get("/health")
async def health():
    """Endpoint sederhana untuk memastikan service berjalan."""
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, response: Response):
    """
    Endpoint utama. Menerima pertanyaan bebas dari user (bisa berupa
    pertanyaan informasi/kebijakan ATAU permintaan cek status tiket),
    lalu meneruskannya ke router untuk diproses.

    Error handling:
      - query kosong            -> 400, status "error"
      - query > MAX_QUERY_LENGTH -> 400, status "error"
      - intent tidak dikenali   -> ditangani router (status "error", intent "unknown")
      - LLM/layanan eksternal gagal merespons -> 503, status "error"
    """
    if not request.query.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "intent": "unknown", "data": "Pertanyaan tidak boleh kosong."},
        )

    if len(request.query) > MAX_QUERY_LENGTH:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "intent": "unknown",
                "data": f"Pertanyaan terlalu panjang (maksimal {MAX_QUERY_LENGTH} karakter). Mohon persingkat pertanyaan Anda.",
            },
        )

    request_metrics = metrics.start()
    t0 = time.perf_counter()
    
    try:
        result = await handle_query(request.query)
    except Exception as exc:
        # Misalkan API key salah, kuota habis, timeout, atau LLM tidak mengembalikan jawaban
        logger.exception("Gagal memproses query: %s", request.query)
        request_metrics["timings_ms"]["server_total"] = round((time.perf_counter() - t0) * 1000, 1)
        request_metrics["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "intent": "unknown",
                "data": "Maaf, layanan AI sedang mengalami gangguan. Silakan coba lagi beberapa saat lagi.",
            },
            headers={"X-Metrics": json.dumps(request_metrics)},
        )

    request_metrics["timings_ms"]["server_total"] = round((time.perf_counter() - t0) * 1000, 1)
    # Metrik latency/token per tahap, untuk keperluan evaluasi (dibaca oleh skrip evaluasi)
    response.headers["X-Metrics"] = json.dumps(request_metrics)
    
    return AskResponse(**result)
