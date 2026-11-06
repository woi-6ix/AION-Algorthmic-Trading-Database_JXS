"""Drop-in AION LTM launcher that preserves the existing dashboard & logo.

Run: python -m uvicorn aion_ltm_with_controls:app --host 127.0.0.1 --port 8501
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse

from aion_live_dashboard import app   # Existing dashboard: unmodified.
from aion_alpaca_control import ROUTER

ROOT = Path(__file__).resolve().parent
app.include_router(ROUTER)


@app.get("/aion-alpaca-controls.js", include_in_schema=False)
def control_javascript():
    return FileResponse(ROOT / "aion_alpaca_controls.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.middleware("http")
async def control_panel(request: Request, call_next):
    # Read the *existing* dashboard HTML; insert an external script without changing
    # the file itself, so user branding/layout and future page updates are preserved.
    if request.method == "GET" and request.url.path == "/":
        dashboard = ROOT / "static" / "index.html"
        if dashboard.is_file():
            original = dashboard.read_text(encoding="utf-8-sig")
            script = '<script src="/aion-alpaca-controls.js" defer></script>'
            if script not in original:
                pos = original.lower().rfind("</body>")
                original = original[:pos] + script + "\n" + original[pos:] if pos >= 0 else original + script
            return HTMLResponse(original, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    return await call_next(request)