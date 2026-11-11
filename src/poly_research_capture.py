"""Public, bounded HTTPS-only Polymarket US document/market receipt capture.

Never accepts arbitrary URLs, headers or credentials. Captured display history is
NOT an executable book or fill; settlement availability is capture time only.
"""
import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

DOCS={
 'fees':'https://docs.polymarket.us/fees.md',
 'history-doc':'https://docs.polymarket.us/api-reference/price-history/get-price-history.md',
 'settlement-doc':'https://docs.polymarket.us/api-reference/markets/get-market-settlement.md',
}
MAX_BYTES=200_000

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('redirect refused: capture host is fixed')


def url_for(kind,slug=None):
    if kind in DOCS:
        if slug is not None: raise ValueError('doc capture takes no slug')
        return DOCS[kind]
    if not slug or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',slug):
        raise ValueError('invalid market slug')
    encoded=urllib.parse.quote(slug,safe='')
    if kind=='history':
        return 'https://gateway.polymarket.us/v1/price-history?'+urllib.parse.urlencode(
            {'symbol':slug,'fixedInterval':'INTERVAL_1D','fidelity':5})
    if kind=='settlement': return 'https://gateway.polymarket.us/v1/markets/'+encoded+'/settlement'
    raise ValueError('unsupported public source kind')


def capture(kind,output,slug=None):
    url=url_for(kind,slug)
    try:
        import certifi
        context=ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context=ssl.create_default_context()
    opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),NoRedirect)
    requested=datetime.now(timezone.utc).isoformat()
    status=None; body=b''; error=None
    try:
        with opener.open(urllib.request.Request(url,headers={'User-Agent':'Valedia-Research-ReadOnly/1.0'}),timeout=12) as resp:
            status=resp.status
            body=resp.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES: raise ValueError('public capture byte limit exceeded')
    except urllib.error.HTTPError as exc:
        status=exc.code
        error='HTTP status (body deliberately not saved)'
    receipt={'url':url,'requested_at_utc':requested,'received_at_utc':datetime.now(timezone.utc).isoformat(),
             'status':status,'sha256':hashlib.sha256(body).hexdigest() if status==200 else None,
             'bytes':len(body) if status==200 else 0,'kind':kind,'market_slug':slug,
             'tls':'certificate validation (certifi CA bundle when available; system CA otherwise)','redirects':'refused',
             'provenance':'HTTPS_PUBLIC_CAPTURE_NOT_EXECUTION_OR_FILL','error':error}
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    filename=kind+('-'+slug if slug else '')
    body_path=None
    if status==200:
        body_path=output/(filename+'-'+receipt['sha256']+'.body')
        try:
            with body_path.open('xb') as stream: stream.write(body)
        except FileExistsError:
            if body_path.read_bytes()!=body:
                raise ValueError('existing content-addressed body mismatch')
        receipt['body_path']=str(body_path.resolve())
    else:
        receipt['body_path']=None
    receipt_path=output/(filename+'-'+uuid.uuid4().hex+'.receipt.json')
    encoded=(json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode('utf-8')
    with receipt_path.open('xb') as stream: stream.write(encoded)
    receipt['receipt_path']=str(receipt_path.resolve())
    receipt['receipt_sha256']=hashlib.sha256(encoded).hexdigest()
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description='Allowlisted public US GET capture, no orders')
    parser.add_argument('kind',choices=(*DOCS,'history','settlement'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--slug')
    args=parser.parse_args(argv)
    print(json.dumps(capture(args.kind,args.output,args.slug),sort_keys=True))

if __name__=='__main__':main()
