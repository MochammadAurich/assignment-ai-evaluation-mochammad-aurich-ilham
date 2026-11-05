"""
Router — menentukan apakah query user butuh RAG (informasi/kebijakan)
atau tool call (aksi spesifik, misal cek status tiket).

Pola ini mengikuti workflow router yang sudah dipelajari di Sesi 12.4:
  user input -> intent detection -> jika butuh data -> retrieval (RAG)
                                  -> jika butuh aksi -> tool call
"""

import os
import re
import logging
from typing import Optional
 
from dotenv import load_dotenv
from google import genai
# from google.genai import types

from app import metrics
from app.rag import answer_from_knowledge_base
from app.tools import get_ticket_status

load_dotenv()
logger = logging.getLogger(__name__)

# LLM client (Gemini AI API) untuk intent detection
_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Model untuk intent detection
GEMINI_MODEL = "gemini-3.5-flash-lite"

# Format ticket_id pada mock_tickets.csv: 'TICKET-XXXX'.
# Toleran terhadap variasi penulisan, misal "ticket 42", "tiket #0042", "TICKET_0042".
_TICKET_PATTERN = re.compile(r"(?:ticket|tiket)[\s\-_#:]*(\d+)", re.IGNORECASE)
 
# Keyword baseline (dipakai sebagai fallback jika LLM gagal merespons)
_TICKET_WORDS = ("tiket", "ticket")
_STATUS_WORDS = ("status", "progres", "progress", "perkembangan", "sudah sampai mana", "update")
 
_INTENT_PROMPT = """Klasifikasikan pertanyaan berikut ke dalam salah satu kategori:
'faq' (pertanyaan informasi/kebijakan umum, termasuk cara/prosedur melakukan sesuatu) atau
'ticket_status' (permintaan mengecek status/progres sebuah tiket tertentu).
Jawab hanya dengan satu kata: faq atau ticket_status.
Pertanyaan: {query}"""


def _keyword_intent(query: str) -> str:
    """Pendekatan sederhana berbasis keyword (baseline / fallback)."""
    q = query.lower()
    if any(w in q for w in _TICKET_WORDS) and any(w in q for w in _STATUS_WORDS):
        return "ticket_status"
    return "faq"


async def detect_intent(query: str) -> str:
    """
    Menentukan intent dari query user. Mengembalikan salah satu:
      - "faq"           -> pertanyaan informasi/kebijakan, arahkan ke RAG
      - "ticket_status"  -> permintaan cek status tiket, arahkan ke tool call
      - "unknown"        -> jawaban LLM di luar dua kategori di atas

    Kriteria:
      1. Jika query memuat ID tiket (TICKET-XXXX) -> ticket_status (aturan pasti, tanpa LLM).
      2. Selain itu, LLM mengklasifikasikan query (structured prompt, jawab satu kata).
      3. Jika LLM gagal, gunakan baseline keyword.
    """
    # Aturan 1: ada ID tiket pada query
    if extract_ticket_id(query) is not None:
        metrics.set_field("intent_source", "regex")
        return "ticket_status"
 
    # Aturan 2: klasifikasi dengan LLM
    try:
        with metrics.timed("llm_intent"):
            response = await _client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=_INTENT_PROMPT.format(query=query),
            )
        metrics.add_usage(response)
        metrics.set_field("intent_source", "llm")
        label = (response.text or "").strip().lower()
    except Exception:
        # Aturan 3: fallback keyword
        logger.exception("Intent detection via LLM gagal, memakai fallback keyword.")
        metrics.set_field("intent_source", "keyword_fallback")
        return _keyword_intent(query)
 
    if "ticket_status" in label:
        return "ticket_status"
    if "faq" in label:
        return "faq"
    return "unknown"


async def handle_query(query: str) -> dict:
    """
    Fungsi utama yang dipanggil oleh endpoint /ask.
    Menjalankan intent detection, lalu merutekan ke RAG atau tool call yang sesuai.
    """
    intent = await detect_intent(query)

    if intent == "ticket_status":
        # Mengekstrak ticket_id dari query
        ticket_id = extract_ticket_id(query)
        if ticket_id is None:
            return {
                "status": "error",
                "intent": "ticket_status",
                "data": "Mohon sertakan ID tiket Anda dengan format TICKET-XXXX, misalnya TICKET-0042.",
            }
        data = get_ticket_status(ticket_id)
        return {"status": "success", "intent": "ticket_status", "data": data}

    elif intent == "faq":
        data = await answer_from_knowledge_base(query)
        return {"status": "success", "intent": "faq", "data": data}

    else:
        return {"status": "error", "intent": "unknown", "data": "Maaf, saya tidak memahami permintaan Anda."}


def extract_ticket_id(query: str) -> Optional[str]:
    """
    Mengekstrak ticket_id dari teks query dan menormalkannya ke format 'TICKET-XXXX'.
    Contoh: "status tiket 42" -> "TICKET-0042". Mengembalikan None jika tidak ada ID tiket.
    """
    match = _TICKET_PATTERN.search(query)
    if not match:
        return None
    return f"TICKET-{match.group(1).zfill(4)}"
