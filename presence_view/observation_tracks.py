"""Convert locally observed snapshots into conservative sampled tracks."""
from collections import defaultdict
from datetime import datetime, timezone
def iso(t): return datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')

def observation_tracks(rows, start, end, unit, max_gap=6):
    points=[]; segments=defaultdict(int); previous_ids=set(); last_time=None
    for i,row in enumerate(rows):
        t=row['t']
        if t>=end:break
        if t<start-max_gap:continue
        valid=(row.get('status')=='online' and row.get('presence') is not None and row.get('count') is not None
               and row.get('coordinate_support') and row.get('position_count_consistent',True))
        targets=row.get('targets',[]) if valid else []
        if row.get('presence') is False or row.get('count')==0:targets=[]
        ids={p['id'] for p in targets}
        if last_time is None or t-last_time>max_gap:previous_ids=set()
        following=rows[i+1] if i+1<len(rows) else None
        next_ids={p['id'] for p in following.get('targets',[])} if following and following.get('status')=='online' else set()
        dt=following['t']-t if following else 0
        ratio=1
        source_unit=row.get('unit')
        if source_unit and unit and source_unit!=unit:
            ratio={('m','cm'):100,('cm','m'):.01,('mm','cm'):.1,('cm','mm'):10}.get((source_unit,unit))
            if ratio is None:targets=[];ids=set()
        for target in targets:
            pid=target['id']
            if pid not in previous_ids:segments[pid]+=1
            valid_until=min(end,following['t']) if following and 0<dt<=max_gap and pid in next_ids else t
            weight=max(0,valid_until-max(t,start))
            # An isolated observation is a visible point, never assumed dwell time.
            if t>=start or weight:
                point=dict(target,t=iso(max(t,start)),segment=segments[pid],weight_seconds=weight,source='local')
                point['x']*=ratio;point['y']*=ratio
                points.append(point)
        previous_ids=ids;last_time=t
    return points
