"""App-private sampled observations. No identity inference or gap interpolation.

The configured byte budget limits SQLite pages (including indexes); DELETE journal
and temporary maintenance overhead mean it is not a byte-perfect filesystem cap.
Only the fixed database in the trusted application data directory is accessed.
"""
import json
import math
from pathlib import Path
import sqlite3
import threading
import time


class HistoryStore:
    def __init__(self, sensor_ids, data_dir='/data', retention_days=30,
                 max_bytes=512 * 1024 * 1024):
        self.ids = frozenset(sensor_ids)
        if not self.ids or any(not isinstance(s, str) or not 1 <= len(s) <= 128 for s in self.ids):
            raise ValueError('Invalid configured sensor IDs')
        if not 0 < retention_days <= 3650 or max_bytes < 65536:
            raise ValueError('Invalid storage limits')
        self.retention = retention_days * 86400
        self.lock = threading.RLock()
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'presence-history.sqlite3'
        if self.path.is_symlink():
            raise ValueError('Database must not be a symbolic link')
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS samples(sensor TEXT, t REAL, payload TEXT NOT NULL, PRIMARY KEY(sensor,t))')
        self.db.execute('CREATE INDEX IF NOT EXISTS samples_time ON samples(t)')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value REAL)')
        self.db.commit()
        page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
        self.page_limit = max_bytes // page_size
        # SQLite will not reduce the limit below an existing file's page count.
        self.db.execute('PRAGMA max_page_count=%d' % self.page_limit)
        self.retention_cleanup()

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError('Invalid finite measurement')
        return value

    def _id(self, sensor):
        if sensor not in self.ids:
            raise ValueError('Unknown sensor')

    def append_snapshot(self, timestamp, sensor):
        t = round(self._number(timestamp), 6)
        sid = sensor.get('id')
        self._id(sid)
        status = sensor.get('status', 'unavailable')
        if status not in ('online', 'offline', 'unavailable', 'unknown'):
            raise ValueError('Invalid availability')
        presence = sensor.get('presence')
        if presence is not None and type(presence) is not bool:
            raise ValueError('Invalid presence')
        count = sensor.get('count')
        if count is not None and (self._number(count) < 0 or count > 1000):
            raise ValueError('Invalid count')
        targets = sensor.get('targets', [])
        if not isinstance(targets, list) or len(targets) > 64:
            raise ValueError('Too many target slots')
        points = []
        seen = set()
        for target in targets:
            pid = target.get('id')
            if not isinstance(pid, str) or not 1 <= len(pid) <= 128:
                raise ValueError('Invalid anonymous slot')
            if pid in seen:
                raise ValueError('Duplicate anonymous slot')
            seen.add(pid)
            point = {'id': pid, 'x': self._number(target['x']), 'y': self._number(target['y'])}
            if target.get('height') is not None:
                point['height'] = self._number(target['height'])
            points.append(point)
        consistent = sensor.get('position_count_consistent', count is not None and len(points) == count)
        if consistent is not None and type(consistent) is not bool:
            raise ValueError('Invalid consistency flag')
        if status != 'online' or presence is not True or count is None or count == 0 or consistent is not True or not sensor.get('coordinate_support', False):
            points = []
        if count is not None and len(points) != count:
            points = []
        payload = dict(id=sid, status=status, presence=presence, count=count, targets=points,
                       position_count_consistent=consistent,
                       coordinate_support=bool(sensor.get('coordinate_support', False)))
        for key in ('unit', 'height_unit'):
            value = sensor.get(key)
            if value is not None:
                if not isinstance(value, str) or len(value) > 32:
                    raise ValueError('Invalid unit')
                payload[key] = value
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        with self.lock:
            for attempt in range(2):
                try:
                    with self.db:
                        self.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?)', (sid, t, encoded))
                    return
                except sqlite3.OperationalError as exc:
                    if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL or attempt:
                        raise
                    self._prune_oldest(1000, 'size_pruned_rows')

    def _prune_oldest(self, limit, key):
        with self.db:
            cur = self.db.execute('DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples ORDER BY t LIMIT ?)', (limit,))
            self.db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value', (key, cur.rowcount))
        return cur.rowcount

    def retention_cleanup(self, now=None):
        now = time.time() if now is None else self._number(now)
        with self.lock:
            with self.db:
                cur = self.db.execute('DELETE FROM samples WHERE t < ?', (now-self.retention,))
                self.db.execute("INSERT INTO metadata VALUES('age_pruned_rows',?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value", (cur.rowcount,))
            # Reclaim gradually, never run a full VACUUM on the polling path.
            self.db.execute('PRAGMA incremental_vacuum(256)')
            self.db.execute('PRAGMA max_page_count=%d' % self.page_limit)
            return self.coverage()

    def query(self, sensor, start, end, limit=50000):
        self._id(sensor)
        start, end = round(self._number(start), 6), round(self._number(end), 6)
        if end <= start or end-start > 86401 or not 1 <= limit <= 50000:
            raise ValueError('Invalid query range or limit')
        with self.lock:
            rows = self.db.execute('SELECT t,payload FROM samples WHERE sensor=? AND t>=? AND t<? ORDER BY t LIMIT ?', (sensor, start, end, limit+1)).fetchall()
        return {'samples': [dict(json.loads(p), t=t) for t,p in rows[:limit]],
                'truncated': len(rows)>limit, 'interval': '[start,end)',
                'note': 'Raw observations only. No sample before start is returned: boundary time before the first sample is unknown and must not be backfilled. Missing intervals are unknown; slots are not persistent people.'}

    def coverage(self):
        with self.lock:
            rows = self.db.execute('SELECT sensor,MIN(t),MAX(t),COUNT(*) FROM samples GROUP BY sensor').fetchall()
            return {'sensors': {s: {'earliest': a, 'latest': b, 'samples': n} for s,a,b,n in rows},
                    'pruning': dict(self.db.execute('SELECT key,value FROM metadata')),
                    'retention_days': self.retention/86400,
                    'database_bytes': self.path.stat().st_size,
                    'page_budget_bytes': self.page_limit*self.db.execute('PRAGMA page_size').fetchone()[0],
                    'note': 'Coverage bounds do not imply continuous observations. Journal overhead is outside the page budget.'}

    def close(self):
        with self.lock:
            self.db.close()
