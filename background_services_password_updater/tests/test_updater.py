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
        self.assertGreater(report["reported_count"],0)
        self.assertLessEqual(report["reported_count"],u.DIAGNOSTIC_ENTRIES)
        self.assertEqual(report["omitted_count_at_least"],12-report["reported_count"])
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


    def make_extras(self, names=("legacy-stage.bin",), mode=0o644):
        for name in names:
            (self.app/name).write_bytes(b"UNREAD EXTRA CONTENT " + name.encode())
            (self.app/name).chmod(mode)
        self.updater=u.Updater(self.root,self.bundle,preserve_extra_files=list(names))
        return {name:u.extra_metadata((self.app/name).lstat()) for name in names}

    def assert_extras_unchanged(self, expected):
        self.assertEqual({name:u.extra_metadata((self.app/name).lstat()) for name in expected},expected)

    def assert_reviewed_source(self, expected):
        self.assertEqual({name:(self.app/name).read_bytes() for name in expected},expected)
        self.assertEqual(set(p.name for p in self.app.iterdir()),set(expected)|set(self.updater.extra_names))

    def test_diagnostic_dynamic_page_contains_fifteen_short_names(self):
        for i in range(15): (self.app/("legacy-stage-%02d.bak"%i)).touch()
        report=self.updater.diagnose()
        self.assertEqual(report["reported_count"],15)
        self.assertTrue(report["complete"])
        self.assertIsNone(report["next_offset"])
        self.assertTrue(report["diagnostic_snapshot"].startswith("diag:"))
        self.assertNotIn("source_fingerprint",report)

    def test_diagnostic_pages_cover_exact_all_names(self):
        expected=[]
        for i in range(60):
            name="\U0001f600"*60+str(i)
            expected.append(os.fsencode(name));(self.app/name).touch()
        seen=[];offset=0;token=""
        while True:
            report=self.updater.diagnose(offset,token)
            seen.extend(base64.b64decode(item["name_bytes_base64"]) for item in report["unexpected_entries"])
            self.assertLessEqual(len(json.dumps(report,ensure_ascii=True).encode("ascii")),u.DIAGNOSTIC_REPORT_BYTES)
            if report["next_offset"] is None:
                self.assertTrue(report["complete"]);break
            self.assertFalse(report["complete"])
            self.assertGreater(report["next_offset"],offset)
            offset=report["next_offset"];token=report["diagnostic_snapshot"]
        self.assertEqual(sorted(seen),sorted(expected))
        self.assertEqual(len(seen),len(set(seen)))

    def test_diagnostic_continuation_requires_unchanged_metadata(self):
        for i in range(45):(self.app/("legacy-item-%02d"%i)).touch()
        first=self.updater.diagnose()
        (self.app/"legacy-item-44").write_bytes(b"changed beyond first page")
        with self.assertRaisesRegex(u.Refused,"snapshot changed"):
            self.updater.diagnose(first["next_offset"],first["diagnostic_snapshot"])

    def test_diagnostic_continuation_requires_unchanged_membership(self):
        for i in range(45):(self.app/("legacy-item-%02d"%i)).touch()
        first=self.updater.diagnose();(self.app/"new-member").touch()
        with self.assertRaisesRegex(u.Refused,"snapshot changed"):
            self.updater.diagnose(first["next_offset"],first["diagnostic_snapshot"])

    def test_diagnostic_bad_cursors_refuse_and_token_cannot_apply(self):
        for offset,token in ((True,""),(-1,""),(256,""),(1,""),(1,"a"*64),(0,"diag:bad"),(0,None)):
            with self.subTest(offset=offset,token=token):
                with self.assertRaises(u.Refused):self.updater.diagnose(offset,token)
        report=self.updater.diagnose()
        with self.assertRaises(u.Refused):self.updater.run("apply",report["diagnostic_snapshot"])
        self.assertFalse((self.root/u.STORE).exists())

    def test_preserve_extra_name_validation(self):
        bad=[{},"legacy.bin",["."],[".."],["../legacy.bin"],["./legacy.bin"],["nested/legacy.bin"],
             ["nested\\legacy.bin"],["legacy.bin/"],["legacy.bin "],["legacy\n.bin"],["\u00e9.bin"],
             ["..legacy.bin"],["%2e%2e.bin"],["x"*129],["one.bin","one.bin"],["One.bin","one.bin"],
             ["server.py"],["SERVER.PY"],["export.py"],["config.json"],["config.yml"],[".dockerignore"],
             ["Dockerfile"],["Dockerfile".upper()],["build.yaml"],["package.json"],["setup.py"],
             ["requirements-dev.txt"],["Makefile"],[".env"],[".gitconfig"],["private.conf"],
             ["item-%d.bin"%i for i in range(u.EXTRA_FILES_MAX+1)]]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(u.Refused):u.Updater(self.root,self.bundle,preserve_extra_files=value)

    def test_preserve_inert_scripts_and_backups_are_allowed(self):
        metadata=self.make_extras(("old-deploy.ps1","old-helper.py","legacy-start.sh","old-source.bak","legacy.bin"))
        self.assertEqual(self.updater.inspect()["preserved_extra_files"],5)
        self.start();self.start("rollback")
        self.assert_extras_unchanged(metadata)
        self.assert_reviewed_source(self.baseline)

    def test_preserve_extra_apply_rollback_never_reads_or_writes_extras(self):
        metadata=self.make_extras(("legacy-a.bin",".legacy-b.bak"))
        names=set(metadata);inodes={value["inode"] for value in metadata.values()}
        original_open=u.os.open;original_rename=u.rename_atomic;original_write=u.os.write;original_unlink=u.os.unlink
        def no_extra_open(path,*args,**kwargs):
            self.assertNotIn(path,names,"Extra content opened")
            return original_open(path,*args,**kwargs)
        def no_extra_rename(srcfd,src,dstfd,dst,exchange=False):
            self.assertNotIn(src,names);self.assertNotIn(dst,names)
            return original_rename(srcfd,src,dstfd,dst,exchange)
        def no_extra_write(fd,data):
            self.assertNotIn(os.fstat(fd).st_ino,inodes)
            return original_write(fd,data)
        def no_extra_unlink(path,*args,**kwargs):
            self.assertNotIn(path,names)
            return original_unlink(path,*args,**kwargs)
        with mock.patch.object(u.os,"open",no_extra_open),mock.patch.object(u,"rename_atomic",no_extra_rename), \
             mock.patch.object(u.os,"write",no_extra_write),mock.patch.object(u.os,"unlink",no_extra_unlink):
            preflight=self.updater.inspect();self.start();self.start("rollback")
        self.assertNotIn("legacy",json.dumps(preflight))
        self.assert_extras_unchanged(metadata);self.assert_reviewed_source(self.baseline)
        blobs=list((self.transaction/"original").iterdir())
        self.assertEqual(len(blobs),len(self.baseline))
        for p in self.transaction.rglob("*"):
            if p.is_file():self.assertNotIn(b"UNREAD EXTRA CONTENT",p.read_bytes())
        self.assertEqual(json.loads((self.transaction/"extras.json").read_bytes()),{"files":metadata})

    def test_preserve_missing_or_unlisted_extras_refuse(self):
        self.make_extras()
        with self.assertRaises(u.Refused):u.Updater(self.root,self.bundle).inspect()
        (self.app/"unlisted.bin").touch()
        with self.assertRaisesRegex(u.Refused,"Unexpected files"):self.updater.inspect()
        (self.app/"unlisted.bin").unlink();(self.app/"legacy-stage.bin").unlink()
        with self.assertRaisesRegex(u.Refused,"extra missing"):self.updater.inspect()

    def test_preserve_symlink_fifo_directory_hardlink_refuse(self):
        name="legacy.bin";p=self.app/name
        updater=u.Updater(self.root,self.bundle,preserve_extra_files=[name])
        p.symlink_to(self.other/"private-file")
        with self.assertRaises(u.Refused):updater.inspect()
        p.unlink();os.mkfifo(p)
        with self.assertRaises(u.Refused):updater.inspect()
        p.unlink();p.mkdir()
        with self.assertRaises(u.Refused):updater.inspect()
        p.rmdir();os.link(self.other/"private-file",p)
        with self.assertRaises(u.Refused):updater.inspect()
        self.assertEqual((self.other/"private-file").read_bytes(),b"unrelated")

    def test_preserve_unsafe_modes_refuse(self):
        self.make_extras()
        p=self.app/"legacy-stage.bin"
        for mode in (0o755,0o664,0o646,0o666,0o1644,0o2644,0o4644,0o000):
            p.chmod(mode)
            with self.subTest(mode=mode):
                with self.assertRaises(u.Refused):self.updater.inspect()
        p.chmod(0o600)
        self.assertEqual(self.updater.inspect()["status"],"baseline")

    def test_preserve_per_file_and_total_size_bounds(self):
        self.make_extras()
        with (self.app/"legacy-stage.bin").open("wb") as f:f.truncate(u.EXTRA_FILE_MAX_BYTES+1)
        with self.assertRaisesRegex(u.Refused,"size limit"):self.updater.inspect()
        (self.app/"legacy-stage.bin").unlink()
        names=["large-%d.bin"%i for i in range(5)]
        self.make_extras(names)
        for name in names:
            with (self.app/name).open("wb") as f:f.truncate(u.EXTRA_FILE_MAX_BYTES)
        with self.assertRaisesRegex(u.Refused,"total size"):self.updater.inspect()

    def test_preserve_fingerprint_binds_metadata_and_names(self):
        self.make_extras()
        first=self.updater.inspect()["source_fingerprint"]
        (self.app/"legacy-stage.bin").write_bytes(b"changed")
        second=self.updater.inspect()["source_fingerprint"]
        self.assertNotEqual(first,second)
        with self.assertRaisesRegex(u.Refused,"fingerprint changed"):self.updater.run("apply",first)
        self.assertFalse((self.root/u.STORE).exists())
        (self.app/"server.py").write_bytes(b"modified baseline")
        with self.assertRaisesRegex(u.Refused,"Source hashes"):self.updater.inspect()

    def test_preserve_capture_snapshot_is_not_mutated_by_guard(self):
        self.make_extras()
        with self.updater.directory(self.root) as root,self.updater.directory(self.app.name,root) as app:
            expected=self.updater.capture(app)
            original=json.loads(json.dumps(expected["_preserved_extras"]))
            (self.app/"legacy-stage.bin").write_bytes(b"later")
            with self.assertRaisesRegex(u.Refused,"preserved-extra modification"):
                self.updater.guard(root,self.app.name,app,expected)
            self.assertEqual(expected["_preserved_extras"],original)

    def test_preserve_race_before_first_write_stops_without_blessing(self):
        self.make_extras()
        def race(e,n):
            if e=="before_replace":(self.app/"legacy-stage.bin").write_bytes(b"concurrent extra")
        with self.assertRaises(u.Refused):self.start(hook=race)
        self.assert_reviewed_source(self.baseline)
        again=u.Updater(self.root,self.bundle,preserve_extra_files=["legacy-stage.bin"])
        with self.assertRaisesRegex(u.Refused,"recovery metadata changed"):again.inspect()
        self.assertEqual((self.app/"legacy-stage.bin").read_bytes(),b"concurrent extra")

    def test_preserve_normal_failure_rolls_back_source_only(self):
        metadata=self.make_extras()
        def fail(e,n):
            if e=="after_replace" and n=="server.py":raise RuntimeError("synthetic failure")
        with self.assertRaisesRegex(u.Refused,"baseline source restored"):self.start(hook=fail)
        self.assert_extras_unchanged(metadata);self.assert_reviewed_source(self.baseline)

    def test_preserve_interrupted_apply_and_rollback_are_recoverable(self):
        metadata=self.make_extras()
        def crash(e,n):
            if e=="after_replace" and n=="server.py":raise Crash()
        with self.assertRaises(Crash):self.start(hook=crash)
        self.updater=u.Updater(self.root,self.bundle,preserve_extra_files=list(metadata))
        self.assertEqual(self.updater.inspect()["status"],"mixed")
        def crash_rollback(e,n):
            if e=="after_remove":raise Crash()
        with self.assertRaises(Crash):self.start("rollback",crash_rollback)
        self.updater=u.Updater(self.root,self.bundle,preserve_extra_files=list(metadata))
        self.assertEqual(self.start("rollback")["status"],"rolled_back")
        self.assert_extras_unchanged(metadata);self.assert_reviewed_source(self.baseline)

    def test_preserve_interrupted_recovery_never_rebaselines_changed_extra(self):
        self.make_extras()
        def crash(e,n):
            if e=="after_replace":raise Crash()
        with self.assertRaises(Crash):self.start(hook=crash)
        (self.app/"legacy-stage.bin").write_bytes(b"changed after crash")
        self.updater=u.Updater(self.root,self.bundle,preserve_extra_files=["legacy-stage.bin"])
        with self.assertRaisesRegex(u.Refused,"recovery metadata changed"):self.updater.inspect()

    def test_preserve_partial_backup_never_rebaselines_changed_extra(self):
        self.make_extras()
        original=self.updater.write_new
        def crash(fd,name,data,mode=0o600):
            original(fd,name,data,mode)
            if name=="extras.json":raise Crash()
        self.updater.write_new=crash
        with self.assertRaises(Crash):self.start()
        self.assert_reviewed_source(self.baseline)
        (self.app/"legacy-stage.bin").write_bytes(b"changed after partial backup")
        again=u.Updater(self.root,self.bundle,preserve_extra_files=["legacy-stage.bin"])
        with self.assertRaisesRegex(u.Refused,"recovery metadata changed"):again.inspect()

    def test_preserve_incomplete_metadata_write_refuses_safely(self):
        self.make_extras()
        original=u.os.write
        def partial(fd,data):
            original(fd,data[:max(1,len(data)//2)])
            raise Crash()
        with mock.patch.object(u.os,"write",partial):
            with self.assertRaises(Crash):self.start()
        self.assert_reviewed_source(self.baseline)
        again=u.Updater(self.root,self.bundle,preserve_extra_files=["legacy-stage.bin"])
        with self.assertRaisesRegex(u.Refused,"Incomplete preserved-extra metadata"):again.inspect()

    def test_preserve_durable_metadata_exists_before_any_source_write(self):
        metadata=self.make_extras()
        def check(e,n):
            if e=="before_replace":
                self.assertEqual(json.loads((self.transaction/"extras.json").read_bytes()),{"files":metadata})
                self.assertTrue((self.transaction/"ready.json").exists())
        self.start(hook=check)
        self.assert_extras_unchanged(metadata)

    def test_preserve_corrupted_metadata_refuses_without_touching_source(self):
        self.make_extras();self.start()
        (self.transaction/"extras.json").write_bytes(b"bad")
        with self.assertRaisesRegex(u.Refused,"recovery metadata changed"):self.updater.inspect()
        self.assert_reviewed_source(self.payload)

    def test_empty_allowlist_accepts_prior_no_extras_recovery(self):
        self.start();(self.transaction/"extras.json").unlink()
        self.assertEqual(self.updater.inspect()["status"],"payload")
        self.assertEqual(self.start("rollback")["status"],"rolled_back")
        self.assert_source(self.baseline)

    def test_source_mismatch_reports_only_fixed_hashes_and_modes(self):
        data=b"PRIVATE CHANGED SOURCE CONTENT"
        (self.app/"server.py").write_bytes(data)
        with self.assertRaises(u.SourceMismatch) as caught:self.updater.inspect()
        result=caught.exception.report
        self.assertEqual(result["status"],"refused")
        self.assertFalse(result["source_verified"])
        self.assertTrue(result["diagnosis_complete"])
        self.assertNotIn("source_fingerprint",result)
        self.assertEqual(result["mismatch_count"],1)
        row=result["mismatches"][0]
        self.assertEqual(row["file"],"server.py")
        self.assertEqual(row["expected_baseline"],self.manifest["baseline"]["files"]["server.py"])
        self.assertEqual(row["expected_payload"],self.manifest["payload"]["files"]["server.py"])
        self.assertEqual(row["observed"],{"sha256":u.digest(data),"mode":0o644})
        self.assertNotIn("PRIVATE",json.dumps(result))
        self.assertNotIn("export.py",json.dumps(result))
        self.assertFalse((self.root/u.STORE).exists())

    def test_source_mode_mismatch_does_not_accept_baseline_hash(self):
        (self.app/"server.py").chmod(0o755)
        with self.assertRaises(u.SourceMismatch) as caught:self.updater.inspect()
        row=caught.exception.report["mismatches"][0]
        self.assertEqual(row["observed"]["sha256"],row["expected_baseline"]["sha256"])
        self.assertEqual(row["observed"]["mode"],0o755)
        self.assertEqual(row["expected_baseline"]["mode"],0o644)
        self.assertFalse((self.root/u.STORE).exists())

    def test_source_mismatch_never_reads_extras_or_writes(self):
        metadata=self.make_extras()
        (self.app/"server.py").write_bytes(b"UNREVIEWED SOURCE")
        original=u.os.open
        def no_extra(path,*args,**kwargs):
            self.assertNotIn(path,metadata)
            return original(path,*args,**kwargs)
        with mock.patch.object(u.os,"open",no_extra), \
             mock.patch.object(u.os,"write",side_effect=AssertionError("write")), \
             mock.patch.object(u.os,"mkdir",side_effect=AssertionError("mkdir")), \
             mock.patch.object(u.os,"unlink",side_effect=AssertionError("unlink")), \
             mock.patch.object(u,"rename_atomic",side_effect=AssertionError("rename")):
            with self.assertRaises(u.SourceMismatch) as caught:self.updater.inspect()
        self.assertNotIn("legacy-stage.bin",json.dumps(caught.exception.report))
        self.assert_extras_unchanged(metadata)
        self.assertFalse((self.root/u.STORE).exists())

    def test_source_mismatch_guard_rejects_raced_snapshot(self):
        (self.app/"server.py").write_bytes(b"first change")
        original=self.updater.classify
        def race(current):
            try:return original(current)
            finally:(self.app/"server.py").write_bytes(b"second change")
        with mock.patch.object(self.updater,"classify",race):
            with self.assertRaises(u.Refused) as caught:self.updater.inspect()
        self.assertNotIsInstance(caught.exception,u.SourceMismatch)
        self.assertFalse((self.root/u.STORE).exists())


    def test_source_mismatch_report_is_bounded_with_explicit_omission(self):
        item=(b"different",(self.app/"server.py").stat())
        names=[("name-%03d-"%i)+"x"*100+".py" for i in range(100)]
        self.updater.names=names
        self.updater.base={n:{"sha256":"0"*64,"mode":0o644} for n in names}
        self.updater.target={n:{"sha256":"1"*64,"mode":0o644} for n in names}
        report=self.updater.mismatch_report({n:item for n in names})
        self.assertLessEqual(len(json.dumps(report,sort_keys=True,ensure_ascii=True).encode("ascii")),u.DIAGNOSTIC_REPORT_BYTES)
        self.assertFalse(report["diagnosis_complete"])
        self.assertEqual(report["mismatch_count"],100)
        self.assertEqual(report["reported_mismatches"]+report["omitted_mismatches"],100)

    def test_source_mismatch_does_not_change_apply_refusal(self):
        fingerprint=self.updater.inspect()["source_fingerprint"]
        (self.app/"server.py").write_bytes(b"modified source")
        with self.assertRaises(u.Refused):self.updater.run("apply",fingerprint)
        self.assertFalse((self.root/u.STORE).exists())
        self.assertEqual((self.app/"server.py").read_bytes(),b"modified source")
























if __name__=="__main__":unittest.main()
