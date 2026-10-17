"""Repair the pinned base image's dangling OpenSSL copyright link."""
from pathlib import Path
import os
import subprocess

notice = Path('/usr/share/doc/openssl/copyright')
target = Path('/usr/share/doc/libssl3t64/copyright')
if not notice.exists():
    if not notice.is_symlink() or os.readlink(notice) != '../libssl3/copyright':
        raise RuntimeError('Unexpected missing OpenSSL notice; review base image')
    metadata = subprocess.check_output([
        'dpkg-query', '-W', '-f=${source:Package}\t${source:Version}\n',
        'openssl', 'libssl3t64'], text=True).splitlines()
    if len(metadata) != 2 or metadata[0] != metadata[1] or not metadata[0].startswith('openssl\t'):
        raise RuntimeError('OpenSSL source package/version mismatch')
    if not target.is_file() or 'Upstream-Name: OpenSSL' not in target.read_text():
        raise RuntimeError('Expected OpenSSL package copyright unavailable')
    notice.unlink()
    notice.symlink_to('../libssl3t64/copyright')
if not notice.is_file():
    raise RuntimeError('OpenSSL notice is unreadable')
