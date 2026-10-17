"""Opt-in S3 archive objects; never substitutes live source pages."""
import json
import os
import re
from urllib.parse import urlsplit
from .core import SimError, digest, identifier

LIMIT=16*1024**2
CREDENTIAL_ENV=('WRS_S3_ACCESS_KEY_ID','WRS_S3_SECRET_ACCESS_KEY','WRS_S3_SESSION_TOKEN')


def storage_config(root):
    path=root/'storage.json'
    if not path.exists():return {'backend':'local'}
    try:
        with path.open('rb') as f:raw=f.read(8193)
        if len(raw)>8192:raise ValueError()
        config=json.loads(raw)
        if not isinstance(config,dict):raise ValueError()
        if config=={'backend':'local'}:return config
        if set(config)-{'backend','endpoint','bucket','prefix','region'} or config.get('backend')!='s3':raise ValueError()
        endpoint=config['endpoint']
        if not isinstance(endpoint,str):raise ValueError()
        p=urlsplit(endpoint)
        if p.scheme!='https' and not (p.scheme=='http' and p.hostname in ('127.0.0.1','localhost','::1')):raise ValueError()
        if not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):raise ValueError()
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',config['bucket']):raise ValueError()
        prefix=config.get('prefix','objects')
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*',prefix) or len(prefix)>200:raise ValueError()
        region=config.get('region','auto')
        if not re.fullmatch(r'[A-Za-z0-9-]{1,64}',region):raise ValueError()
        return {'backend':'s3','endpoint':endpoint.rstrip('/'),'bucket':config['bucket'],'prefix':prefix,'region':region}
    except (ValueError,KeyError,TypeError,OSError):
        raise SimError('storage_config','Invalid non-secret storage configuration.') from None


class S3Objects:
    def __init__(self,config,*,client=None):
        self.config=config
        if client is not None:self.client=client;return
        try:
            import boto3
            from botocore.config import Config
        except ImportError:raise SimError('storage_dependency','Install the s3 optional dependency.') from None
        key=os.environ.get(CREDENTIAL_ENV[0]);secret=os.environ.get(CREDENTIAL_ENV[1])
        if not key or not secret:raise SimError('storage_credentials','Set the dedicated WRS_S3 credentials.')
        # Explicit credentials disable ambient AWS profiles, role/metadata lookup.
        self.client=boto3.client('s3',endpoint_url=config['endpoint'],region_name=config['region'],
            aws_access_key_id=key,aws_secret_access_key=secret,aws_session_token=os.environ.get(CREDENTIAL_ENV[2]),
            config=Config(signature_version='s3v4',connect_timeout=5,read_timeout=15,
                retries={'total_max_attempts':1},proxies={},s3={'addressing_style':'path'},
                request_checksum_calculation='when_required',response_checksum_validation='when_required'))

    def _key(self,sha):
        identifier(sha,64)
        return self.config['prefix']+'/'+sha

    def get(self,sha):
        key=self._key(sha);body=None
        try:
            response=self.client.get_object(Bucket=self.config['bucket'],Key=key)
            body=response['Body']
            if response.get('ContentLength',0)>LIMIT:raise SimError('integrity_error','Archive object exceeds its size limit.',409)
            raw=body.read(LIMIT+1)
            if len(raw)>LIMIT or digest(raw)!=sha:raise SimError('integrity_error','Archive bytes do not match their digest.',409)
            return raw
        except SimError:raise
        except Exception as exc:
            code=getattr(exc,'response',{}).get('Error',{}).get('Code')
            if code in ('NoSuchKey','404'):raise SimError('missing_object','A referenced archive object is missing.',409) from None
            raise SimError('storage_unavailable','Archive storage read failed; no fallback is permitted.',503) from None
        finally:
            if body is not None:
                try:body.close()
                except Exception:pass

    def put(self,raw):
        if not isinstance(raw,bytes) or len(raw)>LIMIT:raise SimError('object_limit','Object must be bytes and at most 16 MiB.')
        sha=digest(raw)
        try:
            self.client.put_object(Bucket=self.config['bucket'],Key=self._key(sha),Body=raw,
                                   ContentType='application/octet-stream',IfNoneMatch='*')
        except Exception as exc:
            code=getattr(exc,'response',{}).get('Error',{}).get('Code')
            if code not in ('PreconditionFailed','412'):raise SimError('storage_unavailable','Archive write failed or is uncertain; no automatic retry.',503) from None
        # Also verifies pre-existing content; never overwrite corrupt collisions.
        if self.get(sha)!=raw:raise SimError('integrity_error','Archive object differs from its digest.',409)
        return sha
