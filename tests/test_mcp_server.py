"""Real SDK stdio subprocess -> actual loopback HTTP API. No paid providers."""
import asyncio
import os
import socket
import sys
import threading
import time

from mcp import Client as MCPClient, StdioServerParameters
import pytest
import uvicorn

from web_research_sim.client import Client as HTTPClient

from web_research_sim.api import create_app


class LocalWorkflow:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def collect(self, world_id, job_id, **kwargs):
        self.calls.append(job_id)
        return {'test_only': True, 'world_id': world_id}

    def capture_discovered(self, world_id, job_id, **kwargs):
        return {'test_only': True, 'sources': kwargs['sources'], 'native_client': kwargs['native_client']}

    def research(self, snapshot_id, job_id, **kwargs):
        return {'test_only': True, 'snapshot_id': snapshot_id}


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN', 'mcp-test-token')
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)
    app = create_app(tmp_path, workflow_factory=LocalWorkflow)
    store = app.state.store
    world = store.create_world('What color is the demo?')['id']
    target = store.add_capture(world, url='https://example.org/target', body=b'Target', text='Target blue', captured_at='2020-01-01T00:00:00Z')
    record = store.add_capture(world, url='https://example.org/source', body=b'Source', text='Blue is the color. Blue again.', captured_at='2020-01-01T00:00:00Z', links=['https://example.org/target'])
    snapshot = store.freeze(world)['id']
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        yield {'url': f'http://127.0.0.1:{sock.getsockname()[1]}', 'world': world, 'record': record, 'target': target, 'snapshot': snapshot, 'store': store}
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


def parameters(api, mode='frozen', pin=None, token='mcp-test-token'):
    args = ['-m', 'web_research_sim.mcp_server', '--mode', mode]
    if pin:
        args += ['--snapshot', pin]
    # Deliberately do not pass ambient model/search credentials to subprocess.
    env = {'PATH': os.environ.get('PATH', ''), 'WR_SIM_URL': api['url'], 'WR_SIM_TOKEN': token,
           'PYTHONUNBUFFERED': '1'}
    return StdioServerParameters(command=sys.executable, args=args, env=env)


def result_data(result):
    assert not result.is_error, result.content
    return result.structured_content


def test_real_stdio_initialize_discover_and_frozen_tools(real_api):
    async def scenario():
        async with MCPClient(parameters(real_api, pin=real_api['snapshot'])) as client:
            assert client.server_info.name == 'Web Research Sim'
            assert client.protocol_version
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {'frozen_search', 'frozen_open', 'frozen_find', 'frozen_follow',
                                  'native_session_start', 'native_session_status', 'native_session_tool',
                                  'native_session_submit', 'native_session_reset'}
            args = {'snapshot_id': real_api['snapshot'], 'document_id': real_api['record']['id']}
            search = result_data(await client.call_tool('frozen_search', {'snapshot_id': real_api['snapshot'], 'query': 'blue'}))
            assert len(search['results']) == 2
            opened = result_data(await client.call_tool('frozen_open', {**args, 'max_chars': 4}))
            assert opened['text'] == 'Blue'
            assert opened['next_offset'] == 4
            assert opened['body_sha256'] == real_api['record']['body_sha256']
            found = result_data(await client.call_tool('frozen_find', {**args, 'query': 'blue'}))
            assert len(found['matches']) == 2
            followed = result_data(await client.call_tool('frozen_follow', {**args, 'url': 'https://example.org/target'}))
            assert followed['id'] == real_api['target']['id']
            assert followed['live_fallback'] is False
            wrong = await client.call_tool('frozen_search', {'snapshot_id': 'f' * 64, 'query': 'blue'})
            assert wrong.is_error
            missing = await client.call_tool('frozen_open', {**args, 'document_id': 'a' * 32})
            assert missing.is_error
            invalid = await client.call_tool('frozen_open', {**args, 'max_chars': 20001})
            assert invalid.is_error
            assert (await client.call_tool('live_collect', {})).is_error
    asyncio.run(scenario())


def test_real_stdio_all_mode_create_freeze_and_idempotent_jobs(real_api):
    async def scenario():
        async with MCPClient(parameters(real_api, mode='all')) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert {'world_create', 'world_freeze', 'live_collect', 'hosted_research', 'job_status', 'workspace_status'} <= tools
            status = result_data(await client.call_tool('workspace_status', {}))
            assert 'providers' in status
            world = result_data(await client.call_tool('world_create', {'question': 'MCP test question'}))
            snap = result_data(await client.call_tool('world_freeze', {'world_id': world['id']}))
            collection = {'world_id': world['id'], 'idempotency_key': 'stdio-collection', 'model_provider': 'dummy', 'model': 'dummy', 'search_provider': 'dummy'}
            first = result_data(await client.call_tool('live_collect', collection))
            duplicate = result_data(await client.call_tool('live_collect', collection))
            assert first['id'] == duplicate['id']
            async def terminal(job_id):
                for _ in range(100):
                    job = result_data(await client.call_tool('job_status', {'job_id': job_id}))
                    if job['status'] in {'completed', 'failed'}:
                        return job
                    await asyncio.sleep(.01)
                pytest.fail('Dummy job did not finish')
            assert (await terminal(first['id']))['status'] == 'completed'
            native_args = {'world_id': world['id'], 'idempotency_key': 'native-capture', 'native_client': 'test-client',
                           'sources': [{'url': 'https://example.org/source', 'query': 'blue', 'rationale': 'Official source for the claim'}]}
            native = result_data(await client.call_tool('live_capture_discovered', native_args))
            assert result_data(await client.call_tool('live_capture_discovered', native_args))['id'] == native['id']
            assert (await terminal(native['id']))['result']['sources'] == native_args['sources']
            research = result_data(await client.call_tool('hosted_research', {'snapshot_id': snap['id'], 'idempotency_key': 'stdio-research', 'model_provider': 'dummy', 'model': 'dummy'}))
            assert (await terminal(research['id']))['result']['snapshot_id'] == snap['id']
            assert all(row['reserved'] == 0 for row in real_api['store'].usage(world['id']).values())
    asyncio.run(scenario())


