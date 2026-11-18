"""Take screenshots of the running app in a real (headless) browser.

Used to check visual changes in Chrome or Edge without anyone clicking through
the app. It drives the browser over the DevTools protocol using the
`websockets` package that Streamlit already installs, so it needs nothing new.
Nothing is sent anywhere: it only talks to the app on this computer.

Start the app first (in another window), then run for example:
    .\\.venv\\Scripts\\python.exe tools\\browser_check.py --out .tmp\\shots ^
        goto:/ shot:home "click:Explore the demo" shot:dashboard scheme:dark shot:dashboard_dark

Steps (run in order):
    goto:<path>       open a page, e.g. goto:/ or goto:/dashboard
    click:<text>      click the first button whose text contains <text>
    scheme:<mode>     light or dark (what "Use system setting" follows)
    width:<px>        viewport width, e.g. width:390 for a phone
    scroll:<px>       scroll the main area to <px> from the top
    wait:<seconds>    pause
    eval:<js>         run JavaScript and print the result
    shot:<name>       save <out>/<name>.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
DEBUG_PORT = 9333
# The page is ready when the main area has content and Streamlit isn't running the script.
READY_JS = ("(() => { const m = document.querySelector('[data-testid=stMain]');"
            " const busy = document.querySelector('[data-testid=stStatusWidget]');"
            " return !!(m && m.innerText.trim().length > 40 && !busy); })()")
SCROLL_JS = ("(() => { const e = [...document.querySelectorAll('section, div')].find(x => x.scrollHeight >"
             " x.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(x).overflowY));"
             " (e || document.scrollingElement).scrollTo(0, Y); })()")


def find_browser() -> str:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    raise SystemExit("Chrome or Edge was not found. Install one, or add its path to BROWSERS.")


async def run(base: str, out: Path, steps: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="bp-browser-")
    browser = subprocess.Popen([find_browser(), "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
                                f"--user-data-dir={profile}", "--hide-scrollbars", "--no-first-run",
                                "--window-size=1440,1000", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json"))
                ws_url = next(tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page")
                break
            except (OSError, StopIteration, ValueError):
                await asyncio.sleep(0.2)
        if ws_url is None:
            raise SystemExit("The browser did not start.")
        async with connect(ws_url, max_size=200 * 1024 * 1024) as ws:
            ids = itertools.count(1)

            async def send(method: str, **params):
                message_id = next(ids)
                await ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
                while True:
                    reply = json.loads(await ws.recv())
                    if reply.get("id") == message_id:
                        if "error" in reply:
                            raise RuntimeError(reply["error"])
                        return reply.get("result", {})

            async def evaluate(expression: str):
                return (await send("Runtime.evaluate", expression=expression, returnByValue=True,
                                   awaitPromise=True))["result"].get("value")

            async def settle(minimum: float) -> None:
                await asyncio.sleep(minimum)
                for _ in range(60):
                    if await evaluate(READY_JS):
                        await asyncio.sleep(1.5)   # let charts and fonts finish drawing
                        return
                    await asyncio.sleep(0.5)
                print("warning: the page did not finish loading")

            width, scheme = 1440, "light"

            async def apply_viewport() -> None:
                await send("Emulation.setDeviceMetricsOverride", width=width, height=1000, deviceScaleFactor=1,
                           mobile=width < 700)
                await send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])

            await apply_viewport()
            for step in steps:
                kind, _, arg = step.partition(":")
                if kind == "goto":
                    await send("Page.navigate", url=base + arg)
                    await settle(3)
                elif kind == "click":
                    before = await evaluate("location.href")
                    found = await evaluate(
                        "(() => { const b = [...document.querySelectorAll('button')].find(e => "
                        f"e.innerText.trim().includes({json.dumps(arg)})); if (b) {{ b.click(); return true; }}"
                        " return false; })()")
                    print(f"click {arg!r}: {'ok' if found else 'NOT FOUND'}")
                    for _ in range(8):   # a click may open another page; wait for it to start
                        await asyncio.sleep(0.5)
                        if await evaluate("location.href") != before:
                            break
                    await settle(1.5)
                elif kind in ("scheme", "width"):
                    scheme = arg if kind == "scheme" else scheme
                    width = int(arg) if kind == "width" else width
                    await apply_viewport()
                    await asyncio.sleep(1.5)
                elif kind == "scroll":
                    # Back to the top first, then a real mouse-wheel turn over the main area,
                    # so whichever element Streamlit scrolls receives it.
                    await evaluate(SCROLL_JS.replace("Y", "0"))
                    await send("Input.dispatchMouseEvent", type="mouseWheel", x=width * 0.6, y=500,
                               deltaX=0, deltaY=int(arg))
                    await asyncio.sleep(1)
                elif kind == "wait":
                    await asyncio.sleep(float(arg))
                elif kind == "eval":
                    print(await evaluate(arg))
                elif kind == "shot":
                    data = (await send("Page.captureScreenshot", format="png"))["data"]
                    (out / f"{arg}.png").write_bytes(base64.b64decode(data))
                    print(f"saved {out / (arg + '.png')}")
                else:
                    print(f"unknown step: {step}")
    finally:
        browser.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8501, help="port the app is running on (default 8501)")
    parser.add_argument("--out", type=Path, default=Path(".tmp") / "screenshots", help="folder for screenshots")
    parser.add_argument("steps", nargs="+")
    args = parser.parse_args()
    asyncio.run(run(f"http://127.0.0.1:{args.port}", args.out, args.steps))


if __name__ == "__main__":
    main()
