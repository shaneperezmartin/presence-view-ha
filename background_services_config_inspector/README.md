# Background Services Config Inspector 0.1.1

Temporary, manual, read-only diagnosis for the existing local dji_audio_archive source at version 0.2.6. This is not a source updater, does not authorize writes and never produces an apply fingerprint. It does not pause/restart the original app, change source/options/security, or enroll a password.

## Scope and permission

The helper requests only a read-only `local_apps` mount. The mount exposes the whole local-apps source tree; code reads bounded config.json files to find exactly one target slug because the actual folder name has not been independently verified. No target path override is exposed. Home Assistant API, Supervisor API, host networking and audio are false. No public port, target-data/archive mount, Docker socket, extra privilege or network call is added. Boot is manual and startup is once. Options/schema are empty objects.

Only the new temporary helper is installed/started/removed through the authorized administrative route. No original-app interruption is needed for diagnosis. Remove the inspector after its one-shot result. This source package itself does not install or run anything on Home Assistant.

## Bounded read and output contract

- Public reference is the exact reviewed public 0.2.6 config, pinned by a public SHA256. No private config, observed private hash, password, verifier or export digest is bundled.
- At most 256 immediate entries per directory, 64 KiB per config, JSON depth 8, 1024 value nodes, a five-second cooperative diagnostic budget and an 8192-byte report. The deadline is checked between operations; it is not an OS-enforced hard timeout for a blocked filesystem operation.
- Descriptor-relative reads reject symlinks, hard links, nonregular files, changed identities/bytes, duplicate JSON keys, nonfinite numbers, absent/duplicate target slugs and wrong target version.
- Discovery alone accepts regular single-link configs with exactly mode 0400, 0440, 0444, 0600, 0640 or 0644. No execute/special bits, group/world-write bits or owner-unreadable modes are permitted. A matching target must be exactly 0644 before it is selected, and all target rereads and the public template still require 0644. No chmod or baseline normalization occurs.
- Discovery only supports config.json. A config.yaml/config.yml in a scanned local-app directory fails closed for separate review; YAML bytes are never opened. Any unrelated malformed, unsupported-mode or otherwise unsupported config, or oversized directory listing, also causes safe refusal rather than skipping a possible conflicting app. Hidden root recovery directories are not app candidates.
- Source extra files are never opened; their names are only enumerated to check config layout/entry bounds. No source hash/mode baseline is changed or learned.
- Output lists only paths already present in the pinned public template, JSON type labels and presence/type/equality booleans. Unknown key names and all field values remain private. Error messages are fixed content-free strings. No config bytes, private hashes, fingerprints, expected/observed values or source folder names are printed.

The output compares all known fields. In particular `non_options_exact=true` establishes that top-level keys plus identity, permissions, schema, services, ingress, watchdog and all other non-options content equal the reference. `other_option_defaults_exact=true` establishes equality of all existing defaults except the two historical export fields. Unknown keys or missing/changed known keys stop recognition of the historical variant.

## Historical transformation check

The former reviewed source updater could set only `options.transcript_export_enabled=true` and `options.transcript_export_token_sha256` to a lowercase 64-hex digest, then serialize with two-space indentation and a final newline. This inspector can reconstruct that transformation entirely in process memory using the observed digest, and compare the full bytes.

`legacy_export_variant_shape=true` means that exact permitted two-field structural shape is present. `legacy_export_variant_bytes_exact=true` additionally means all observed bytes match the old transformation's serialization. Neither proves that the private digest is historically authorized/correct, nor authorizes accepting this config as an update baseline. The reconstruction is never saved, logged, hashed for output, or transmitted. Every result explicitly has `source_baseline_accepted_for_writes=false` and `whole_source_verified=false`.

If either flag is false, inspect the fixed-name difference booleans and stop for review. If both are true, a separately reviewed private-preserving exact transaction may be prepared; the frozen updater must not be weakened, repointed at an observed hash, or rerun automatically. A compatible write transaction remains blocked on this observation and its own review/approval.

## Validation

Run `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v` using Python 3.12. Tests use synthetic files/private markers only and cover exact/public/legacy/formatting variants, every schema/other-option change, type confusion, unknown keys, output redaction, filesystem guards, races and no-write behavior. No third-party dependency is required. Docker and real HA execution have not been performed during preparation.

## Version 0.1.1 change

Only bounded discovery permission handling is refined: unrelated configs can have the six explicitly listed ordinary read modes. The target identity, exact target mode, byte-comparison rules, bounds, read-only mount, fixed paths and value-free output remain unchanged. A mode refusal from 0.1.0 alone does not establish which scanned file differed. This change does not grant any write or source-baseline acceptance.