def test_real_stdio_http_auth_error_is_tool_error(real_api):
    async def scenario():
        async with MCPClient(parameters(real_api, token='wrong-token')) as client:
            result = await client.call_tool('frozen_search', {'snapshot_id': real_api['snapshot'], 'query': 'blue'})
            assert result.is_error
            assert 'wrong-token' not in str(result.content)
    asyncio.run(scenario())


def test_real_stdio_native_session_answer_trace_reset_and_compare(real_api):
    async def scenario():
        async with MCPClient(parameters(real_api, pin=real_api['snapshot'])) as client:
            start_args = {'snapshot_id': real_api['snapshot'], 'provider': 'native-test-client',
                          'model': 'self-reported-test-model', 'tool_limit': 5, 'request_id': 'native-session-1'}
            session = result_data(await client.call_tool('native_session_start', start_args))
            repeated = result_data(await client.call_tool('native_session_start', start_args))
            assert repeated['id'] == session['id']
            identity = {'snapshot_id': real_api['snapshot'], 'session_id': session['id']}
            searched = result_data(await client.call_tool('native_session_tool', {
                **identity, 'action': 'search', 'arguments': {'query': 'blue'}, 'operation_key': 'search-1'}))
            assert searched['ok'] is True
            opened_args = {**identity, 'action': 'open', 'arguments': {'document_id': real_api['record']['id']}, 'operation_key': 'open-1'}
            opened = result_data(await client.call_tool('native_session_tool', opened_args))
            assert opened['ok'] is True
            assert result_data(await client.call_tool('native_session_tool', opened_args)) == opened
            submit = {**identity, 'answer': 'The source says blue.',
                      'citations': [{'document_id': real_api['record']['id'], 'quote': 'Blue is the color.'}]}
            run = result_data(await client.call_tool('native_session_submit', submit))
            assert run['status'] == 'completed'
            assert run['citation_errors'] == []
            assert run['session_id'] == session['id']
            assert run['trace']
            assert result_data(await client.call_tool('native_session_submit', submit))['id'] == run['id']
            changed = await client.call_tool('native_session_submit', {**submit, 'answer': 'A different answer'})
            assert changed.is_error
            state = result_data(await client.call_tool('native_session_status', identity))
            assert state['tool_calls'] == 2
            reset_args = {**identity, 'request_id': 'native-session-reset-1'}
            reset = result_data(await client.call_tool('native_session_reset', reset_args))
            assert reset['id'] != session['id']
            assert reset['parent_id'] == session['id']
            assert reset['tool_calls'] == 0
            assert result_data(await client.call_tool('native_session_reset', reset_args))['id'] == reset['id']
            second_identity = {**identity, 'session_id': reset['id']}
            result_data(await client.call_tool('native_session_tool', {
                **second_identity, 'action': 'open', 'arguments': {'document_id': real_api['record']['id']}, 'operation_key': 'open-2'}))
            second_run = result_data(await client.call_tool('native_session_submit', {**submit, **second_identity}))
            with HTTPClient(real_api['url'], token='mcp-test-token') as api:
                comparison = api.post('/api/compare', {'run_ids': [run['id'], second_run['id']]})
            assert comparison['snapshot_id'] == real_api['snapshot']
            assert len(comparison['metrics']) == 2
            assert all(row['reserved'] == 0 for row in real_api['store'].usage(real_api['world']).values())
            wrong_pin = await client.call_tool('native_session_status', {**identity, 'snapshot_id': 'f' * 64})
            assert wrong_pin.is_error
            with HTTPClient(real_api['url'], token='mcp-test-token') as api:
                foreign_world = api.post('/api/worlds', {'question': 'Foreign world'})
                foreign_snapshot = api.post(f"/api/worlds/{foreign_world['id']}/freeze", {})
                foreign_session = api.post(f"/api/snapshots/{foreign_snapshot['id']}/sessions", {
                    'provider': 'dummy', 'model': 'dummy', 'request_id': 'foreign-session', 'tool_limit': 5})
            foreign = await client.call_tool('native_session_status', {**identity, 'session_id': foreign_session['id']})
            assert foreign.is_error
    asyncio.run(scenario())
