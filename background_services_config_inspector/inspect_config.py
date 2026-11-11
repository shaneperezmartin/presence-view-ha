"""One-shot read-only source-config comparison. Never print source values/hashes."""
from __future__ import annotations
import contextlib
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = '/local_apps'
TEMPLATE = '/app/public-baseline.blob'
OPTIONS = '/data/options.json'
PUBLIC_BASELINE_SHA256 = '956278add6534da3ad13116654e961e6df1def168f02ea215ac4196569a95f99'
SLUG = 'dji_audio_archive'
VERSION = '0.2.6'
MAX_CONFIG_BYTES = 65536
MAX_ENTRIES = 256
MAX_NODES = 1024
MAX_DEPTH = 8
MAX_REPORT_BYTES = 8192
MAX_SECONDS = 5.0
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
DIRFLAGS = FLAGS | os.O_DIRECTORY
# Discovery may read restrictive ordinary configs, never execute/special/writable-by-others modes.
DISCOVERY_MODES = frozenset((0o400, 0o440, 0o444, 0o600, 0o640, 0o644))


class Refused(Exception):
    """Only fixed, content-free messages may be supplied."""


def require(value, reason):
    if not value:
        raise Refused(reason)


def signature(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size,
            st.st_uid, st.st_gid, st.st_mtime_ns, st.st_ctime_ns)


@contextlib.contextmanager
def directory(path, parent=None):
    if parent is not None:
        require(isinstance(path,str) and NAME.fullmatch(path), 'Unsafe target directory')
        fd=os.open(path,DIRFLAGS,dir_fd=parent)
    else:
        parts=Path(path).parts
        require('..' not in parts, 'Parent traversal refused')
        fd=os.open('/' if os.path.isabs(path) else '.',DIRFLAGS)
        try:
            for part in parts:
                if part in ('/','.'):continue
                child=os.open(part,DIRFLAGS,dir_fd=fd);os.close(fd);fd=child
        except BaseException:
            os.close(fd);raise
    try:yield fd
    finally:os.close(fd)


def read_config(parent, name='config.json', *, discovery=False):
    try:fd=os.open(name,FLAGS,dir_fd=parent)
    except FileNotFoundError:return None
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1,'Config must be a regular single-link file')
        mode=stat.S_IMODE(before.st_mode)
        if discovery:
            require(mode in DISCOVERY_MODES,'Discovery config mode is not safe for bounded read')
        else:
            require(mode==0o644,'Config mode differs from reviewed mode')
        require(0<=before.st_size<=MAX_CONFIG_BYTES,'Config exceeds bounded size')
        result=bytearray()
        while True:
            block=os.read(fd,min(4096,MAX_CONFIG_BYTES+1-len(result)))
            if not block:break
            result.extend(block)
            require(len(result)<=MAX_CONFIG_BYTES,'Config exceeds bounded size')
        after=os.fstat(fd)
        require(signature(before)==signature(after)==signature(os.stat(name,dir_fd=parent,follow_symlinks=False)),
                'Config changed during read')
        return bytes(result),signature(after)
    finally:os.close(fd)


def reject_constant(_):
    raise Refused('Nonstandard JSON constant refused')


def unique_object(pairs):
    result={}
    for key,value in pairs:
        require(key not in result,'Duplicate JSON key refused')
        result[key]=value
    return result


def parse_config(data):
    try:
        value=json.loads(data,object_pairs_hook=unique_object,parse_constant=reject_constant)
    except (ValueError,UnicodeError,RecursionError):
        raise Refused('Config is not bounded strict JSON') from None
    count=0
    def walk(node,depth):
        nonlocal count
        count+=1
        require(depth<=MAX_DEPTH and count<=MAX_NODES,'JSON structural limit exceeded')
        if isinstance(node,float):
            require(math.isfinite(node),'Non-finite JSON number refused')
        if isinstance(node,dict):
            for child in node.values():walk(child,depth+1)
        elif isinstance(node,list):
            for child in node:walk(child,depth+1)
    walk(value,0)
    require(type(value) is dict,'Config root must be an object')
    return value


def same(left,right):
    if type(left) is not type(right):return False
    if isinstance(left,dict):return left.keys()==right.keys() and all(same(left[k],right[k]) for k in left)
    if isinstance(left,list):return len(left)==len(right) and all(same(a,b) for a,b in zip(left,right))
    return left==right


def kind(value):
    return {dict:'object',list:'array',str:'string',bool:'boolean',int:'number',float:'number',type(None):'null'}.get(type(value),'unsupported')


def budget(deadline):
    require(time.monotonic()<=deadline,'Diagnostic time budget exceeded')


def entries(fd):
    with os.scandir(fd) as scan:result=[x.name for x in itertools.islice(scan,MAX_ENTRIES+1)]
    require(len(result)<=MAX_ENTRIES,'Directory entry limit exceeded')
    return sorted(result)


def check_config_layout(fd):
    names=entries(fd)
    require('config.yaml' not in names and 'config.yml' not in names,'YAML or multiple-config layout requires separate review')
    return names


def discover(root, deadline):
    """Bounded unique JSON-slug discovery; no configurable target override."""
    names=entries(root)
    selected=[n for n in names if not n.startswith('.')]
    matches=[]
    for child in selected:
        budget(deadline)
        require(NAME.fullmatch(child) is not None,'Unsupported local-app directory name')
        st=os.stat(child,dir_fd=root,follow_symlinks=False)
        require(not stat.S_ISLNK(st.st_mode),'Symlinked local-app entry refused')
        if not stat.S_ISDIR(st.st_mode):continue
        with directory(child,root) as fd:
            require((st.st_dev,st.st_ino)==(os.fstat(fd).st_dev,os.fstat(fd).st_ino),'Directory replaced during discovery')
            check_config_layout(fd)
            item=read_config(fd,discovery=True)
            if item is None:continue
            cfg=parse_config(item[0])
            if cfg.get('slug')==SLUG:
                require(stat.S_IMODE(item[1][2])==0o644,'Target config mode differs from reviewed mode')
                require(type(cfg.get('version')) is str and cfg['version']==VERSION,'Target source version is not reviewed baseline')
                matches.append((child,st.st_dev,st.st_ino,item[1]))
    require(len(matches)==1,'Target absent or ambiguous')
    return matches[0],names


