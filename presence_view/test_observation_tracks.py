import unittest
from observation_tracks import observation_tracks
class ObservationTests(unittest.TestCase):
 def row(self,t,**kw):
  r={'t':t,'status':'online','presence':True,'count':1,'coordinate_support':True,'unit':'cm','targets':[{'id':'1','x':10,'y':20}]};r.update(kw);return r
 def test_stationary_weights(self):
  p=observation_tracks([self.row(0),self.row(2),self.row(4)],0,6,'cm');self.assertEqual([x['weight_seconds'] for x in p],[2,2,0])
 def test_gap_break(self):
  p=observation_tracks([self.row(0),self.row(20)],0,25,'cm');self.assertEqual([x['weight_seconds'] for x in p],[0,0]);self.assertNotEqual(p[0]['segment'],p[1]['segment'])
 def test_empty_break(self):
  p=observation_tracks([self.row(0),self.row(2,count=0,presence=False,targets=[]),self.row(4)],0,6,'cm');self.assertNotEqual(p[0]['segment'],p[1]['segment'])
 def test_no_coords(self):self.assertEqual(observation_tracks([self.row(0,coordinate_support=False)],0,2,'cm'),[])
 def test_clip_boundary(self):
  p=observation_tracks([self.row(0),self.row(2)],1,1.5,'cm');self.assertEqual(p[0]['weight_seconds'],.5)
 def test_units(self):
  p=observation_tracks([self.row(0,unit='m')],0,2,'cm');self.assertEqual(p[0]['x'],1000)
if __name__=='__main__':unittest.main()
