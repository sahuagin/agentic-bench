#!/usr/bin/env python3
"""
web_probe — run an HTML artifact in a REAL headless Chrome and report what the
runtime says about it, as the verifier for the harness-experiment batteries
(mu-316wl battery 1: real-runtime self-verification is the capability lever).

Text report, first line is the verdict:
  PASS  = page fired load with zero uncaught exceptions (and no crash)
  FAIL  = otherwise; the exceptions (with file:line:col) are listed
then console.error / console.log, requestAnimationFrame ticks/s, whether two
screenshots a second apart differ (animation), whether pixels change after a
scripted input burst (input response), exceptions thrown during input, and
the screenshot paths. `--json` also writes the structured result.

Chrome: connect to a running headless Chrome over CDP (`--cdp`, default the
jail tunnel http://localhost:9223 -> chrome-for-testing on the GPU box, see
battery/README.md), or launch one (`--chrome /path/to/chrome`). With a remote
Chrome the artifact is copied to the remote host (`--remote-host`, scp) and
loaded as file://; a single self-contained HTML file is assumed.

  PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright /usr/local/bin/python3.11 \
      harness/web_probe.py game.html --remote-host ollama --json out.json

Gotchas (learned the hard way): WebGL canvases without preserveDrawingBuffer
read back empty in-page, so render checks hash page.screenshot() (compositor
output); SwiftShader boot is slow, hence the 8 s default settle; headless
Firefox cannot do WebGL at all.
"""
import argparse, hashlib, json, os, subprocess, sys, time

DEFAULT_INPUT = [
    {"type": "click", "x": 640, "y": 400},
    {"type": "wait", "ms": 800},
    {"type": "key", "key": "w", "hold_ms": 2000},
    {"type": "key", "key": " ", "hold_ms": 300},
]
BLANK_PNG_BYTES = 8000  # blank pages compress to a few KB


def shot(page, path):
    png = page.screenshot()
    with open(path, "wb") as f:
        f.write(png)
    return {"path": path, "hash": hashlib.sha256(png).hexdigest()[:16], "bytes": len(png)}


def fmt_error(e):
    # playwright pageerror -> "Name: message\n    at fn (url:line:col)"
    s = getattr(e, "stack", None) or str(e)
    head = s.splitlines()[0] if s else "Uncaught"
    loc = ""
    for ln in s.splitlines()[1:]:
        ln = ln.strip()
        if ln.startswith("at "):
            loc = " @ " + ln[3:].rsplit("/", 1)[-1].rstrip(")")
            break
    return (head + loc)[:300]


def dispatch(page, ev):
    t = ev.get("type")
    if t == "click":
        page.mouse.click(ev.get("x", 640), ev.get("y", 400))
    elif t == "key":
        page.keyboard.down(ev["key"])
        time.sleep(min(ev.get("hold_ms", 120), 5000) / 1000)
        page.keyboard.up(ev["key"])
    elif t == "wait":
        time.sleep(min(ev.get("ms", 500), 10000) / 1000)
    elif t == "move":
        page.mouse.move(ev.get("x", 700), ev.get("y", 400))
    else:
        raise ValueError(f"unknown input event type {t!r}")


def probe(page, url, settle, events, shots_dir, stem, timeout_s):
    r = {"url": url, "exceptions": [], "console_errors": [], "console_logs": [], "notes": []}
    page.on("pageerror", lambda e: r["exceptions"].append(fmt_error(e)))

    def on_console(m):
        line = m.text[:300]
        if m.type in ("error", "assert"):
            if "favicon.ico" not in line:
                r["console_errors"].append(line)
        elif len(r["console_logs"]) < 160:
            r["console_logs"].append(("[warn] " if m.type == "warning" else "") + line)

    page.on("console", on_console)
    page.on("crash", lambda _: r.__setitem__("crashed", True))
    t0 = time.time()
    try:
        page.goto(url, wait_until="load", timeout=timeout_s * 1000)
        r["loaded_after_s"] = round(time.time() - t0, 2)
    except Exception as e:
        r["notes"].append(f"load event not fired: {str(e)[:160]}")
    time.sleep(settle)
    r["boot_exception_count"] = len(r["exceptions"])
    if r.get("crashed"):
        return r
    r["raf_per_s"] = page.evaluate(
        "() => new Promise(res => {let n=0;const t0=performance.now();"
        "function f(){n++;if(performance.now()-t0<1000)requestAnimationFrame(f);else res(n)}"
        "requestAnimationFrame(f);setTimeout(()=>res(n),2500)})"
    )
    os.makedirs(shots_dir, exist_ok=True)
    tag = f"{stem}-{int(time.time()*1000)}"
    a = shot(page, os.path.join(shots_dir, f"{tag}-a.png"))
    time.sleep(1)
    b = shot(page, os.path.join(shots_dir, f"{tag}-b.png"))
    r["shot_a"], r["shot_b"] = a, b
    r["render_nonblank"] = a["bytes"] > BLANK_PNG_BYTES
    r["animating"] = a["hash"] != b["hash"]
    if events:
        before = len(r["exceptions"])
        for ev in events:
            dispatch(page, ev)
        time.sleep(0.5)
        c = shot(page, os.path.join(shots_dir, f"{tag}-input.png"))
        r["shot_after_input"] = c
        r["input_dispatched"] = len(events)
        r["responded_to_input"] = b["hash"] != c["hash"]
        r["input_errors"] = r["exceptions"][before:]
    r["elapsed_s"] = round(time.time() - t0, 1)
    return r


