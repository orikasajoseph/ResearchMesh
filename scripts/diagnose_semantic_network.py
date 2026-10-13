"""Key-free transport diagnosis; HEAD only, no paper search/model invocation."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://api.semanticscholar.org/graph/v1/paper/search'


def proxy_summary(value):
    try:
        p = urlsplit(value if '://' in value else 'http://' + value)
        return {'scheme': p.scheme, 'host': p.hostname, 'port': p.port,
                'credentialsPresent': bool(p.username or p.password)}
    except (ValueError, TypeError):
        return {'configured': True, 'parseable': False}


def error_summary(error):
    chain = []; current = error
    for _ in range(8):
        if current is None or id(current) in [x[0] for x in chain]:
            break
        chain.append((id(current), type(current).__name__))
        current = current.__cause__ or current.__context__
    return {'exceptionType': type(error).__name__, 'causeTypes': [x[1] for x in chain],
            'certificateFailure': any(x[1] == 'SSLCertVerificationError' for x in chain)}


async def probe(trust_env):
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(trust_env=trust_env, timeout=httpx.Timeout(12, connect=8),
                                     follow_redirects=False) as client:
            response = await client.head(URL, headers={'User-Agent': 'ResearchMesh-key-free-network-check/1'})
        result = {'transportReachedServer': True, 'httpStatus': response.status_code}
    except Exception as error:
        result = {'transportReachedServer': False, 'httpStatus': None, **error_summary(error)}
    return {'mode': 'environment' if trust_env else 'direct', 'apiKeySent': False,
            'durationMs': round((time.perf_counter() - started) * 1000, 3), **result}


async def main():
    records = []
    for trust_env in (True, False):
        records.append(await probe(trust_env))
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo('api.semanticscholar.org', 443)})
        dns = {'resolved': True, 'addresses': addresses}
    except OSError as error:
        dns = {'resolved': False, 'exceptionType': type(error).__name__}
    record = {'scope': 'KEY_FREE_HEAD_ONLY_NO_SEARCH_NO_MODEL', 'python': sys.executable,
              'proxySourcesPresent': [name for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                                                       'http_proxy','https_proxy','all_proxy','no_proxy') if os.environ.get(name)],
              'certificateOverridesPresent': [name for name in ('SSL_CERT_FILE','SSL_CERT_DIR') if os.environ.get(name)],
              'effectiveProxies': {k: proxy_summary(v) for k, v in getproxies().items() if k != 'no'},
              'dns': dns, 'probes': records}
    output = ROOT / 'outputs' / datetime.now().strftime('semantic-network-%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'diagnosis.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print('记录：', output)


if __name__ == '__main__':
    asyncio.run(main())
