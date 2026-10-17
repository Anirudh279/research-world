import json
import httpx
import pytest
from web_research_sim.providers import OpenAI, Anthropic, Scrappa, Brave, ProviderError


def client(payload, status=200, seen=None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_wire_and_served_model():
    seen = []
    payload = {'status': 'completed', 'model': 'served-model', 'usage': {'input_tokens': 4},
               'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"answer":42}'}]}]}
    result = OpenAI('fixture-key', 'chosen-model', client=client(payload, seen=seen)).complete([{'role': 'user', 'content': 'ask'}])
    assert result.data == {'answer': 42} and result.model == 'served-model'
    wire = json.loads(seen[0].content)
    assert wire['model'] == 'chosen-model' and wire['store'] is False
    assert wire['text']['format']['type'] == 'json_object'


def test_anthropic_system_and_usage():
    seen = []
    payload = {'stop_reason': 'end_turn', 'model': 'served', 'content': [{'type': 'text', 'text': '{"ok":true}'}]}
    reply = Anthropic('fixture-key', 'chosen', client=client(payload, seen=seen)).complete([
        {'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'ask'}])
    wire = json.loads(seen[0].content)
    assert wire['messages'] == [{'role': 'user', 'content': 'ask'}]
    assert wire['system'].startswith('rules') and reply.usage == {}
    assert seen[0].headers['anthropic-version'] == '2023-06-01'


@pytest.mark.parametrize('text', ['{}', '[]', '```json\n{}\n```', 'no'])
def test_invalid_generation_preserves_raw(text):
    raw = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]}
    with pytest.raises(ProviderError) as caught:
        Anthropic('fixture-key', 'chosen', client=client(raw)).complete([{'role':'user','content':'x'}])
    assert caught.value.raw == raw


def test_no_retry_on_rate_limit():
    seen = []
    with pytest.raises(ProviderError) as caught:
        Brave('fixture-key', client=client({'error': 'rate'}, 429, seen)).search('query')
    assert caught.value.status == 429 and len(seen) == 1


def test_scrappa_results_and_wire():
    seen = []
    raw = {'data': {'organic_results': [{'link':'https://example.org/a','title':'A','snippet':'S'}, {'link':'file:///secret'}]}}
    reply = Scrappa('fixture-key', client=client(raw, seen=seen)).search('test', 5)
    assert reply.results == [{'url':'https://example.org/a','title':'A','snippet':'S','rank':1}]
    assert seen[0].url.params['query'] == 'test' and seen[0].url.params['num'] == '10'
    assert reply.raw == raw


def test_brave_results():
    raw = {'web': {'results': [{'url':'https://example.org','title':'A','description':'S'}]}}
    assert Brave('fixture-key', client=client(raw)).search('test').results[0]['snippet'] == 'S'


def test_secret_reflection_never_retained():
    with pytest.raises(ProviderError) as caught:
        Brave('fixture-key', client=client({'message':'fixture-key'})).search('test')
    assert caught.value.raw is None and 'fixture-key' not in str(caught.value)
