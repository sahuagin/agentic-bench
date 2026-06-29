#!/usr/bin/env python3
"""Run local Ollama code-review benchmark cases."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

SYSTEM_PROMPT = """You are an independent code-reviewer.

Review only the diff and case instructions supplied by the user. Do not claim you ran tests, opened files, or verified behavior outside the provided diff.

Return ONLY a JSON object with this shape:
{
  "findings": [
    {
      "id": "short-stable-id",
      "severity": "blocker|major|minor|nit",
      "file": "path/from/diff",
      "line": 123,
      "summary": "one sentence",
      "rationale": "why this matters",
      "confidence": 0.0
    }
  ],
  "overall": "approve|comment|request_changes",
  "notes": "optional"
}

Severity guide:
- blocker: security hole, data loss, auth bypass, unsound external side effect
- major: correctness/reliability bug likely to affect users
- minor: edge-case bug or maintainability issue worth fixing
- nit: style only

Prefer a small number of evidence-tied findings over broad speculation.
"""


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def ollama_models(base_url: str) -> list[str]:
    tags = http_json(f"{base_url.rstrip('/')}/api/tags", timeout=20)
    names = []
    for model in tags.get("models", []):
        name = model.get("name", "")
        if name and "embedding" not in name.lower():
            names.append(name)
    return names


def load_cases(limit: int | None) -> list[dict[str, Any]]:
    manifest = json.loads((ROOT / "cases" / "manifest.json").read_text())
    cases = [json.loads((ROOT / "cases" / name).read_text()) for name in manifest]
    return cases[:limit] if limit else cases


def make_prompt(case: dict[str, Any]) -> str:
    return f"""Case: {case['id']}
Title: {case['title']}
Language: {case['language']}
Instructions: {case['instructions']}

Diff:
```diff
{case['diff']}
```
"""


def extract_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    text = text.strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None, "no JSON object found"
    try:
        return json.loads(match.group(0)), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def norm(s: Any) -> str:
    # Treat hyphen/underscore differences as spaces so "SQL injection" matches "sql-injection".
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", str(s or "").lower())).strip()


def finding_text(f: dict[str, Any]) -> str:
    return norm(" ".join(str(f.get(k, "")) for k in ("id", "severity", "file", "summary", "rationale")))


def matches_expected(f: dict[str, Any], expected: dict[str, Any]) -> bool:
    file_ok = norm(f.get("file")) == norm(expected.get("file")) or norm(expected.get("file")) in norm(f.get("file"))
    text = finding_text(f)
    kws = [norm(k) for k in expected.get("keywords", [])]
    hits = 0
    for k in kws:
        if k in text:
            hits += 1
        else:
            parts = [p for p in k.split() if p]
            if len(parts) > 1 and all(p in text for p in parts):
                hits += 1
    # File match plus at least two semantic hits; or very strong textual hit without exact file.
    return (file_ok and hits >= 2) or hits >= 4


def score(case: dict[str, Any], parsed: dict[str, Any] | None, raw: str, parse_error: str | None) -> dict[str, Any]:
    expected = case.get("expected_findings", [])
    out = {
        "parse_ok": parsed is not None,
        "expected_count": len(expected),
        "matched_count": 0,
        "false_positive_count": 0,
        "forbidden_claim_count": 0,
        "matches": [],
        "misses": [],
        "false_positives": [],
        "severity_points": 0.0,
        "score": 0.0,
        "parse_error": parse_error,
    }
    if parsed is None:
        out["misses"] = [e["id"] for e in expected]
        return out

    findings = parsed.get("findings", [])
    if not isinstance(findings, list):
        findings = []

    matched_finding_indexes: set[int] = set()
    severity_rank = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
    for e in expected:
        best_idx = None
        for i, f in enumerate(findings):
            if i not in matched_finding_indexes and isinstance(f, dict) and matches_expected(f, e):
                best_idx = i
                break
        if best_idx is None:
            out["misses"].append(e["id"])
            continue
        matched_finding_indexes.add(best_idx)
        f = findings[best_idx]
        got = norm(f.get("severity"))
        want = norm(e.get("severity"))
        delta = abs(severity_rank.get(got, -10) - severity_rank.get(want, -10))
        sev_points = 1.0 if delta == 0 else 0.5 if delta == 1 else 0.0
        out["severity_points"] += sev_points
        out["matches"].append({"expected": e["id"], "finding": f, "severity_points": sev_points})

    for i, f in enumerate(findings):
        if i not in matched_finding_indexes:
            out["false_positives"].append(f)

    raw_l = raw.lower()
    for claim in case.get("forbidden_claims", []):
        if claim.lower() in raw_l:
            out["forbidden_claim_count"] += 1

    out["matched_count"] = len(out["matches"])
    out["false_positive_count"] = len(out["false_positives"])
    recall = out["matched_count"] / max(1, len(expected))
    precision = out["matched_count"] / max(1, out["matched_count"] + out["false_positive_count"])
    severity = out["severity_points"] / max(1, out["matched_count"])
    claim_penalty = min(0.25, 0.10 * out["forbidden_claim_count"])
    out["score"] = max(0.0, (0.50 * recall) + (0.25 * precision) + (0.25 * severity) - claim_penalty)
    return out


def options(temperature: float, num_ctx: int, num_predict: int | None) -> dict[str, Any]:
    # Do NOT send temperature or num_ctx. temperature overrides the model's
    # Modelfile sampling (temp 0 is off-distribution and breaks these models);
    # num_ctx forces ollama to reload the model OFF the server's configured max
    # context (262144) into a smaller window, evicting co-resident models. The
    # model already ships correct sampling + context — run it as shipped.
    # num_predict is kept: it's the MTP trigger. Whether it forces a reload is
    # an OPEN question (untested on 0.30.11) — left intact pending that.
    opts: dict[str, Any] = {}
    if num_predict is not None:
        opts["num_predict"] = num_predict
    return opts


def openai_chat(base_url: str, model: str, messages: list[dict[str, str]], temperature: float,
                num_predict: int | None, timeout: int, force_json: bool) -> tuple[dict[str, Any], str]:
    """POST an OpenAI-style chat completion (llama-server /v1). Returns (ollama-shaped metrics, raw text)."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    if num_predict is not None:
        payload["max_tokens"] = num_predict
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    response = http_json(f"{base_url.rstrip('/')}/v1/chat/completions", payload, timeout=timeout)
    raw = (response.get("choices") or [{}])[0].get("message", {}).get("content", "")
    timings = response.get("timings") or {}
    usage = response.get("usage") or {}
    # Map llama-server timings/usage onto the ollama metric names the scorer reports.
    metrics = {
        "total_duration": None,
        "load_duration": None,
        "prompt_eval_count": usage.get("prompt_tokens"),
        "prompt_eval_duration": int(timings["prompt_ms"] * 1e6) if timings.get("prompt_ms") else None,
        "eval_count": usage.get("completion_tokens"),
        "eval_duration": int(timings["predicted_ms"] * 1e6) if timings.get("predicted_ms") else None,
    }
    return metrics, raw


