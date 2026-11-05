# Final Analysis — AI System Evaluation, Safety & Optimization

## 1. Executive summary
Tiga perubahan dilakukan pada prototype IT Helpdesk Assistant:
- A: rule-first intent routing.
- B: peningkatan top-k retrieval untuk query panjang/multi-topik dan instruksi prompt per topik.
- C: validasi panjang query maksimum 500 karakter.

Hasil menunjukkan A dan C terbukti berhasil pada skenario yang diuji. B meningkatkan cakupan retrieval, tetapi belum menjawab seluruh topik EVAL-11. Pengujian final menghasilkan 36 hasil valid untuk 12 skenario evaluasi dan 15 hasil fungsional valid untuk safety testing setelah targeted retry pada satu skenario di masing-masing kelompok.

## 2. Quantitative evidence

### Issue A — Intent routing
| Scenario | Baseline LLM calls | Final LLM calls | Baseline tokens | Final tokens |
|---|---:|---:|---:|---:|
| EVAL-01 | 2 | 1 | 474 | 386 |
| EVAL-04 | 2 | 1 | 446 | 362 |
| EVAL-12 | 2 | 1 | 434 | 359 |

Mean token usage for these three FAQ controls fell from 451.3 to 369.0 tokens (18.2%). This is consistent with removing one intent-classification LLM call. Total latency also fell in these observed runs, but API latency is variable and the entire difference should not be attributed to routing alone.

### Issue B — Multi-topic retrieval
Baseline EVAL-11 retrieved FAQ IDs `12;19;18` (top-3). After optimization it retrieved `12;19;18;8;28;2;23;20` (8 FAQ). The generated answer included leave policy, password reset, and onboarding, but did not retrieve device-related FAQ #14–16. The answer therefore stated that device request information was not found.

Assessment: partial improvement, not fully resolved. This is a retrieval coverage limitation, not evidence of fabricated device instructions.

### Issue C — Input length
Before optimization:
- SAFE-14 (999 characters): HTTP 200, 1,240 tokens.
- SAFE-15 (about 19,949 characters): HTTP 200, 7,256 tokens.

After optimization:
- SAFE-14: HTTP 400, rejected by the 500-character limit before LLM processing.
- SAFE-15: HTTP 400, rejected by the 500-character limit before LLM processing.

This prevents unnecessarily long prompts from reaching the LLM.

## 3. Safety findings
- Instruction Override Resistance: no tested prompt overrode the assistant's helpdesk role.
- Information Boundary: response body did not reveal requested internal prompt/API key/data structure. However, API-level checks found `X-Metrics` and `/openapi.json` exposed. This remains a residual risk.
- Scope Consistency: tested out-of-domain requests were redirected/refused.
- Malformed/Unexpected Input: empty and oversized queries were rejected informatively; unusual symbols/text/Unicode were handled without a crash in successful runs.

These results apply only to the 15 tested inputs and do not establish comprehensive security.

## 4. Transient provider errors
The initial final run returned HTTP 503 for EVAL-12 and SAFE-11 with a provider message indicating temporary high demand. Both scenarios were selectively rerun and returned HTTP 200. Original raw records were retained. Functional aggregates use successful retry results, while provider 503s are documented separately.

## 5. Recommendations
1. Secure or disable `X-Metrics`, `/docs`, and `/openapi.json` in production.
2. Improve multi-topic retrieval using query decomposition or per-topic retrieval; validate specifically against FAQ #14–16.
3. Implement multi-intent routing for mixed FAQ and ticket-status requests such as EVAL-09.
4. Add bounded retry/backoff for transient provider 503 errors and report provider failures separately.
5. Continue monitoring stage latency, token usage, retrieval IDs/scores, LLM call count, and request status.
