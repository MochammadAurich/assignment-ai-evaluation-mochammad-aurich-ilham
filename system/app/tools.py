"""
Modul Tools — fungsi-fungsi "aksi" yang dipanggil router ketika intent
BUKAN pertanyaan informasi/kebijakan, melainkan permintaan spesifik
yang butuh pengecekan data langsung (bukan retrieval semantik).

Pola ini mengikuti konsep Tool Integration di Sesi 12.3.
"""

import pandas as pd

MOCK_TICKETS_PATH = "data/mock_tickets.csv"


def get_ticket_status(ticket_id: str) -> str:
    """
    Mencari status tiket berdasarkan ticket_id pada data mock_tickets.csv.
    Jika ticket_id tidak ditemukan, mengembalikan pesan yang informatif
    (bukan error yang membuat aplikasi crash).
    """
    # 1. Memuat mock_tickets.csv menggunakan pandas
    try:
        df = pd.read_csv(MOCK_TICKETS_PATH, dtype=str)
    except FileNotFoundError:
        return "Maaf, data tiket tidak dapat diakses saat ini. Silakan coba lagi nanti."
 
    # 2. Mencari baris dengan ticket_id yang cocok (abaikan huruf besar/kecil dan spasi)
    ticket_id = (ticket_id or "").strip().upper()
    match = df[df["ticket_id"].str.strip().str.upper() == ticket_id]
 
    # 3. Jika tidak ditemukan, mengembalikan pesan bahwa tiket tidak ditemukan
    if match.empty:
        return (
            f"Tiket {ticket_id} tidak ditemukan. "
            "Mohon periksa kembali ID tiket Anda (format: TICKET-XXXX)."
        )
 
    # 4. Jika ditemukan, menyusun string jawaban (subject, status, last_update)
    row = match.iloc[0]
    return (
        f"Tiket {row['ticket_id']} — Subjek: {row['subject']}. "
        f"Kategori: {row['category']}. "
        f"Status: {row['status']}. "
        f"Update terakhir: {row['last_update']}."
    )