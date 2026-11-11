"""Authenticate only a pre-recorded historical two-field config transformation.

No private pin or observed value is bundled, printed, accepted as an override,
or learned as a new baseline. Effective bytes exist only in process memory and
in the existing source transaction's protected local recovery store.
"""
import json
import math
import os
import re
import stat
import time

from updater import (FLAGS, canonical, digest, file_info, inode, read_at,
                     Refused, require, signature)

MAX_CONFIG_BYTES = 65536
MAX_DEPTH = 8
MAX_NODES = 1024
DISCOVERY_LIMIT = 256
SECONDS = 5.0
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def strict_json(data):
    require(type(data) is bytes and len(data) <= MAX_CONFIG_BYTES, 'Compatibility config exceeds size limit')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate compatibility config key')
            value[key] = item
        return value
    def nonfinite(_):
        raise Refused('Nonstandard compatibility config number')
    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('Invalid bounded compatibility config') from None
    count = 0
    def walk(value, depth):
        nonlocal count
        count += 1
        require(depth <= MAX_DEPTH and count <= MAX_NODES, 'Compatibility config structural limit exceeded')
        if type(value) is float:
            require(math.isfinite(value), 'Nonstandard compatibility config number')
        if type(value) is dict:
            for item in value.values(): walk(item, depth + 1)
        elif type(value) is list:
            for item in value: walk(item, depth + 1)
    walk(result, 0)
    require(type(result) is dict, 'Compatibility config must be an object')
    return result


def transform(public_bytes, verifier):
    """Exact historical assembler: two assignments, indent=2, final newline."""
    require(type(verifier) is str and HEX64.fullmatch(verifier), 'Invalid historical verifier shape')
    cfg = strict_json(public_bytes)
    defaults = cfg.get('options')
    require(type(defaults) is dict and defaults.get('transcript_export_enabled') is False and
            defaults.get('transcript_export_token_sha256') == '', 'Invalid public compatibility defaults')
    defaults['transcript_export_enabled'] = True
    defaults['transcript_export_token_sha256'] = verifier
    return (json.dumps(cfg, indent=2) + '\n').encode()


def read_source_config(app):
    """Nofollow stable bounded read; exact reviewed target mode is mandatory."""
    fd = os.open('config.json', FLAGS, dir_fd=app)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                'Compatibility config must be regular and single-link')
        require(stat.S_IMODE(before.st_mode) == 0o644, 'Compatibility config mode must be reviewed 0644')
        require(0 <= before.st_size <= MAX_CONFIG_BYTES, 'Compatibility config exceeds size limit')
        data = bytearray()
        while True:
            block = os.read(fd, min(4096, MAX_CONFIG_BYTES + 1 - len(data)))
            if not block: break
            data.extend(block)
            require(len(data) <= MAX_CONFIG_BYTES, 'Compatibility config exceeds size limit')
        after = os.fstat(fd)
        path = os.stat('config.json', dir_fd=app, follow_symlinks=False)
        require(signature(before) == signature(after) == signature(path) and
                before.st_uid == after.st_uid == path.st_uid and
                before.st_gid == after.st_gid == path.st_gid and
                before.st_nlink == after.st_nlink == path.st_nlink == 1,
                'Compatibility config changed during read')
        return bytes(data), after
    finally:
        os.close(fd)


def preserve_historical_defaults(worker, expected_pin):
    require(type(expected_pin) is str and (not expected_pin or HEX64.fullmatch(expected_pin)),
            'Private historical baseline pin must be empty or lowercase SHA256')
    if not expected_pin:
        return  # Public-only behavior still refuses all private variants.
    deadline = time.monotonic() + SECONDS
    with worker.directory(worker.bundlepath) as bundle:
        baseline = read_at(bundle, 'baseline-config.blob')
        require(file_info(baseline) == worker.base[worker.config], 'Public baseline template changed')
    with worker.directory(worker.rootpath) as root:
        found = worker.discover(root, DISCOVERY_LIMIT, deadline)
        child, _, identity = found
        with worker.directory(child, root) as app:
            require(inode(os.fstat(app)) == identity, 'Compatibility target replaced')
            observed, original_stat = read_source_config(app)
            cfg = strict_json(observed)
            defaults = cfg.get('options')
            require(type(defaults) is dict and defaults.get('transcript_export_enabled') is True and
                    type(defaults.get('transcript_export_token_sha256')) is str and
                    HEX64.fullmatch(defaults['transcript_export_token_sha256']),
                    'Source is not the reviewed historical defaults variant')
            verifier = defaults['transcript_export_token_sha256']
            private_base = transform(baseline[0], verifier)
            require(digest(private_base) == expected_pin, 'Independent historical baseline pin mismatch')
            private_target = transform(worker.payload[worker.config], verifier)
            require(observed in (private_base, private_target),
                    'Source config is not an exact reviewed historical transformation')
            require(worker.discover(root, DISCOVERY_LIMIT, deadline) == found,
                    'Compatibility discovery changed')
            after, after_stat = read_source_config(app)
            require(after == observed and signature(after_stat) == signature(original_stat),
                    'Compatibility source changed')
            with worker.directory(worker.rootpath) as now:
                require(inode(os.fstat(now)) == inode(os.fstat(root)), 'Compatibility source mount changed')
            require(time.monotonic() <= deadline, 'Compatibility time budget exceeded')
    # Only config entries vary, after public templates, private pin and exact
    # bytes have ALL matched. Normal full-tree hash/mode/fingerprint gates follow.
    worker.base[worker.config] = {'sha256': expected_pin, 'mode': worker.base[worker.config]['mode']}
    worker.target[worker.config] = {'sha256': digest(private_target), 'mode': worker.target[worker.config]['mode']}
    worker.payload[worker.config] = private_target
    worker.bundle_id = digest(canonical(worker.manifest))
    worker.customized = True
