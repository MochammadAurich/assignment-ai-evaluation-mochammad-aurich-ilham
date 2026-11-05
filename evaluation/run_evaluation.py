"""
Skrip evaluasi: menjalankan 12 skenario di evaluation_test_scenarios.csv
terhadap endpoint /ask, lalu menyimpan hasilnya.

Output (di folder yang sama dengan skrip ini):
  - evaluation_results.csv   : 1 baris per skenario (response aktual, latency, token, latency per tahap)
  - evaluation_runs_raw.csv  : 1 baris per percobaan (untuk melihat konsistensi jawaban antar-run)

Cara pakai (server sistem harus sudah berjalan, dari dalam folder system/):
  python evaluation/run_evaluation.py
  python evaluation/run_evaluation.py --runs 3 --base-url http://127.0.0.1:8000
  python evaluation/run_evaluation.py --runs 3 --min-interval 10   # beri jeda agar tidak kena batas kuota API

Catatan: statistik waktu dan token dihitung hanya dari percobaan yang berhasil (HTTP 200);
jumlah percobaan gagal dicatat terpisah di kolom failed_runs / status_codes.

Kolom `observation` sengaja dikosongkan; diisi manual setelah hasil ditinjau.
"""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
WARMUP_QUERY = "berapa jatah cuti tahunan saya?"  # memicu pembangunan index FAISS sebelum pengukuran

STAGES = ["llm_intent", "embedding", "faiss_search", "llm_answer", "server_total"]


