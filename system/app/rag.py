"""
Modul RAG — mengubah knowledge base menjadi embedding, menyimpannya ke FAISS,
melakukan semantic search, lalu menghasilkan jawaban menggunakan LLM.

Alur ini serupa dengan yang sudah dibangun di Assignment 3 (Semantic Search &
LLM Application) dan Sesi 11 (Building a Simple RAG System) — di assignment ini,
alur tersebut dibungkus menjadi fungsi yang dipanggil oleh router.py.
"""

import os
import asyncio
import threading

import pandas as pd
import numpy as np
import faiss
from dotenv import load_dotenv
from google import genai  # sesuaikan dengan library Gemini API yang digunakan
from google.genai import types

from app import metrics

# Membaca file .env agar GEMINI_API_KEY terbaca saat dijalankan lokal.
# Di Docker, variabel diberikan lewat `docker run --env-file .env`, jadi baris ini aman diabaikan.
load_dotenv()

_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Nama model
EMBEDDING_MODEL = "gemini-embedding-001"
GEMINI_MODEL = "gemini-3.5-flash-lite"

KNOWLEDGE_BASE_PATH = "data/it_helpdesk_knowledge_base.csv"

# Perbaikan Isu B: query panjang umumnya memuat beberapa topik sekaligus. Satu vektor query dengan
# top_k=3 tidak menjangkau semua topik (EVAL-11: retrieval hanya FAQ #12, #19, #18, tanpa FAQ cuti
# maupun #14), sehingga LLM keliru menyatakan informasi "belum dimiliki". Query yang lebih panjang
# dari LONG_QUERY_CHARS memakai mode multi-topik: top_k lebih besar dan instruksi menjawab per topik.
# Query pendek tidak berubah (prompt dan jumlah token sama seperti sebelumnya).
DEFAULT_TOP_K = 3
MULTI_TOPIC_TOP_K = 8
LONG_QUERY_CHARS = 100

# Index FAISS dibangun sekali (lazy-loading): saat request pertama masuk,
# lalu disimpan di variabel global ini dan dipakai ulang di request berikutnya.
_index = None
_documents = None
_df = None
_build_lock = threading.Lock()


