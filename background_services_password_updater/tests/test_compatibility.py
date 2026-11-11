"""Synthetic independent-pin, preservation, refusal and disclosure regressions."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
import bounded as b
import compatibility as c
import updater as u

VERIFIER='ab'*32
BASE=(SOURCE/'bundle/baseline-config.blob').read_bytes()
PRIVATE_BASE=c.transform(BASE,VERIFIER)
PIN=u.digest(PRIVATE_BASE)  # Synthetic independent fixture, never a live value.


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.outer=Path(self.temp.name);self.root=self.outer/'local_apps';self.app=self.root/'source-folder'
        self.app.mkdir(parents=True);self.bundle=SOURCE/'bundle'
        self.public=b.BoundedUpdater(self.root,self.bundle)
        self.target=c.transform(self.public.payload['config.json'],VERIFIER)
        for name,data in self.public.payload.items():(self.app/name).write_bytes(data)
        (self.app/'config.json').write_bytes(self.target)
        self.opts={'private_baseline_config_sha256':PIN}

    def worker(self,pin=PIN,extras=None):return b.BoundedUpdater(self.root,self.bundle,extras,pin)
    def tree(self):
        return {str(p.relative_to(self.outer)):(u.extra_metadata(p.lstat()),p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in self.outer.rglob('*')}
    def refuse(self,fn):
        before=self.tree()
        with self.assertRaises((u.Refused,OSError,TypeError)) as caught:fn()
        self.assertEqual(before,self.tree())
        self.assertNotIn(VERIFIER,str(caught.exception));self.assertNotIn(PIN,str(caught.exception))
        return caught.exception
    def private_free(self,result):
        out=json.dumps(result)
        for private in (VERIFIER,PIN,u.digest(self.target),u.digest((self.app/'config.json').read_bytes())):
            self.assertNotIn(private,out)
        return out

    def test_exact_transform_matches_historical_serializer(self):
        cfg=json.loads(BASE);cfg['options']['transcript_export_enabled']=True
        cfg['options']['transcript_export_token_sha256']=VERIFIER
        self.assertEqual(PRIVATE_BASE,(json.dumps(cfg,indent=2)+'\n').encode())

    def test_private_payload_exact_and_no_writes(self):
        before=self.tree();worker=self.worker();result=worker.inspect()
        self.assertEqual(result['status'],'payload');self.assertTrue(result['historical_defaults_preserved'])
        self.assertTrue(result['private_pin_verified']);self.assertEqual(before,self.tree())
        self.private_free(result)
        self.assertEqual(result['bundle_sha256'],b.BUNDLE_SHA256)
        self.assertNotIn(worker.bundle_id,json.dumps(result))

    def test_all_existing_defaults_preserved_and_only_config_hashes_change(self):
        worker=self.worker();old=json.loads(PRIVATE_BASE);new=json.loads(worker.payload['config.json'])
        self.assertEqual(len(old['options']),9);self.assertEqual(len(new['options']),13)
        for name,value in old['options'].items():self.assertEqual(new['options'][name],value)
        for phase in ('baseline','payload'):
            for name,info in self.public.manifest[phase]['files'].items():
                if name!='config.json':self.assertEqual(worker.manifest[phase]['files'][name],info)
        for name,data in self.public.payload.items():
            if name!='config.json':self.assertEqual(worker.payload[name],data)
        self.assertEqual(worker.base['config.json'],{'sha256':PIN,'mode':0o644})
        self.assertEqual(worker.changed,self.public.changed)

    def test_public_bundle_on_disk_never_customized(self):
        before={str(p):p.read_bytes() for p in self.bundle.rglob('*') if p.is_file()}
        self.worker().inspect()
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.bundle.rglob('*') if p.is_file()})

    def test_empty_pin_still_refuses_private_source_and_redacts_hashes(self):
        err=self.refuse(lambda:self.public.inspect())
        self.assertIsInstance(err,u.SourceMismatch);self.private_free(err.report)
        self.assertTrue(err.report['hashes_redacted']);self.assertNotIn('source_fingerprint',err.report)

    def test_invalid_pin_types_or_shapes_refuse(self):
        for pin in (None,True,42,[],{},'0'*63,'0'*65,'F'*64,'PRIVATE_VALUE',' '+PIN):
            with self.subTest(type=type(pin).__name__):self.refuse(lambda:self.worker(pin))

    def test_different_wellformed_pin_refuses(self):self.refuse(lambda:self.worker('0'*64))

    def test_public_source_cannot_use_private_pin(self):
        (self.app/'config.json').write_bytes(self.public.payload['config.json'])
        self.refuse(self.worker)

    def test_wrong_existing_verifier_cannot_match_pin(self):
        (self.app/'config.json').write_bytes(c.transform(self.public.payload['config.json'],'cd'*32))
        self.refuse(self.worker)

    def test_shape_alone_never_authorizes_arbitrary_baseline_hash(self):
        cfg=json.loads(PRIVATE_BASE);cfg['homeassistant_api']=True
        wrong=(json.dumps(cfg,indent=2)+'\n').encode();(self.app/'config.json').write_bytes(wrong)
        self.refuse(lambda:self.worker(u.digest(wrong)))

    def test_unknown_schema_options_and_privilege_changes_refuse(self):
        cases=[]
        for scope in (None,'options','schema'):
            cfg=json.loads(self.target);(cfg if scope is None else cfg[scope])['PRIVATE_UNKNOWN_KEY']='PRIVATE_UNKNOWN_VALUE';cases.append(cfg)
        for key in json.loads(self.target):
            if key in ('slug','options'):continue
            cfg=json.loads(self.target);cfg[key]='PRIVATE_CHANGED_VALUE';cases.append(cfg)
        for cfg in cases:
            (self.app/'config.json').write_bytes((json.dumps(cfg,indent=2)+'\n').encode());self.refuse(self.worker)

    def test_every_other_option_default_change_refuses(self):
        for key in json.loads(self.target)['options']:
            if key in ('transcript_export_enabled','transcript_export_token_sha256'):continue
            cfg=json.loads(self.target);cfg['options'][key]='PRIVATE_CHANGED_VALUE'
            (self.app/'config.json').write_bytes((json.dumps(cfg,indent=2)+'\n').encode());self.refuse(self.worker)

    def test_duplicate_keys_and_formatting_refuse(self):
        for raw in (self.target+b'\n',json.dumps(json.loads(self.target)).encode(),
                    self.target.replace(b'"version": "0.2.7"',b'"version": "0.2.7", "version": "0.2.7"')):
            (self.app/'config.json').write_bytes(raw);self.refuse(self.worker)

    def test_bool_number_and_nonfinite_confusions_refuse(self):
        for key,value in (('transcript_export_enabled',1),('transcript_export_token_sha256',True),('whisper_port',10300.0)):
            cfg=json.loads(self.target);cfg['options'][key]=value
            (self.app/'config.json').write_bytes((json.dumps(cfg,indent=2)+'\n').encode());self.refuse(self.worker)
        for raw in (b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e999}',b'[]',b'{"x":1,"x":2}'):
            with self.assertRaises(u.Refused):c.strict_json(raw)

    def test_strict_parser_bounded(self):
        for raw in (b' '*(c.MAX_CONFIG_BYTES+1),json.dumps({'x':list(range(c.MAX_NODES+1))}).encode(),
                    json.dumps({'x':[[[[[[[[[0]]]]]]]]]}).encode()):
            with self.assertRaises(u.Refused):c.strict_json(raw)

    def test_target_mode_stays_exact(self):
        for mode in (0o400,0o600,0o666,0o755):
            (self.app/'config.json').chmod(mode);self.refuse(self.worker)

    def test_target_links_and_size_refuse(self):
        p=self.app/'config.json';os.link(p,self.outer/'hardlink');self.refuse(self.worker)
        (self.outer/'hardlink').unlink();p.unlink();p.symlink_to(self.bundle/'baseline-config.blob');self.refuse(self.worker)
        p.unlink();p.write_bytes(b' '*(c.MAX_CONFIG_BYTES+1));self.refuse(self.worker)

    def test_all_other_source_hash_and_mode_checks_remain(self):
        worker=self.worker();p=self.app/'server.py';raw=p.read_bytes()
        p.write_bytes(raw+b'\n');err=self.refuse(worker.inspect)
        self.private_free(err.report);p.write_bytes(raw);p.chmod(0o600);self.refuse(worker.inspect)

    def test_fresh_fingerprint_and_both_attestations_remain(self):
        worker=self.worker();fp=worker.inspect()['source_fingerprint']
        for kwargs in ({},{'maintenance_confirmed':True},{'rollback_security_loss_confirmed':True}):
            self.refuse(lambda:worker.run('apply',fp,**kwargs))
        self.refuse(lambda:worker.run('apply','0'*64,maintenance_confirmed=True,rollback_security_loss_confirmed=True))

    def test_safe_extra_content_never_read(self):
        p=self.app/'.private-extra.tgz';p.write_bytes(b'PRIVATE_EXTRA_CONTENT');p.chmod(0o600)
        original=u.os.open
        def guard(path,*args,**kwargs):
            self.assertNotEqual(path,'.private-extra.tgz');return original(path,*args,**kwargs)
        before=self.tree()
        with mock.patch.object(u.os,'open',guard):result=self.worker(extras=['.private-extra.tgz']).inspect()
        self.assertEqual(result['preserved_extra_files'],1);self.assertEqual(before,self.tree())
        self.refuse(lambda:self.worker().inspect())

    def test_constructor_uses_readonly_flags_and_no_write_syscalls(self):
        original=u.os.open
        def guard(path,flags,*args,**kwargs):
            self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY)
            self.assertFalse(flags&(os.O_TRUNC|os.O_CREAT|os.O_APPEND));return original(path,flags,*args,**kwargs)
        with mock.patch.object(u.os,'open',guard),mock.patch.object(u.os,'write',side_effect=AssertionError('write')),\
             mock.patch.object(u.os,'chmod',side_effect=AssertionError('chmod')),mock.patch.object(u.os,'rename',side_effect=AssertionError('rename')):
            self.worker().inspect()

    def test_source_change_after_compatibility_does_not_change_baseline(self):
        worker=self.worker();(self.app/'config.json').write_bytes(c.transform(self.public.payload['config.json'],'cd'*32))
        self.refuse(worker.inspect);self.assertEqual(worker.base['config.json']['sha256'],PIN)

    def test_config_race_during_compatibility_refuses(self):
        real=c.read_source_config;calls=0
        def race(*args):
            nonlocal calls
            value=real(*args);calls+=1
            if calls==1:(self.app/'config.json').write_bytes(PRIVATE_BASE)
            return value
        with mock.patch.object(c,'read_source_config',race):
            with self.assertRaisesRegex(u.Refused,'changed'):self.worker()

    def test_discovery_limit_and_deadline_remain(self):
        with mock.patch.object(c,'SECONDS',-1):self.refuse(self.worker)
        for i in range(c.DISCOVERY_LIMIT):(self.root/f'extra{i}').touch()
        self.refuse(self.worker)

    def test_cli_never_emits_private_pin_verifier_or_config_hash(self):
        p=self.outer/'options.json';p.write_text(json.dumps(self.opts));out=io.StringIO();real=b.dispatch
        with mock.patch.object(b,'OPTIONS',str(p)),mock.patch.object(sys,'argv',['bounded.py']),\
             mock.patch.object(b,'dispatch',lambda opts:real(opts,self.root,self.bundle)),contextlib.redirect_stdout(out):
            self.assertEqual(b.main(),0)
        self.assertEqual(len(out.getvalue().splitlines()),1);self.private_free(json.loads(out.getvalue()))

    def test_cli_wrong_pin_error_is_content_free(self):
        p=self.outer/'options.json';p.write_text(json.dumps({'private_baseline_config_sha256':'cd'*32}))
        out=io.StringIO();real=b.dispatch
        with mock.patch.object(b,'OPTIONS',str(p)),mock.patch.object(sys,'argv',['bounded.py']),\
             mock.patch.object(b,'dispatch',lambda opts:real(opts,self.root,self.bundle)),contextlib.redirect_stdout(out):
            self.assertEqual(b.main(),1)
        self.assertNotIn('cd'*32,out.getvalue());self.private_free(json.loads(out.getvalue()))


if __name__=='__main__':unittest.main()
