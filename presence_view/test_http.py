import unittest, threading, urllib.request, urllib.error, json
from http.server import ThreadingHTTPServer
from server import Handler
class Stub:
 def status(self): return {'sensors':[]}
 def hours(self,d): return {'hours':[]}
 def history(self,a,b): return {'sensors':[]}
class HTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.s=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.s.service=Stub();cls.s.local_test=True
  cls.t=threading.Thread(target=cls.s.serve_forever,daemon=True);cls.t.start();cls.url='http://127.0.0.1:'+str(cls.s.server_port)
 @classmethod
 def tearDownClass(cls):cls.s.shutdown();cls.s.server_close();cls.t.join()
 def test_status(self):
  with urllib.request.urlopen(self.url+'/api/status') as r:
   self.assertEqual(json.load(r),{'sensors':[]});self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertIn("connect-src 'self'",r.headers['Content-Security-Policy'])
 def test_no_service_proxy(self):
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/api/services/light/turn_on')
  self.assertEqual(e.exception.code,404);e.exception.close()
 def test_post_forbidden(self):
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(self.url+'/api/status',data=b'{}'))
  self.assertEqual(e.exception.code,405);e.exception.close()
 def test_ingress_restricted(self):
  self.s.local_test=False
  try:
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/api/status')
   self.assertEqual(e.exception.code,403);e.exception.close()
  finally:self.s.local_test=True
 def test_no_path_escape(self):
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/../server.py')
  self.assertEqual(e.exception.code,404);e.exception.close()
if __name__=='__main__':unittest.main()
