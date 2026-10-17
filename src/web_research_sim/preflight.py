"""Local setup checks. Never contact model providers or print credential values."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


def preflight(data, port=8000):
    checks = []
    def add(name, status, detail):
        checks.append(dict(name=name, status=status, detail=detail))
    docker = shutil.which('docker')
    if docker is None:
        add('docker', 'unavailable', 'Install Docker for the container route, or use the Python server.')
    else:
        try:
            result = subprocess.run([docker, 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=8)
            add('docker', 'ready' if result.returncode == 0 else 'unavailable',
                'Docker daemon responds.' if result.returncode == 0 else 'Start Docker and check your Docker context; daemon did not respond successfully.')
        except (OSError, subprocess.TimeoutExpired):
            add('docker', 'unavailable', 'Docker check failed or timed out; start Docker and check its context.')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
            add('local_port', 'ready', 'Loopback port is available now; availability can change before startup.')
        except OSError:
            add('local_port', 'unavailable', 'Port is occupied or cannot be bound. Check the existing server or choose another port.')
    root = Path(data)
    # Do not create an archive or change permissions during a diagnostic.
    if not root.is_dir():
        add('storage', 'unverified', 'Data directory does not exist. Create it, then rerun this check.')
    else:
        try:
            with tempfile.TemporaryFile(dir=root) as probe:
                probe.write(b'research-world-preflight\n'); probe.flush(); os.fsync(probe.fileno())
                probe.seek(0)
                if probe.read() != b'research-world-preflight\n':
                    raise OSError('roundtrip')
            add('storage', 'ready', 'Temporary write/read/fsync passed. Capacity and long-term durability are not certified.')
        except OSError:
            add('storage', 'unavailable', 'Cannot write and sync the data directory. Check mount permissions and free space.')
    from .storage_diagnostics import storage_status
    archive = storage_status(root)
    add('archive_backend', archive['status'], archive['detail'])
    for provider, variable in [('openai','OPENAI_API_KEY'), ('anthropic','ANTHROPIC_API_KEY'), ('scrappa','SCRAPPA_API_KEY'), ('brave','BRAVE_API_KEY')]:
        add(provider, 'configured_unverified' if os.environ.get(variable) else 'not_configured',
            'Credential exists in this process; access, quota and billing were not tested.' if os.environ.get(variable) else 'No credential in this process. Native subscription clients manage their own authentication.')
    return {'schema':'local-preflight-v1','checks':checks,'provider_requests':0,
            'scope':'Local process only. Container environment may differ; ready does not mean live provider verification.'}
