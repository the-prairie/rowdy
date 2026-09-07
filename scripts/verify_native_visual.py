"""Run the actual macOS native test executable against the real local service.

Never mutates OS Screen Recording settings. The GPUI runner captures its own
Metal-rendered surface. A passed test is not a packaged app or warehouse approval.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading

from rowdy.server import Server, examples


def run(binary, output):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    binary = Path(binary).resolve()
    if sys.platform != 'darwin':
        raise RuntimeError('Actual Metal/native verification requires macOS; no simulated substitute')
    with tempfile.TemporaryDirectory(prefix='rowdy-native-') as directory:
        home = Path(directory).resolve() / 'state'
        home.mkdir(mode=0o700)
        roots = examples(home)
        # Explicit cloud-off capabilities. Reviewed writes are enabled only for
        # the disposable synthetic test projects; the flow must undo its edit.
        server = Server(roots, home, allow_dbt=False, allow_bigquery=False, writable=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'CREDENTIAL', 'API_KEY'))}
        env.update(ROWDY_HOME=str(home), ROWDY_PYTHON=sys.executable,
                   ROWDY_NATIVE_PROJECT=str(roots[0].resolve()), ROWDY_NATIVE_OUTPUT=str(output),
                   ZED_STATELESS='1', ZED_RELEASE_CHANNEL='dev', RUST_BACKTRACE='1')
        try:
            with (output / 'native-runtime.txt').open('w') as log:
                completed = subprocess.run([str(binary)], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
            result_path = output / 'result.json'
            if completed.returncode != 0 or not result_path.exists():
                raise RuntimeError('Native runtime did not pass; inspect native-runtime.txt')
            result = json.loads(result_path.read_text())
            if result.get('status') != 'passed' or result.get('cloud_enabled') is not False or result.get('guarded_apply_undo') is not True:
                raise RuntimeError('Incomplete or unsafe native evidence')
            if len(list(output.glob('*native*.png'))) != 9:
                raise RuntimeError('Expected real native captures not found')
            print('Native GPUI pointer/buffer/bridge/service workflow passed. Not a signed app or cloud test.')
        finally:
            server.shutdown(); server.server_close(); thread.join(3); server.service.db.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--binary', required=True); p.add_argument('--out', required=True)
    a = p.parse_args(); run(a.binary, a.out)
