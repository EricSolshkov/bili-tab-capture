"""Start the packaged app and verify bundled templates/assets over loopback."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

executable = Path(sys.argv[1]).resolve()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = dict(os.environ, PORT=str(port), BTAB_NO_BROWSER='1', PYTHONUTF8='1')
with tempfile.TemporaryFile() as log:
    process = subprocess.Popen([str(executable)], env=env, stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 90
        while True:
            if process.poll() is not None:
                raise RuntimeError('Packaged application exited before serving HTTP')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                    assert b'applyScoreRows' in response.read(), 'Missing row-review template'
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Packaged application startup timed out')
                time.sleep(1)
        for asset in ['score_rows.js', 'score_sequence.js']:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/{asset}', timeout=5) as response:
                assert len(response.read()) > 100, f'Missing asset: {asset}'
        print('Packaged executable serves row-review UI and assets successfully')
    except Exception:
        log.seek(0)
        print(log.read().decode('utf-8', errors='replace'))
        raise
    finally:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=15)
