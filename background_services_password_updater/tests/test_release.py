"""Pinned production entrypoint tests. Every fixture is synthetic/local."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import bounded as b
import updater as u
import test_updater as legacy


class PublicSchemaTwoTests(legacy.UpdaterTests):
    def setUp(self):
        super().setUp()
        for phase, contents, version in (('baseline', self.baseline, '0.2.6'),
                                          ('payload', self.payload, '0.2.7')):
            cfg = {'slug':'dji_audio_archive', 'version':version, 'options':{
                'transcript_export_enabled':False, 'transcript_export_token_sha256':''}}
            contents['config.json'] = (json.dumps(cfg, indent=2)+'\n').encode()
            self.manifest[phase]['version'] = version
            self.manifest[phase]['files']['config.json']['sha256'] = legacy.u.digest(contents['config.json'])
        self.manifest['schema'] = 2
        (self.app/'config.json').write_bytes(self.baseline['config.json'])
        self.payload_path('config.json').write_bytes(self.payload['config.json'])
        (self.bundle/'baseline-config.blob').write_bytes(self.baseline['config.json'])
        self.save_manifest()
        self.updater = legacy.u.Updater(self.root,self.bundle)

    def test_template_corruption_refused(self):
        (self.bundle/'baseline-config.blob').write_bytes(b'changed')
        with self.assertRaises(legacy.u.Refused):legacy.u.Updater(self.root,self.bundle)

    def test_unexpected_template_bundle_entry_refused(self):
        (self.bundle/'unexpected').write_bytes(b'no')
        with self.assertRaises(legacy.u.Refused):legacy.u.Updater(self.root,self.bundle)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.outer=Path(self.temp.name);self.root=self.outer/'local_apps'
        self.app=self.root/'original-folder';self.app.mkdir(parents=True)
        self.bundle=SOURCE/'bundle'
        self.worker=b.BoundedUpdater(self.root,self.bundle)
        for name,data in self.worker.payload.items():(self.app/name).write_bytes(data)
        self.opts={'mode':'apply','expected_fingerprint':self.worker.inspect()['source_fingerprint'],
                   'maintenance_confirmed':True,'rollback_security_loss_confirmed':True}

    def tree(self):
        return {str(p.relative_to(self.outer)):(u.extra_metadata(p.lstat()),p.read_bytes() if p.is_file() else None)
                for p in self.outer.rglob('*')}

    def refused_no_write(self,fn):
        before=self.tree()
        with self.assertRaises((u.Refused,OSError,TypeError)):fn()
        self.assertEqual(before,self.tree())

    def test_pin_identity_and_empty_public_defaults(self):
        r=self.worker.inspect()
        self.assertEqual(r['installed_target'],'local_dji_audio_archive')
        self.assertEqual(r['bundle_sha256'],b.BUNDLE_SHA256)
        self.assertEqual((r['baseline_version'],r['payload_version']),('0.2.6','0.2.7'))
        self.assertFalse(r['runtime_data_access']);self.assertFalse(r['maintenance_state_verified_by_helper'])
        cfg=json.loads(self.worker.payload['config.json'])
        for n in ('app_password_verifier','app_https_hostname','app_ingress_core_peer','app_enrollment_approval'):
            self.assertEqual(cfg['options'][n],'')

    def test_inspect_default_is_read_only(self):
        before=self.tree();self.assertEqual(b.dispatch({},self.root,self.bundle)['status'],'payload')
        self.assertEqual(before,self.tree())

    def test_both_attestations_required_before_write(self):
        for name in ('maintenance_confirmed','rollback_security_loss_confirmed'):
            opts=self.opts.copy();opts[name]=False
            self.refused_no_write(lambda:b.dispatch(opts,self.root,self.bundle))
        self.refused_no_write(lambda:self.worker.run('apply',self.opts['expected_fingerprint']))

    def test_string_and_numeric_attestations_rejected(self):
        for n in ('maintenance_confirmed','rollback_security_loss_confirmed'):
            for value in ('true',1,None,[],{}):
                opts=self.opts.copy();opts[n]=value
                self.refused_no_write(lambda:b.dispatch(opts,self.root,self.bundle))

    def test_empty_and_stale_fingerprints_refused(self):
        for value in ('','0'*64,'diag:'+'0'*64):
            opts=self.opts.copy();opts['expected_fingerprint']=value
            self.refused_no_write(lambda:b.dispatch(opts,self.root,self.bundle))

    def test_noop_already_payload_is_read_only(self):
        before=self.tree();self.assertEqual(b.dispatch(self.opts,self.root,self.bundle)['status'],'already_applied')
        self.assertEqual(before,self.tree())

    def test_private_source_config_variant_refuses_without_logging_values(self):
        cfg=json.loads((self.app/'config.json').read_bytes())
        cfg['options']['transcript_export_enabled']=True
        cfg['options']['transcript_export_token_sha256']='SYNTHETIC_PRIVATE_VALUE'
        (self.app/'config.json').write_text(json.dumps(cfg))
        before=self.tree()
        with self.assertRaises(u.SourceMismatch) as caught:self.worker.inspect()
        out=json.dumps(caught.exception.report)
        self.assertNotIn('SYNTHETIC_PRIVATE_VALUE',out);self.assertNotIn('source_fingerprint',out)
        self.assertEqual(before,self.tree())

    def test_unknown_option_and_legacy_digest_rejected(self):
        for name in ('transcript_export_token_sha256','baseline_hash_override','root','bundle','force'):
            opts=self.opts.copy();opts[name]='private-placeholder'
            self.refused_no_write(lambda:b.dispatch(opts,self.root,self.bundle))

    def test_legacy_permission_modes_not_exposed(self):
        for mode in ('inspect_permissions','normalize_permissions','rebuild','install'):
            self.refused_no_write(lambda:b.dispatch({'mode':mode},self.root,self.bundle))
        self.assertFalse(hasattr(self.worker,'normalize_permissions'))
        self.assertFalse(hasattr(u,'main'))

    def test_private_extra_default_refusal_and_allowlist_preservation(self):
        extra=self.app/'private-extra.bin';extra.write_bytes(b'UNREAD_SYNTHETIC_PRIVATE_EXTRA');extra.chmod(0o600)
        self.refused_no_write(self.worker.inspect)
        before=u.extra_metadata(extra.stat())
        worker=b.BoundedUpdater(self.root,self.bundle,['private-extra.bin'])
        original=u.os.open
        def no_extra(path,*a,**kw):
            self.assertNotEqual(path,'private-extra.bin');return original(path,*a,**kw)
        with mock.patch.object(u.os,'open',no_extra):r=worker.inspect()
        self.assertEqual(r['preserved_extra_files'],1)
        self.assertEqual(before,u.extra_metadata(extra.stat()))

    def test_release_pin_rejects_same_version_modified_bundle(self):
        other=self.outer/'bundle';shutil.copytree(self.bundle,other)
        m=json.loads((other/'manifest.json').read_bytes())
        p=other/'files'/self.worker.backup_name('server.py');p.write_bytes(p.read_bytes()+b'\n')
        m['payload']['files']['server.py']['sha256']=u.digest(p.read_bytes())
        (other/'manifest.json').write_bytes(u.canonical(m))
        with self.assertRaisesRegex(u.Refused,'pinned release'):b.BoundedUpdater(self.root,other)

    def test_release_refuses_old_version_bundle(self):
        other=self.outer/'bundle';shutil.copytree(self.bundle,other)
        m=json.loads((other/'manifest.json').read_bytes())
        cfg=json.loads((other/'baseline-config.blob').read_bytes());cfg['version']='0.2.5'
        (other/'baseline-config.blob').write_bytes(u.canonical(cfg))
        m['baseline']['version']='0.2.5';m['baseline']['files']['config.json']['sha256']=u.digest(u.canonical(cfg))
        (other/'manifest.json').write_bytes(u.canonical(m))
        with self.assertRaisesRegex(u.Refused,'only the reviewed'):b.BoundedUpdater(self.root,other)

    def test_cli_refuses_runtime_overrides_without_reading_options(self):
        out=io.StringIO()
        with mock.patch.object(sys,'argv',['bounded.py','--root','/elsewhere']),\
             mock.patch.object(Path,'read_bytes',side_effect=AssertionError('read')),\
             contextlib.redirect_stdout(out):self.assertEqual(b.main(),1)
        self.assertEqual(json.loads(out.getvalue())['status'],'refused')

    def test_cli_sanitizes_input_errors(self):
        options=self.outer/'options.json';options.write_bytes(b'BAD PRIVATE CONTENT')
        out=io.StringIO()
        with mock.patch.object(sys,'argv',['bounded.py']),mock.patch.object(b,'OPTIONS',str(options)),contextlib.redirect_stdout(out):
            self.assertEqual(b.main(),1)
        self.assertEqual(len(out.getvalue().splitlines()),1);self.assertNotIn('PRIVATE',out.getvalue())

    def test_cli_diagnostic_escaped_line(self):
        options=self.outer/'options.json';options.write_text('{"mode":"diagnose"}')
        (self.app/'odd\nfilename').touch()
        out=io.StringIO();real=b.dispatch
        with mock.patch.object(sys,'argv',['bounded.py']),mock.patch.object(b,'OPTIONS',str(options)),\
             mock.patch.object(b,'dispatch',lambda opts:real(opts,self.root,self.bundle)),contextlib.redirect_stdout(out):
            self.assertEqual(b.main(),0)
        self.assertEqual(len(out.getvalue().splitlines()),1)
        self.assertFalse(json.loads(out.getvalue())['source_verified'])

    def test_cli_mismatch_never_exposes_config_content(self):
        cfg=json.loads((self.app/'config.json').read_bytes());cfg['options']['private']='DO_NOT_EMIT'
        (self.app/'config.json').write_text(json.dumps(cfg))
        options=self.outer/'options.json';options.write_text('{"mode":"inspect"}')
        out=io.StringIO();real=b.dispatch
        with mock.patch.object(sys,'argv',['bounded.py']),mock.patch.object(b,'OPTIONS',str(options)),\
             mock.patch.object(b,'dispatch',lambda opts:real(opts,self.root,self.bundle)),contextlib.redirect_stdout(out):
            self.assertEqual(b.main(),1)
        self.assertEqual(len(out.getvalue().splitlines()),1)
        self.assertNotIn('DO_NOT_EMIT',out.getvalue());self.assertNotIn('source_fingerprint',out.getvalue())


if __name__=='__main__':unittest.main()
