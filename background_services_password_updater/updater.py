# Derived from the tested source transaction engine, with legacy private-config
# customization, permission normalization and unrestricted CLI removed.
"""One-shot, offline, fixed-manifest source replacement. Linux only."""
from __future__ import annotations

import base64
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

import yaml

FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
DIRFLAGS = FLAGS | os.O_DIRECTORY
STORE = ".background-services-password-updater"
CONFIGS = ("config.json", "config.yaml", "config.yml")
MAX_FILE = 16 * 1024 * 1024
DIAGNOSTIC_SCAN_ENTRIES = 256
DIAGNOSTIC_ROOT_ENTRIES = 256
DIAGNOSTIC_SECONDS = 5.0
DIAGNOSTIC_ENTRIES = 32
EXTRA_FILES_MAX = 32
EXTRA_FILE_MAX_BYTES = 16 * 1024 * 1024
EXTRA_TOTAL_MAX_BYTES = 64 * 1024 * 1024
EXTRA_NAME = re.compile(r"\.?[A-Za-z0-9][A-Za-z0-9_.-]{0,126}\Z")
DIAGNOSTIC_NAME_CHARS = 96
DIAGNOSTIC_NAME_JSON_BYTES = 256
DIAGNOSTIC_REPORT_BYTES = 8192
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class Refused(Exception):
    """Safe, content-free reason suitable for the operator log."""


class SourceMismatch(Refused):
    def __init__(self, report):
        super().__init__("Source hashes or modes do not match reviewed baseline/payload")
        self.report = report


class ConcurrentRace(Refused):
    pass


def rename_atomic(src_fd, src, dst_fd, dst, exchange=False):
    """Linux atomic exchange, or no-clobber rename. Never emulate with replace."""
    libc = ctypes.CDLL(None, use_errno=True)
    require(hasattr(libc, "renameat2"), "Linux atomic rename support required")
    fn = libc.renameat2
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    fn.restype = ctypes.c_int
    if fn(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), 2 if exchange else 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, "Atomic rename failed")


def require(condition, message):
    if not condition:
        raise Refused(message)


