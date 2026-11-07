import base64
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import stat
import sys
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "updater.py"
spec = importlib.util.spec_from_file_location("updater", SOURCE)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class Crash(BaseException):
    pass


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "local_apps"
        self.app = self.root / "custom-folder-name"
        self.app.mkdir(parents=True)
        self.other = self.root / "another-app"
        self.other.mkdir()
        (self.other / "config.json").write_text('{"slug":"other","version":"1"}')
        (self.other / "private-file").write_bytes(b"unrelated")
        self.outside = Path(self.temp.name) / "outside"
        self.outside.mkdir()
        self.bundle = Path(self.temp.name) / "bundle"
        (self.bundle / "files").mkdir(parents=True)
        self.baseline = {"config.json": b'{"slug":"dji_audio_archive","version":"0.2.2","options":{"private":"do-not-log"}}',
                         "server.py": b"old server\n", "unchanged.py": b"same\n", "Dockerfile": b"old build\n"}
        self.payload = {**self.baseline, "config.json": b'{"slug":"dji_audio_archive","version":"0.2.4","options":{"transcript_export_enabled":false,"transcript_export_token_sha256":""}}',
                        "server.py": b"new server\n", "export.py": b"new module\n"}
        for name, data in self.baseline.items():
            (self.app / name).write_bytes(data)
        for name, data in self.payload.items():
            self.payload_path(name).write_bytes(data)
        def files(values):
            return {name: {"sha256": u.digest(data), "mode": 0o644} for name, data in values.items()}
        self.manifest = {"schema":1,"slug":"dji_audio_archive","config":"config.json",
                         "baseline":{"version":"0.2.2","files":files(self.baseline)},
                         "payload":{"version":"0.2.4","files":files(self.payload)}}
        self.save_manifest()
        self.updater = u.Updater(self.root, self.bundle)

    def payload_path(self, name):
        return self.bundle / "files" / (u.digest(name.encode()) + ".blob")

    def save_manifest(self):
        (self.bundle / "manifest.json").write_bytes(u.canonical(self.manifest))

    def start(self, mode="apply", hook=lambda e, n: None):
        return self.updater.run(mode, self.updater.inspect()["source_fingerprint"], hook)

    def assert_source(self, expected):
        self.assertEqual({p.name:p.read_bytes() for p in self.app.iterdir()}, expected)
        self.assertEqual((self.other / "private-file").read_bytes(), b"unrelated")

    @property
    def transaction(self):
        return self.root / u.STORE / self.updater.bundle_id

    def test_inspect_is_read_only_and_content_free(self):
        before = {str(p.relative_to(self.root)):p.stat() for p in self.root.rglob("*")}
        result = self.updater.inspect()
        after = {str(p.relative_to(self.root)):p.stat() for p in self.root.rglob("*")}
        self.assertEqual(set(before),set(after))
        self.assertEqual([(n,s.st_mtime_ns) for n,s in before.items()],[(n,s.st_mtime_ns) for n,s in after.items()])
        self.assertEqual(result["status"], "baseline")
        self.assertNotIn("do-not-log", json.dumps(result))
        self.assertFalse((self.root/u.STORE).exists())

    def test_apply_scope_config_last_and_idempotence(self):
        original = {p.name:p.stat().st_ino for p in self.app.iterdir()}
        events = []
        result = self.start(hook=lambda e,n: events.append((e,n)))
        self.assertEqual(result["status"],"applied")
        self.assert_source(self.payload)
        written = [n for e,n in events if e=="after_replace"]
        self.assertEqual(written, ["export.py","server.py","config.json"])
        self.assertEqual((self.app/"unchanged.py").stat().st_ino,original["unchanged.py"])
        self.assertEqual((self.app/"Dockerfile").stat().st_ino,original["Dockerfile"])
        self.assertEqual(self.start()["status"],"already_applied")
        self.assertFalse(any(p.name in u.CONFIGS for p in (self.root/u.STORE).rglob("*")))
        self.assertEqual((self.root/u.STORE).stat().st_mode & 0o777,0o700)

    def test_explicit_rollback_and_idempotence(self):
        self.start()
        self.assertEqual(self.start("rollback")["status"],"rolled_back")
        self.assert_source(self.baseline)
        self.assertEqual(self.start("rollback")["status"],"already_baseline")
        self.assertEqual(self.start()["status"],"applied")

    def test_wrong_fingerprint_no_writes(self):
        with self.assertRaises(u.Refused): self.updater.run("apply","0"*64)
        self.assertFalse((self.root/u.STORE).exists())
        self.assert_source(self.baseline)

    def test_directory_identity_bound_to_fingerprint(self):
        fp = self.updater.inspect()["source_fingerprint"]
        self.app.rename(self.root/"old-app")
        shutil.copytree(self.root/"old-app",self.app)
        shutil.rmtree(self.root/"old-app")
        with self.assertRaises(u.Refused): self.updater.run("apply",fp)
        self.assertFalse((self.root/u.STORE).exists())

    def test_unknown_file_no_deletion(self):
        (self.app/"notes.txt").write_bytes(b"mine")
        with self.assertRaises(u.Refused): self.updater.inspect()
        self.assertEqual((self.app/"notes.txt").read_bytes(),b"mine")

    def test_unknown_directory_no_deletion(self):
        (self.app/"data").mkdir()
        with self.assertRaises(u.Refused): self.updater.inspect()
        self.assertTrue((self.app/"data").is_dir())

    def test_user_modified_hash_refused(self):
        (self.app/"server.py").write_bytes(b"custom")
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_unsupported_version_refused(self):
        (self.app/"config.json").write_bytes(b'{"slug":"dji_audio_archive","version":"0.2.3"}')
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_duplicate_slug_refused(self):
        (self.other/"config.json").write_bytes(self.baseline["config.json"])
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_multiple_config_names_refused(self):
        (self.app/"config.yaml").write_text("slug: other\n")
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_target_symlink_refused(self):
        self.app.rename(self.outside/"app")
        self.app.symlink_to(self.outside/"app",target_is_directory=True)
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_source_symlink_refused(self):
        p=self.app/"server.py";p.unlink();p.symlink_to(self.other/"private-file")
        with self.assertRaises((u.Refused,OSError)): self.updater.inspect()
        self.assertEqual((self.other/"private-file").read_bytes(),b"unrelated")

    def test_source_hardlink_refused(self):
        os.link(self.app/"server.py", self.outside/"linked")
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_source_directory_instead_of_file_refused(self):
        p=self.app/"server.py";p.unlink();p.mkdir()
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_payload_tampering_refused(self):
        self.payload_path("server.py").write_bytes(b"bad")
        with self.assertRaises(u.Refused): u.Updater(self.root,self.bundle)

    def test_path_escape_manifest_refused(self):
        self.manifest["payload"]["files"]["../escape"]={"sha256":"0"*64,"mode":0o644};self.save_manifest()
        with self.assertRaises(u.Refused): u.Updater(self.root,self.bundle)

    def test_partial_apply_exception_rolls_back(self):
        def fail(e,n):
            if e=="after_replace" and n=="server.py": raise RuntimeError("disk error")
        with self.assertRaisesRegex(u.Refused,"baseline source restored"): self.start(hook=fail)
        self.assert_source(self.baseline)

    def test_crash_each_replace_boundary_recovers(self):
        for boundary in ("before_replace","after_replace"):
            for filename in self.updater.changed:
                with self.subTest(boundary=boundary,filename=filename):
                    def crash(e,n):
                        if e==boundary and n==filename: raise Crash()
                    with self.assertRaises(Crash): self.start(hook=crash)
                    phase=self.updater.inspect()["status"]
                    if phase=="mixed":
                        self.assertEqual(self.start()["status"],"rolled_back")
                    elif phase=="payload":
                        self.start("rollback")
                    else:
                        # No source mutation: an apply may resume normally.
                        self.start();self.start("rollback")
                    self.assert_source(self.baseline)

    def test_crash_during_rollback_can_resume(self):
        self.start()
        def crash(e,n):
            if e=="after_remove": raise Crash()
        with self.assertRaises(Crash): self.start("rollback",crash)
        self.assertEqual(self.start("rollback")["status"],"rolled_back")
        self.assert_source(self.baseline)

    def test_unknown_edit_after_crash_not_overwritten(self):
        def crash(e,n):
            if e=="after_replace" and n=="export.py":raise Crash()
        with self.assertRaises(Crash):self.start(hook=crash)
        (self.app/"server.py").write_bytes(b"human change")
        with self.assertRaises(u.Refused):self.updater.inspect()
        self.assertEqual((self.app/"server.py").read_bytes(),b"human change")

    def test_before_write_source_race_is_refused(self):
        def race(e,n):
            if e=="before_replace":(self.app/"server.py").write_bytes(b"concurrent")
        with self.assertRaises(u.Refused):self.start(hook=race)
        self.assertEqual((self.app/"server.py").read_bytes(),b"concurrent")
        self.assertFalse((self.app/"export.py").exists())

    def test_before_write_symlink_race_is_refused(self):
        def race(e,n):
            if e=="before_replace":
                p=self.app/"server.py";p.unlink();p.symlink_to(self.other/"private-file")
        with self.assertRaises(u.Refused):self.start(hook=race)
        self.assertEqual((self.other/"private-file").read_bytes(),b"unrelated")
        self.assertFalse((self.app/"export.py").exists())

    def test_target_directory_swap_before_write_is_refused(self):
        def race(e,n):
            if e=="before_replace":
                self.app.rename(self.outside/"original")
                self.app.mkdir()
        with self.assertRaises(u.Refused):self.start(hook=race)
        self.assertEqual(list(self.app.iterdir()),[])
        self.assertFalse((self.outside/"original"/"export.py").exists())

    def test_root_directory_swap_before_write_is_refused(self):
        def race(e,n):
            if e=="before_replace":
                self.root.rename(self.outside/"root")
                self.root.mkdir()
        with self.assertRaises(u.Refused):self.start(hook=race)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertFalse((self.outside/"root"/self.app.name/"export.py").exists())

    def test_post_write_race_blocks_unsafe_rollback(self):
        def race(e,n):
            if e=="after_replace":(self.app/n).write_bytes(b"human")
        with self.assertRaisesRegex(u.Refused,"rollback blocked"):self.start(hook=race)
        self.assertEqual((self.app/"export.py").read_bytes(),b"human")

    def test_corrupted_backup_refused(self):
        self.start()
        (self.transaction/"original"/self.updater.backup_name("server.py")).write_bytes(b"bad")
        with self.assertRaises(u.Refused):self.updater.inspect()
        self.assert_source(self.payload)

    def test_corrupted_journal_refused(self):
        self.start();(self.transaction/"journal.json").write_bytes(b"bad")
        with self.assertRaises(u.Refused):self.updater.inspect()

    def test_partial_backup_creation_resumes(self):
        original=self.updater.write_new
        def crash(parent,name,data,mode=0o600):
            original(parent,name,data,mode)
            if name.endswith(".blob"):raise Crash()
        self.updater.write_new=crash
        with self.assertRaises(Crash):self.start()
        self.assert_source(self.baseline)
        self.updater.write_new=original
        self.assertEqual(self.start()["status"],"applied")

    def test_partial_backup_bytes_refused_without_source_write(self):
        original=self.updater.write_new
        def crash(parent,name,data,mode=0o600):
            if name.endswith(".blob"):
                original(parent,name,b"partial",mode);raise Crash()
            original(parent,name,data,mode)
        self.updater.write_new=crash
        with self.assertRaises(Crash):self.start()
        self.updater.write_new=original
        with self.assertRaises(u.Refused):self.start()
        self.assert_source(self.baseline)

    def test_recovery_storage_symlink_refused(self):
        (self.root/u.STORE).symlink_to(self.outside,target_is_directory=True)
        with self.assertRaises((u.Refused,OSError)):self.start()
        self.assertEqual(list(self.outside.iterdir()),[])

    def test_cooperative_lock_blocks_second_writer(self):
        fd=os.open(self.app,os.O_RDONLY|os.O_DIRECTORY)
        try:
            u.fcntl.flock(fd,u.fcntl.LOCK_EX|u.fcntl.LOCK_NB)
            with self.assertRaises(u.Refused):self.start()
        finally:os.close(fd)
        self.assertFalse((self.root/u.STORE).exists())

    def test_fifo_refused_without_blocking(self):
        p = self.app / "server.py"
        p.unlink()
        os.mkfifo(p)
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_mid_write_power_cut_at_every_write(self):
        original = u.os.write
        calls = []
        def count(fd, data):
            calls.append(1)
            return original(fd, data)
        with mock.patch.object(u.os, "write", count): self.start()
        total = len(calls)
        self.start("rollback")
        for cut in range(total):
            with self.subTest(write_number=cut):
                shutil.rmtree(self.root/u.STORE)
                index = [0]
                def power_cut(fd, data):
                    at = index[0]
                    index[0] += 1
                    if at == cut:
                        original(fd, data[:max(1,len(data)//2)])
                        raise Crash()
                    return original(fd, data)
                with mock.patch.object(u.os, "write", power_cut):
                    with self.assertRaises(Crash): self.start()
                self.updater.inspect()
                self.start()
                if self.updater.inspect()["status"] == "payload": self.start("rollback")
                self.assert_source(self.baseline)

    def test_final_syscall_window_preserves_and_flags_concurrent_bytes(self):
        original = u.rename_atomic
        def race(srcfd, src, dstfd, dst, exchange=False):
            if dst == "server.py" and exchange:
                (self.app/"server.py").write_bytes(b"late human edit")
            return original(srcfd, src, dstfd, dst, exchange)
        with mock.patch.object(u, "rename_atomic", race):
            with self.assertRaisesRegex(u.ConcurrentRace,"preserved"): self.start()
        retained = [p.read_bytes() for p in (self.transaction/"retired").iterdir()]
        self.assertIn(b"late human edit", retained)
        with self.assertRaises(u.Refused): self.updater.inspect()

    def test_final_addition_window_never_overwrites_new_file(self):
        original = u.rename_atomic
        def race(srcfd, src, dstfd, dst, exchange=False):
            if dst == "export.py": (self.app/"export.py").write_bytes(b"new human file")
            return original(srcfd, src, dstfd, dst, exchange)
        with mock.patch.object(u, "rename_atomic", race):
            with self.assertRaises(u.Refused): self.start()
        self.assertEqual((self.app/"export.py").read_bytes(),b"new human file")

    def test_final_removal_window_preserves_and_flags_concurrent_bytes(self):
        self.start()
        original = u.rename_atomic
        def race(srcfd, src, dstfd, dst, exchange=False):
            if src == "export.py": (self.app/"export.py").write_bytes(b"late rollback edit")
            return original(srcfd, src, dstfd, dst, exchange)
        with mock.patch.object(u, "rename_atomic", race):
            with self.assertRaises(u.ConcurrentRace): self.start("rollback")
        self.assertIn(b"late rollback edit",[p.read_bytes() for p in (self.transaction/"retired").iterdir()])

    def test_open_old_inode_late_write_is_retained_and_detected(self):
        fd = os.open(self.app/"server.py",os.O_WRONLY)
        try:
            self.start()
            os.write(fd,b"late writer")
        finally: os.close(fd)
        with self.assertRaises(u.Refused): self.updater.inspect()
        self.assertTrue(any(b"late writer" in p.read_bytes() for p in (self.transaction/"retired").iterdir()))

    def test_private_defaults_only_two_fields_and_exact_rollback(self):
        public_bytes = self.payload_path("config.json").read_bytes()
        value = "a1" * 32
        self.updater = u.Updater(self.root, self.bundle, value)
        before = json.loads(public_bytes)
        after = json.loads(self.updater.payload["config.json"])
        self.assertTrue(after["options"]["transcript_export_enabled"])
        self.assertEqual(after["options"]["transcript_export_token_sha256"],value)
        after["options"] = before["options"]
        self.assertEqual(after,before)
        result = self.start()
        self.assertTrue(result["private_export_defaults"])
        self.assertNotIn(value,json.dumps(result))
        self.assertEqual(self.payload_path("config.json").read_bytes(),public_bytes)
        with self.assertRaises(u.Refused): u.Updater(self.root,self.bundle).inspect()
        with self.assertRaises(u.Refused): u.Updater(self.root,self.bundle,"b2"*32).inspect()
        self.start("rollback")
        self.assert_source(self.baseline)

    def test_invalid_private_digest_refused(self):
        for value in ("raw-token", "A"*64,"a"*63,"a"*65,"a"*63+"g", True, {"options":{}}):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(u.Refused): u.Updater(self.root,self.bundle,value)

    def test_private_digest_bound_to_fingerprint(self):
        one = u.Updater(self.root,self.bundle,"11"*32)
        two = u.Updater(self.root,self.bundle,"22"*32)
        self.assertNotEqual(one.inspect()["source_fingerprint"],two.inspect()["source_fingerprint"])
        with self.assertRaises(u.Refused): two.run("apply",one.inspect()["source_fingerprint"])

    def test_forbidden_public_default_change_refused(self):
        p=self.payload_path("config.json")
        cfg=json.loads(p.read_bytes());cfg["options"]["transcript_export_enabled"]=True
        p.write_bytes(u.canonical(cfg))
        self.manifest["payload"]["files"]["config.json"]["sha256"]=u.digest(p.read_bytes())
        self.save_manifest()
        with self.assertRaises(u.Refused):u.Updater(self.root,self.bundle,"11"*32)

    def test_unknown_recovery_file_preserved_and_refused(self):
        self.start()
        p=self.transaction/"user-note.txt";p.write_bytes(b"preserve")
        with self.assertRaises(u.Refused):self.updater.inspect()
        self.assertEqual(p.read_bytes(),b"preserve")

    def test_idempotent_apply_validates_backup_before_success(self):
        result=self.start()
        (self.transaction/"journal.json").write_bytes(b"corrupt")
        with self.assertRaises(u.Refused):self.updater.run("apply",result["source_fingerprint"])

    def test_root_ancestor_symlink_refused(self):
        link = Path(self.temp.name)/"linked-parent"
        link.symlink_to(self.root.parent,target_is_directory=True)
        updater=u.Updater(link/self.root.name,self.bundle)
        with self.assertRaises(OSError):updater.inspect()

    def test_parent_directory_escape_refused(self):
        updater=u.Updater(self.root/".."/self.root.name,self.bundle)
        with self.assertRaises(u.Refused):updater.inspect()

    def test_diagnose_unknown_types_without_reading_contents_or_writing(self):
        (self.app/"unexpected-file").write_bytes(b"PRIVATE UNKNOWN CONTENT")
        (self.app/"unexpected-dir").mkdir()
        (self.app/"unexpected-dir"/"secret").write_bytes(b"PRIVATE NESTED CONTENT")
        (self.app/"unexpected-link").symlink_to(self.outside/"not-present")
        os.mkfifo(self.app/"unexpected-fifo")
        before = {p.name: (p.lstat().st_mtime_ns,p.lstat().st_ctime_ns) for p in self.app.iterdir()}
        original_open = u.os.open
        def allow_discovery_only(path, flags, *args, **kwargs):
            if not flags & os.O_DIRECTORY:
                self.assertIn(path,u.CONFIGS,"Diagnostic opened non-config content")
            return original_open(path,flags,*args,**kwargs)
        with mock.patch.object(u.os,"open",allow_discovery_only), \
             mock.patch.object(u.os,"write",side_effect=AssertionError("write forbidden")), \
             mock.patch.object(u.os,"mkdir",side_effect=AssertionError("mkdir forbidden")), \
             mock.patch.object(u.os,"unlink",side_effect=AssertionError("unlink forbidden")), \
             mock.patch.object(u,"rename_atomic",side_effect=AssertionError("rename forbidden")):
            report = self.updater.diagnose()
        kinds = {item["name"]:item["type"] for item in report["unexpected_entries"]}
        self.assertEqual(kinds,{"unexpected-file":"regular_file","unexpected-dir":"directory",
                                "unexpected-link":"symlink","unexpected-fifo":"fifo"})
        self.assertEqual(report["status"],"diagnostic_only")
        self.assertFalse(report["source_verified"])
        self.assertNotIn("source_fingerprint",report)
        self.assertNotIn("PRIVATE",json.dumps(report))
        self.assertNotIn("secret",json.dumps(report))
        self.assertNotIn("not-present",json.dumps(report))
        after = {p.name: (p.lstat().st_mtime_ns,p.lstat().st_ctime_ns) for p in self.app.iterdir()}
        self.assertEqual(before,after)
        self.assertFalse((self.root/u.STORE).exists())

    def test_diagnose_metadata_uses_no_follow(self):
        (self.app/"unexpected-link").symlink_to(self.other/"private-file")
        original = u.os.stat
        calls=[]
        def checked(path,*args,**kwargs):
            if path == "unexpected-link":
                self.assertIs(kwargs.get("follow_symlinks"),False)
                calls.append(1)
            return original(path,*args,**kwargs)
        with mock.patch.object(u.os,"stat",checked): report=self.updater.diagnose()
        self.assertEqual(len(calls),2)
        item=report["unexpected_entries"][0]
        self.assertEqual(item["type"],"symlink")
        self.assertNotIn("size_bytes",item)

    def test_diagnose_name_escaping_and_exact_bytes(self):
        name='unexpected\n"\\\x1b[31m.bin'
        (self.app/name).write_bytes(b"hidden")
        report=self.updater.diagnose()
        item=report["unexpected_entries"][0]
        self.assertEqual(base64.b64decode(item["name_bytes_base64"]),os.fsencode(name))
        encoded=json.dumps(report,ensure_ascii=True)
        self.assertNotIn("\n",encoded)
        self.assertNotIn("\x1b",encoded)
        self.assertEqual(json.loads(encoded)["unexpected_entries"][0]["name"],name)

    def test_diagnose_long_unicode_name_preview_and_total_bounds(self):
        for i in range(12):
            name="\U0001f600"*61+str(i)
            (self.app/name).write_bytes(b"hidden")
        report=self.updater.diagnose()
        self.assertEqual(report["reported_count"],u.DIAGNOSTIC_ENTRIES)
        self.assertEqual(report["omitted_count_at_least"],12-u.DIAGNOSTIC_ENTRIES)
        self.assertTrue(report["unexpected_count_is_exact"])
        self.assertLessEqual(len(json.dumps(report,sort_keys=True,ensure_ascii=True).encode("ascii")),u.DIAGNOSTIC_REPORT_BYTES)
        for item in report["unexpected_entries"]:
            raw=base64.b64decode(item["name_bytes_base64"])
            self.assertTrue((self.app/os.fsdecode(raw)).exists())
            self.assertTrue(item["name_truncated"])
            self.assertEqual(item["name_sha256"],u.digest(raw))
            self.assertLessEqual(len(item["name"]),u.DIAGNOSTIC_NAME_CHARS)
            self.assertLessEqual(len(json.dumps(item["name"],ensure_ascii=True).encode("ascii")),u.DIAGNOSTIC_NAME_JSON_BYTES)

    def test_diagnose_invalid_utf8_filename_is_exact_and_ascii_safe(self):
        name=b"unexpected-\xff-\xfe"
        fd=os.open(os.fsencode(self.app)+b"/"+name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
        report=self.updater.diagnose()
        encoded=json.dumps(report,ensure_ascii=True).encode("ascii")
        self.assertEqual(base64.b64decode(report["unexpected_entries"][0]["name_bytes_base64"]),name)
        self.assertLessEqual(len(encoded),u.DIAGNOSTIC_REPORT_BYTES)

    def test_diagnose_scan_limit_marks_lower_bound(self):
        for i in range(320): (self.app/("unknown-%03d"%i)).touch()
        report=self.updater.diagnose()
        self.assertEqual(report["entries_scanned"],u.DIAGNOSTIC_SCAN_ENTRIES)
        self.assertTrue(report["listing_truncated"])
        self.assertFalse(report["unexpected_count_is_exact"])
        self.assertEqual(report["reported_count"],u.DIAGNOSTIC_ENTRIES)
        self.assertLessEqual(report["unexpected_count"],u.DIAGNOSTIC_SCAN_ENTRIES)
        self.assertFalse((self.root/u.STORE).exists())

    def test_diagnose_root_discovery_limit_refuses(self):
        for i in range(260): (self.root/("other-%03d"%i)).mkdir()
        with self.assertRaisesRegex(u.Refused,"discovery scan limit"):self.updater.diagnose()
        self.assertFalse((self.root/u.STORE).exists())

    def test_diagnose_time_budget_refuses(self):
        with mock.patch.object(u.time,"monotonic",side_effect=[100.0,106.0]):
            with self.assertRaisesRegex(u.Refused,"time budget"):self.updater.diagnose()
        self.assertFalse((self.root/u.STORE).exists())

    def test_diagnose_duplicate_slug_remains_refused(self):
        (self.other/"config.json").write_bytes(self.baseline["config.json"])
        with self.assertRaisesRegex(u.Refused,"ambiguous"):self.updater.diagnose()

    def test_diagnose_target_symlink_remains_refused(self):
        self.app.rename(self.outside/"app")
        self.app.symlink_to(self.outside/"app",target_is_directory=True)
        with self.assertRaises(u.Refused):self.updater.diagnose()

    def test_diagnose_target_directory_swap_refused(self):
        (self.app/"unexpected").touch()
        original=u.Updater.diagnostic_entry
        def swap(name,st):
            self.app.rename(self.outside/"moved-app")
            self.app.mkdir()
            return original(name,st)
        with mock.patch.object(self.updater,"diagnostic_entry",swap):
            with self.assertRaisesRegex(u.Refused,"directory replaced"):self.updater.diagnose()
        self.assertEqual(list(self.app.iterdir()),[])

    def test_diagnose_does_not_broaden_inspect_or_apply(self):
        fp=self.updater.inspect()["source_fingerprint"]
        (self.app/"unexpected").touch()
        self.assertEqual(self.updater.diagnose()["status"],"diagnostic_only")
        with self.assertRaisesRegex(u.Refused,"Unexpected files"):self.updater.inspect()
        with self.assertRaisesRegex(u.Refused,"Unexpected files"):self.updater.run("apply",fp)
        self.assertFalse((self.root/u.STORE).exists())

    def test_diagnose_empty_report_still_never_approves_source(self):
        (self.app/"server.py").write_bytes(b"unreviewed known file")
        result=self.updater.diagnose()
        self.assertEqual(result["unexpected_count"],0)
        self.assertFalse(result["source_verified"])
        self.assertNotIn("source_fingerprint",result)
        with self.assertRaises(u.Refused):self.updater.inspect()

    def test_diagnose_cli_emits_one_escaped_json_line(self):
        (self.app/"unexpected\nname").touch()
        options=Path(self.temp.name)/"options.json"
        options.write_text('{"mode":"diagnose"}')
        stdout=io.StringIO()
        argv=["updater.py","--root",str(self.root),"--bundle",str(self.bundle),"--options",str(options)]
        with mock.patch.object(sys,"argv",argv),contextlib.redirect_stdout(stdout):
            self.assertEqual(u.main(),0)
        lines=stdout.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        self.assertEqual(json.loads(lines[0])["status"],"diagnostic_only")
        self.assertFalse((self.root/u.STORE).exists())


if __name__=="__main__":unittest.main()
