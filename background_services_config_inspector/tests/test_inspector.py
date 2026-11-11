"""Synthetic-only privacy, exact-variant and filesystem guard regressions."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
import inspect_config as d
BASE=(SOURCE/'public-baseline.blob').read_bytes()
PRIVATE='a1'*32


def serialize(value):return (json.dumps(value,indent=2)+'\n').encode()
def private_variant():
    cfg=json.loads(BASE);cfg['options']['transcript_export_enabled']=True
    cfg['options']['transcript_export_token_sha256']=PRIVATE
    return cfg


class CompareTests(unittest.TestCase):
    def compare(self,value):
        result=d.compare(BASE,value if isinstance(value,bytes) else serialize(value))
        self.assertNotIn(PRIVATE,json.dumps(result));self.assertFalse(result['source_baseline_accepted_for_writes'])
        self.assertFalse(result['whole_source_verified']);self.assertTrue(result['diagnostic_only'])
        self.assertLessEqual(len(json.dumps(result).encode()),d.MAX_REPORT_BYTES)
        return result

    def test_public_exact(self):
        r=self.compare(BASE)
        self.assertTrue(r['public_bytes_exact']);self.assertTrue(r['public_semantics_exact'])
        self.assertTrue(r['non_options_exact']);self.assertTrue(r['other_option_defaults_exact'])
        self.assertFalse(r['legacy_export_variant_shape']);self.assertFalse(r['legacy_export_variant_bytes_exact'])
        self.assertTrue(all(x['matches_public'] and x['type_matches'] for x in r['known_fields']))

    def test_historical_exact_variant(self):
        r=self.compare(private_variant())
        self.assertTrue(r['legacy_export_variant_shape']);self.assertTrue(r['legacy_export_variant_bytes_exact'])
        self.assertTrue(r['non_options_exact']);self.assertTrue(r['other_option_defaults_exact'])
        self.assertFalse(r['public_bytes_exact']);self.assertFalse(r['public_semantics_exact'])
        differences={x['path'] for x in r['known_fields'] if not x['matches_public']}
        self.assertEqual(differences,{'/options','/options/transcript_export_enabled','/options/transcript_export_token_sha256'})

    def test_historical_semantics_not_enough_for_byte_match(self):
        for raw in (json.dumps(private_variant()).encode(),serialize(private_variant())+b'\n',
                    serialize(dict(reversed(list(private_variant().items()))))):
            with self.subTest(raw_size=len(raw)):
                r=self.compare(raw);self.assertTrue(r['legacy_export_variant_shape'])
                self.assertFalse(r['legacy_export_variant_bytes_exact'])

    def test_public_formatting_difference_not_private_variant(self):
        r=self.compare(json.dumps(json.loads(BASE)).encode())
        self.assertTrue(r['public_semantics_exact']);self.assertFalse(r['public_bytes_exact'])
        self.assertFalse(r['legacy_export_variant_shape'])

    def test_every_other_option_change_blocks_known_transform_without_values(self):
        for key in json.loads(BASE)['options']:
            if key in ('transcript_export_enabled','transcript_export_token_sha256'):continue
            cfg=private_variant();cfg['options'][key]='DO_NOT_EMIT_PRIVATE_VALUE'
            with self.subTest(key=key):
                r=self.compare(cfg);self.assertFalse(r['other_option_defaults_exact'])
                self.assertFalse(r['legacy_export_variant_shape']);self.assertNotIn('DO_NOT_EMIT',json.dumps(r))

    def test_each_schema_change_blocks_known_transform(self):
        for key in json.loads(BASE)['schema']:
            cfg=private_variant();cfg['schema'][key]='DO_NOT_EMIT_PRIVATE_SCHEMA'
            with self.subTest(key=key):
                r=self.compare(cfg);self.assertFalse(r['non_options_exact'])
                self.assertFalse(r['legacy_export_variant_shape']);self.assertNotIn('DO_NOT_EMIT',json.dumps(r))

    def test_security_or_other_top_level_change_blocks_known_transform(self):
        for key in json.loads(BASE):
            if key in ('options','slug','version','schema'):continue
            cfg=private_variant();cfg[key]='DO_NOT_EMIT_PRIVATE_VALUE'
            with self.subTest(key=key):
                r=self.compare(cfg);self.assertFalse(r['non_options_exact'])
                self.assertFalse(r['legacy_export_variant_shape']);self.assertNotIn('DO_NOT_EMIT',json.dumps(r))

    def test_unknown_key_names_and_values_never_emitted(self):
        for scope in ('root','options','schema'):
            cfg=private_variant();obj=cfg if scope=='root' else cfg[scope]
            obj['PRIVATE_SECRET_IN_KEY']='PRIVATE_SECRET_IN_VALUE'
            r=self.compare(cfg);self.assertTrue(r['unknown_keys_present'])
            self.assertFalse(r['legacy_export_variant_shape'])
            self.assertNotIn('PRIVATE_SECRET',json.dumps(r))

    def test_missing_public_keys_report_only_known_paths(self):
        for scope,key in (('root','host_network'),('options','language'),('schema','language')):
            cfg=private_variant();obj=cfg if scope=='root' else cfg[scope];del obj[key]
            r=self.compare(cfg);self.assertFalse(r['legacy_export_variant_shape'])
            path=('/' if scope=='root' else '/'+scope+'/')+key
            row=next(x for x in r['known_fields'] if x['path']==path)
            self.assertFalse(row['present']);self.assertEqual(row['observed_type'],'missing')

    def test_no_bool_integer_type_coercion(self):
        cfg=private_variant();cfg['homeassistant_api']=0
        r=self.compare(cfg);self.assertFalse(r['non_options_exact'])
        row=next(x for x in r['known_fields'] if x['path']=='/homeassistant_api')
        self.assertFalse(row['type_matches']);self.assertFalse(row['matches_public'])
        cfg=private_variant();cfg['options']['transcript_export_enabled']=1
        self.assertFalse(self.compare(cfg)['legacy_export_variant_shape'])

    def test_no_integer_float_type_coercion(self):
        cfg=private_variant();cfg['options']['whisper_port']=10300.0
        r=self.compare(cfg);self.assertFalse(r['other_option_defaults_exact'])
        self.assertFalse(r['legacy_export_variant_shape'])

    def test_invalid_digest_shape_never_matched_or_emitted(self):
        for value in ('A1'*32,'a'*63,'a'*65,'DO_NOT_EMIT_RAW_TOKEN','',None,True,{},[]):
            cfg=private_variant();cfg['options']['transcript_export_token_sha256']=value
            r=self.compare(cfg);self.assertFalse(r['legacy_export_variant_shape'])
            self.assertNotIn('DO_NOT_EMIT',json.dumps(r))

    def test_enabled_false_with_digest_is_not_known_variant(self):
        cfg=private_variant();cfg['options']['transcript_export_enabled']=False
        self.assertFalse(self.compare(cfg)['legacy_export_variant_shape'])

    def test_nonobject_options_do_not_crash_or_leak(self):
        for v in (None,'DO_NOT_EMIT',False,[],7):
            cfg=private_variant();cfg['options']=v
            r=self.compare(cfg);self.assertFalse(r['options_known_keys_exact'])
            self.assertFalse(r['legacy_export_variant_shape']);self.assertNotIn('DO_NOT_EMIT',json.dumps(r))

    def test_wrong_slug_or_version_refused(self):
        for k,v in (('slug','DO_NOT_EMIT'),('version','0.2.7'),('version',26)):
            cfg=private_variant();cfg[k]=v
            with self.assertRaises(d.Refused) as caught:self.compare(cfg)
            self.assertNotIn('DO_NOT_EMIT',str(caught.exception))

    def test_duplicate_root_and_nested_keys_refused(self):
        for raw in (b'{"slug":"dji_audio_archive","slug":"x"}',
                    b'{"options":{"PRIVATE_KEY":1,"PRIVATE_KEY":2}}'):
            with self.assertRaisesRegex(d.Refused,'Duplicate'):d.parse_config(raw)

    def test_invalid_json_and_constants_refused(self):
        for raw in (b'PRIVATE_INVALID',b'\xff',b'[]',b'null',b'{"private":NaN}',b'{"private":Infinity}',b'{"private":1e999}'):
            with self.assertRaises(d.Refused) as caught:d.parse_config(raw)
            self.assertNotIn('PRIVATE',str(caught.exception))

    def test_depth_and_node_caps(self):
        for obj in ({'nested':[[[[[[[[[0]]]]]]]]]}, {'nodes':list(range(d.MAX_NODES+1))}):
            with self.assertRaises(d.Refused):d.parse_config(json.dumps(obj).encode())

    def test_template_pin_mismatch_refused(self):
        with self.assertRaisesRegex(d.Refused,'template pin'):d.compare(BASE+b'\n',BASE)

    def test_output_paths_are_whitelisted_and_no_hashes(self):
        r=self.compare(private_variant());baseline=json.loads(BASE)
        allowed={'/'+k for k in baseline}|{'/'+scope+'/'+k for scope in ('options','schema') for k in baseline[scope]}
        self.assertEqual({x['path'] for x in r['known_fields']},allowed)
        out=json.dumps(r)
        for forbidden in (PRIVATE,hashlib.sha256(serialize(private_variant())).hexdigest(),d.PUBLIC_BASELINE_SHA256):
            self.assertNotIn(forbidden,out)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.outer=Path(self.temp.name);self.root=self.outer/'local_apps';self.app=self.root/'verified-by-slug'
        self.app.mkdir(parents=True);(self.app/'config.json').write_bytes(serialize(private_variant()))
        self.other=self.root/'other';self.other.mkdir();(self.other/'config.json').write_text('{"slug":"other","version":"1"}')
        self.template=self.outer/'public-baseline.blob';self.template.write_bytes(BASE)

    def run_inspect(self):return d.inspect_config(self.root,self.template)

    def snapshot(self):
        return {str(p.relative_to(self.outer)):(d.signature(p.lstat()),p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in self.outer.rglob('*')}

    def test_readonly_and_extras_never_opened(self):
        extra=self.app/'.private-extra.tgz';extra.write_bytes(b'PRIVATE_UNREAD_EXTRA')
        before=self.snapshot();original=d.os.open
        def opens(path,*args,**kw):
            self.assertNotEqual(path,'.private-extra.tgz')
            flags=args[0] if args else kw['flags']
            self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY)
            self.assertFalse(flags&(os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            return original(path,*args,**kw)
        with mock.patch.object(d.os,'open',opens),mock.patch.object(d.os,'write',side_effect=AssertionError('write')),\
             mock.patch.object(d.os,'mkdir',side_effect=AssertionError('mkdir')),mock.patch.object(d.os,'rename',side_effect=AssertionError('rename')),\
             mock.patch.object(d.os,'unlink',side_effect=AssertionError('unlink')),mock.patch.object(d.os,'chmod',side_effect=AssertionError('chmod')):
            r=self.run_inspect()
        self.assertTrue(r['legacy_export_variant_bytes_exact']);self.assertEqual(before,self.snapshot())

    def test_absent_or_duplicate_target(self):
        (self.other/'config.json').write_bytes(BASE)
        with self.assertRaisesRegex(d.Refused,'ambiguous'):self.run_inspect()
        (self.app/'config.json').write_text('{"slug":"not-target"}')
        (self.other/'config.json').write_text('{"slug":"other"}')
        with self.assertRaisesRegex(d.Refused,'absent'):self.run_inspect()

    def test_yaml_layout_refuses_without_opening_yaml(self):
        (self.other/'config.yaml').write_bytes(b'PRIVATE_YAML_CONTENT')
        original=d.os.open
        def opens(path,*a,**kw):
            self.assertNotEqual(path,'config.yaml');return original(path,*a,**kw)
        with mock.patch.object(d.os,'open',opens):
            with self.assertRaisesRegex(d.Refused,'YAML'):self.run_inspect()

    def test_symlink_source_rejected(self):
        p=self.app/'config.json';p.unlink();p.symlink_to(self.other/'config.json')
        with self.assertRaises((d.Refused,OSError)):self.run_inspect()

    def test_hardlink_source_rejected(self):
        os.link(self.app/'config.json',self.outer/'hardlink')
        with self.assertRaisesRegex(d.Refused,'single-link'):self.run_inspect()

    def test_nonregular_source_rejected_without_blocking(self):
        p=self.app/'config.json';p.unlink();os.mkfifo(p)
        with self.assertRaises(d.Refused):self.run_inspect()

    def test_symlinked_target_directory_rejected(self):
        moved=self.outer/'moved';self.app.rename(moved);self.app.symlink_to(moved,target_is_directory=True)
        with self.assertRaises((d.Refused,OSError)):self.run_inspect()

    def test_symlinked_root_ancestor_rejected(self):
        link=self.outer/'link';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises((d.Refused,OSError)):d.inspect_config(link,self.template)

    def test_parent_traversal_rejected(self):
        with self.assertRaises(d.Refused):d.inspect_config(self.root/'..'/'local_apps',self.template)

    def test_source_mode_mismatch_rejected_without_normalization(self):
        p=self.app/'config.json';p.chmod(0o666)
        with self.assertRaisesRegex(d.Refused,'mode'):self.run_inspect()
        self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o666)

    def test_large_source_rejected(self):
        (self.app/'config.json').write_bytes(b' '*(d.MAX_CONFIG_BYTES+1))
        with self.assertRaisesRegex(d.Refused,'size'):self.run_inspect()

    def test_root_entry_limit(self):
        for i in range(d.MAX_ENTRIES):(self.root/f'extra{i}').touch()
        with self.assertRaisesRegex(d.Refused,'entry limit'):self.run_inspect()

    def test_app_entry_limit(self):
        for i in range(d.MAX_ENTRIES):(self.app/f'extra{i}').touch()
        with self.assertRaisesRegex(d.Refused,'entry limit'):self.run_inspect()

    def test_source_change_after_discovery_rejected(self):
        real=d.read_config;count=0
        def race(fd,name='config.json'):
            nonlocal count
            result=real(fd,name)
            if name=='config.json' and result and PRIVATE.encode() in result[0]:
                count+=1
                if count==1:(self.app/'config.json').write_bytes(BASE)
            return result
        with mock.patch.object(d,'read_config',race):
            with self.assertRaisesRegex(d.Refused,'changed'):self.run_inspect()

    def test_deadline_refuses(self):
        with mock.patch.object(d,'MAX_SECONDS',-1):
            with self.assertRaisesRegex(d.Refused,'time budget'):self.run_inspect()

    def test_template_missing_and_tampered_refuse(self):
        self.template.write_bytes(BASE+b'\n')
        with self.assertRaisesRegex(d.Refused,'pin'):self.run_inspect()
        self.template.unlink()
        with self.assertRaisesRegex(d.Refused,'missing'):self.run_inspect()

    def test_cli_output_private_value_and_hash_free(self):
        options=self.outer/'options.json';options.write_text('{}');out=io.StringIO();real=d.inspect_config
        with mock.patch.object(d,'OPTIONS',str(options)),mock.patch.object(sys,'argv',['inspect_config.py']),\
             mock.patch.object(d,'inspect_config',lambda:real(self.root,self.template)),contextlib.redirect_stdout(out):
            self.assertEqual(d.main(),0)
        self.assertEqual(len(out.getvalue().splitlines()),1)
        self.assertNotIn(PRIVATE,out.getvalue())
        self.assertNotIn(hashlib.sha256((self.app/'config.json').read_bytes()).hexdigest(),out.getvalue())

    def test_cli_errors_never_print_values_or_os_paths(self):
        options=self.outer/'options.json';options.write_text('{}')
        for error in (OSError('PRIVATE_PATH'),ValueError('PRIVATE_VALUE'),TypeError('PRIVATE_VALUE')):
            out=io.StringIO()
            with mock.patch.object(d,'OPTIONS',str(options)),mock.patch.object(sys,'argv',['inspect_config.py']),\
                 mock.patch.object(d,'inspect_config',side_effect=error),contextlib.redirect_stdout(out):
                self.assertEqual(d.main(),1)
            self.assertNotIn('PRIVATE',out.getvalue());self.assertEqual(len(out.getvalue().splitlines()),1)

    def test_cli_rejects_arguments_and_option_overrides(self):
        options=self.outer/'options.json';options.write_text('{"target_directory":"PRIVATE_DIRECTORY"}')
        for argv in (['inspect_config.py'],['inspect_config.py','--root','/elsewhere']):
            out=io.StringIO()
            with mock.patch.object(d,'OPTIONS',str(options)),mock.patch.object(sys,'argv',argv),contextlib.redirect_stdout(out):
                self.assertEqual(d.main(),1)
            self.assertNotIn('PRIVATE_DIRECTORY',out.getvalue())


if __name__=='__main__':unittest.main()
