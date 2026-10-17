import json
import pytest
from web_research_sim.providers import ProviderError, public_provider_error


@pytest.mark.parametrize('exc,code',[
    (ProviderError('missing_credential'),'provider_credential_missing'),
    (ProviderError('provider_http_error',status=401,raw={'secret':'FAKE-PRIVATE'}),'provider_access_denied'),
    (ProviderError('provider_http_error',status=403),'provider_access_denied'),
    (ProviderError('provider_http_error',status=429),'provider_rate_or_quota_limit'),
    (ProviderError('transport_outcome_uncertain'),'provider_outcome_uncertain'),
    (ProviderError('FAKE-PRIVATE',raw={'message':'FAKE-PRIVATE'}),'provider_response_failed'),
])
def test_fixed_provider_guidance_without_raw_payload(exc,code):
    result=public_provider_error(exc)
    assert result['code']==code
    assert 'FAKE-PRIVATE' not in json.dumps(result)


def test_unknown_exception_not_represented_as_provider_error():
    assert public_provider_error(RuntimeError('private exception')) is None
