from email.message import Message
from types import SimpleNamespace
import json
import unittest
from route_check import describe
class RouteCheckTests(unittest.TestCase):
    def fixture(self):
        h=Message()
        for k,v in {'X-Ingress-Path':'/api/hassio_ingress/'+'a'*32,'X-Forwarded-Proto':'https','X-Forwarded-Host':'fixture.tailtest.ts.net','X-Forwarded-For':'100.64.0.2, 127.0.0.1, 172.30.32.1'}.items():h[k]=v
        return SimpleNamespace(headers=h,client_address=('172.30.32.2',5))
    def test_valid(self):
        d=describe(self.fixture());self.assertTrue(d['ingress_prefix_valid']);self.assertEqual(d['forwarded_peers'],['100.64.0.2','127.0.0.1','172.30.32.1'])
    def test_direct_denied(self):
        h=self.fixture();h.client_address=('127.0.0.1',2);self.assertIsNone(describe(h))
    def test_credentials_and_prefix_not_returned(self):
        h=self.fixture();h.headers['Cookie']='fixture-secret';h.headers['Authorization']='Bearer fixture-secret'
        s=json.dumps(describe(h));self.assertNotIn('fixture-secret',s);self.assertNotIn('a'*32,s)
    def test_duplicate_headers_fail_closed(self):
        for name,key,expected in [('X-Forwarded-For','forwarded_peers',[]),('X-Forwarded-Host','forwarded_host','invalid'),('X-Forwarded-Proto','forwarded_proto','invalid'),('X-Ingress-Path','ingress_prefix_valid',False)]:
            h=self.fixture();h.headers[name]=h.headers[name];self.assertEqual(describe(h)[key],expected)
    def test_invalid_chain_never_echoes_raw(self):
        for value in ['secret','1.2.3.4:443','fe80::1%eth0','1.2.3.4,'*20,'x'*257]:
            h=self.fixture();h.headers.replace_header('X-Forwarded-For',value);self.assertEqual(describe(h)['forwarded_peers'],[])
    def test_invalid_host_never_echoes(self):
        for host in ['<script>secret</script>','..ts.net','-bad.tail.ts.net','a'*64+'.tail.ts.net']:
            h=self.fixture();h.headers.replace_header('X-Forwarded-Host',host);self.assertEqual(describe(h)['forwarded_host'],'invalid')
if __name__=='__main__':unittest.main()
