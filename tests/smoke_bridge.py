"""Exercise the *packaged executable*, not just the Python sources, on macOS CI.

This local smoke test checks the actual authenticated loopback health endpoint.
No production SSH server, GitHub token or remote API is used.
"""
from __future__ import annotations

import http.client
from pathlib import Path
import socket
import subprocess
import sys
import time


def main(path: str) -> int:
    helper = Path(path).resolve(strict=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    token = 'CI-LOCAL-ONLY-' * 4
    proc = subprocess.Popen(
        [str(helper), '--port', str(port), '--secret', token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            if proc.poll() is not None:
                raise RuntimeError('Packaged SSH bridge terminated before it answered localhost health check')
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                conn.request('GET', '/health', headers={'Authorization': 'Bearer ' + token})
                response = conn.getresponse()
                body = response.read()
                conn.close()
                if response.status == 200 and b'"ok":true' in body:
                    print(f'Packaged SSH bridge startup succeeded: {helper.name}')
                    return 0
                raise RuntimeError('Packaged SSH bridge returned an unexpected authenticated health status')
            except (OSError, TimeoutError):
                time.sleep(.25)
        raise RuntimeError('Packaged SSH bridge did not answer localhost health check')
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python tests/smoke_bridge.py PATH_TO_PACKAGED_HELPER')
    raise SystemExit(main(sys.argv[1]))