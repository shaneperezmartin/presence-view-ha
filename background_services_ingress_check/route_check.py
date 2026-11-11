"""Temporary ingress-only diagnostic. Never reads bodies, cookies or credentials."""
import ipaddress
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PROXY='172.30.32.2'
PREFIX=re.compile(r'/api/hassio_ingress/[A-Za-z0-9_-]{16,256}')
def describe(handler):
    if handler.client_address[0] != PROXY:
        return None
    def one(name):
        values=handler.headers.get_all(name,[])
        return values[0] if len(values)==1 and len(values[0])<=256 else ''
    proto=one('X-Forwarded-Proto')
    host=one('X-Forwarded-Host')
    host_ok=(len(host)<=253 and bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\.ts\.net',host)) and all(len(label)<=63 for label in host.split('.')))
    raw=one('X-Forwarded-For')
    peers=[]
    try:
        values=raw.split(',')
        if not 1<=len(values)<=5 or any('%' in s for s in values):raise ValueError()
        peers=[str(ipaddress.ip_address(s.strip())) for s in values]
    except ValueError:
        pass
    return {'diagnostic':'ingress-route-v1','socket_peer':PROXY,
            'ingress_prefix_valid':bool(PREFIX.fullmatch(one('X-Ingress-Path'))),
            'forwarded_proto':proto if proto in ('https','http') else 'invalid',
            'forwarded_host':host if host_ok else 'invalid',
            'forwarded_peers':peers,'forwarded_peers_valid':bool(peers),
            'note':'Network-hop observations only, not cryptographic TLS attestation. Never enter a password here.'}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        report=describe(self)
        if report is None:status,data=403,{'error':'HA ingress required'}
        elif self.path=='/health':status,data=200,{'ok':True}
        elif self.path=='/':status,data=200,report
        else:status,data=404,{'error':'Not found'}
        body=json.dumps(data).encode()
        self.send_response(status)
        for name,value in [('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Content-Security-Policy',"default-src 'none'; frame-ancestors 'self'")]:self.send_header(name,value)
        self.end_headers();self.wfile.write(body)
    def setup(self):
        super().setup();self.connection.settimeout(5)
if __name__=='__main__':HTTPServer(('0.0.0.0',8099),Handler).serve_forever()
