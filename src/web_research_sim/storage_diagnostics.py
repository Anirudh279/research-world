"""Storage configuration checks without object reads, writes or remote calls."""
import importlib.util
import os
from pathlib import Path
import sqlite3
from .core import SimError, encoded
from .storage import storage_config


def storage_status(root):
    root = Path(root)
    report = {'backend': 'unknown', 'status': 'unavailable', 'metadata': 'local_sqlite',
              'connectivity_verified': False, 'remote_requests': 0}
    try:
        config = storage_config(root)
    except SimError:
        return {**report, 'detail': 'Invalid storage.json. Use the documented non-secret fields; keep credentials in environment variables.'}
    report['backend'] = config['backend']
    dbpath = root / 'state.sqlite3'
    if dbpath.exists():
        try:
            db = sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                prior = db.execute("SELECT value FROM metadata WHERE key='object_backend'").fetchone()
                if prior and prior[0] != encoded(config).decode():
                    return {**report, 'detail': 'Storage selection differs from the saved workspace. Restore its configuration, or import into a new workspace.'}
                if not prior and config['backend'] != 'local' and db.execute('SELECT COUNT(*) FROM worlds').fetchone()[0]:
                    return {**report, 'detail': 'Existing local workspace cannot switch to S3. Import a permitted bundle into a new workspace.'}
            finally:
                db.close()
        except sqlite3.Error:
            return {**report, 'detail': 'Cannot verify the metadata database. Check permissions, database format and other processes.'}
    if config['backend'] == 's3':
        if importlib.util.find_spec('boto3') is None:
            return {**report, 'detail': 'Install the s3 extra or select the supplied S3 container build.'}
        if not all(os.environ.get(k) for k in ('WRS_S3_ACCESS_KEY_ID', 'WRS_S3_SECRET_ACCESS_KEY')):
            return {**report, 'detail': 'Set both dedicated WRS_S3 archive credentials in the server environment.'}
        detail = 'S3 configuration and credential presence checked. Endpoint access, permissions, quota and object integrity remain unverified. SQLite stays on the local volume.'
    else:
        detail = 'Local archive selected. This configuration check does not verify object integrity or long-term durability.'
    return {**report, 'status': 'configured_unverified', 'detail': detail}
