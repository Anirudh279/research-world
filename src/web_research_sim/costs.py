"""Durable monetary admission; default hosted workflows reserve before transmission."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from .core import SimError, digest, encoded, now

MAX_MICROUSD = 10**15
USAGE_FIELDS = {'input_tokens', 'output_tokens', 'search_credits', 'subscription_usage'}


def money(value, name):
    if type(value) is not int or not 0 <= value <= MAX_MICROUSD:
        raise SimError('invalid_cost', f'{name} must be explicit integer USD microdollars, from 0 to {MAX_MICROUSD}.')
    return value


def label(value, name):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
        raise SimError('invalid_cost_identity', f'Declare {name} explicitly.')
    return value


def usage_metadata(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - USAGE_FIELDS:
        raise SimError('invalid_usage', 'Use input_tokens, output_tokens, search_credits and subscription_usage only.')
    result = {field: value.get(field) for field in sorted(USAGE_FIELDS)}
    for field in ('input_tokens', 'output_tokens', 'search_credits'):
        if result[field] is not None and (type(result[field]) is not int or result[field] < 0):
            raise SimError('invalid_usage', f'{field} must be a nonnegative integer or null (unknown).')
    if result['subscription_usage'] is not None and not isinstance(result['subscription_usage'], dict):
        raise SimError('invalid_usage', 'subscription_usage must be a JSON object or null (unknown).')
    if len(encoded(result)) > 16384:
        raise SimError('invalid_usage', 'Usage metadata exceeds 16 KiB.')
    return result


class CostLedger:
    """One immutable allocation per workspace directory. All mutations serialize."""
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'costs.sqlite3'
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS allocation(singleton INTEGER PRIMARY KEY CHECK(singleton=1), amount INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cost_operations(
                  operation_key TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                  provider TEXT NOT NULL, model TEXT NOT NULL, request_hash TEXT NOT NULL,
                  max_cost INTEGER NOT NULL, bound_basis TEXT NOT NULL, created_at TEXT NOT NULL,
                  status TEXT NOT NULL, actual_cost INTEGER, charged INTEGER NOT NULL,
                  usage TEXT NOT NULL, completed_at TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def allocate(self, micro_usd):
        money(micro_usd, 'allocation')
        with self.db() as db:
            old = db.execute('SELECT amount FROM allocation WHERE singleton=1').fetchone()
            if old and old['amount'] != micro_usd:
                raise SimError('allocation_conflict', 'Workspace allocation is immutable. Do not reset it to bypass consumed costs.', 409)
            db.execute('INSERT OR IGNORE INTO allocation VALUES(1,?,?)', (micro_usd, now()))
        return self.summary()

    def reserve(self, operation_key, *, provider, model, request, max_cost_micro_usd, bound_basis):
        """Call before transmitting. A duplicate reservation NEVER authorizes replay."""
        for name, value in [('operation_key', operation_key), ('provider', provider), ('model', model), ('bound_basis', bound_basis)]:
            label(value, name)
        money(max_cost_micro_usd, 'maximum cost')
        request_hash = digest(encoded(request))
        with self.db() as db:
            old = db.execute('SELECT * FROM cost_operations WHERE operation_key=?', (operation_key,)).fetchone()
            if old:
                same = (old['provider'], old['model'], old['request_hash'], old['max_cost'], old['bound_basis']) == (provider, model, request_hash, max_cost_micro_usd, bound_basis)
                raise SimError('cost_operation_consumed' if same else 'cost_operation_conflict', 'Observe the existing operation; do not transmit it again.', 409)
            allocation = db.execute('SELECT amount FROM allocation WHERE singleton=1').fetchone()
            if not allocation:
                raise SimError('allocation_required', 'Declare a workspace monetary allocation before admission.', 409)
            if db.execute("SELECT 1 FROM cost_operations WHERE status='overspent' LIMIT 1").fetchone():
                raise SimError('cost_bound_breached', 'A provider exceeded its admitted maximum; all further admission is blocked.', 409)
            used = db.execute('SELECT COALESCE(SUM(charged),0) FROM cost_operations').fetchone()[0]
            if used + max_cost_micro_usd > allocation['amount']:
                raise SimError('cost_budget_exhausted', 'The maximum cost does not fit the remaining allocation.', 409)
            operation_id = uuid.uuid4().hex
            db.execute('INSERT INTO cost_operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                       (operation_key, operation_id, provider, model, request_hash, max_cost_micro_usd, bound_basis, now(),
                        'reserved', None, max_cost_micro_usd, encoded(usage_metadata()).decode()))
        return self.operation(operation_key)

    def settle(self, operation_key, *, actual_cost_micro_usd=None, outcome='completed', usage=None):
        """One terminal report. Uncertain or unknown cost retains the full bound."""
        label(operation_key, 'operation_key')
        if outcome not in {'completed', 'failed', 'uncertain'}:
            raise SimError('invalid_cost_outcome', 'Use completed, failed or uncertain.')
        if actual_cost_micro_usd is not None:
            money(actual_cost_micro_usd, 'actual cost')
        metadata = usage_metadata(usage)
        with self.db() as db:
            row = db.execute('SELECT * FROM cost_operations WHERE operation_key=?', (operation_key,)).fetchone()
            if not row:
                raise SimError('cost_operation_missing', 'No admitted operation exists.', 404)
            if row['status'] != 'reserved':
                raise SimError('cost_operation_consumed', 'A terminal cost report is immutable.', 409)
            over = actual_cost_micro_usd is not None and actual_cost_micro_usd > row['max_cost']
            charged = max(row['max_cost'], actual_cost_micro_usd or 0) if outcome == 'uncertain' or actual_cost_micro_usd is None or over else actual_cost_micro_usd
            status = 'overspent' if over else outcome
            db.execute('UPDATE cost_operations SET status=?,actual_cost=?,charged=?,usage=?,completed_at=? WHERE operation_key=?',
                       (status, actual_cost_micro_usd, charged, encoded(metadata).decode(), now(), operation_key))
        return self.operation(operation_key)

    def operation(self, operation_key):
        with self.db() as db:
            row = db.execute('SELECT * FROM cost_operations WHERE operation_key=?', (operation_key,)).fetchone()
        if not row:
            raise SimError('cost_operation_missing', 'No admitted operation exists.', 404)
        result = dict(row)
        result['usage'] = json.loads(result['usage'])
        result['overspend_micro_usd'] = max(0, (result['actual_cost'] or 0) - result['max_cost'])
        return result

    def summary(self):
        with self.db() as db:
            allocation = db.execute('SELECT amount FROM allocation WHERE singleton=1').fetchone()
            rows = db.execute('SELECT * FROM cost_operations').fetchall()
        amount = allocation['amount'] if allocation else None
        charged = sum(row['charged'] for row in rows)
        return {'currency': 'USD', 'unit': 'microdollars', 'allocation': amount, 'charged': charged,
                'remaining': max(0, amount - charged) if amount is not None else None,
                'allocation_overrun': max(0, charged - amount) if amount is not None else None,
                'admission_blocked': amount is None or any(r['status'] == 'overspent' for r in rows),
                'operations': len(rows), 'actual_cost_unknown_operations': sum(r['actual_cost'] is None for r in rows),
                'integration': 'default_hosted_workflows_with_operator_reviewed_bounds'}
