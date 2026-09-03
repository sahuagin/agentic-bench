#!/usr/bin/env python3
"""
vllm-proxy — phase-2 (mu-316wl) capture proxy for the vllm nvfp4 lane.

  usage: vllm-proxy.py <listen_port> <capture_path> [think]

Mutations (logged to stderr per request):
  - /v1/chat/completions + think: chat_template_kwargs {"enable_thinking": true}
    (per-request thinking on the non-thinking nvfp4 template; wire-verified
    phase-1 close-out).
Everything else passes through byte-identical. Upstream 10.1.1.143:11435.
Capture framing = struct ">BII" (dir, conn, len), parse with parse-wire.py.
"""
import asyncio, sys, struct, itertools, json, re

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(sys.argv[1])
CAP_PATH    = sys.argv[2]
THINK       = len(sys.argv) > 3 and sys.argv[3] == "think"
UP_HOST     = "10.1.1.143"
UP_PORT     = 11435
LIMIT       = 16 * 1024 * 1024

NEEDLE = f"Host: {LISTEN_HOST}:{LISTEN_PORT}\r\n".encode()
REPL   = f"Host: {UP_HOST}:{UP_PORT}\r\n".encode()
_conn_ids = itertools.count(1)
_capf = None

def frame(direction, conn, payload):
    _capf.write(struct.pack(">BII", direction, conn, len(payload)))
    _capf.write(payload)
    _capf.flush()

def adapt_body(head, body, conn):
    first = head.split(b"\r\n", 1)[0]
    if b"/v1/chat/completions" not in first or not THINK:
        return head, body
    try:
        j = json.loads(body)
    except Exception:
        return head, body
    j["chat_template_kwargs"] = {"enable_thinking": True}
    new_body = json.dumps(j, separators=(",", ":")).encode()
    head = re.sub(rb"(?im)^content-length:[^\r\n]*",
                  b"Content-Length: " + str(len(new_body)).encode(),
                  head, count=1)
    sys.stderr.write(f"[conn {conn}] enable_thinking=true "
                     f"({len(body)}B -> {len(new_body)}B)\n")
    return head, new_body

async def request_side(client_reader, client_writer, up_writer, conn):
    while True:
        try:
            head = await client_reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            break
        first = head.split(b"\r\n", 1)[0].decode("latin1")
        cl = 0
        for ln in head.split(b"\r\n")[1:]:
            if ln[:15].lower() == b"content-length:":
                cl = int(ln.split(b":", 1)[1].strip() or b"0")
        body = await client_reader.readexactly(cl) if cl else b""
        if "HTTP/1." not in first:
            up_writer.write(head + body); frame(1, conn, head + body)
            await _raw(client_reader, up_writer, 1, conn)
            return
        head2, body2 = adapt_body(head, body, conn)
        out = head2.replace(NEEDLE, REPL) + body2
        up_writer.write(out); await up_writer.drain()
        frame(1, conn, out)

async def _raw(reader, writer, direction, conn):
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        writer.write(chunk); await writer.drain()
        frame(direction, conn, chunk)

async def handle(client_reader, client_writer):
    conn = next(_conn_ids)
    try:
        up_reader, up_writer = await asyncio.open_connection(
            UP_HOST, UP_PORT, limit=LIMIT)
    except Exception as e:
        sys.stderr.write(f"[conn {conn}] upstream connect failed: {e}\n")
        client_writer.close(); return
    tasks = [asyncio.create_task(request_side(client_reader, client_writer,
                                              up_writer, conn)),
             asyncio.create_task(_raw(up_reader, client_writer, 2, conn))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for w in (up_writer, client_writer):
            try: w.close()
            except Exception: pass

async def main():
    global _capf
    _capf = open(CAP_PATH, "ab")
    srv = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT, limit=LIMIT)
    print(f"vllm-proxy: :{LISTEN_PORT} -> {UP_HOST}:{UP_PORT} "
          f"think={THINK} capture={CAP_PATH}", flush=True)
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