def passed(r):
    return "loaded_after_s" in r and not r["exceptions"] and not r.get("crashed")


def report(r, artifact):
    out = [f"VERIFY web {'PASS' if passed(r) else 'FAIL'} — {artifact}",
           f"url: {r['url']}  elapsed: {r.get('elapsed_s', '?')}s"]
    out.append(f"boot: load event after {r['loaded_after_s']}s" if "loaded_after_s" in r else "boot: LOAD EVENT NOT FIRED")
    if r.get("crashed"):
        out.append("renderer: CRASHED")
    boot = r["exceptions"][: r.get("boot_exception_count", len(r["exceptions"]))]
    out.append(f"uncaught exceptions during boot/settle: {len(boot)}")
    out += [f"  {i+1}. {e}" for i, e in enumerate(boot[:40])]
    out.append(f"console.error: {len(r['console_errors'])}")
    out += [f"  - {e}" for e in r["console_errors"][:40]]
    out.append(f"console.log: {len(r['console_logs'])} line(s)")
    out += [f"  | {l}" for l in r["console_logs"][:40]]
    if "raf_per_s" in r:
        n = r["raf_per_s"]
        out.append(f"requestAnimationFrame: {n} frame(s) in 1s ({'ticking' if n else 'NOT ticking'})")
    if "shot_a" in r:
        out.append(f"render: screenshot {r['shot_a']['bytes']} bytes ({'content drawn' if r['render_nonblank'] else 'mostly blank'}); "
                   f"animating (two samples 1s apart differ): {'yes' if r['animating'] else 'no'}")
    if "input_dispatched" in r:
        out.append(f"input: {r['input_dispatched']} event(s) dispatched; pixels changed after input: "
                   f"{'yes' if r['responded_to_input'] else 'no'}; new uncaught exceptions during input: {len(r['input_errors'])}")
        out += [f"  - {e}" for e in r["input_errors"]]
    shots = [r[k]["path"] for k in ("shot_a", "shot_b", "shot_after_input") if k in r]
    if shots:
        out.append("screenshots: " + " ".join(shots))
    out += [f"note: {n}" for n in r["notes"]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifact", help="path to a self-contained .html file")
    ap.add_argument("--cdp", default=os.environ.get("WEB_PROBE_CDP", "http://localhost:9223"),
                    help="CDP URL of a running headless Chrome (default: the jail tunnel to the GPU box)")
    ap.add_argument("--chrome", help="launch this Chrome binary locally instead of connecting over CDP")
    ap.add_argument("--remote-host", default=os.environ.get("WEB_PROBE_REMOTE_HOST"),
                    help="ssh host the CDP Chrome runs on; the artifact is scp'd there and loaded as file://")
    ap.add_argument("--settle", type=float, default=8.0, help="seconds after load before sampling (SwiftShader is slow)")
    ap.add_argument("--timeout", type=float, default=90.0, help="load timeout (s)")
    ap.add_argument("--input", help="JSON list of input events (click/key/wait/move); default: click + hold W + space")
    ap.add_argument("--no-input", action="store_true")
    ap.add_argument("--shots", default=os.environ.get("WEB_PROBE_SHOTS", "/tmp/web-probe-shots"))
    ap.add_argument("--json", help="also write the structured result here")
    args = ap.parse_args()

    artifact = os.path.abspath(args.artifact)
    if not os.path.isfile(artifact):
        sys.exit(f"web_probe: not a file: {artifact}")
    events = [] if args.no_input else (json.loads(args.input) if args.input else DEFAULT_INPUT)
    stem = os.path.splitext(os.path.basename(artifact))[0]

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if args.chrome:
            browser = p.chromium.launch(
                executable_path=args.chrome,
                args=["--headless=new", "--disable-gpu", "--enable-unsafe-swiftshader", "--no-sandbox", "--window-size=1280,800"],
            )
            url = "file://" + artifact
        else:
            browser = p.chromium.connect_over_cdp(args.cdp)
            if args.remote_host:
                rn = f"/tmp/web-probe-{stem}-{os.getpid()}.html"
                subprocess.run(["scp", "-q", artifact, f"{args.remote_host}:{rn}"], check=True)
                url = "file://" + rn
            else:
                url = "file://" + artifact
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        try:
            r = probe(page, url, args.settle, events, args.shots, stem, args.timeout)
        finally:
            page.close()
            if args.chrome:
                browser.close()
    text = report(r, artifact)
    print(text)
    if args.json:
        r["artifact"] = artifact
        r["report"] = text
        with open(args.json, "w") as f:
            json.dump(r, f, indent=1)
    sys.exit(0 if passed(r) else 1)


if __name__ == "__main__":
    main()