def compare(template_bytes, observed_bytes):
    require(hashlib.sha256(template_bytes).hexdigest()==PUBLIC_BASELINE_SHA256,'Public template pin mismatch')
    baseline=parse_config(template_bytes);observed=parse_config(observed_bytes)
    require(baseline.get('slug')==SLUG and baseline.get('version')==VERSION,'Invalid public template identity')
    require(observed.get('slug')==SLUG and type(observed.get('version')) is str and observed['version']==VERSION,
            'Unexpected observed config identity or version')
    details=[]
    unknown=False
    def compare_known(expected, actual, prefix=''):
        nonlocal unknown
        if not isinstance(actual,dict):return
        unknown=unknown or bool(set(actual)-set(expected))
        for key,value in expected.items():
            path=prefix+'/'+key
            present=key in actual
            item={'path':path,'present':present,'expected_type':kind(value),
                  'observed_type':kind(actual[key]) if present else 'missing',
                  'type_matches':present and type(value) is type(actual[key]),
                  'matches_public':present and same(value,actual[key])}
            details.append(item)
            if isinstance(value,dict) and present and isinstance(actual[key],dict):
                compare_known(value,actual[key],path)
    compare_known(baseline,observed)
    base_options=baseline['options'];options=observed.get('options')
    option_keys_exact=type(options) is dict and options.keys()==base_options.keys()
    non_options_exact=observed.keys()==baseline.keys() and all(same(baseline[k],observed[k]) for k in baseline if k!='options')
    other_options_exact=option_keys_exact and all(same(v,options[k]) for k,v in base_options.items()
        if k not in ('transcript_export_enabled','transcript_export_token_sha256'))
    legacy_shape=bool(non_options_exact and other_options_exact and options.get('transcript_export_enabled') is True
                      and type(options.get('transcript_export_token_sha256')) is str
                      and re.fullmatch(r'[0-9a-f]{64}',options['transcript_export_token_sha256']))
    legacy_bytes_exact=False
    if legacy_shape:
        # Reproduce only the historical two-field transformation in memory.
        # Never save it, emit it, emit its hash, or treat it as write authorization.
        reconstructed=json.loads(template_bytes)
        reconstructed['options']['transcript_export_enabled']=True
        reconstructed['options']['transcript_export_token_sha256']=options['transcript_export_token_sha256']
        legacy_bytes_exact=(json.dumps(reconstructed,indent=2)+'\n').encode()==observed_bytes
    result={'diagnostic_only':True,'completed':True,'read_only':True,'source_values_emitted':False,
            'source_baseline_accepted_for_writes':False,'whole_source_verified':False,'public_bytes_exact':template_bytes==observed_bytes,
            'public_semantics_exact':same(baseline,observed),'non_options_exact':non_options_exact,
            'options_known_keys_exact':option_keys_exact,'other_option_defaults_exact':other_options_exact,
            'unknown_keys_present':unknown,'legacy_export_variant_shape':legacy_shape,
            'legacy_export_variant_bytes_exact':legacy_bytes_exact,'known_fields':details}
    require(len(json.dumps(result,sort_keys=True).encode())<=MAX_REPORT_BYTES,'Report exceeds fixed output bound')
    return result


def inspect_config(root=ROOT, template=TEMPLATE):
    deadline=time.monotonic()+MAX_SECONDS
    template=Path(template)
    with directory(template.parent) as fd:
        reference=read_config(fd,template.name)
        require(reference is not None,'Public template missing')
    with directory(root) as rootfd:
        root_identity=(os.fstat(rootfd).st_dev,os.fstat(rootfd).st_ino)
        found,listing=discover(rootfd,deadline)
        child,device,inode,config_signature=found
        with directory(child,rootfd) as app:
            require((os.fstat(app).st_dev,os.fstat(app).st_ino)==(device,inode),'Target directory replaced')
            source=read_config(app)
            require(source is not None and source[1]==config_signature,'Source changed after discovery')
            result=compare(reference[0],source[0])
            budget(deadline)
            # Fresh discovery and source reread must agree before any result is emitted.
            again,after_listing=discover(rootfd,deadline)
            require(again==found and after_listing==listing,'Local source discovery changed')
            after=read_config(app)
            require(after==source,'Source changed during comparison')
            with directory(root) as now:
                require((os.fstat(now).st_dev,os.fstat(now).st_ino)==root_identity,'Source mount replaced')
            budget(deadline)
            return result


def main():
    try:
        require(len(sys.argv)==1,'Runtime path overrides are not supported')
        options=json.loads(Path(OPTIONS).read_bytes())
        require(type(options) is dict and not options,'Diagnostic has no configurable options')
        result=inspect_config()
        print(json.dumps(result,sort_keys=True,ensure_ascii=True),flush=True)
        return 0
    except (Refused,OSError,ValueError,TypeError,KeyError,RecursionError):
        error=sys.exception()
        reason=str(error) if isinstance(error,Refused) else 'Filesystem or input validation failed'
        print(json.dumps({'diagnostic_only':True,'completed':False,'refused':True,'reason':reason},sort_keys=True),flush=True)
        return 1


if __name__=='__main__':sys.exit(main())
