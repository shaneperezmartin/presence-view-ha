# Background Services Password Source Updater 0.1.1

Preparation-only release candidate. Nothing has been published, installed, or run on Home Assistant. No live password, enrollment code, verifier, or private source file was read or created during this preparation. Tests use synthetic private markers only. The bundled baseline is the public reviewed 0.2.6 release, not a fresh observation of the installed app.

This separately named helper updates only the reviewed source files belonging to `dji_audio_archive` (installed identity `local_dji_audio_archive`) from 0.2.6 to the independent-password 0.2.7 candidate. The original app folder/installed slug remain. The helper does not perform the normal app Update, stop/start the app, change Home Assistant/network settings, enroll a password, or restore controls.

## Fail-closed contract

- Default `mode=inspect`, empty fingerprint, empty extra-file allowlist, an empty private historical baseline pin, and both maintenance/security-loss attestations false.
- The production entrypoint pins the entire public manifest digest and both versions. No runtime source path, bundle path, arbitrary baseline-hash override, force mode, private-default override, permission normalization, or old-updater CLI is exposed.
- Every reviewed source file must have the exact expected bytes and mode. Config whitespace differences, unrecognized private source defaults, modified files, unknown files/directories, missing files, links, modes, or ambiguous slugs stop the transaction without overwriting them.
- `inspect` provides an exact fingerprint only for a recognized baseline/payload or recoverable mixed state. `diagnose` reports bounded filename/type/mode/size metadata only and cannot authorize a write. Source-mismatch output shows fixed reviewed file names and modes, never config contents or source hashes. Success reports expose only the public bundle ID and opaque fresh source fingerprint, not private config or effective-bundle hashes.
- `apply` and `rollback` require the fresh matching fingerprint plus boolean maintenance and rollback-security-loss attestations. These flags are operator attestations, not proof of user consent or a measurement of live queue/control state. Use the approved runbook.
- Known source file replacements are descriptor-relative and atomic, config last; durable verified backups and recovery journal precede any replacement. Multi-file replacement is not atomic as a whole. The app must be stopped first.
- An interrupted mixed transaction is restored to baseline, not silently advanced. Unknown concurrent edits are retained and stop recovery. Prior helper recovery namespaces are never modified.

## Preservation and limits

This helper never mounts or reads the target app's `/data`, saved Supervisor options, recordings, transcripts, models, password state, or MQTT controls. Existing saved option values therefore stay unchanged, including an enabled legacy transcript-export setting. The candidate deliberately disables its old bearer HTTP export route regardless of that setting. MQTT controls remain outside the independent web-password boundary.

For reviewed inert extra files, populate `preserve_extra_files` with exact simple filenames. Their content is never opened or copied; inode, mode, ownership, size and timestamps are checked before/after every write. At most 32 regular single-link files, 16 MiB each, 64 MiB total, modes 0600 or 0644. Config/build-control files, directories, symlinks and unknown entries are never deleted or accepted silently. If any existing private extras cannot meet this contract, stop for a newly reviewed strategy that preserves them. Do not remove them to make the helper pass.

Private values in **source config defaults** are different from installed Supervisor options. Without a private pin, this package retains public-only exact-baseline behavior and refuses all private variants. Version 0.1.1 can preserve only the previously reviewed historical two-field customization described below. Never reset source defaults or paste private values into the public bundle.

Recovery data uses `.background-services-password-updater/<bundle digest>/` under the local-apps mount: private 0700 directories and 0600 backup/journal records. Backup names are hashed `.blob` files so HA cannot discover them as apps. Retired source inodes are retained for race recovery. Do not delete recovery data automatically. Source rollback does not roll back runtime data or password enrollment, and removing the independent password boundary requires explicit authorization.

See `ROLLOUT_AND_ROLLBACK.md`, `PERMISSIONS.json`, and `SOURCE_SCOPE.json`. Full target transport/enrollment limits are in the bundled public `SECURITY.md`. Docker and live HA/iPhone verification are still required; local tests do not prove deployment compatibility.

## Independently pinned historical compatibility

`private_baseline_config_sha256` is empty by default and masked in the private administrator options UI. Populate it only from an independently retained, previously verified installation record, never from a new mismatch report. The helper cannot verify the record's provenance; the operator must establish that before execution. It is not a generic baseline override. No verifier option is exposed.

With that pin, the helper first uses the existing engine's discovery reader, which may read up to 16 MiB per JSON/YAML config while finding the unique slug. It then separately reads the selected target without following links and requires regular single-link 0644 mode, at most 64 KiB, stable identity/bytes, strict JSON without duplicate keys, and bounded depth/nodes. The 64 KiB bound applies to compatibility verification, not the earlier discovery reads. The existing export-enabled flag must be exactly true and the existing export verifier must have lowercase 64-hex shape. This verifier is not created, replaced or printed.

The exact historical assembler changes only `options.transcript_export_enabled` from false to true and `options.transcript_export_token_sha256` from empty to the existing verifier, then serializes using `json.dumps(indent=2)` plus one newline. The pinned public 0.2.6 template is transformed in memory and its SHA256 must equal the independent runtime pin. The observed source config must match every byte of this reconstructed baseline or the equivalently transformed pinned 0.2.7 payload. Unknown keys, other defaults, schema/security changes, type coercion, formatting differences or a forged hash of arbitrary content still fail closed. Full-file hash/mode and fresh-fingerprint verification follows.

Only the two effective config manifest entries and payload config are derived in memory. The public bundle is never modified. All nine existing defaults are preserved into the thirteen-option candidate; the four new password/route defaults remain empty. The candidate still disables legacy HTTP bearer export independently of the retained default.

This construction also works after a crash or at the exact payload: the current config must be one of the two exact recognized forms, and the same independent baseline pin must be supplied for inspect, apply and rollback. All journal checks and recovery IDs use the private effective manifest internally. Exact original bytes and private effective hashes remain solely in protected local recovery (0700 directories, 0600 records). They are neither printed nor bundled. Do not delete the pin record or recovery data.

Discovery retains the existing engine's nofollow, single-link, stable reads. Its permission handling is not the unrelated-config mode policy from the separate read-only inspector. Compatibility discovery is bounded to 256 root entries and a five-second cooperative budget; filesystem calls themselves are not hard-timed. The target mode remains exact 0644.

## Offline development

Use Python 3.12 and PyYAML 6.0.3. Run `python -m unittest discover -s tests -v`. The release integration script outside this helper tests real public baseline/payload source on temporary fixtures, including every before/after file-replacement crash boundary. No test uses a live app or real credentials. Image build must also be validated on the intended architecture before any authorized rollout.
