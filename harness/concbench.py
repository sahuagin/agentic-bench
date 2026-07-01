#!/usr/bin/env python3
"""Concurrency / batching probe for local ollama models.

Fires N concurrent /api/generate requests at CB_MODEL and measures AGGREGATE
throughput vs N. It answers one question: does this model BATCH (serve multiple
sequences in parallel) or SERIALIZE (queue through a single slot)?

Read the curve alongside the ollama load log (`journalctl -u ollama`):
  - STANDARD attention -> `n_seq_max = 8`, KV cache on ALL layers, aggregate
    tok/s CLIMBS with N   (qwen3:8b @8192: 90 -> ~280, ~3x, peak ~N=6).
  - HYBRID attention   -> `n_seq_max = 1`, KV cache on a SUBSET of layers (the
    linear/recurrent layers carry no KV), aggregate tok/s FLAT regardless of N
    (ornith:9b 8/…, ornith:35b 10/…, qwen3.6:35b-a3b 10/…). llama.cpp does not
    batch the recurrent path, so it pins one sequence — verified even alone on
    72GB, i.e. structural, not a memory shortfall.
  MoE is ORTHOGONAL: a hybrid-MoE (qwen3.6:35b-a3b) still won't batch. The
  determinant is hybrid-vs-full attention (KV-on-all-layers?), not MoE.

Env: CB_MODEL (default ornith:9b), CB_NCTX (default 262144), CB_EP (endpoint).
Run under the ollama-box lock (with-ollama-lease) and HOLD it for the whole
sweep, so nothing evicts the model mid-run.
"""
import concurrent.futures, urllib.request, json, time, os

EP = os.environ.get("CB_EP", "http://10.1.1.143:11434")
MODEL = os.environ.get("CB_MODEL", "ornith:9b")
NCTX = int(os.environ.get("CB_NCTX", "262144"))
PROMPT = ("Write about 400 words explaining how a write-ahead log ensures durability and "
          "crash recovery in a database: the log-before-data rule, fsync, checkpointing, and replay.")

def one(_):
    body = json.dumps({"model": MODEL, "prompt": PROMPT, "stream": False,
                       "options": {"num_predict": 400, "num_ctx": NCTX}}).encode()
    try:
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(EP + "/api/generate", data=body,
                                   headers={"Content-Type": "application/json"}), timeout=600))
        return d.get("eval_count", 0), (d.get("eval_duration", 1) or 1) / 1e9
    except Exception:
        return 0, 0.0

print(f"warming {MODEL} @ num_ctx={NCTX} ...", flush=True)
one(0)
print(f"{'N':>3} {'agg_tok/s':>10} {'per_req_t/s':>12} {'wall_s':>7}", flush=True)
for N in [1, 2, 4, 6, 8, 12, 16]:
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
        res = list(ex.map(one, range(N)))
    wall = time.monotonic() - t0
    tot = sum(r[0] for r in res)
    per = [r[0] / r[1] for r in res if r[1] > 0]
    agg = tot / wall if wall > 0 else 0
    peravg = sum(per) / len(per) if per else 0
    print(f"{N:>3} {agg:>10.1f} {peravg:>12.1f} {wall:>7.1f}", flush=True)
