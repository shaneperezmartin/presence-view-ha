import tempfile,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from server import PresenceService,iso
from history_store import HistoryStore
from test_server import C,states
class FakeHA:
 def __init__(self,start):self.start=start;self.history_calls=0;self.fail=False
 def get(self,path):
  if path=='config':return {'time_zone':'UTC'}
  if self.fail:raise RuntimeError('unavailable')
  values=states()
  for eid,s in values.items():s.update(entity_id=eid,last_updated=iso(self.start))
  if path=='states':return list(values.values())
  if path.startswith('history/period/'):
   self.history_calls+=1;return [[s] for s in values.values()]
  raise AssertionError(path)
class ServiceHistoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=HistoryStore(['a'],self.tmp.name);self.now=round(time.time()-60,6);self.ha=FakeHA(self.now);self.service=PresenceService(self.ha,[C],self.store)
 def tearDown(self):self.service.stop_collection();self.store.close();self.tmp.cleanup()
 def snap(self):return {'id':'a','status':'online','presence':True,'count':1,'coordinate_support':True,'unit':'cm','targets':[{'id':'1','x':20,'y':100}]}
 @patch('server.shutil.disk_usage',return_value=SimpleNamespace(free=10**10))
 def test_background_without_browser(self,_):
  self.service.start_collection();limit=time.monotonic()+3
  while time.monotonic()<limit and not self.store.coverage()['sensors']:time.sleep(.02)
  self.service.stop_collection();self.assertGreater(self.store.coverage()['sensors']['a']['samples'],0)
 @patch('server.shutil.disk_usage',return_value=SimpleNamespace(free=10**10))
 def test_failure_is_unavailable(self,_):
  self.ha.fail=True;self.service.collect_once();r=self.store.query('a',self.now,time.time()+1)['samples'];self.assertEqual(r[0]['status'],'unavailable');self.assertEqual(r[0]['targets'],[])
 @patch('server.shutil.disk_usage',return_value=SimpleNamespace(free=1000))
 def test_low_disk_pause(self,_):
  self.service.collect_once();self.assertTrue(self.service.storage_error);self.assertFalse(self.store.coverage()['sensors'])
 def test_local_only_no_ha_history(self):
  self.store.append_snapshot(self.now,self.snap());self.store.append_snapshot(self.now+2,self.snap())
  r=self.service.history(iso(self.now),iso(self.now+4));self.assertEqual(self.ha.history_calls,0);self.assertEqual(len(r['sensors'][0]['points']),2);self.assertEqual(sum(p['weight_seconds'] for p in r['sensors'][0]['points']),2)
 def test_mixed_no_overlap(self):
  self.store.append_snapshot(self.now+10,self.snap());self.store.append_snapshot(self.now+12,self.snap())
  r=self.service.history(iso(self.now),iso(self.now+14));points=r['sensors'][0]['points'];self.assertEqual(len(points),7);self.assertEqual(sum(p['weight_seconds'] for p in points),12);self.assertNotEqual(points[4]['segment'],points[5]['segment'])
if __name__=='__main__':unittest.main()
