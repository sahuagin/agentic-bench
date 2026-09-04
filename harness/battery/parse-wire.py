#!/usr/bin/env python3
"""
parse-wire (parse half) — turn anthropic-wiretap's framed capture into JSONL
fixtures for mu-ai's anthropic provider tests. Runs offline on a recorded
capture; safe to re-run as the fixture format evolves.

  python3 parse-wire.py [capture.bin] [out.jsonl]

One JSONL record per request/response exchange:
  {ts_conn, request:{method,path,headers,body}, response:{status,headers,
   content_type, sse_events:[{event,data}...], body_json}}
Auth headers + set-cookie are redacted here (the raw capture keeps them).
HTTP/1.1 only; a non-HTTP/1.x connection is reported and skipped.

!! REDACTION IS HEADER-ONLY — THE BODY IS NOT SCRUBBED. !!
  request.body still contains, for real Claude Code traffic:
    * metadata.user_id  -> live device_id / account_uuid / session_id
    * system[]          -> Anthropic's proprietary system prompt
    * tools[]           -> Claude Code's full (incl. unreleased) tool catalog
  None of that belongs in a committed/public repo. These fixtures are a LOCAL
  test oracle. If you extract any fixture for a repo, take the RESPONSE side
  only (SSE/body_json) and verify it carries no identifiers or prompt text.
  (2026-06-13: a fresh agent's first move was to commit the request body to a
  public repo, identifiers and all. The rule "fine unless committed" did not
  bind at the moment of action. Hence this banner, at the point of action.)
"""
import sys, os, struct, json, gzip, zlib
try:
    import brotli
except ImportError:
    brotli = None

CAP_PATH   = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cc-capture.bin"
JSONL_PATH = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cc-fixtures.jsonl"
REDACT_REQ  = {"authorization", "x-api-key", "proxy-authorization",
               "x-claude-code-session-id", "cookie"}
# Any header matching these prefixes is also redacted (catches future
# x-claude-* / session / trace identifiers without enumerating each).
REDACT_REQ_PREFIX = ("x-claude-",)
REDACT_RESP = {"set-cookie"}

def read_frames(path):
    """Reconstruct per-connection byte streams: {conn: {1: bytes, 2: bytes}}."""
    conns = {}
    with open(path, "rb") as f:
        while True:
            hdr = f.read(9)
            if len(hdr) < 9:
                break
            direction, conn, length = struct.unpack(">BII", hdr)
            payload = f.read(length)
            conns.setdefault(conn, {1: bytearray(), 2: bytearray()})[direction] += payload
    return conns

def parse_head(head):
    lines = head.split(b"\r\n")
    first = lines[0].decode("latin1")
    headers = []
    for ln in lines[1:]:
        if not ln:
            continue
        k, _, v = ln.partition(b":")
        headers.append((k.decode("latin1").strip(), v.decode("latin1").strip()))
    return first, headers

def hlower(headers):
    return {k.lower(): v for k, v in headers}

def redact(headers, names):
    def hit(k):
        kl = k.lower()
        return kl in names or kl.startswith(REDACT_REQ_PREFIX)
    return {k: ("<redacted>" if hit(k) else v) for k, v in headers}

def scrub_request_body(body):
    """CONTROL (not a reminder): strip identifier-bearing and third-party
    proprietary fields from a request body BEFORE it can reach a fixture, so a
    careless extract-and-commit can't leak them. Shape is preserved (keys stay,
    values are replaced) so fixtures still exercise the wire structure.

      metadata.user_id -> live device_id/account_uuid/session_id
      system[]         -> Anthropic's proprietary system prompt
      tools[]          -> Claude Code's (incl. unreleased) tool catalog
      messages[]       -> user/assistant prompt text
    """
    if not isinstance(body, dict):
        return body
    if os.environ.get("NO_SCRUB"):
        # Local analysis only (battery scorers read message text). Output
        # produced this way carries prompt text and identifiers: never commit it.
        return body
    if isinstance(body.get("metadata"), dict) and "user_id" in body["metadata"]:
        body["metadata"]["user_id"] = "<redacted>"
    if "system" in body:
        body["system"] = "<redacted>"
    if isinstance(body.get("tools"), list):
        body["tools"] = [{"name": t.get("name", "<redacted>"), "description": "<redacted>",
                          "input_schema": "<redacted>"} if isinstance(t, dict) else "<redacted>"
                         for t in body["tools"]]
    if isinstance(body.get("messages"), list):
        for m in body["messages"]:
            if isinstance(m, dict):
                m["content"] = "<redacted>"
    return body

