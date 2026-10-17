"""Provider admission using explicit operator-reviewed bounds; never invent prices."""
import json
from pathlib import Path
from .core import SimError, encoded, digest, timestamp, now
from .costs import CostLedger, money


class ProviderAdmission:
    def __init__(self, root):
        self.root = Path(root)
        self.ledger = CostLedger(root)

    def reserve(self, world_id, kind, key, request):
        path = self.root / 'cost-policy.json'
        try:
            with path.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            policy = json.loads(raw)
            if policy['schema'] != 'provider-cost-policy-v1' or not isinstance(policy['bounds'], list):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise SimError('cost_policy_required','Install a reviewed cost-policy.json and allocate a monetary budget before hosted calls.',409) from None
        if timestamp(policy.get('expires_at')) <= timestamp(now()):
            raise SimError('cost_policy_expired','Recheck provider prices and replace the expired bound policy.',409)
        matches = [b for b in policy['bounds'] if isinstance(b,dict) and
                   (b.get('kind'),b.get('provider'),b.get('model','')) == (kind,request.get('provider'),request.get('model',''))]
        if len(matches) != 1:
            raise SimError('cost_bound_required','Exactly one reviewed bound must match this provider, model and operation kind.',409)
        bound = matches[0]
        ceiling = bound.get('max_request_bytes')
        if type(ceiling) is not int or not 1 <= ceiling <= 4*1024**2 or len(encoded(request)) > ceiling:
            raise SimError('cost_request_limit','Request exceeds its reviewed pricing envelope.',409)
        basis = bound.get('basis')
        if not isinstance(basis,str) or not 1 <= len(basis) <= 300 or bound.get('operator_reviewed') is not True:
            raise SimError('cost_bound_unreviewed','Provide the reviewed pricing source and bound calculation.',409)
        if kind == 'model_calls' and bound.get('max_output_tokens') != 2048:
            raise SimError('cost_output_limit','Current hosted adapters require a bound for 2048 output tokens.',409)
        maximum = money(bound.get('max_cost_micro_usd'),'per-call bound')
        if maximum == 0:
            raise SimError('cost_bound_required','Hosted calls require a positive bound; free credits are not assumed.',409)
        operation_key = world_id + ':' + key
        self.ledger.reserve(operation_key,provider=request['provider'],model=request.get('model','search'),
            request=request,max_cost_micro_usd=maximum,bound_basis='policy_sha256='+digest(raw)+'; '+basis)
        return operation_key

    def assert_ready(self, key, request):
        """Revalidate after blocking admission, immediately before invoking adapter."""
        operation = self.ledger.operation(key)
        if operation['status'] != 'reserved':
            raise SimError('cost_operation_consumed','This reservation cannot authorize another transmission.',409)
        if (operation['request_hash'], operation['provider'], operation['model']) != (
                digest(encoded(request)), request.get('provider'), request.get('model','search')):
            raise SimError('cost_operation_conflict','Request changed after monetary admission.',409)
        try:
            with (self.root / 'cost-policy.json').open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536 or not operation['bound_basis'].startswith('policy_sha256='+digest(raw)+'; '):
                raise ValueError()
            policy = json.loads(raw)
        except (OSError, ValueError, TypeError):
            raise SimError('cost_policy_changed','Pricing policy changed after admission. No transmission is authorized.',409) from None
        # Clock check is last: ledger/file access may have waited for a writer.
        if timestamp(policy.get('expires_at')) <= timestamp(now()):
            raise SimError('cost_policy_expired','Pricing policy expired while admission was pending. No transmission is authorized.',409)

    def settle_unknown(self,key,outcome,usage=None):
        # Usage is evidence, not a provider invoice. Never release a bound based
        # only on locally estimated prices, or treat a failed call as free.
        fields = {}
        if isinstance(usage,dict):
            for name in ('input_tokens','output_tokens'):
                value=usage.get(name)
                if type(value) is int and value >= 0: fields[name]=value
        return self.ledger.settle(key,outcome=outcome,usage=fields)
