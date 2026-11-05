"""Safely install the original AION Quant Labs logo into the existing dashboard."""
from __future__ import annotations

import ast
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_PATH = ROOT / "aion_live_dashboard.py"
PAGE_PATH = ROOT / "static" / "index.html"
LOGO_PATH = ROOT / "static" / "aion-logo.png"

LOGO_ENDPOINT = '''@app.get("/static/aion-logo.png", include_in_schema=False)
def aion_brand_logo():
    """Serve only the official logo PNG (never expose other local static files)."""
    return FileResponse(
        ROOT / "static" / "aion-logo.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


'''
DESKTOP_OLD = '<div class="brand"><div class="mark">A</div>'
DESKTOP_NEW = '<div class="brand"><img class="mark brand-logo" src="/static/aion-logo.png" alt="AION Quant Labs logo" width="48" height="48" />'
MOBILE_OLD = '<div class="mobiletop"><div class="mark">A</div>'
MOBILE_NEW = '<div class="mobiletop"><img class="mark brand-logo" src="/static/aion-logo.png" alt="AION Quant Labs logo" width="36" height="36" />'
CSS_ANCHOR = '.mobilenav{display:none}.mobiletop{display:none}'
CSS_ADDITION = '''
    /* Official AION Quant Labs emblem, gold on deep navy. */
    .mark.brand-logo{display:block;width:48px;height:48px;object-fit:contain;flex-shrink:0;background:transparent;border:none;border-radius:50%;box-shadow:0 0 16px rgba(232,186,104,.12)}
    .mobiletop .mark.brand-logo{width:36px;height:36px;border-radius:50%;box-shadow:none}
'''
FAVICON = '<link rel="icon" type="image/png" href="/static/aion-logo.png" />'


def update(app: str, page: str) -> tuple[str, str]:
    # Refuse to modify unfamiliar versions of the website to avoid breaking them.
    if '@app.get("/static/aion-logo.png"' not in app:
        anchor = '@app.get("/")'
        if app.count(anchor) != 1:
            raise RuntimeError("Original FastAPI homepage route not found; no files were changed")
        app = app.replace(anchor, LOGO_ENDPOINT + anchor, 1)
    if DESKTOP_NEW not in page:
        if page.count(DESKTOP_OLD) != 1:
            raise RuntimeError("Original desktop logo placeholder not found; no files were changed")
        page = page.replace(DESKTOP_OLD, DESKTOP_NEW, 1)
    if MOBILE_NEW not in page:
        if page.count(MOBILE_OLD) != 1:
            raise RuntimeError("Original mobile logo placeholder not found; no files were changed")
        page = page.replace(MOBILE_OLD, MOBILE_NEW, 1)
    if '.mark.brand-logo{' not in page:
        if page.count(CSS_ANCHOR) != 1:
            raise RuntimeError("Original dashboard CSS anchor not found; no files were changed")
        page = page.replace(CSS_ANCHOR, CSS_ANCHOR + CSS_ADDITION, 1)
    if FAVICON not in page:
        anchor = '<title>AION | Live Trading Monitor</title>'
        if page.count(anchor) != 1:
            raise RuntimeError("Original HTML title not found; no files were changed")
        page = page.replace(anchor, anchor + '\n  ' + FAVICON + '\n  <link rel="apple-touch-icon" href="/static/aion-logo.png" />', 1)
    ast.parse(app)
    return app, page


def main():
    if not APP_PATH.is_file() or not PAGE_PATH.is_file():
        raise SystemExit("Missing aion_live_dashboard.py or static/index.html. Run inside your AION-Execution folder.")
    if not LOGO_PATH.is_file():
        raise SystemExit("Missing static/aion-logo.png. Extract the complete update ZIP first.")
    app_original = APP_PATH.read_text(encoding="utf-8")
    page_original = PAGE_PATH.read_text(encoding="utf-8")
    app_updated, page_updated = update(app_original, page_original)
    if app_updated == app_original and page_updated == page_original:
        print("The AION logo is already installed; nothing changed.")
        return
    backup = ROOT / "aion-logo-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(APP_PATH, backup / "aion_live_dashboard.py")
    shutil.copy2(PAGE_PATH, backup / "index.html")
    APP_PATH.write_text(app_updated, encoding="utf-8")
    PAGE_PATH.write_text(page_updated, encoding="utf-8")
    print("SUCCESS: AION gold/navy logo added to desktop header, mobile header, and browser tab.")
    print("Backups:", backup)
    print("Restart the dashboard (port 8501) to serve its new logo route. Leave Tailscale running.")
    print("No AION trading, webhook, SnapTrade or brokerage order code was changed.")


if __name__ == "__main__":
    main()