def _get_embedding(text: str, task_type: str = "RETRIEVAL_QUERY"):
    result = _client.models.embed_content(
        model=EMBEDDING_MODEL,  # sesuaikan versi model embedding
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return result.embeddings[0].values


def _get_embeddings_batch(texts: list, task_type: str = "RETRIEVAL_DOCUMENT", batch_size: int = 50):
    """Embedding banyak teks sekaligus (per batch) agar tidak memanggil API satu per satu."""
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        result = _client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        vectors.extend(e.values for e in result.embeddings)
    return vectors


def build_index():
    """
    Membangun index FAISS dari knowledge base CSV.
    Alur: load data -> unit retrievable -> embedding -> simpan ke FAISS.
    """
    global _index, _documents, _df
 
    # 1. Load data
    df = pd.read_csv(KNOWLEDGE_BASE_PATH)
    df = df.dropna(subset=["question", "answer"]).reset_index(drop=True)
 
    # 2. Unit retrievable: gabungan kategori + pertanyaan + jawaban per baris FAQ
    documents = [
        f"Kategori: {row.category}\nPertanyaan: {row.question}\nJawaban: {row.answer}"
        for row in df.itertuples()
    ]
 
    # 3. Embedding (dokumen)
    vectors = np.array(_get_embeddings_batch(documents, "RETRIEVAL_DOCUMENT"), dtype="float32")
 
    # 4. Menyimpan ke FAISS. Vektor dinormalisasi + inner product = cosine similarity
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
 
    # Variabel global diisi di akhir, agar index setengah jadi tidak pernah tersimpan
    _df = df
    _documents = documents
    _index = index


def _ensure_index():
    """Membangun index hanya jika belum ada (aman dipanggil dari banyak request sekaligus)."""
    if _index is None:
        with _build_lock:
            if _index is None:
                with metrics.timed("index_build"):
                    build_index()


def semantic_search(query: str, top_k: int = 3):
    """
    Semantic search terhadap index yang sudah dibangun.
    Mengembalikan list dict: faq_id, category, question, answer, score (urut dari paling relevan).
    """
    _ensure_index()
 
    with metrics.timed("embedding"):
        query_vec = np.array([_get_embedding(query, "RETRIEVAL_QUERY")], dtype="float32")
    faiss.normalize_L2(query_vec)
 
    k = min(top_k, _index.ntotal)
    with metrics.timed("faiss_search"):
        scores, ids = _index.search(query_vec, k)
 
    results = []
    for score, idx in zip(scores[0], ids[0]):
        if idx == -1:
            continue
        row = _df.iloc[idx]
        results.append({
            "faq_id": int(row["faq_id"]),
            "category": row["category"],
            "question": row["question"],
            "answer": row["answer"],
            "score": float(score),
        })
    return results


def _build_prompt(query: str, results: list, multi_topic: bool = False) -> str:
    """Menyusun prompt dengan struktur Persona / Task / Context / Format."""
    task_extra = ""
    length_rule = "- Jawab dalam 2-4 kalimat; gunakan daftar bernomor hanya jika menjelaskan langkah/prosedur."
    if multi_topic:
        task_extra = (
            "\nPertanyaan ini mencakup beberapa topik. Jawab setiap topik yang tersedia di CONTEXT. "
            "Untuk topik yang tidak ada di CONTEXT, sebutkan topik tersebut secara spesifik sebagai belum "
            "ditemukan pada hasil pencarian kali ini dan sarankan karyawan menanyakannya secara terpisah; "
            "jangan menyimpulkan bahwa informasi tersebut tidak ada."
        )
        length_rule = "- Gunakan daftar bernomor, satu butir per topik, maksimal 3 kalimat per butir."
 
    context = "\n\n".join(
        f"[{i}] Kategori: {r['category']}\nPertanyaan: {r['question']}\nJawaban: {r['answer']}"
        for i, r in enumerate(results, start=1)
    )
 
    return f"""PERSONA:
Anda adalah asisten virtual IT Helpdesk & People Operations di sebuah perusahaan. Anda ramah, profesional, dan ringkas.
 
TASK:
Jawab pertanyaan karyawan di bawah ini HANYA berdasarkan informasi pada bagian CONTEXT.
Jika CONTEXT tidak memuat jawaban yang relevan, jangan mengarang. Katakan bahwa Anda belum memiliki informasi tersebut dan sarankan karyawan menghubungi tim IT Support atau HR melalui tiket.{task_extra}
 
CONTEXT:
{context}
 
PERTANYAAN KARYAWAN:
{query}
 
FORMAT:
- Gunakan Bahasa Indonesia.
{length_rule}
- Jangan menyebut kata "CONTEXT" atau nomor sumber pada jawaban."""


async def answer_from_knowledge_base(query: str) -> str:
    """
    Fungsi yang dipanggil router.py ketika intent = 'faq'.
    Menjalankan semantic search, menyusun prompt dengan context hasil retrieval,
    lalu memanggil LLM untuk menghasilkan jawaban.
    """
    multi_topic = len(query) > LONG_QUERY_CHARS
    top_k = MULTI_TOPIC_TOP_K if multi_topic else DEFAULT_TOP_K
    metrics.set_field("multi_topic", multi_topic)
    results = await asyncio.to_thread(semantic_search, query, top_k)
    metrics.set_field("retrieved", [{"faq_id": r["faq_id"], "score": round(r["score"], 4)} for r in results])
 
    prompt = _build_prompt(query, results, multi_topic)
 
    with metrics.timed("llm_answer"):
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
    metrics.add_usage(response)
 
    answer = (response.text or "").strip()
    if not answer:
        raise RuntimeError("LLM mengembalikan respons kosong.")
    return answer