def load_scenarios(path: Path) -> list:
    """
    Membaca skenario. Kolom `expected_behavior_note` pada beberapa baris mengandung koma
    tanpa tanda kutip, sehingga bagian setelah kolom ke-4 digabung kembali.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    scenarios = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        scenarios.append({
            "scenario_id": row[0].strip(),
            "category": row[1].strip(),
            "query": row[2].strip(),
            "evaluation_focus": row[3].strip(),
            "expected_behavior": ",".join(row[4:]).strip().strip(",").strip(),
        })
    return scenarios


def call_ask(base_url: str, query: str, timeout: int = 90) -> dict:
    """Kirim satu query ke /ask dan ukur waktu respons (detik, sisi client)."""
    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{base_url}/ask", json={"query": query}, timeout=timeout)
    except requests.RequestException as exc:
        return {
            "http_status": "CONNECTION_ERROR", "status": "", "intent": "",
            "data": str(exc), "response_time_s": round(time.perf_counter() - t0, 3), "metrics": {},
        }
    elapsed = round(time.perf_counter() - t0, 3)

    try:
        body = resp.json()
    except ValueError:
        body = {"status": "", "intent": "", "data": resp.text}

    try:
        metrics = json.loads(resp.headers.get("X-Metrics", "{}"))
    except ValueError:
        metrics = {}

    return {
        "http_status": resp.status_code,
        "status": body.get("status", ""),
        "intent": body.get("intent", ""),
        "data": body.get("data", body.get("detail", "")),
        "response_time_s": elapsed,
        "metrics": metrics,
    }


def retrieval_summary(metrics: dict):
    """Ringkas hasil retrieval dari X-Metrics: (faq_id dipisah ';', skor dipisah ';', skor tertinggi)."""
    items = metrics.get("retrieved", []) or []
    ids = ";".join(str(i["faq_id"]) for i in items)
    scores = ";".join(str(i["score"]) for i in items)
    top = max((i["score"] for i in items), default="")
    return ids, scores, top


def median_or_blank(values: list):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else ""


def main():
    parser = argparse.ArgumentParser(description="Jalankan evaluation scenarios terhadap /ask")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=3, help="jumlah percobaan per skenario")
    parser.add_argument("--min-interval", type=float, default=0.0,
                        help="jeda minimum (detik) antar-awal request yang memakai LLM; 0 = tanpa jeda (mode burst)")
    parser.add_argument("--scenarios", default=str(BASE_DIR / "evaluation_test_scenarios.csv"))
    parser.add_argument("--output", default=str(BASE_DIR / "evaluation_results.csv"))
    parser.add_argument("--raw-output", default=str(BASE_DIR / "evaluation_runs_raw.csv"))
    args = parser.parse_args()

    scenarios = load_scenarios(Path(args.scenarios))
    print(f"{len(scenarios)} skenario dimuat, {args.runs} percobaan per skenario.")

    # Warm-up: request pertama membangun index FAISS, jadi tidak ikut dihitung
    warmup = call_ask(args.base_url, WARMUP_QUERY)
    print(f"Warm-up: HTTP {warmup['http_status']}, {warmup['response_time_s']} detik (tidak dihitung)")
    if warmup["http_status"] != 200:
        print("PERINGATAN: warm-up tidak berstatus 200. Periksa server dan API key sebelum melanjutkan.")

    result_rows, raw_rows = [], []
    pacing = {"last_start": time.perf_counter(), "used_llm": True}  # warm-up memakai LLM

    def paced_call(query: str) -> dict:
        """Panggil /ask; jika request sebelumnya memakai LLM, tunggu agar jarak antar-awal >= min-interval."""
        if args.min_interval > 0 and pacing["used_llm"]:
            wait = pacing["last_start"] + args.min_interval - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        pacing["last_start"] = time.perf_counter()
        result = call_ask(args.base_url, query)
        llm_calls = result["metrics"].get("llm_calls", 0) or 0
        pacing["used_llm"] = llm_calls > 0 or result["http_status"] != 200
        return result

    for sc in scenarios:
        runs = []
        for run_no in range(1, args.runs + 1):
            r = paced_call(sc["query"])
            runs.append(r)
            m = r["metrics"]
            ret_ids, ret_scores, _ = retrieval_summary(m)
            raw_rows.append({
                "scenario_id": sc["scenario_id"], "run": run_no, "query": sc["query"],
                "http_status": r["http_status"], "status": r["status"], "intent": r["intent"],
                "response_time_s": r["response_time_s"],
                "intent_source": m.get("intent_source", ""),
                "llm_calls": m.get("llm_calls", ""),
                "retrieved_faq_ids": ret_ids,
                "retrieved_scores": ret_scores,
                "error": m.get("error", ""),
                "prompt_tokens": m.get("tokens", {}).get("prompt", ""),
                "output_tokens": m.get("tokens", {}).get("output", ""),
                "thinking_tokens": m.get("tokens", {}).get("thinking", ""),
                "total_tokens": m.get("tokens", {}).get("total", ""),
                "actual_response": r["data"],
            })

        ok_runs = [r for r in runs if r["http_status"] == 200]
        times = [r["response_time_s"] for r in ok_runs]
        first = ok_runs[0] if ok_runs else runs[0]  # contoh response: percobaan sukses pertama jika ada
        m_first = first["metrics"]
        ret_ids, ret_scores, top_score = retrieval_summary(m_first)
        failed = [r for r in runs if r["http_status"] != 200]
        first_error = next((r["metrics"].get("error", "") for r in failed if r["metrics"].get("error")), "")
        row = {
            "scenario_id": sc["scenario_id"],
            "category": sc["category"],
            "query": sc["query"],
            "expected_behavior": sc["expected_behavior"],
            "http_status": first["http_status"],
            "status": first["status"],
            "intent": first["intent"],
            "intent_source": m_first.get("intent_source", ""),
            "actual_response": first["data"],
            "response_time_s_median": median_or_blank(times),
            "response_time_s_min": min(times) if times else "",
            "response_time_s_max": max(times) if times else "",
            "runs": args.runs,
            "ok_runs": len(ok_runs),
            "failed_runs": len(failed),
            "status_codes": ";".join(str(r["http_status"]) for r in runs),
            "pacing_s": args.min_interval,
            "first_error": first_error,
            "retrieved_faq_ids": ret_ids,
            "retrieved_scores": ret_scores,
            "top_score": top_score,
            "llm_calls": m_first.get("llm_calls", ""),
            "prompt_tokens": median_or_blank([r["metrics"].get("tokens", {}).get("prompt") for r in ok_runs]),
            "output_tokens": median_or_blank([r["metrics"].get("tokens", {}).get("output") for r in ok_runs]),
            "thinking_tokens": median_or_blank([r["metrics"].get("tokens", {}).get("thinking") for r in ok_runs]),
            "total_tokens": median_or_blank([r["metrics"].get("tokens", {}).get("total") for r in ok_runs]),
        }
        for stage in STAGES:
            row[f"ms_{stage}"] = median_or_blank([r["metrics"].get("timings_ms", {}).get(stage) for r in ok_runs])
        row["observation"] = ""  # diisi manual setelah hasil ditinjau
        result_rows.append(row)

        print(f"{sc['scenario_id']}: intent={row['intent']:<14} median={row['response_time_s_median'] if row['response_time_s_median'] != '' else '-'}s "
              f"tokens={row['total_tokens']} ok={row['ok_runs']}/{args.runs} codes={row['status_codes']}")

    for path, rows in ((args.output, result_rows), (args.raw_output, raw_rows)):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Tersimpan: {path}")


if __name__ == "__main__":
    main()