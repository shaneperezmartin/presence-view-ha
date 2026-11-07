import unittest
from server import number, sensor_snapshot, reconstruct, PresenceService, stamp
C={'id':'a','name':'Sensor A','model':'test','unit':'cm','presence':'binary_sensor.a','count':'sensor.count','bounds':{'xmin':-600,'xmax':600,'ymin':0,'ymax':600},'targets':[{'id':'1','x':'sensor.x','y':'sensor.y','zero_pair_absent':True}]}
def state(v,t='2026-11-07T12:00:00Z'):return {'state':str(v),'last_updated':t}
def states(x=20,y=100,count=1,pres='on'):return {'binary_sensor.a':state(pres),'sensor.count':state(count),'sensor.x':state(x),'sensor.y':state(y)}
class Tests(unittest.TestCase):
 def test_nan(self):
  for v in ('nan','inf',None,'unavailable'): self.assertIsNone(number(v))
 def test_real(self):self.assertEqual(sensor_snapshot(C,states())['targets'][0]['x'],20)
 def test_axis_zero_valid(self):self.assertEqual(len(sensor_snapshot(C,states(x=0))['targets']),1)
 def test_zero_pair_absent(self):self.assertEqual(sensor_snapshot(C,states(x=0,y=0))['targets'],[])
 def test_mismatched_count_hides_uncertain_slots(self):
  r=sensor_snapshot(C,states(count=2));self.assertEqual(r['targets'],[]);self.assertFalse(r['position_count_consistent'])
 def test_presence_off(self):self.assertEqual(sensor_snapshot(C,states(pres='off'))['targets'],[])
 def test_count_zero(self):self.assertEqual(sensor_snapshot(C,states(count=0))['targets'],[])
 def test_count_unavailable(self):self.assertEqual(sensor_snapshot(C,states(count='unavailable'))['targets'],[])
 def test_presence_unavailable(self):self.assertEqual(sensor_snapshot(C,states(pres='unavailable'))['targets'],[])
 def test_missing_axis(self):
  data=states();del data['sensor.x'];self.assertEqual(sensor_snapshot(C,data)['targets'],[])
 def test_z_only_valid_origin(self):
  c=dict(C,targets=[dict(C['targets'][0],height='sensor.z',zero_pair_absent=False,zero_triplet_absent=True)]);data=states(x=0,y=0);data['sensor.z']=state(50);self.assertEqual(len(sensor_snapshot(c,data)['targets']),1);data['sensor.z']=state(0);self.assertEqual(sensor_snapshot(c,data)['targets'],[])
 def test_no_height_invented(self):self.assertNotIn('height',sensor_snapshot(C,states())['targets'][0])
 def test_actual_height(self):
  c=dict(C,targets=[dict(C['targets'][0],height='sensor.height')]);data=states();data['sensor.height']=state(170)
  self.assertEqual(sensor_snapshot(c,data)['targets'][0]['height'],170)
 def test_occupancy_only(self):
  c=dict(C,targets=[]);r=sensor_snapshot(c,states());self.assertFalse(r['coordinate_support']);self.assertTrue(r['presence']);self.assertEqual(r['targets'],[])
 def test_unavailable(self):self.assertEqual(sensor_snapshot(C,{})['status'],'unavailable')
 def test_history(self):
  hist={k:[v] for k,v in states().items()};r=reconstruct([C],hist,stamp('2026-11-07T12:00:00Z'),stamp('2026-11-07T12:00:06Z'),'America/New_York')
  self.assertEqual(len(r['sensors'][0]['points']),3);self.assertEqual(sum(p['weight_seconds'] for p in r['sensors'][0]['points']),6)
 def test_empty_history(self):
  r=reconstruct([C],{},0,4,'UTC');self.assertEqual(r['sensors'][0]['points'],[])
 def test_history_known_empty(self):
  hist={k:[v] for k,v in states(count=0,pres='off').items()}
  start=stamp('2026-11-07T12:00:00Z');r=reconstruct([C],hist,start,start+4,'UTC')
  self.assertEqual(r['sensors'][0]['history_status'],'no_targets')
 def test_history_uncertainty_not_vacancy(self):
  for changes in ({'pres':'unavailable'},{'count':'unknown'},{'count':0,'pres':'unknown'},{'count':0,'pres':'on'},{'count':1,'pres':'off'},{'x':0,'y':0}):
   with self.subTest(changes=changes):
    hist={k:[v] for k,v in states(**changes).items()}
    start=stamp('2026-11-07T12:00:00Z');r=reconstruct([C],hist,start,start+4,'UTC')
    self.assertEqual(r['sensors'][0]['points'],[])
    self.assertEqual(r['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_history_missing_presence_not_vacancy(self):
  hist={k:[v] for k,v in states(count=0,pres='off').items() if k!='binary_sensor.a'}
  start=stamp('2026-11-07T12:00:00Z');r=reconstruct([C],hist,start,start+4,'UTC')
  self.assertEqual(r['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_history_partial_unknown_not_vacancy(self):
  hist={k:[v] for k,v in states(count=0,pres='off').items()}
  hist['binary_sensor.a']=[state('unavailable'),state('off','2026-11-07T12:00:02Z')]
  start=stamp('2026-11-07T12:00:00Z');r=reconstruct([C],hist,start,start+4,'UTC')
  self.assertEqual(r['sensors'][0]['history_status'],'no_valid_coordinates')
 def test_missing_axis_history(self):
  hist={k:[v] for k,v in states().items() if k!='sensor.x'};r=reconstruct([C],hist,stamp('2026-11-07T12:00:00Z'),stamp('2026-11-07T12:00:04Z'),'UTC');self.assertEqual(r['sensors'][0]['points'],[])
 def test_subsample_disappearance_breaks(self):
  hist={k:[v] for k,v in states().items()};hist['sensor.count'] += [state(0,'2026-11-07T12:00:00.5Z'),state(1,'2026-11-07T12:00:01Z')]
  r=reconstruct([C],hist,stamp('2026-11-07T12:00:00Z'),stamp('2026-11-07T12:00:04Z'),'UTC');a,b=r['sensors'][0]['points'];self.assertNotEqual(a['segment'],b['segment'])
 def test_days(self):
  class Fake:
   def get(self,p):return {'time_zone':'America/New_York'}
  service=PresenceService(Fake(),[C]);self.assertEqual(len(service.hours('2026-11-01')['hours']),25);self.assertEqual(len(service.hours('2026-03-08')['hours']),23)
  hours=service.hours('2026-11-01')['hours'];self.assertNotEqual(hours[1]['label'],hours[2]['label'])
 def test_range_validation(self):
  class Fake:
   def get(self,p):return {'time_zone':'UTC'}
  service=PresenceService(Fake(),[C])
  with self.assertRaises(ValueError):service.history('2026-01-01T00:00:00Z','2026-01-03T00:00:00Z')
if __name__=='__main__':unittest.main()
