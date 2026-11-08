"""Windows Task Scheduler launcher for AION's SIMULATION-ONLY signal services.

Run as: pythonw aion_signal_background.py receiver|gateway
Requires a local virtual environment and existing AION .env; this does not
arm or operate the separate Alpaca PAPER trading worker.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICES = {
    "receiver": ("aion_kairos_server:app", 8001),
    "gateway": ("aion_tv_gateway:app", 8002),
}


def main() -> int:
    os.chdir(ROOT)
    (ROOT / "logs").mkdir(exist_ok=True)
    service = sys.argv[1].lower() if len(sys.argv) == 2 else "invalid"
    if service not in SERVICES:
        # pythonw has no console; never start an unspecified service.
        return 2

    with (ROOT / "logs" / f"aion-{service}-background.log").open(
        "a", encoding="utf-8", buffering=1
    ) as stream:
        sys.stdout = stream
        sys.stderr = stream
        try:
            print(f"{datetime.now(timezone.utc).isoformat()} starting {service} on loopback")
            # Uvicorn imports the respective module, which enforces SIMULATION and,
            # in the gateway, the dedicated webhook secret.
            import uvicorn

            app, port = SERVICES[service]
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
            )
            return 0
        except Exception:
            traceback.print_exc(file=stream)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
