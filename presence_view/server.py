"""Read-only Home Assistant presence adapter. No credentials are persisted; observations stay in app-private storage."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json, math, os, threading, time, shutil, re
from history_store import HistoryStore
from observation_tracks import observation_tracks

ROOT=Path(__file__).parent
UTC=timezone.utc
UNKNOWN={'unknown','unavailable','none','',None}
MAX_BYTES=32*1024*1024

def stamp(value):
    if isinstance(value,(float,int)): return float(value)
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()

def iso(value): return datetime.fromtimestamp(value,UTC).isoformat().replace('+00:00','Z')

def number(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError): return None

def source_value(source, states):
    if not source: return None
    if isinstance(source,str): source={'entity':source}
    state=states.get(source['entity'],{})
    value=state.get('state')
    if 'attribute' in source:
        value=state.get('attributes',{})
        for key in source['attribute'].split('.'):
            if isinstance(value,dict): value=value.get(key)
            else: return None
    return value

def sensor_entities(sensor):
    ids=set()
    for src in [sensor.get('presence'),sensor.get('count')]+[src for target in sensor.get('targets',[]) for src in (target.get('x'),target.get('y'),target.get('height'))]:
        if src: ids.add(src if isinstance(src,str) else src['entity'])
    return ids

def sensor_snapshot(config,states):
    result={k:config.get(k) for k in ('id','name','model','bounds','unit','notes','height_label','height_unit')}
    result['coordinate_support']=bool(config.get('targets'))
    result['height_support']=any(t.get('height') for t in config.get('targets',[]))
    raw_presence=source_value(config.get('presence'),states)
    result['presence']=True if raw_presence in ('on','detected',True) else False if raw_presence in ('off','clear',False) else None
    result['count']=number(source_value(config.get('count'),states))
    positions=[]
    valid_presence=not config.get('presence') or result['presence'] is not None
    valid_count=not config.get('count') or result['count'] is not None
    for target in config.get('targets',[]):
        x=number(source_value(target['x'],states)); y=number(source_value(target['y'],states))
        if x is None or y is None: continue
        height=number(source_value(target.get('height'),states))
        if target.get('zero_triplet_absent',False) and x==0 and y==0 and (height is None or height==0):continue
        if target.get('zero_pair_absent',False) and x==0 and y==0: continue
        if not valid_presence or not valid_count or result['presence'] is False or result['count']==0: continue
        point={'id':str(target['id']),'x':x,'y':y}
        height=number(source_value(target.get('height'),states))
        if height is not None: point['height']=height
        positions.append(point)
    result['position_count_consistent']=result['count'] is None or len(positions)==result['count']
    if config.get('targets') and not result['position_count_consistent']:
        result['notes']=list(result.get('notes') or [])+['Target count and coordinate slots do not yet agree. Positions are hidden until a consistent sample is available.']
        positions=[]
    result['targets']=positions
    relevant=[states.get(e,{}) for e in sensor_entities(config)]
    available=any(v.get('state') not in UNKNOWN for v in relevant)
    result['status']='online' if available else 'unavailable'
    dates=[v.get('last_updated') for v in relevant if v.get('last_updated')]
    result['updated_at']=max(dates) if dates else None
    if result['coordinate_support'] and result['count'] and not positions:
        result['notes']=list(result.get('notes') or [])+['Presence is reported, but valid target coordinates are not available.']
    return result

class HA:
    def __init__(self):
        self.token=os.environ.get('SUPERVISOR_TOKEN')
        self.base='http://supervisor/core/api/'
        if not self.token: raise RuntimeError('Supervisor-managed Home Assistant API access is required. No user token entry is supported.')
    def get(self,path):
        # Only fixed read-only routes. User input cannot select an arbitrary service, URL or method.
        if not (path in ('states','config') or path.startswith('history/period/')): raise ValueError('Unsupported read route')
        request=Request(self.base+path,headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'},method='GET')
        try:
            with urlopen(request,timeout=35) as response:
                body=response.read(MAX_BYTES+1)
                if len(body)>MAX_BYTES: raise RuntimeError('History response is too large. Choose a shorter time window.')
                return json.loads(body)
        except HTTPError as e: raise RuntimeError('Home Assistant returned HTTP '+str(e.code)) from None
        except RuntimeError: raise
        except Exception: raise RuntimeError('Home Assistant read request failed. Check that Core is running and app API access is enabled.') from None

class PresenceService:
    def __init__(self,ha,configs,history_store=None):
        self.ha=ha; self.configs=configs; self.store=history_store
        self.stop_event=threading.Event(); self.collector=None; self.storage_error=None; self.last_observation=None; self.last_cleanup=0; self._coverage=None
        self.ids=set().union(*(sensor_entities(s) for s in configs))
        self.lock=threading.Lock(); self.cache=None; self.last_poll=0; self.zone='UTC'
        self.history_lock=threading.Lock(); self.history_cache={}
        try: self.zone=ha.get('config').get('time_zone') or self.zone
        except RuntimeError: pass
    def status(self):
        with self.lock:
            if self.cache and time.monotonic()-self.last_poll<1: return self.cache
            states={s['entity_id']:s for s in self.ha.get('states') if s['entity_id'] in self.ids}
            now=time.time()
            result={'now':iso(now),'time_zone':self.zone,'sensors':[sensor_snapshot(c,states) for c in self.configs],
                    'history_support':True,'recording':{'enabled':self.store is not None,'last_observation':self.last_observation,'error':self.storage_error,'coverage':self._coverage},'history_note':'History combines Home Assistant recorder data before local coverage with app-local observations. Target slots are not stable person identities; no measurements are invented.'}
            self.cache=result; self.last_poll=time.monotonic()
            return result
    def start_collection(self):
        if self.store is None or self.collector is not None:return
        self.collector=threading.Thread(target=self._collect_loop,name='presence-history',daemon=True)
        self.collector.start()
    def collect_once(self):
        try:
            status=self.status(); observed=stamp(status['now']); sensors=status['sensors']
        except RuntimeError:
            observed=time.time()
            sensors=[{'id':c['id'],'status':'unavailable','presence':None,'count':None,'coordinate_support':bool(c.get('targets')),'targets':[]} for c in self.configs]
        try:
            if shutil.disk_usage(self.store.path.parent).free<1024**3:
                raise RuntimeError('Local recording paused: less than 1 GiB free storage.')
            for sensor in sensors:self.store.append_snapshot(observed,sensor)
            self.last_observation=iso(observed);self.storage_error=None
            if time.monotonic()-self.last_cleanup>60:
                self._coverage=self.store.retention_cleanup();self.last_cleanup=time.monotonic()
        except Exception:
            self.storage_error='Local history recording is paused or incomplete. Check app storage; missing observations remain gaps.'
    def _collect_loop(self):
        while not self.stop_event.is_set():
            self.collect_once();self.stop_event.wait(2)
    def stop_collection(self):
        self.stop_event.set()
        if self.collector:self.collector.join(timeout=40)
    def hours(self,date):
        day=datetime.strptime(date,'%Y-%m-%d').date(); zone=ZoneInfo(self.zone)
        a=datetime.combine(day,datetime.min.time(),zone).timestamp()
        b=datetime.combine(day+timedelta(days=1),datetime.min.time(),zone).timestamp()
        rows=[]; cursor=a
        while cursor<b:
            local=datetime.fromtimestamp(cursor,zone)
            rows.append({'label':local.strftime('%H:%M %Z (UTC%z)'),'start':iso(cursor),'end':iso(min(cursor+3600,b))})
            cursor+=3600
        return {'date':date,'time_zone':self.zone,'hours':rows}
    def history(self,start,end):
        start=stamp(start); end=stamp(end); now=time.time()
        if end<=start or end-start>24*3600+1: raise ValueError('Choose a positive time range no longer than 24 hours.')
        if start>now: raise ValueError('The selected time range is in the future.')
        end=min(end,now)
        key=(start,end)
        if not self.history_lock.acquire(False): raise RuntimeError('A history request is already running. Please wait and try again.')
        try:
            if key in self.history_cache and time.monotonic()-self.history_cache[key][0]<60: return self.history_cache[key][1]
            histories=defaultdict(list); cursor=start
            coverage=self.store.coverage()['sensors'] if self.store else {}
            local_queries={c['id']:self.store.query(c['id'],start,end) for c in self.configs} if self.store else {}
            ha_end=max(min(end,coverage.get(c['id'],{}).get('earliest',end)) for c in self.configs)
            if 0<ha_end-start<0.000001:ha_end=start
            while cursor<ha_end:
                until=min(cursor+3600,ha_end)
                query_params={'filter_entity_id':','.join(sorted(self.ids)),'end_time':iso(until),'significant_changes_only':'0'}
                sources=[src for c in self.configs for src in [c.get('presence'),c.get('count')]+[target.get(k) for target in c.get('targets',[]) for k in ('x','y','height')]]
                if not any(isinstance(src,dict) and src.get('attribute') for src in sources):
                    query_params.update(minimal_response='',no_attributes='')
                query=urlencode(query_params)
                # Core defaults significant_changes_only to true; explicitly retain attribute-only changes too.
                response=self.ha.get('history/period/'+quote(iso(cursor),safe='')+'?'+query)
                for sequence in response:
                    if not sequence: continue
                    eid=sequence[0].get('entity_id')
                    if eid not in self.ids: continue
                    for row in sequence:
                        row=dict(row); row['entity_id']=eid
                        histories[eid].append(row)
                cursor=until
            result=reconstruct(self.configs,histories,start,ha_end,self.zone) if ha_end>start else {'start':iso(start),'end':iso(end),'time_zone':self.zone,'sensors':[{'id':c['id'],'points':[],'coordinate_support':bool(c.get('targets')),'history_status':'no_coordinate_history'} for c in self.configs],'warnings':[]}
            result['end']=iso(end)
            for c, sensor in zip(self.configs,result['sensors']):
                boundary=coverage.get(c['id'],{}).get('earliest',end)
                sensor['points']=[dict(p,weight_seconds=min(p['weight_seconds'],boundary-stamp(p['t']),end-stamp(p['t']))) for p in sensor['points'] if stamp(p['t'])<boundary]
                query=local_queries.get(c['id'],{'samples':[]})
                local_points=observation_tracks(query['samples'],start,end,c.get('unit'))
                for p in local_points:p['segment']='local-'+str(p['segment'])
                sensor['points'].extend(local_points)
                sensor['sources']=['Home Assistant recorder before local coverage','App-local observations'] if self.store else ['Home Assistant recorder']
                if sensor['points']:sensor['history_status']='available'
                elif query['samples'] and all(known_empty(r) for r in query['samples']) and (boundary<=start or sensor['history_status']=='no_targets'):sensor['history_status']='no_targets'
                elif query['samples']:sensor['history_status']='no_valid_coordinates'
                if query.get('truncated'):result['warnings'].append(c['name']+': local samples were truncated; select a shorter range.')
            result['warnings'].append('App-local heatmaps count only consecutive observed target samples, with gaps over 6 seconds excluded. Recording retention is bounded; coverage does not imply continuous data.')
            self.history_cache={key:(time.monotonic(),result)}
            return result
        finally: self.history_lock.release()

def known_empty(snapshot):
    """Only mutually consistent, known presence and count can establish vacancy."""
    return snapshot.get('status')=='online' and snapshot.get('presence') is False and snapshot.get('count')==0

def reconstruct(configs,histories,start,end,zone):
    """Join asynchronous coordinate axes as-of each timestamp; record 2-second samples.

    Never bridge an explicitly empty, unavailable, or missing state. Sampling is
    disclosed: this is observed sensor history, not precise human trajectories.
    """
    result={'start':iso(start),'end':iso(end),'time_zone':zone,'sensors':[],
            'warnings':['Tracks use sensor target slots, not identified people. Positions are sampled at 2-second intervals; gaps and recorder limits may omit movement. Heatmaps show sampled presence, not a calibrated probability map.']}
    for config in configs:
        ids=sensor_entities(config); events=[]
        for eid in ids:
            for row in histories.get(eid,[]):
                t=row.get('last_updated') or row.get('last_changed')
                if t:
                    try: events.append((stamp(t),eid,row))
                    except (ValueError,TypeError): pass
        events.sort(key=lambda x:x[0]); current={}; index=0; points=[]; segment=defaultdict(int); was_active=set()
        support=bool(config.get('targets')); sampled=False; all_empty=True
        if support:
            t=start
            while t<end:
                while index<len(events) and events[index][0]<=t:
                    _,eid,row=events[index]; current[eid]=row; index+=1
                    # Preserve even sub-sample disappearances as separate track segments.
                    event_active={p['id'] for p in sensor_snapshot(config,current)['targets']}
                    was_active.intersection_update(event_active)
                snap=sensor_snapshot(config,current); active=set()
                sampled=True; all_empty=all_empty and known_empty(snap)
                for p in snap['targets']:
                    pid=p['id']; active.add(pid)
                    if pid not in was_active: segment[pid]+=1
                    points.append(dict(p,t=iso(t),segment=segment[pid],weight_seconds=min(2,end-t)))
                was_active=active
                t+=2
        coordinate_ids={src if isinstance(src,str) else src['entity'] for target in config.get('targets',[]) for src in (target['x'],target['y'])}
        coordinate_history=bool(coordinate_ids) and all(histories.get(e) for e in coordinate_ids)
        result['sensors'].append({'id':config['id'],'points':points,'coordinate_support':support,
            'history_status':'available' if points else 'no_targets' if coordinate_history and sampled and all_empty else 'no_valid_coordinates' if coordinate_history else 'no_coordinate_history' if support else 'unsupported',
            'entity_history_count':sum(bool(histories.get(e)) for e in ids),'sample_interval_seconds':2})
    return result

class Handler(BaseHTTPRequestHandler):
    server_version='PresenceView/0.2.2'
    def log_message(self,*args): pass  # Do not retain private presence/query details.
    def do_GET(self):
        # HA ingress is the sole ingress route. No host port is published.
        if not getattr(self.server,'local_test',False) and self.client_address[0]!='172.30.32.2':
            return self.reply(403,{'error':'Use Home Assistant authenticated ingress.'})
        parsed=urlsplit(self.path); path=parsed.path.rstrip('/')
        try:
            if path in ('','/index.html'):
                data=(ROOT/'index.html').read_bytes(); return self.reply(200,data,'text/html; charset=utf-8')
            if path=='/api/status': return self.reply(200,self.server.service.status())
            query=parse_qs(parsed.query)
            if path=='/api/hours': return self.reply(200,self.server.service.hours(query['date'][0]))
            if path=='/api/history': return self.reply(200,self.server.service.history(query['start'][0],query['end'][0]))
            return self.reply(404,{'error':'Not found'})
        except (ValueError,KeyError): return self.reply(400,{'error':'Invalid date or time range. Use a range of up to 24 hours.'})
        except RuntimeError as e: return self.reply(503,{'error':str(e)})
        except Exception: return self.reply(500,{'error':'Unable to read sensor data. No settings have been changed.'})
    def do_POST(self): self.reply(405,{'error':'This app is read-only.'})
    def reply(self,status,value,kind='application/json'):
        payload=value if isinstance(value,bytes) else json.dumps(value,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; object-src 'none'; form-action 'none'")
        self.end_headers()
        try:self.wfile.write(payload)
        except (ConnectionError,OSError):pass

def load_configs():
    options_path=Path('/data/options.json')
    options=json.loads(options_path.read_text()) if options_path.exists() else {}
    configs=options.get('sensors')
    if configs is None and (ROOT/'sensors.json').exists():configs=json.loads((ROOT/'sensors.json').read_text())
    if not isinstance(configs,list) or not 1<=len(configs)<=10:raise RuntimeError('Configure 1 to 10 sensors in the app options before starting.')
    ids=set()
    for c in configs:
        if not isinstance(c,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',c.get('id','')) or c['id'] in ids:raise ValueError('Invalid or duplicate sensor ID')
        ids.add(c['id'])
        if any(not isinstance(c.get(k),str) or not 1<=len(c[k])<=160 for k in ('name','model')):raise ValueError('Invalid display name')
        targets=c.get('targets',[])
        if not isinstance(targets,list) or len(targets)>16:raise ValueError('Invalid target mapping')
        target_ids=set()
        for target in targets:
            if not isinstance(target,dict) or not isinstance(target.get('id'),str) or not target['id'] or target['id'] in target_ids or not target.get('x') or not target.get('y'):raise ValueError('Invalid or duplicate target mapping')
            target_ids.add(target['id'])
        for eid in sensor_entities(c):
            if not re.fullmatch(r'(?:sensor|binary_sensor)\.[a-z0-9_]+',eid):raise ValueError('Only sensor entity reads are supported')
        if targets:
            b=c.get('bounds',{})
            if any(number(b.get(k)) is None for k in ('xmin','xmax','ymin','ymax')) or b['xmin']>=b['xmax'] or b['ymin']>=b['ymax']:raise ValueError('Invalid plot bounds')
    return configs,options

if __name__=='__main__':
    configs,options=load_configs()
    store=HistoryStore([c['id'] for c in configs],retention_days=options.get('retention_days',30),max_bytes=options.get('history_budget_mb',512)*1024**2)
    server=ThreadingHTTPServer(('0.0.0.0',8099),Handler)
    server.service=PresenceService(HA(),configs,store)
    server.service.start_collection()
    print('Presence View ready on authenticated Home Assistant ingress.',flush=True)
    try:server.serve_forever()
    finally:
        server.service.stop_collection();store.close();server.server_close()
