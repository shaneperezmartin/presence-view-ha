import tempfile,time,unittest
from urllib.parse import parse_qs,urlsplit
from types import SimpleNamespace
from unittest.mock import patch
from server import PresenceService,iso,stamp
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
 def test_per_sensor_unaligned_boundary_clips_weight(self):
  with tempfile.TemporaryDirectory() as directory:
   store=HistoryStore(['a','b'],directory)
   try:
    for offset in (1,3,5):store.append_snapshot(self.now+offset,self.snap())
    for offset in (5,7):store.append_snapshot(self.now+offset,dict(self.snap(),id='b'))
    configs=[C,dict(C,id='b',name='Sensor B')]
    result=PresenceService(self.ha,configs,store).history(iso(self.now),iso(self.now+8))
    points=result['sensors'][0]['points']
    self.assertEqual(points[0]['weight_seconds'],1)
    self.assertEqual(sum(p['weight_seconds'] for p in points),5)
    for sensor,boundary in zip(result['sensors'],(self.now+1,self.now+5)):
     for point in sensor['points']:
      if point.get('source')!='local':self.assertLessEqual(stamp(point['t'])+point['weight_seconds'],boundary)
   finally:store.close()
 def test_local_unknown_presence_not_vacancy(self):
  self.store.append_snapshot(self.now,dict(self.snap(),presence=None,count=0,targets=[]))
  result=self.service.history(iso(self.now),iso(self.now+4))
  self.assertEqual(result['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_local_contradictory_presence_not_vacancy(self):
  self.store.append_snapshot(self.now,dict(self.snap(),presence=True,count=0,targets=[]))
  result=self.service.history(iso(self.now),iso(self.now+4))
  self.assertEqual(result['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_local_known_empty(self):
  self.store.append_snapshot(self.now,dict(self.snap(),presence=False,count=0,targets=[]))
  result=self.service.history(iso(self.now),iso(self.now+4))
  self.assertEqual(result['sensors'][0]['history_status'],'no_targets')
 def test_local_empty_does_not_erase_unknown_recorder(self):
  self.store.append_snapshot(self.now+2,dict(self.snap(),presence=False,count=0,targets=[]))
  original=self.ha.get
  def unknown_history(path):return [] if path.startswith('history/period/') else original(path)
  self.ha.get=unknown_history
  result=self.service.history(iso(self.now),iso(self.now+4))
  self.assertEqual(result['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_history_requests_all_changes(self):
  original=self.ha.get;calls=[]
  def record(path):
   calls.append(path);return original(path)
  self.ha.get=record
  self.service.history(iso(self.now),iso(self.now+4))
  params=parse_qs(urlsplit(next(p for p in calls if p.startswith('history/period/'))).query,keep_blank_values=True)
  self.assertEqual(params['significant_changes_only'],['0'])
  self.assertIn('no_attributes',params);self.assertIn('minimal_response',params)
 def test_attribute_mappings_retain_full_history(self):
  for field in ('presence','count','x','y','height'):
   with self.subTest(field=field):
    config=dict(C,targets=[dict(C['targets'][0])])
    (config if field in ('presence','count') else config['targets'][0])[field]={'entity':'sensor.attributes','attribute':field}
    calls=[];original=self.ha.get
    def record(path):
     calls.append(path);return original(path)
    with patch.object(self.ha,'get',side_effect=record):
     service=PresenceService(self.ha,[config]);service.history(iso(self.now),iso(self.now+4))
    params=parse_qs(urlsplit(next(p for p in calls if p.startswith('history/period/'))).query,keep_blank_values=True)
    self.assertEqual(params['significant_changes_only'],['0'])
    self.assertNotIn('no_attributes',params);self.assertNotIn('minimal_response',params)
if __name__=='__main__':unittest.main()