def warmup(base_url: str, model: str, temperature: float, num_ctx: int, num_predict: int | None, timeout: int, api: str = "ollama") -> dict[str, Any]:
    started = time.time()
    if api == "openai":
        try:
            metrics, _ = openai_chat(base_url, model, [{"role": "user", "content": "Return exactly: ok"}],
                                     temperature, num_predict, timeout, force_json=False)
            return {"elapsed_s": time.time() - started, "error": None, "ollama_metrics": metrics}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"elapsed_s": time.time() - started, "error": repr(exc), "ollama_metrics": {}}
    payload = {
        "model": model,
        "prompt": "Return exactly: ok",
        "stream": False,
        "options": options(temperature, num_ctx, num_predict),
        "keep_alive": "24h",
    }
    try:
        response = http_json(f"{base_url.rstrip('/')}/api/generate", payload, timeout=timeout)
        error = None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        response = {}
        error = repr(exc)
    return {
        "elapsed_s": time.time() - started,
        "error": error,
        "ollama_metrics": {k: response.get(k) for k in (
            "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration"
        )},
    }


def run_one(base_url: str, model: str, case: dict[str, Any], temperature: float, num_ctx: int, num_predict: int | None, timeout: int, api: str = "ollama") -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": make_prompt(case)},
    ]
    started = time.time()
    response: dict[str, Any] = {}
    if api == "openai":
        try:
            metrics, raw = openai_chat(base_url, model, messages, temperature, num_predict, timeout, force_json=True)
            response = metrics
            error = None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            error = repr(exc)
            raw = ""
    else:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": options(temperature, num_ctx, num_predict),
            "keep_alive": "24h",
        }
        try:
            response = http_json(f"{base_url.rstrip('/')}/api/chat", payload, timeout=timeout)
            error = None
            raw = response.get("message", {}).get("content", "")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            response = {}
            error = repr(exc)
            raw = ""
    elapsed = time.time() - started
    parsed, parse_error = extract_json(raw) if raw else (None, error or "empty response")
    scored = score(case, parsed, raw, parse_error)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "case_id": case["id"],
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "temperature": temperature,
        "elapsed_s": elapsed,
        "error": error,
        "raw_response": raw,
        "parsed": parsed,
        "score": scored,
        "ollama_metrics": {k: response.get(k) for k in (
            "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration"
        )},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA))
    ap.add_argument("--models", nargs="*", help="Model names to benchmark")
    ap.add_argument("--models-from-ollama", action="store_true", help="Benchmark every installed non-embedding Ollama model")
    ap.add_argument("--limit-cases", type=int)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--num-predict", type=int, default=2048)
    ap.add_argument("--warmup", action="store_true", help="Load each model with a tiny request before scoring cases")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--api", choices=("ollama", "openai"), default="ollama",
                    help="Server API: 'ollama' (/api/chat) or 'openai' (/v1/chat/completions, e.g. llama-server)")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    args = ap.parse_args()

    models = list(args.models or [])
    if args.models_from_ollama:
        models.extend(m for m in ollama_models(args.ollama) if m not in models)
    if not models:
        ap.error("provide --models or --models-from-ollama")

    cases = load_cases(args.limit_cases)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"run-{stamp}.jsonl"

    with out_path.open("w") as fh:
        for model in models:
            if args.warmup:
                print(f"{model} :: warmup", file=sys.stderr, flush=True)
                rec = {
                    "run_at": datetime.now(timezone.utc).isoformat(),
                    "record_type": "warmup",
                    "model": model,
                    "num_ctx": args.num_ctx,
                    "num_predict": args.num_predict,
                    "temperature": args.temperature,
                    **warmup(args.ollama, model, args.temperature, args.num_ctx, args.num_predict, args.timeout, args.api),
                }
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
            for case in cases:
                print(f"{model} :: {case['id']}", file=sys.stderr, flush=True)
                rec = run_one(args.ollama, model, case, args.temperature, args.num_ctx, args.num_predict, args.timeout, args.api)
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
