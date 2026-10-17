"""Explicit credential, no-retry provider adapters. Callers reserve budgets first."""
from dataclasses import dataclass
import json
import os
from urllib.parse import urlsplit
import httpx


class ProviderError(RuntimeError):
    def __init__(self, code, *, status=None, raw=None):
        self.code, self.status, self.raw = code, status, raw
        super().__init__(code)


@dataclass
class ModelReply:
    data: dict
    raw: dict
    model: str
    usage: dict


@dataclass
class SearchReply:
    results: list[dict]
    raw: dict
    provider: str


class _API:
    def __init__(self, key, *, client=None):
        if not isinstance(key, str) or not key.strip():
            raise ProviderError('missing_credential')
        self.key = key
        self.client = client or httpx.Client(timeout=60, trust_env=False, follow_redirects=False)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    def _request(self, method, url, **kwargs):
        # A timeout may already be billed. Never retry here.
        try:
            with self.client.stream(method, url, **kwargs) as response:
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 4 * 1024**2:
                        raise ProviderError('response_too_large')
                    chunks.append(chunk)
                body = b''.join(chunks)
                if self.key.encode() in body:
                    raise ProviderError('credential_reflection')
                try:
                    raw = json.loads(body)
                except (ValueError, UnicodeError):
                    raise ProviderError('invalid_provider_json', status=response.status_code) from None
                if not isinstance(raw, dict):
                    raise ProviderError('invalid_provider_object', status=response.status_code)
                if not 200 <= response.status_code < 300:
                    raise ProviderError('provider_http_error', status=response.status_code, raw=raw)
                return raw
        except httpx.HTTPError:
            raise ProviderError('transport_outcome_uncertain') from None


def _json_object(text, raw):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ProviderError('model_invalid_json', raw=raw) from None
    if not isinstance(data, dict) or not data:
        raise ProviderError('model_requires_nonempty_object', raw=raw)
    return data


def _messages(messages, max_tokens):
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError('max_tokens must be positive')
    if not messages or any(not isinstance(m, dict) or m.get('role') not in
                          ('system', 'developer', 'user', 'assistant') or
                          not isinstance(m.get('content'), str) for m in messages):
        raise ValueError('messages must contain text and supported roles')


class OpenAI(_API):
    def __init__(self, key, model, **kwargs):
        super().__init__(key, **kwargs)
        if not isinstance(model, str) or not model.strip():
            raise ValueError('Explicit model required')
        self.model = model

    def complete(self, messages, max_tokens=2048):
        _messages(messages, max_tokens)
        raw = self._request('POST', 'https://api.openai.com/v1/responses',
            headers={'Authorization': 'Bearer ' + self.key}, json={
                'model': self.model, 'input': messages,
                'instructions': 'Return one nonempty JSON object. Do not wrap it in Markdown.',
                'max_output_tokens': max_tokens, 'store': False,
                'text': {'format': {'type': 'json_object'}}})
        if raw.get('status') != 'completed':
            raise ProviderError('model_response_incomplete', raw=raw)
        text = ''.join(c.get('text', '') for o in raw.get('output', [])
                       if o.get('type') == 'message' for c in o.get('content', [])
                       if c.get('type') == 'output_text')
        return ModelReply(_json_object(text, raw), raw, raw.get('model', self.model), raw.get('usage') or {})


class Anthropic(OpenAI):
    def complete(self, messages, max_tokens=2048):
        _messages(messages, max_tokens)
        system = '\n'.join(m['content'] for m in messages if m['role'] in ('system', 'developer'))
        turns = [m for m in messages if m['role'] in ('user', 'assistant')]
        if not turns:
            raise ValueError('At least one conversational message required')
        raw = self._request('POST', 'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, json={
                'model': self.model, 'messages': turns, 'max_tokens': max_tokens,
                'system': system + '\nReturn one nonempty JSON object, without Markdown.'})
        if raw.get('stop_reason') != 'end_turn':
            raise ProviderError('model_response_incomplete', raw=raw)
        text = ''.join(c.get('text', '') for c in raw.get('content', []) if c.get('type') == 'text')
        return ModelReply(_json_object(text, raw), raw, raw.get('model', self.model), raw.get('usage') or {})


def _search_args(query, limit):
    if not isinstance(query, str) or not query.strip() or len(query) > 600:
        raise ValueError('query must contain 1–600 characters')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError('limit must be 1–20')


def _results(rows, limit, link_key, snippet_key):
    if not isinstance(rows, list):
        raise ProviderError('missing_search_results')
    output = []
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        url = row.get(link_key)
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        output.append({'url': url, 'title': str(row.get('title', '')),
                       'snippet': str(row.get(snippet_key, '')), 'rank': rank})
        if len(output) == limit:
            break
    return output


class Scrappa(_API):
    def search(self, query, limit=5):
        _search_args(query, limit)
        raw = self._request('GET', 'https://scrappa.co/api/search-light',
            headers={'x-api-key': self.key}, params={'query': query, 'num': 10 if limit <= 10 else 20, 'page': 1, 'hl': 'en'})
        obj = raw.get('data', raw)
        if not isinstance(obj, dict):
            raise ProviderError('missing_search_results', raw=raw)
        return SearchReply(_results(obj.get('organic_results'), limit, 'link', 'snippet'), raw, 'scrappa')


class Brave(_API):
    def search(self, query, limit=5):
        _search_args(query, limit)
        raw = self._request('GET', 'https://api.search.brave.com/res/v1/web/search',
            headers={'X-Subscription-Token': self.key}, params={'q': query, 'count': limit})
        # Brave omits web when no web results exist. Error objects are never empty success.
        if raw.get('error'):
            raise ProviderError('search_provider_error', raw=raw)
        return SearchReply(_results(raw.get('web', {}).get('results', []), limit, 'url', 'description'), raw, 'brave')


def get_llm(provider, model):
    classes = {'openai': (OpenAI, 'OPENAI_API_KEY'), 'anthropic': (Anthropic, 'ANTHROPIC_API_KEY')}
    if provider not in classes:
        raise ValueError('Unsupported embedded LLM provider')
    cls, env = classes[provider]
    return cls(os.environ.get(env, ''), model)


def get_search(provider):
    classes = {'scrappa': (Scrappa, 'SCRAPPA_API_KEY'), 'brave': (Brave, 'BRAVE_API_KEY')}
    if provider not in classes:
        raise ValueError('Unsupported search provider')
    cls, env = classes[provider]
    return cls(os.environ.get(env, ''))


def public_provider_error(exc):
    """Map known failures to fixed remediation; never expose provider payloads."""
    if not isinstance(exc, ProviderError):
        return None
    if exc.code == 'missing_credential':
        return {'code':'provider_credential_missing','message':'Configure the selected provider key in the server environment, then restart the server. Subscription sign-in does not configure an API key.'}
    if exc.status in (401,403):
        return {'code':'provider_access_denied','message':'The provider denied access. Check the API key, account permissions and model eligibility. No automatic retry was made; any admitted reservation remains held.'}
    if exc.status == 429:
        return {'code':'provider_rate_or_quota_limit','message':'The provider reported a rate or quota limit. Check its account dashboard before starting a new job. No automatic retry was made; the reservation remains held.'}
    if exc.code == 'transport_outcome_uncertain':
        return {'code':'provider_outcome_uncertain','message':'The connection failed after submission may have occurred. Inspect this saved job and provider billing before starting another. The reservation remains held.'}
    return {'code':'provider_response_failed','message':'The provider response could not complete this operation. Inspect the saved job and provider account. No automatic retry or refund was made.'}
