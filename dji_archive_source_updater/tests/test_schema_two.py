"""Repeat applicable legacy guards on schema 2 and test exact private baselines."""
import json
import os
import stat
import test_updater as legacy

u = legacy.u


class SchemaTwoTests(legacy.UpdaterTests):
    def setUp(self):
        super().setUp()
        cfg = {"slug": "dji_audio_archive", "version": "0.2.4", "options": {
            "audio_source": "PUBLIC_PLACEHOLDER", "transcript_export_enabled": False,
            "transcript_export_token_sha256": ""}}
        self.baseline["config.json"] = self.serialize(cfg)
        cfg["version"] = "0.2.5"
        self.payload["config.json"] = self.serialize(cfg)
        self.manifest["schema"] = 2
        for phase, contents, version in (("baseline", self.baseline, "0.2.4"), ("payload", self.payload, "0.2.5")):
            self.manifest[phase]["version"] = version
            self.manifest[phase]["files"]["config.json"]["sha256"] = u.digest(contents["config.json"])
        (self.app / "config.json").write_bytes(self.baseline["config.json"])
        self.payload_path("config.json").write_bytes(self.payload["config.json"])
        (self.bundle / "baseline-config.blob").write_bytes(self.baseline["config.json"])
        self.save_manifest()
        self.updater = u.Updater(self.root, self.bundle)

    @staticmethod
    def serialize(value):
        return (json.dumps(value, indent=2) + "\n").encode()

    def customize_source(self, verifier="a1" * 32):
        cfg = json.loads(self.baseline["config.json"])
        cfg["options"]["transcript_export_enabled"] = True
        cfg["options"]["transcript_export_token_sha256"] = verifier
        self.baseline["config.json"] = self.serialize(cfg)
        (self.app / "config.json").write_bytes(self.baseline["config.json"])
        self.updater = u.Updater(self.root, self.bundle, verifier)
        return verifier

    def test_private_defaults_only_two_fields_and_exact_rollback(self):
        public_base = (self.bundle / "baseline-config.blob").read_bytes()
        public_target = self.payload_path("config.json").read_bytes()
        value = self.customize_source()
        for old, new in ((public_base, self.baseline["config.json"]), (public_target, self.updater.payload["config.json"])):
            before, after = json.loads(old), json.loads(new)
            self.assertIs(after["options"].pop("transcript_export_enabled"), True)
            self.assertEqual(after["options"].pop("transcript_export_token_sha256"), value)
            before["options"].pop("transcript_export_enabled")
            before["options"].pop("transcript_export_token_sha256")
            self.assertEqual(before, after)
        report = self.updater.inspect()
        self.assertEqual(report["baseline_version"], "0.2.4")
        self.assertEqual(report["payload_version"], "0.2.5")
        self.assertEqual(report["baseline_config_sha256"], u.digest(self.baseline["config.json"]))
        self.assertNotIn(value, json.dumps(report))
        self.assertTrue(report["private_export_defaults"])
        self.assertEqual(self.start()["status"], "applied")
        self.assertEqual(self.start()["status"], "already_applied")
        for verifier in ("", "b2" * 32):
            with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle, verifier).inspect()
        self.updater = u.Updater(self.root, self.bundle, value)
        self.assertEqual(self.start("rollback")["status"], "rolled_back")
        self.assertEqual(self.start("rollback")["status"], "already_baseline")
        self.assert_source(self.baseline)
        self.assertEqual((self.bundle / "baseline-config.blob").read_bytes(), public_base)
        self.assertEqual(self.payload_path("config.json").read_bytes(), public_target)

    def test_private_digest_bound_to_fingerprint(self):
        self.customize_source()
        fingerprint = self.updater.inspect()["source_fingerprint"]
        for verifier in ("", "22" * 32):
            other = u.Updater(self.root, self.bundle, verifier)
            self.assertNotEqual(self.updater.bundle_id, other.bundle_id)
            with self.assertRaises(u.Refused): other.inspect()
            with self.assertRaises(u.Refused): other.run("apply", fingerprint)
        self.assertFalse((self.root / u.STORE).exists())
        self.assert_source(self.baseline)

    def test_schema_two_does_not_activate_export_on_public_baseline(self):
        other = u.Updater(self.root, self.bundle, "11" * 32)
        with self.assertRaises(u.Refused): other.inspect()
        with self.assertRaises(u.Refused): other.run("apply", self.updater.inspect()["source_fingerprint"])
        self.assertFalse((self.root / u.STORE).exists())
        self.assert_source(self.baseline)

    def test_schema_two_no_digest_roundtrip_retains_disabled_export(self):
        self.start()
        self.assert_source(self.payload)
        cfg = json.loads((self.app / "config.json").read_bytes())
        self.assertIs(cfg["options"]["transcript_export_enabled"], False)
        self.assertEqual(cfg["options"]["transcript_export_token_sha256"], "")
        self.start("rollback")
        self.assert_source(self.baseline)

    def test_schema_two_rejects_config_tamper_without_learning(self):
        self.customize_source()
        original = self.baseline["config.json"]
        changes = [original + b"\n", json.dumps(json.loads(original)).encode()]
        for field, value in (("audio_source", "unexpected"), ("transcript_export_enabled", False),
                             ("transcript_export_token_sha256", "b2" * 32)):
            changed = json.loads(original)
            changed["options"][field] = value
            changes.append(self.serialize(changed))
        for changed in changes:
            with self.subTest(changed=u.digest(changed)):
                (self.app / "config.json").write_bytes(changed)
                with self.assertRaises(u.Refused): self.updater.inspect()
                self.assertEqual((self.app / "config.json").read_bytes(), changed)
                self.assertFalse((self.root / u.STORE).exists())
        (self.app / "config.json").write_bytes(original)
        (self.app / "config.json").chmod(0o666)
        with self.assertRaises(u.Refused): self.updater.inspect()
        self.assertFalse((self.root / u.STORE).exists())

    def test_schema_two_template_content_mode_links_and_missing_refused(self):
        p = self.bundle / "baseline-config.blob"
        original = p.read_bytes()
        p.write_bytes(b"tamper")
        with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle)
        p.write_bytes(original); p.chmod(0o600)
        with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle)
        p.chmod(0o644)
        link = self.outside / "template-link"
        os.link(p, link)
        with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle)
        link.unlink(); p.unlink()
        with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle)
        p.symlink_to(self.app / "config.json")
        with self.assertRaises((u.Refused, OSError)): u.Updater(self.root, self.bundle)
        self.assert_source(self.baseline)

    def test_schema_two_template_identity_and_public_defaults_refused(self):
        p = self.bundle / "baseline-config.blob"
        original = p.read_bytes()
        changes = []
        for field, value in (("slug", "other"), ("version", "0.2.3")):
            changed = json.loads(original); changed[field] = value; changes.append(changed)
        for field, value in (("transcript_export_enabled", True), ("transcript_export_token_sha256", "a1" * 32)):
            changed = json.loads(original); changed["options"][field] = value; changes.append(changed)
        for cfg in changes:
            p.write_bytes(self.serialize(cfg))
            self.manifest["baseline"]["files"]["config.json"]["sha256"] = u.digest(p.read_bytes())
            self.save_manifest()
            for verifier in ("", "a1" * 32):
                with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle, verifier)

    def test_schema_two_extra_bundle_file_refused(self):
        (self.bundle / "unexpected").write_bytes(b"no")
        with self.assertRaises(u.Refused): u.Updater(self.root, self.bundle)

    def test_schema_two_normalization_unavailable_and_no_source_mode_change(self):
        for mode in (0o644, 0o666):
            for n in self.baseline: (self.app / n).chmod(mode)
            with self.assertRaisesRegex(u.Refused, "unavailable"): self.updater.inspect_permissions()
            with self.assertRaisesRegex(u.Refused, "unavailable"):
                self.updater.normalize_permissions("perm:" + "0" * 64)
            self.assertTrue(all(stat.S_IMODE((self.app / n).stat().st_mode) == mode for n in self.baseline))
            self.assertFalse((self.root / u.STORE).exists())

    def test_schema_two_private_crash_every_replace_recovers_to_exact_baseline(self):
        value = self.customize_source()
        expected = self.updater.payload.copy()
        for boundary in ("before_replace", "after_replace"):
            for filename in self.updater.changed:
                with self.subTest(boundary=boundary, filename=filename):
                    def crash(event, name):
                        if event == boundary and name == filename: raise legacy.Crash()
                    with self.assertRaises(legacy.Crash): self.start(hook=crash)
                    self.updater = u.Updater(self.root, self.bundle, value)
                    phase = self.updater.inspect()["status"]
                    if phase == "mixed": self.assertEqual(self.start()["status"], "rolled_back")
                    elif phase == "payload": self.start("rollback")
                    else:
                        self.start(); self.assert_source(expected); self.start("rollback")
                    self.assert_source(self.baseline)

    def test_schema_two_private_rollback_interruption_recovers(self):
        value = self.customize_source()
        self.start()
        def crash(event, name):
            if event == "after_remove": raise legacy.Crash()
        with self.assertRaises(legacy.Crash): self.start("rollback", crash)
        self.updater = u.Updater(self.root, self.bundle, value)
        self.assertEqual(self.start("rollback")["status"], "rolled_back")
        self.assert_source(self.baseline)

    def test_schema_two_private_failure_restores_exact_baseline(self):
        self.customize_source()
        def fail(event, name):
            if event == "after_replace" and name == "server.py": raise RuntimeError("failure")
        with self.assertRaisesRegex(u.Refused, "baseline source restored"): self.start(hook=fail)
        self.assert_source(self.baseline)

    def test_schema_two_private_extras_and_old_transactions_preserved(self):
        value = self.customize_source()
        metadata = self.make_extras()
        self.updater = u.Updater(self.root, self.bundle, value, list(metadata))
        store = self.root / u.STORE
        store.mkdir(mode=0o700)
        old = store / ("0" * 64)
        old.mkdir(mode=0o700)
        (old / "original-evidence.blob").write_bytes(b"old immutable recovery")
        old_stat = u.extra_metadata((old / "original-evidence.blob").stat())
        self.start()
        self.updater = u.Updater(self.root, self.bundle, value, list(metadata))
        self.start("rollback")
        self.assert_reviewed_source(self.baseline)
        self.assert_extras_unchanged(metadata)
        self.assertEqual((old / "original-evidence.blob").read_bytes(), b"old immutable recovery")
        self.assertEqual(u.extra_metadata((old / "original-evidence.blob").stat()), old_stat)


# All 19 original permission-operation regressions still run in test_updater.
# The schema 2 package has no normalization operation, tested as refusal above.
for _name in dir(SchemaTwoTests):
    if _name.startswith("test_permission_"):
        setattr(SchemaTwoTests, _name, None)