def check_diagnostic_budget(deadline):
    if deadline is not None:
        require(time.monotonic() <= deadline, "Diagnostic time budget exceeded")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def signature(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def inode(st):
    return (st.st_dev, st.st_ino)


def read_at(fd, name):
    """Read a stable, single-link regular file without following symlinks."""
    try:
        handle = os.open(name, FLAGS, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                "Non-regular or hard-linked source/storage file")
        require(before.st_size <= MAX_FILE, "File exceeds review limit")
        data = bytearray()
        while True:
            block = os.read(handle, 65536)
            if not block:
                break
            data.extend(block)
            require(len(data) <= MAX_FILE, "File exceeds review limit")
        after = os.fstat(handle)
        require(signature(before) == signature(after), "File changed during read")
        require(signature(after) == signature(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                "File replaced during read")
        return bytes(data), after
    finally:
        os.close(handle)


def file_info(item):
    if item is None:
        return None
    data, st = item
    return {"sha256": digest(data), "mode": stat.S_IMODE(st.st_mode)}


def parse_config(name, data):
    try:
        value = json.loads(data) if name.endswith(".json") else yaml.safe_load(data)
        require(isinstance(value, dict), "Invalid application config")
        return value
    except (ValueError, yaml.YAMLError, UnicodeError):
        raise Refused("Invalid application config") from None


def validate_extra_names(value, source_names):
    require(isinstance(value, list) and len(value) <= EXTRA_FILES_MAX, "Invalid preserved-extra allowlist")
    require(all(isinstance(name, str) and EXTRA_NAME.fullmatch(name) for name in value),
            "Preserved-extra names must be simple single-component ASCII filenames")
    folded = [name.casefold() for name in value]
    require(len(set(folded)) == len(folded), "Duplicate or aliased preserved-extra names")
    require(not (set(folded) & {name.casefold() for name in source_names}), "Preserved extra overlaps reviewed source")
    controls = {"dockerfile", "containerfile", "build", "makefile", "gnumakefile", "cmakelists.txt",
                "setup.py", "pipfile", "pipfile.lock", "poetry.lock", "uv.lock", "yarn.lock",
                "pnpm-lock.yaml", "package.json", "package-lock.json", "requirements.txt", "constraints.txt"}
    for name in folded:
        require(name not in controls and not name.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf")) and
                not name.startswith((".git", ".docker", ".env", STORE)) and
                not (name.endswith(".txt") and name.startswith(("requirements", "constraints"))),
                "Config or build-control files may not be preserved extras")
    return tuple(sorted(value))


def extra_metadata(st):
    return {"device": st.st_dev, "inode": st.st_ino, "mode": st.st_mode,
            "links": st.st_nlink, "size": st.st_size, "uid": st.st_uid, "gid": st.st_gid,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def validate_manifest(manifest):
    require(set(manifest) == {"schema", "slug", "config", "baseline", "payload"}, "Invalid manifest fields")
    require(type(manifest["schema"]) is int and manifest["schema"] in (1, 2) and
            manifest["slug"] == "dji_audio_archive", "Unsupported manifest identity")
    require(manifest["config"] in CONFIGS, "Unsupported application config name")
    for phase in ("baseline", "payload"):
        part = manifest[phase]
        require(set(part) == {"version", "files"} and isinstance(part["version"], str), "Invalid version manifest")
        require(isinstance(part["files"], dict) and 1 <= len(part["files"]) <= 100, "Invalid file whitelist")
        require(manifest["config"] in part["files"], "Config missing from whitelist")
        require(sum(name in part["files"] for name in CONFIGS) == 1, "Multiple config names")
        for name, item in part["files"].items():
            require(isinstance(name, str) and SAFE_NAME.fullmatch(name) is not None, "Unsafe source filename")
            require(set(item) == {"sha256", "mode"} and re.fullmatch("[0-9a-f]{64}", item["sha256"]) is not None,
                    "Invalid file manifest")
            require(item["mode"] in (0o644, 0o755), "Unsupported source file mode")
    require(set(manifest["baseline"]["files"]) <= set(manifest["payload"]["files"]),
            "Source removal is not supported")
    require(manifest["baseline"]["version"] != manifest["payload"]["version"], "Version must change")


class Updater:
    def __init__(self, root, bundle, preserve_extra_files=None):
        self.rootpath = str(root)
        self.bundlepath = Path(bundle)
        self.manifest = json.loads((self.bundlepath / "manifest.json").read_bytes())
        validate_manifest(self.manifest)
        self.bundle_id = digest(canonical(self.manifest))
        self.public_bundle_id = self.bundle_id
        self.customized = False
        self.base = self.manifest["baseline"]["files"]
        self.target = self.manifest["payload"]["files"]
        self.names = sorted(self.target)
        self.extra_names = validate_extra_names([] if preserve_extra_files is None else preserve_extra_files, self.names)
        self.config = self.manifest["config"]
        # Commit the visible version last. On rollback, restore it last as well.
        self.changed = [n for n in self.names if self.base.get(n) != self.target[n] and n != self.config]
        self.changed.append(self.config)
        self.payload = {}
        with self.directory(self.bundlepath / "files") as fd:
            require(set(os.listdir(fd)) == {self.backup_name(n) for n in self.target}, "Payload file set differs from manifest")
            for name in self.names:
                item = read_at(fd, self.backup_name(name))
                require(file_info(item) == self.target[name], "Payload hash or mode mismatch")
                self.payload[name] = item[0]
        cfg = parse_config(self.config, self.payload[self.config])
        require(cfg.get("slug") == self.manifest["slug"] and str(cfg.get("version")) == self.manifest["payload"]["version"],
                "Payload config identity/version mismatch")
        baseline_cfg = None
        if self.manifest["schema"] == 2:
            # Schema 2 pins a PUBLIC baseline template by the same exact hash
            # and mode used for source verification. Never derive a baseline
            # from observed source or accept an operator-supplied hash/override.
            require(self.config == "config.json", "Public baseline template requires JSON config")
            with self.directory(self.bundlepath) as fd:
                require(set(os.listdir(fd)) == {"manifest.json", "files", "baseline-config.blob"},
                        "Unexpected schema 2 bundle entries")
                item = read_at(fd, "baseline-config.blob")
                require(file_info(item) == self.base[self.config], "Baseline config template hash or mode mismatch")
                baseline_cfg = parse_config(self.config, item[0])
            require(baseline_cfg.get("slug") == self.manifest["slug"] and
                    str(baseline_cfg.get("version")) == self.manifest["baseline"]["version"],
                    "Baseline config template identity/version mismatch")
            for template in (baseline_cfg, cfg):
                defaults = template.get("options")
                require(isinstance(defaults, dict) and defaults.get("transcript_export_enabled") is False and
                        defaults.get("transcript_export_token_sha256") == "",
                        "Public export defaults must be disabled and empty")

    @staticmethod
    @contextlib.contextmanager
    def directory(path, parent=None):
        # Walk every component, not just the final directory, without links.
        path = os.fspath(path)
        if parent is not None:
            require(path not in ("", ".", "..") and "/" not in path, "Unsafe directory component")
            fd = os.open(path, DIRFLAGS, dir_fd=parent)
        else:
            parts = Path(path).parts
            require(".." not in parts, "Parent directory traversal refused")
            fd = os.open("/" if os.path.isabs(path) else ".", DIRFLAGS)
            try:
                for part in parts:
                    if part in ("/", "."):
                        continue
                    next_fd = os.open(part, DIRFLAGS, dir_fd=fd)
                    os.close(fd)
                    fd = next_fd
            except BaseException:
                os.close(fd)
                raise
        try:
            yield fd
        finally:
            os.close(fd)

    def discover(self, root, scan_limit=None, deadline=None):
        check_diagnostic_budget(deadline)
        matches = []
        if scan_limit is None:
            children = os.listdir(root)
        else:
            with os.scandir(root) as scan:
                children = [entry.name for entry in itertools.islice(scan, scan_limit + 1)]
            require(len(children) <= scan_limit, "Local app discovery scan limit exceeded")
        # Home Assistant local app source folders are direct children. Hidden
        # storage is not an app and contains no config.{json,yaml,yml} anywhere.
        for child in sorted(children):
            check_diagnostic_budget(deadline)
            if child.startswith("."):
                continue
            entry = os.stat(child, dir_fd=root, follow_symlinks=False)
            require(not stat.S_ISLNK(entry.st_mode), "Symlink in local app discovery")
            if not stat.S_ISDIR(entry.st_mode):
                continue
            with self.directory(child, root) as fd:
                require(inode(entry) == inode(os.fstat(fd)), "App directory replaced during discovery")
                candidates = []
                for name in CONFIGS:
                    check_diagnostic_budget(deadline)
                    value = read_at(fd, name)
                    check_diagnostic_budget(deadline)
                    if value is not None:
                        candidates.append((name, parse_config(name, value[0])))
                for name, cfg in candidates:
                    if cfg.get("slug") == self.manifest["slug"]:
                        require(len(candidates) == 1, "Multiple configs in target app")
                        matches.append((child, name, inode(entry)))
        require(len(matches) == 1, "Target slug absent or ambiguous")
        require(matches[0][1] == self.config, "Unexpected target config filename")
        return matches[0]

    @staticmethod
    def diagnostic_entry(name, st):
        kinds = ((stat.S_ISREG, "regular_file"), (stat.S_ISDIR, "directory"),
                 (stat.S_ISLNK, "symlink"), (stat.S_ISFIFO, "fifo"),
                 (stat.S_ISSOCK, "socket"), (stat.S_ISCHR, "character_device"),
                 (stat.S_ISBLK, "block_device"))
        prefix = name[:DIAGNOSTIC_NAME_CHARS]
        while len(json.dumps(prefix, ensure_ascii=True).encode("ascii")) > DIAGNOSTIC_NAME_JSON_BYTES:
            prefix = prefix[:-1]
        result = {"name": prefix, "name_truncated": prefix != name,
                  "name_bytes_base64": base64.b64encode(os.fsencode(name)).decode("ascii"),
                  "type": next((kind for predicate, kind in kinds if predicate(st.st_mode)), "other"),
                  "mode": format(stat.S_IMODE(st.st_mode), "04o")}
        if prefix != name:
            result["name_sha256"] = digest(os.fsencode(name))
        if stat.S_ISREG(st.st_mode):
            result["size_bytes"] = st.st_size
            result["link_count"] = st.st_nlink
        return result

    def diagnose(self, offset=0, snapshot=""):
        """Metadata-only pages; diagnostic tokens never authorize source writes."""
        require(type(offset) is int and 0 <= offset < DIAGNOSTIC_SCAN_ENTRIES, "Invalid diagnostic offset")
        require(isinstance(snapshot, str) and (not snapshot or re.fullmatch(r"diag:[0-9a-f]{64}", snapshot)),
                "Invalid diagnostic snapshot token")
        require(offset == 0 or snapshot, "Diagnostic continuation requires its snapshot token")
        deadline = time.monotonic() + DIAGNOSTIC_SECONDS
        with self.directory(self.rootpath) as root:
            child, _, ident = self.discover(root, DIAGNOSTIC_ROOT_ENTRIES, deadline)
            with self.directory(child, root) as app:
                require(inode(os.fstat(app)) == ident, "Target directory replaced")
                before = signature(os.fstat(app))
                with os.scandir(app) as scan:
                    entries = {entry.name for entry in itertools.islice(scan, DIAGNOSTIC_SCAN_ENTRIES)}
                listing_truncated = len(entries) == DIAGNOSTIC_SCAN_ENTRIES
                unknown = sorted(entries - set(self.names))
                metadata = {}
                for name in unknown:
                    check_diagnostic_budget(deadline)
                    metadata[name] = os.stat(name, dir_fd=app, follow_symlinks=False)
                token = "diag:" + digest(canonical({"directory": before, "entries": sorted(entries),
                                                     "unknown": {n: extra_metadata(st) for n, st in metadata.items()}}))
                require(not snapshot or snapshot == token, "Diagnostic snapshot changed; restart diagnosis at offset zero")
                require(offset < len(unknown) or offset == 0, "Diagnostic offset exceeds listed entries")
                result = {"status": "diagnostic_only", "source_verified": False,
                          "diagnostic_snapshot": token, "offset": offset, "next_offset": None,
                          "complete": False, "entries_scanned": len(entries), "listing_truncated": listing_truncated,
                          "unexpected_count": len(unknown), "unexpected_count_is_exact": not listing_truncated,
                          "reported_count": 0, "omitted_count_at_least": len(unknown), "unexpected_entries": []}
                # Add as many exact metadata records as fit, up to the fixed
                # per-page limit. Reserve space for final counters/continuation.
                for name in unknown[offset:offset + DIAGNOSTIC_ENTRIES]:
                    check_diagnostic_budget(deadline)
                    result["unexpected_entries"].append(self.diagnostic_entry(name, metadata[name]))
                    if len(json.dumps(result, sort_keys=True, ensure_ascii=True).encode("ascii")) > DIAGNOSTIC_REPORT_BYTES - 128:
                        result["unexpected_entries"].pop()
                        break
                count = len(result["unexpected_entries"])
                require(count > 0 or not unknown, "Diagnostic record exceeds output limit")
                end = offset + count
                result["reported_count"] = count
                result["omitted_count_at_least"] = len(unknown) - end
                result["next_offset"] = end if end < len(unknown) else None
                result["complete"] = not listing_truncated and end == len(unknown)
                # Recheck the entire scanned metadata snapshot, including
                # omitted entries, so pagination never blesses a changing list.
                with self.directory(self.rootpath) as current_root:
                    require(inode(os.fstat(current_root)) == inode(os.fstat(root)), "Source mount replaced")
                require(inode(os.stat(child, dir_fd=root, follow_symlinks=False)) == ident, "Target directory replaced")
                require(self.discover(root, DIAGNOSTIC_ROOT_ENTRIES, deadline) == (child, self.config, ident), "Target discovery changed")
                with os.scandir(app) as scan:
                    after_entries = {entry.name for entry in itertools.islice(scan, DIAGNOSTIC_SCAN_ENTRIES)}
                require(entries == after_entries and before == signature(os.fstat(app)), "Target entries changed during diagnosis")
                for name, expected in metadata.items():
                    check_diagnostic_budget(deadline)
                    require(extra_metadata(os.stat(name, dir_fd=app, follow_symlinks=False)) == extra_metadata(expected),
                            "Target entry changed during diagnosis")
                check_diagnostic_budget(deadline)
                require(len(json.dumps(result, sort_keys=True, ensure_ascii=True).encode("ascii")) <= DIAGNOSTIC_REPORT_BYTES,
                        "Diagnostic metadata exceeds output limit")
                return result

    def capture_extra_metadata(self, app):
        result = {}
        total = 0
        for name in self.extra_names:
            st = os.stat(name, dir_fd=app, follow_symlinks=False)
            require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "Preserved extras must be regular single-link files")
            require(stat.S_IMODE(st.st_mode) in (0o600, 0o644), "Unsafe preserved-extra permissions")
            require(0 <= st.st_size <= EXTRA_FILE_MAX_BYTES, "Preserved extra exceeds size limit")
            total += st.st_size
            require(total <= EXTRA_TOTAL_MAX_BYTES, "Preserved extras exceed total size limit")
            result[name] = extra_metadata(st)
        return result

    def capture(self, app):
        self.app_identity = inode(os.fstat(app))
        entries = set(os.listdir(app))
        require(entries <= set(self.names) | set(self.extra_names), "Unexpected files or directories in target app")
        require(set(self.base) <= entries, "Baseline source file missing")
        require(set(self.extra_names) <= entries, "Preserved extra missing")
        extras = self.capture_extra_metadata(app)
        current = {name: read_at(app, name) for name in self.names}
        require(extras == self.capture_extra_metadata(app), "Preserved extra changed during source capture")
        # Metadata is captured with this snapshot, never written into shared
        # expected state by a later guard. Extras are never opened for content.
        current["_preserved_extras"] = extras
        return current

    def classify(self, current):
        observed = {name: file_info(current[name]) for name in self.names}
        baseline = {name: self.base.get(name) for name in self.names}
        if observed == baseline:
            phase = "baseline"
        elif observed == self.target:
            phase = "payload"
        elif all(observed[n] in (self.base.get(n), self.target[n]) for n in self.names):
            phase = "mixed"
        else:
            raise Refused("Source hashes or modes do not match reviewed baseline/payload")
        cfg = parse_config(self.config, current[self.config][0])
        require(cfg.get("slug") == self.manifest["slug"], "Unexpected target identity")
        require(str(cfg.get("version")) in (self.manifest["baseline"]["version"], self.manifest["payload"]["version"]),
                "Unsupported source version")
        fingerprint = digest(canonical({"bundle": self.bundle_id, "target_inode": self.app_identity, "files": observed,
                                        "preserve_extra_files": self.extra_names, "extra_metadata": current["_preserved_extras"]}))
        return phase, fingerprint

    def guard(self, root, child, app, expected):
        with self.directory(self.rootpath) as current_root:
            require(inode(os.fstat(current_root)) == inode(os.fstat(root)), "Source mount replaced")
        require(inode(os.stat(child, dir_fd=root, follow_symlinks=False)) == inode(os.fstat(app)), "Target directory replaced")
        found = self.discover(root)
        require(found[0] == child and found[2] == inode(os.fstat(app)), "Target discovery changed")
        now = self.capture(app)
        require(now["_preserved_extras"] == expected["_preserved_extras"], "Concurrent preserved-extra modification detected")
        for name in self.names:
            a, b = expected[name], now[name]
            require((a is None and b is None) or
                    (a is not None and b is not None and signature(a[1]) == signature(b[1]) and a[0] == b[0]),
                    "Concurrent source modification detected")
        return now

    def report(self, phase, fingerprint, recovery=False):
        return {"status": phase, "bundle_sha256": self.bundle_id, "public_bundle_sha256": self.public_bundle_id,
                "private_export_defaults": self.customized, "config_sha256": self.target[self.config]["sha256"],
                "baseline_config_sha256": self.base[self.config]["sha256"],
                "source_fingerprint": fingerprint,
                "baseline_version": self.manifest["baseline"]["version"],
                "payload_version": self.manifest["payload"]["version"],
                "whitelisted_files": len(self.names), "changed_files": len(self.changed),
                "preserved_extra_files": len(self.extra_names),
                "recovery_available": recovery}

    def existing_backup(self, root, phase, extras):
        recovery = False
        if STORE in os.listdir(root):
            with self.directory(STORE, root) as store:
                self.check_private_directory(store)
                if self.bundle_id in os.listdir(store):
                    with self.directory(self.bundle_id, store) as transaction:
                        self.check_private_directory(transaction)
                        self.check_storage_entries(transaction)
                        self.check_extra_record(transaction, extras, required=False)
                        if read_at(transaction, "ready.json") is not None:
                            self.check_backup(transaction, extras)
                            self.check_journal(transaction, phase)
                            recovery = True
                        else:
                            require(phase == "baseline", "Incomplete recovery backup with changed source")
        require(phase != "mixed" or recovery, "Mixed source without verified recovery backup")
        return recovery

    def mismatch_report(self, current):
        rows = []
        for name in self.names:
            observed = file_info(current[name])
            if observed not in (self.base.get(name), self.target[name]):
                rows.append({"file": name, "expected_baseline": self.base.get(name),
                             "expected_payload": self.target[name], "observed": observed})
        result = {"status": "refused", "reason": "Source hashes or modes do not match reviewed baseline/payload",
                  "source_verified": False, "mismatch_count": len(rows), "reported_mismatches": 0,
                  "omitted_mismatches": len(rows), "diagnosis_complete": False, "mismatches": []}
        for row in rows:
            result["mismatches"].append(row)
            if len(json.dumps(result, sort_keys=True, ensure_ascii=True).encode("ascii")) > DIAGNOSTIC_REPORT_BYTES - 64:
                result["mismatches"].pop()
                break
        count = len(result["mismatches"])
        result["reported_mismatches"] = count
        result["omitted_mismatches"] = len(rows) - count
        result["diagnosis_complete"] = count == len(rows)
        require(len(json.dumps(result, sort_keys=True, ensure_ascii=True).encode("ascii")) <= DIAGNOSTIC_REPORT_BYTES,
                "Source diagnostic exceeds output limit")
        return result

    def inspect(self):
        with self.directory(self.rootpath) as root:
            child, _, ident = self.discover(root)
            with self.directory(child, root) as app:
                require(inode(os.fstat(app)) == ident, "Target directory replaced")
                current = self.capture(app)
                try:
                    phase, fingerprint = self.classify(current)
                except Refused as error:
                    if str(error) != "Source hashes or modes do not match reviewed baseline/payload":
                        raise
                    # Recheck the exact known-source/extra snapshot before
                    # exposing hashes. This never turns a mismatch into approval.
                    self.guard(root, child, app, current)
                    raise SourceMismatch(self.mismatch_report(current)) from None
                self.guard(root, child, app, current)
                return self.report(phase, fingerprint, self.existing_backup(root, phase, current["_preserved_extras"]))

    @staticmethod
    def check_private_directory(fd):
        st = os.fstat(fd)
        require(stat.S_IMODE(st.st_mode) == 0o700 and st.st_uid == os.geteuid(), "Unsafe recovery directory permissions/owner")

    @staticmethod
    def mkdir_at(parent, name):
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        fd = os.open(name, DIRFLAGS, dir_fd=parent)
        try:
            Updater.check_private_directory(fd)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def discard_pending(parent, name):
        item = read_at(parent, name)
        if item is not None:
            require(item[1].st_uid == os.geteuid(), "Unexpected pending file owner")
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)

    @staticmethod
    def write_new(parent, name, data, mode=0o600):
        # Only the .pending name may contain a short write. It is explicitly
        # non-authoritative and safely discarded/recreated after interruption.
        pending = name + ".pending"
        Updater.discard_pending(parent, pending)
        require(read_at(parent, name) is None, "Refusing to replace existing recovery object")
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=parent)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                require(written > 0, "Short staging write")
                view = view[written:]
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        require(read_at(parent, name) is None, "Recovery object appeared concurrently")
        require(read_at(parent, pending)[0] == data, "Pending write verification failed")
        rename_atomic(parent, pending, parent, name)
        os.fsync(parent)

    def backup_name(self, name):
        return digest(name.encode()) + ".blob"

    def check_extra_record(self, transaction, extras, required=True):
        saved = read_at(transaction, "extras.json")
        if saved is None:
            if extras:
                pending = read_at(transaction, "extras.json.pending")
                require(pending is None or pending[0] == canonical({"files": extras}),
                        "Incomplete preserved-extra metadata requires review")
            require(not required or not extras, "Preserved-extra recovery metadata missing")
        else:
            require(saved[0] == canonical({"files": extras}), "Preserved-extra recovery metadata changed")

    def check_backup(self, transaction, extras):
        self.check_extra_record(transaction, extras)
        saved = read_at(transaction, "review.json")
        require(saved is not None and saved[0] == canonical(self.manifest), "Recovery manifest missing/mismatched")
        with self.directory("original", transaction) as original:
            self.check_private_directory(original)
            expected = {self.backup_name(n) for n in self.base}
            require(set(os.listdir(original)) <= expected | {n + ".pending" for n in expected}, "Unexpected recovery backup contents")
            for name, info in self.base.items():
                item = read_at(original, self.backup_name(name))
                require(item is not None and digest(item[0]) == info["sha256"], "Recovery backup hash mismatch")

    def check_storage_entries(self, transaction):
        names = {"review.json", "ready.json", "journal.json", "journal.next", "conflict.json", "extras.json"}
        names |= {self.stage_name(n) for n in self.changed}
        require(set(os.listdir(transaction)) <= names | {n + ".pending" for n in names} | {"original", "retired"},
                "Unexpected recovery transaction contents")
        require(read_at(transaction, "conflict.json") is None, "Concurrent source conflict retained; manual reconciliation required")
        if "retired" in os.listdir(transaction):
            with self.directory("retired", transaction) as retired:
                self.check_private_directory(retired)
                for name in os.listdir(retired):
                    require(re.fullmatch(r"[0-9a-f]{64}_[0-9a-f]{32}\.blob", name) is not None, "Unknown retired source object")
                    source = next((n for n in self.changed if digest(n.encode()) == name.split("_")[0]), None)
                    require(source is not None, "Unknown retired source identity")
                    observed = file_info(read_at(retired, name))
                    require(observed in (self.base.get(source), self.target[source]),
                            "Changed source bytes retained; manual reconciliation required")

    def check_journal(self, transaction, phase):
        self.check_storage_entries(transaction)
        ready = read_at(transaction, "ready.json")
        require(ready is not None and ready[0] == canonical({"bundle": self.bundle_id}), "Invalid backup readiness marker")
        journal = read_at(transaction, "journal.json")
        if journal is None:
            require(phase == "baseline", "Changed source without recovery journal")
            return
        try:
            value = json.loads(journal[0])
        except (ValueError, UnicodeError):
            raise Refused("Invalid recovery journal") from None
        require(set(value) == {"bundle", "phase"} and value["bundle"] == self.bundle_id and
                value["phase"] in ("applying", "applied", "rolling_back", "rolled_back"), "Invalid recovery journal")

    def prepare_backup(self, transaction, current):
        self.check_storage_entries(transaction)
        if read_at(transaction, "ready.json") is not None:
            self.check_backup(transaction, current["_preserved_extras"])
            self.check_journal(transaction, self.classify(current)[0])
            return
        # No live mutation is allowed until ready.json is durable. Incomplete
        # preparation is resumable only from the exact baseline.
        require(self.classify(current)[0] == "baseline", "Incomplete backup requires unchanged baseline")
        if read_at(transaction, "extras.json") is None:
            self.write_new(transaction, "extras.json", canonical({"files": current["_preserved_extras"]}))
        else:
            self.check_extra_record(transaction, current["_preserved_extras"])
        saved = read_at(transaction, "review.json")
        if saved is None:
            self.write_new(transaction, "review.json", canonical(self.manifest))
        else:
            require(saved[0] == canonical(self.manifest), "Recovery manifest mismatch")
        original = self.mkdir_at(transaction, "original")
        try:
            expected = {self.backup_name(n) for n in self.base}
            require(set(os.listdir(original)) <= expected | {n + ".pending" for n in expected}, "Unexpected recovery backup contents")
            for name in self.base:
                blob = self.backup_name(name)
                item = read_at(original, blob)
                if item is None:
                    self.write_new(original, blob, current[name][0])
                else:
                    require(digest(item[0]) == self.base[name]["sha256"], "Incomplete backup content mismatch")
                    self.discard_pending(original, blob + ".pending")
        finally:
            os.close(original)
        self.check_backup(transaction, current["_preserved_extras"])
        self.write_new(transaction, "ready.json", canonical({"bundle": self.bundle_id}))

    def set_state(self, transaction, phase):
        # A previous interrupted journal temp has no authority over source.
        temp = "journal.next"
        if temp in os.listdir(transaction):
            require(read_at(transaction, temp) is not None, "Invalid temporary journal")
            os.unlink(temp, dir_fd=transaction)
        self.write_new(transaction, temp, canonical({"bundle": self.bundle_id, "phase": phase}))
        os.replace(temp, "journal.json", src_dir_fd=transaction, dst_dir_fd=transaction)
        os.fsync(transaction)

    def verify_storage(self, root, store, transaction, extras):
        require(inode(os.stat(STORE, dir_fd=root, follow_symlinks=False)) == inode(os.fstat(store)), "Recovery storage replaced")
        require(inode(os.stat(self.bundle_id, dir_fd=store, follow_symlinks=False)) == inode(os.fstat(transaction)), "Recovery transaction replaced")
        self.check_backup(transaction, extras)
        self.check_storage_entries(transaction)

    def stage_name(self, name):
        return "replacement-" + digest(name.encode()) + ".blob"

    def retire(self, transaction, name, filename):
        retired = self.mkdir_at(transaction, "retired")
        try:
            dest = digest(name.encode()) + "_" + uuid.uuid4().hex + ".blob"
            rename_atomic(transaction, filename, retired, dest)
            os.fsync(retired)
            os.fsync(transaction)
            return read_at(retired, dest)
        finally:
            os.close(retired)

    def record_conflict(self, transaction):
        if read_at(transaction, "conflict.json") is None:
            self.write_new(transaction, "conflict.json", canonical({"status": "concurrent_source_change"}))
        raise ConcurrentRace("Concurrent source change preserved in recovery storage; manual reconciliation required")

    def recover_staging(self, transaction):
        for name in self.changed:
            temp = self.stage_name(name)
            item = read_at(transaction, temp)
            if item is not None:
                allowed = {self.target[name]["sha256"]}
                if name in self.base:
                    allowed.add(self.base[name]["sha256"])
                # Could be a prepared new file, or the previous file displaced
                # atomically immediately before a crash. Never discard it.
                if digest(item[0]) not in allowed:
                    self.record_conflict(transaction)
                self.retire(transaction, name, temp)

    def replace_file(self, root, child, app, store, transaction, expected, name, data, mode, hook):
        temp = self.stage_name(name)
        require(temp not in os.listdir(transaction), "Unexpected staged replacement")
        self.write_new(transaction, temp, data, mode)
        staged = read_at(transaction, temp)
        require(staged is not None and staged[0] == data and stat.S_IMODE(staged[1].st_mode) == mode, "Staging verification failed")
        hook("before_replace", name)
        self.guard(root, child, app, expected)
        self.verify_storage(root, store, transaction, expected["_preserved_extras"])
        previous = expected[name]
        if previous is None:
            # A concurrent creation cannot be overwritten, even in the final
            # guard-to-syscall window.
            rename_atomic(transaction, temp, app, name)
        else:
            # Exchange retains the old inode, including late writes from a
            # process holding it open. Retired files are never auto-deleted.
            rename_atomic(transaction, temp, app, name, exchange=True)
        os.fsync(app)
        os.fsync(transaction)
        result = read_at(app, name)
        require(result is not None and result[0] == data and stat.S_IMODE(result[1].st_mode) == mode,
                "Replacement verification failed")
        expected[name] = result
        if previous is not None:
            displaced = self.retire(transaction, name, temp)
            # Rename can update ctime. All other immutable snapshot fields and
            # bytes must still match the file that passed the final guard.
            same = displaced is not None and displaced[0] == previous[0] and \
                signature(displaced[1])[:-1] == signature(previous[1])[:-1]
            if not same:
                self.record_conflict(transaction)
        hook("after_replace", name)
        self.guard(root, child, app, expected)

    def restore(self, root, child, app, store, transaction, current, hook):
        self.classify(current)  # Never overwrite unknown edits.
        self.verify_storage(root, store, transaction, current["_preserved_extras"])
        self.set_state(transaction, "rolling_back")
        for name in self.changed:
            if file_info(current[name]) == self.base.get(name):
                continue
            self.guard(root, child, app, current)
            if name not in self.base:
                hook("before_remove", name)
                self.guard(root, child, app, current)
                self.verify_storage(root, store, transaction, current["_preserved_extras"])
                previous = current[name]
                temp = self.stage_name(name)
                rename_atomic(app, name, transaction, temp)
                os.fsync(app)
                os.fsync(transaction)
                current[name] = None
                displaced = self.retire(transaction, name, temp)
                if displaced is None or displaced[0] != previous[0] or signature(displaced[1])[:-1] != signature(previous[1])[:-1]:
                    self.record_conflict(transaction)
                hook("after_remove", name)
                self.guard(root, child, app, current)
            else:
                with self.directory("original", transaction) as original:
                    data = read_at(original, self.backup_name(name))[0]
                self.replace_file(root, child, app, store, transaction, current, name, data, self.base[name]["mode"], hook)
        require(self.classify(current)[0] == "baseline", "Rollback verification failed")
        self.set_state(transaction, "rolled_back")

    def run(self, mode, fingerprint, hook=lambda event, name: None):
        require(mode in ("apply", "rollback"), "Unsupported write operation")
        require(re.fullmatch("[0-9a-f]{64}", fingerprint or "") is not None, "Explicit preflight fingerprint required")
        with self.directory(self.rootpath) as root:
            child, _, ident = self.discover(root)
            with self.directory(child, root) as app:
                require(inode(os.fstat(app)) == ident, "Target directory replaced")
                # Cooperative serialization only; does not grant extra access.
                try:
                    fcntl.flock(app, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Refused("Another updater is active") from None
                current = self.capture(app)
                phase, actual = self.classify(current)
                require(actual == fingerprint, "Preflight fingerprint changed")
                self.guard(root, child, app, current)
                recovery = self.existing_backup(root, phase, current["_preserved_extras"])
                if phase == "payload" and mode == "apply":
                    return self.report("already_applied", actual, recovery)
                if phase == "baseline" and mode == "rollback":
                    return self.report("already_baseline", actual, recovery)
                # No writes before the full baseline/fingerprint gate above.
                if mode == "rollback" or phase == "mixed":
                    require(STORE in os.listdir(root), "Verified backup required for recovery")
                store = self.mkdir_at(root, STORE)
                try:
                    require(os.fstat(root).st_dev == os.fstat(app).st_dev == os.fstat(store).st_dev, "Source and recovery storage must share filesystem")
                    if mode == "rollback" or phase == "mixed":
                        require(self.bundle_id in os.listdir(store), "Verified transaction required for recovery")
                    transaction = self.mkdir_at(store, self.bundle_id)
                    try:
                        self.prepare_backup(transaction, current)
                        self.verify_storage(root, store, transaction, current["_preserved_extras"])
                        self.recover_staging(transaction)
                        if phase == "mixed" or mode == "rollback":
                            self.restore(root, child, app, store, transaction, current, hook)
                            return self.report("rolled_back", self.classify(current)[1], True)
                        self.set_state(transaction, "applying")
                        try:
                            for name in self.changed:
                                self.replace_file(root, child, app, store, transaction, current, name,
                                                  self.payload[name], self.target[name]["mode"], hook)
                            require(self.classify(current)[0] == "payload", "Final source verification failed")
                            self.verify_storage(root, store, transaction, current["_preserved_extras"])
                            self.set_state(transaction, "applied")
                        except ConcurrentRace:
                            raise
                        except Exception as error:
                            # Do not hide a concurrent edit by recapturing it as
                            # authoritative. If original snapshot no longer
                            # holds, leave verified backup for explicit repair.
                            try:
                                self.guard(root, child, app, current)
                                self.recover_staging(transaction)
                                self.restore(root, child, app, store, transaction, current, lambda e, n: None)
                            except Exception:
                                raise Refused("Apply stopped; automatic rollback blocked; preserve recovery backup and inspect") from error
                            raise Refused("Apply failed; baseline source restored and verified") from error
                        return self.report("applied", self.classify(current)[1], True)
                    finally:
                        os.close(transaction)
                finally:
                    os.close(store)

