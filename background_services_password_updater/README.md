# Background Services Password Source Updater 0.1.0

Preparation-only release candidate. Nothing has been published, installed, or run on Home Assistant. No password, enrollment code, verifier, or private source file was read or created during this preparation. The bundled baseline is the public reviewed 0.2.6 release, not a fresh observation of the installed app.

This separately named helper updates only the reviewed source files belonging to `dji_audio_archive` (installed identity `local_dji_audio_archive`) from 0.2.6 to the independent-password 0.2.7 candidate. The original app folder/installed slug remain. The helper does not perform the normal app Update, stop/start the app, change Home Assistant/network settings, enroll a password, or restore controls.

## Fail-closed contract

- Default `mode=inspect`, empty fingerprint, empty extra-file allowlist, and both maintenance/security-loss attestations false.
- The production entrypoint pins the entire public manifest digest and both versions. No runtime source path, bundle path, baseline-hash override, force mode, private-default override, permission normalization, or old-updater CLI is exposed.
- Every reviewed source file must have the exact expected bytes and mode. Config whitespace differences, private source defaults, modified files, unknown files/directories, missing files, links, modes, or ambiguous slugs stop the transaction without overwriting them.
- `inspect` provides an exact fingerprint only for a recognized baseline/payload or recoverable mixed state. `diagnose` reports bounded filename/type/mode/size metadata only and cannot authorize a write. Source-mismatch output shows fixed file hashes/modes, never config contents.
- `apply` and `rollback` require the fresh matching fingerprint plus boolean maintenance and rollback-security-loss attestations. These flags are operator attestations, not proof of user consent or a measurement of live queue/control state. Use the approved runbook.
- Known source file replacements are descriptor-relative and atomic, config last; durable verified backups and recovery journal precede any replacement. Multi-file replacement is not atomic as a whole. The app must be stopped first.
- An interrupted mixed transaction is restored to baseline, not silently advanced. Unknown concurrent edits are retained and stop recovery. Prior helper recovery namespaces are never modified.

## Preservation and limits

This helper never mounts or reads the target app's `/data`, saved Supervisor options, recordings, transcripts, models, password state, or MQTT controls. Existing saved option values therefore stay unchanged, including an enabled legacy transcript-export setting. The candidate deliberately disables its old bearer HTTP export route regardless of that setting. MQTT controls remain outside the independent web-password boundary.

For reviewed inert extra files, populate `preserve_extra_files` with exact simple filenames. Their content is never opened or copied; inode, mode, ownership, size and timestamps are checked before/after every write. At most 32 regular single-link files, 16 MiB each, 64 MiB total, modes 0600 or 0644. Config/build-control files, directories, symlinks and unknown entries are never deleted or accepted silently. If any existing private extras cannot meet this contract, stop for a newly reviewed strategy that preserves them. Do not remove them to make the helper pass.

Private values in **source config defaults** are different from installed Supervisor options. This public package intentionally refuses such a source-config variant. Never reset those defaults, paste private values into the public bundle, or replace expected hashes with observed ones merely to pass. A separately reviewed exact private-compatible baseline is required if that condition is found.

Recovery data uses `.background-services-password-updater/<bundle digest>/` under the local-apps mount: private 0700 directories and 0600 backup/journal records. Backup names are hashed `.blob` files so HA cannot discover them as apps. Retired source inodes are retained for race recovery. Do not delete recovery data automatically. Source rollback does not roll back runtime data or password enrollment, and removing the independent password boundary requires explicit authorization.

See `ROLLOUT_AND_ROLLBACK.md`, `PERMISSIONS.json`, and `SOURCE_SCOPE.json`. Full target transport/enrollment limits are in the bundled public `SECURITY.md`. Docker and live HA/iPhone verification are still required; local tests do not prove deployment compatibility.

## Offline development

Use Python 3.12 and PyYAML 6.0.3. Run `python -m unittest discover -s tests -v`. The release integration script outside this helper tests real public baseline/payload source on temporary fixtures, including every before/after file-replacement crash boundary. No test uses a live app or real credentials. Image build must also be validated on the intended architecture before any authorized rollout.