def decode_body(hl, body):
    """Reverse content-encoding (gzip/deflate/br). Anthropic gzips SSE when the
    client advertises it, so this is needed for real Claude Code captures."""
    enc = hl.get("content-encoding", "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(body)
        if "br" in enc:
            return brotli.decompress(body) if brotli else body
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception as e:
        sys.stderr.write(f"warn: {enc} decode failed ({e}); leaving body raw\n")
    return body

def maybe_json(body):
    try:
        return json.loads(body)
    except Exception:
        return {"_raw": body.decode("utf-8", "replace")}

def dechunk(buf, i):
    """Decode a chunked body starting at buf[i]. -> (decoded, next_index)."""
    out = bytearray()
    while True:
        nl = buf.find(b"\r\n", i)
        if nl < 0:
            break
        try:
            size = int(buf[i:nl].split(b";")[0] or b"0", 16)
        except ValueError:
            break
        i = nl + 2
        if size == 0:
            i = buf.find(b"\r\n", i)                 # consume trailers/final CRLF
            i = (i + 2) if i >= 0 else len(buf)
            break
        out += buf[i:i + size]
        i += size + 2                                # data + trailing CRLF
    return bytes(out), i

def parse_sse(body):
    events = []
    for block in body.decode("utf-8", "replace").split("\n\n"):
        block = block.strip("\r\n")
        if not block:
            continue
        ev, datas = None, []
        for ln in block.split("\n"):
            ln = ln.rstrip("\r")
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                datas.append(ln[5:].lstrip())
        if ev is not None or datas:
            events.append({"event": ev, "data": "\n".join(datas)})
    return events

def split_messages(buf, is_response):
    """Walk a reconstructed byte stream into HTTP/1.x messages.
    -> list of (first_line, headers, body_bytes)."""
    msgs, i, n = [], 0, len(buf)
    while i < n:
        end = buf.find(b"\r\n\r\n", i)
        if end < 0:
            break
        head = buf[i:end + 4]
        first, headers = parse_head(head)
        hl = hlower(headers)
        i = end + 4
        if not is_response:                          # request body via CL
            cl = int(hl.get("content-length", "0") or "0")
            body = bytes(buf[i:i + cl]); i += cl
        elif "chunked" in hl.get("transfer-encoding", "").lower():
            body, i = dechunk(buf, i)
        elif "content-length" in hl:
            cl = int(hl["content-length"] or "0")
            body = bytes(buf[i:i + cl]); i += cl
        else:
            body = bytes(buf[i:]); i = n             # framing-by-close
        msgs.append((first, headers, body))
    return msgs

def main():
    conns = read_frames(CAP_PATH)
    out = open(JSONL_PATH, "w")
    n_rec = 0
    for conn in sorted(conns):
        reqs = split_messages(conns[conn][1], is_response=False)
        resps = split_messages(conns[conn][2], is_response=True)
        if reqs and "HTTP/1." not in reqs[0][0]:
            sys.stderr.write(f"[conn {conn}] non-HTTP/1.x ({reqs[0][0]!r}); skipped — see raw capture\n")
            continue
        for k, (req, resp) in enumerate(zip(reqs, resps)):
            rfirst, rheaders, rbody = req
            sfirst, sheaders, sbody = resp
            rbody = decode_body(hlower(rheaders), rbody)
            sbody = decode_body(hlower(sheaders), sbody)
            rp = rfirst.split(" ")
            sp = sfirst.split(" ")
            ctype = hlower(sheaders).get("content-type", "")
            rec = {
                "conn": conn, "seq": k,
                "request": {
                    "method": rp[0] if rp else "?",
                    "path": rp[1] if len(rp) > 1 else "?",
                    "headers": redact(rheaders, REDACT_REQ),
                    "body": scrub_request_body(maybe_json(rbody)) if rbody else None,
                },
                "response": {
                    "status": int(sp[1]) if len(sp) > 1 and sp[1].isdigit() else None,
                    "headers": redact(sheaders, REDACT_RESP),
                    "content_type": ctype,
                    "sse_events": parse_sse(sbody) if "text/event-stream" in ctype else None,
                    "body_json": maybe_json(sbody) if "application/json" in ctype else None,
                },
            }
            out.write(json.dumps(rec) + "\n")
            n_rec += 1
    out.close()
    print(f"parsed {n_rec} exchange(s) from {len(conns)} connection(s) -> {JSONL_PATH}")

if __name__ == "__main__":
    main()
