"""Run the existing control-enabled AION LTM as a background Windows logon task.

Uses the existing per-user control token; never touches the PAPER worker arm state.
Run using .venv\\Scripts\\pythonw.exe from the AION-Execution directory.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / 'logs' / 'aion-ltm-background.log'
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{48,}$')


def load_control_token(token_file: Path) -> str:
    """Create once; reuse the exact token from start_aion_ltm_controls.ps1."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with token_file.open('x', encoding='ascii') as handle:
            # Atomic create prevents replacing an existing, possibly in-use token.
            handle.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    token = token_file.read_text(encoding='ascii').strip()
    if not TOKEN_RE.fullmatch(token):
        raise RuntimeError('Existing AION LTM token is invalid; refusing to rotate it')
    return token


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # pythonw.exe normally has no console streams; retain diagnostics privately.
    stream = LOG.open('a', encoding='utf-8', buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s', force=True)
    try:
        local_app_data = os.environ.get('LOCALAPPDATA', '').strip()
        if not local_app_data:
            raise RuntimeError('LOCALAPPDATA unavailable: run under your Windows user account')
        if not (ROOT / 'aion_ltm_with_controls.py').is_file():
            raise RuntimeError('Missing aion_ltm_with_controls.py (control-enabled dashboard)')
        token_file = Path(local_app_data) / 'AION' / 'ltm-control-token.txt'
        os.environ['AION_CONTROL_TOKEN'] = load_control_token(token_file)
        os.chdir(ROOT)
        import uvicorn
        logging.info('Starting control-enabled AION LTM on 127.0.0.1:8501 (PAPER controls)')
        uvicorn.run('aion_ltm_with_controls:app', host='127.0.0.1', port=8501,
                    log_config=None, access_log=False, workers=1)
    except BaseException:
        logging.exception('AION LTM background process stopped unexpectedly')
        raise


if __name__ == '__main__':
    main()
