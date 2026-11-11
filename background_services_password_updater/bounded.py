"""Fixed 0.2.6 -> 0.2.7 source transaction entrypoint. No HA API or networking."""
import json
from pathlib import Path
import sys

from updater import Updater, Refused, SourceMismatch, require, parse_config
from release_pin import BUNDLE_SHA256
from compatibility import preserve_historical_defaults

ROOT = '/local_apps'
BUNDLE = '/app/bundle'
OPTIONS = '/data/options.json'
OPTION_NAMES = {'mode', 'expected_fingerprint', 'maintenance_confirmed',
                'rollback_security_loss_confirmed', 'preserve_extra_files',
                'diagnostic_offset', 'diagnostic_snapshot', 'private_baseline_config_sha256'}


class BoundedUpdater(Updater):
    def __init__(self, root, bundle, preserve_extra_files=None, private_baseline_config_sha256=""):
        super().__init__(root, bundle, preserve_extra_files=preserve_extra_files)
        require(self.manifest['schema'] == 2 and self.config == 'config.json' and
                self.manifest['baseline']['version'] == '0.2.6' and
                self.manifest['payload']['version'] == '0.2.7',
                'This helper supports only the reviewed 0.2.6 to 0.2.7 transaction')
        require(self.bundle_id == BUNDLE_SHA256, 'Bundle differs from the pinned release')
        cfg = parse_config(self.config, self.payload[self.config])
        require(cfg['slug'] == 'dji_audio_archive' and cfg['name'] == 'Background Services',
                'Unexpected target identity')
        require(cfg['homeassistant_api'] is False and cfg['hassio_api'] is False and
                cfg['host_network'] is False and 'ports' not in cfg and 'map' not in cfg,
                'Unexpected target privileges')
        for name in ('app_password_verifier', 'app_https_hostname',
                     'app_ingress_core_peer', 'app_enrollment_approval'):
            require(cfg['options'].get(name) == '', 'Public password/route defaults must be empty')
        preserve_historical_defaults(self, private_baseline_config_sha256)

    def run(self, mode, fingerprint, *, maintenance_confirmed=False,
            rollback_security_loss_confirmed=False, hook=lambda event, name: None):
        # These attestations are NOT proof of owner approval, controls state, or
        # queue state. The separate approved runbook must establish all three.
        # Even apply may need rollback after an interrupted or failed write.
        require(maintenance_confirmed is True,
                'Approved cutover must pause capture, drain work, and stop this app first')
        require(rollback_security_loss_confirmed is True,
                'Acknowledge that rollback to 0.2.6 removes the app password boundary')
        return super().run(mode, fingerprint, hook)

    def report(self, phase, fingerprint, recovery=False):
        result = super().report(phase, fingerprint, recovery)
        result.update({'helper_version': '0.1.1', 'installed_target': 'local_dji_audio_archive',
                       'fresh_live_compatibility_only': True,
                       'maintenance_state_verified_by_helper': False,
                       'runtime_data_access': False})
        # Effective config/bundle hashes remain private in local recovery only.
        result.pop('config_sha256', None)
        result.pop('baseline_config_sha256', None)
        result['bundle_sha256'] = self.public_bundle_id
        result['historical_defaults_preserved'] = self.customized
        result['private_pin_verified'] = self.customized
        return result

    def mismatch_report(self, current):
        result = super().mismatch_report(current)
        for row in result['mismatches']:
            for key in ('expected_baseline', 'expected_payload', 'observed'):
                if row[key] is not None:
                    row[key] = {'mode': row[key]['mode']}
        result['hashes_redacted'] = True
        return result


def dispatch(options, root=ROOT, bundle=BUNDLE):
    require(isinstance(options, dict) and set(options) <= OPTION_NAMES, 'Unexpected updater option')
    mode = options.get('mode', 'inspect')
    require(mode in ('inspect', 'diagnose', 'apply', 'rollback'), 'Unsupported operation')
    for name in ('maintenance_confirmed', 'rollback_security_loss_confirmed'):
        require(type(options.get(name, False)) is bool, 'Confirmation flags must be booleans')
    worker = BoundedUpdater(root, bundle, options.get('preserve_extra_files', []),
                            options.get('private_baseline_config_sha256', ''))
    if mode == 'inspect':
        return worker.inspect()
    if mode == 'diagnose':
        return worker.diagnose(options.get('diagnostic_offset', 0), options.get('diagnostic_snapshot', ''))
    return worker.run(mode, options.get('expected_fingerprint', ''),
                      maintenance_confirmed=options.get('maintenance_confirmed', False),
                      rollback_security_loss_confirmed=options.get('rollback_security_loss_confirmed', False))


def main():
    try:
        require(len(sys.argv) == 1, 'Runtime path or bundle overrides are not supported')
        options = json.loads(Path(OPTIONS).read_bytes())
        result = dispatch(options)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True), flush=True)
        return 0
    except (Refused, OSError, ValueError, TypeError, KeyError, RecursionError):
        error = sys.exception()
        reason = str(error) if isinstance(error, Refused) else 'Filesystem or input validation failed'
        result = error.report if isinstance(error, SourceMismatch) else {'status': 'refused', 'reason': reason}
        print(json.dumps(result, sort_keys=True, ensure_ascii=True), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
