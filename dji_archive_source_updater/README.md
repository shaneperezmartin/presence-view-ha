# DJI Archive Source Updater

Version 1.1.1. A temporary, manually started Home Assistant app that makes one
narrow source change, then exits. It does **not** update a running container, stop recording,
call Home Assistant/Supervisor APIs, create credentials, or access audio/private
app data. The fixed payload upgrades the original reviewed DJI Audio Archive
0.2.5 source to 0.2.6. Unrecognized source is refused, including 0.2.4 and earlier versions.

## Permissions and prerequisites

- Linux with `renameat2` atomic exchange and no-replace support is required.
- Supervisor 2026.10.5 supports the writable `local_apps` map at `/local_apps`.
  The permission covers the **entire local app-source tree**, not one app.
  Code-level checks narrow the actual target. Legacy `addons` maps expose the
  same tree at `/addons`; this build intentionally requests only `local_apps`.
- No Home Assistant API, Supervisor API, Docker socket, host networking,
  privileged capabilities, audio, extra device mapping, or protection-mode
  override is requested. Protection mode must remain on.
- Startup is `once`, boot is `manual`. There is no watchdog or service listener.
- All payload bytes are included in the image. Runtime does not use the network,
  download code, run shell commands, or read recordings/transcripts/models.
- Keep source writers, editors, other updaters, builds and store refreshes
  quiescent during apply/recovery. Keep recording and transcription OFF, capture
  stopped, the queue empty, and labeling idle throughout this rollout. This helper cannot enforce runtime
  controls itself: the operator must verify them without enabling either one.

## No permission normalization

This package accepts only the reviewed 0644 source modes. Permission inspection
and normalization modes are removed from its options schema, and both methods
refuse schema 2 before creating storage or changing source. Historical schema 1
code remains only to run the unchanged previous regression suite. This package
ships a schema 2 transaction and cannot use permission normalization. Unexpected
modes require investigation, not a chmod or a newly learned baseline.

## Read-only source hash/mode mismatch report

The existing `inspect` hash/mode refusal remains read-only. If secure
capture succeeds but a reviewed source file matches neither its baseline nor
payload hash/mode, inspection rechecks the immutable snapshot and returns a
structured refusal. Rows name only fixed manifest filenames and show expected
baseline/payload SHA-256 and numeric Unix modes versus observed SHA-256/mode.
Only mismatches are included. Baseline-absent new payload files are not reported
as mismatches merely because they are absent.

The result remains `status: refused`, `source_verified: false`, with exit status
1 and **no apply fingerprint**. No file contents, option values, private extra
names or extra content hashes are emitted. Existing unknown-extra, type/link,
confinement, missing-file and concurrent-change gates remain unchanged. An unsafe
or changing file produces a safe refusal instead of a hash report.

Output stays below 8192 bytes. `diagnosis_complete`, `reported_mismatches` and
`omitted_mismatches` are explicit. The fixed 29-file payload can exceed the
bound when many files differ: a worst-case 29-mismatch report lists 22 rows and
explicitly omits 7. An incomplete report is never authority to accept or replace
any source. The safety gate still checks every file and emits no fingerprint.
Investigate mismatches; do not substitute observed hashes into the baseline.

## Read-only diagnosis and continuation

If `inspect` refuses because the target contains unexpected files/directories,
set `mode: diagnose`, `diagnostic_offset: 0`, and `diagnostic_snapshot: ""`.
Start once. Diagnosis never approves source, produces an apply fingerprint,
changes the whitelist, follows unexpected symlinks, reads their targets or
unexpected file contents, enters unexpected directories, or writes recovery or
source data. Existing config reads are still needed for unique-slug discovery.

The diagnostic scans at most 256 direct target-directory entries and gathers
lstat metadata for the unexpected names. Each JSON page includes as many names
as fit within 8192 bytes, up to 32 names. Each entry has its type/mode; regular
files also have size/link count. No contents, content hashes, symlink targets,
child listings, owners or timestamps are reported. Display names are
JSON-escaped and bounded to 96 characters/256 escaped bytes. Exact filename
bytes are always available as base64, including invalid UTF-8 or truncated
previews. A truncated preview also has a filename-only SHA-256.

If `next_offset` is an integer, retain the exact `diagnostic_snapshot` token,
set `diagnostic_offset` to `next_offset`, set `diagnostic_snapshot` to that token,
and run `diagnose` again. The token begins with `diag:` and cannot be an apply
fingerprint. It binds the target directory, scanned membership and full stat
metadata of **all** scanned unexpected entries, including omitted ones.
Changed membership/metadata refuses continuation: restart at offset zero with
an empty token and reassess the complete new result. Do not combine pages from
different snapshots. Only `complete: true` confirms the final page of a complete
listing; it says nothing about source acceptance.

Reaching the 256-entry target scan cap conservatively reports
`listing_truncated: true`, lower-bound counts and `complete: false`, even on the
last available page. The updater cannot enumerate beyond this cap: stop and
investigate rather than treating it as a complete diagnosis. Diagnostic root
discovery refuses more than 256 entries, using one lookahead entry. A shared
five-second cooperative time budget is checked between filesystem operations;
it cannot interrupt a blocked kernel filesystem call. The bounded final recheck
validates the full scanned metadata snapshot before emitting a page.

Diagnostic filenames are private runtime data. Do not copy them into public
issues, source bundles or repositories. They never appear in public defaults.

## Explicit preservation of reviewed regular extras

The private option `preserve_extra_files` defaults to an empty list. It does not
remove files or accept arbitrary source changes. Leave it empty until a complete
diagnostic has been reviewed and the exact files are explicitly approved for
preservation. This is appropriate only for unmanaged files that are not active
source or build controls for the fixed reviewed archive build/runtime.

Allowed names are exact single-component ASCII filenames, at most 128
characters, starting with an alphanumeric character or one leading dot followed
by an alphanumeric character. Only alphanumerics, dots, hyphens and underscores
are allowed. Maximum 32 names; no duplicates, case aliases, path separators,
parent traversal or overlap with any original/new source name is accepted.
Config formats (`.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.conf`), known
build-control filenames, dependency requirement/control names, and `.git*`,
`.docker*`, `.env*` or updater storage/control names are refused. Inert legacy
scripts or backup files are not rejected solely because of their extension.

Every listed extra must exist as a regular, single-link file with mode 0600 or
0644. Symlinks, hardlinks, directories, devices, FIFOs, sockets, executable or
writable-by-group/others modes and special permission bits are refused. Limits
are 16 MiB per file and 64 MiB total. Unlisted extras still refuse. A denied name,
type or limit is not permission to rename, delete or normalize the file.

The updater never opens preserved extras for contents, copies their contents to
backup, hashes their contents, writes, changes permissions, renames, deletes or
executes them. The fixed reviewed Dockerfiles copy an explicit source whitelist,
so preserved extras are not copied into the resulting archive image, and the
running app does not mount the source directory. This is **not** a promise that
Docker cannot read/package them while preparing its local build context.

Preflight fingerprints bind the canonical exact name list and each extra's
filesystem device/inode, full mode, link count, size, ownership, modification
time and change time. Guards compare fresh metadata against the immutable source
snapshot before/after writes. Replacing or changing an extra invalidates the
fingerprint. Even with approved extras, all original source hashes/modes remain
strictly required. No new source baseline is learned from diagnosis or extras.

Before source mutation, private `extras.json` durably records only the bound
names/stat metadata in recovery storage. Recovery and later runs require that
same metadata; they cannot silently rebaseline extras changed after interruption.
A partial initial metadata write that cannot be validated refuses for review
while original source is still untouched. The record contains private filenames
and must never be published. Apply and rollback affect only the reviewed source
whitelist; unchanged extras retain their original bytes, inode and metadata.

Operator sequence:

1. Diagnose at offset zero, follow snapshot-bound pages if needed, and require a
   complete stable listing. Assess every extra; do not infer approval from type.
2. Only after explicit approval, privately set `preserve_extra_files` to the
   exact approved names. Keep the same list for inspection, apply and rollback.
   No live filename is authorized by this public package or its examples.
3. Reset `diagnostic_offset: 0` and `diagnostic_snapshot: ""`; set `mode: inspect`.
   Require the unchanged reviewed baseline and a fresh source fingerprint.
4. Use that fingerprint for `apply` under the existing rollout procedure below.
   If any source/extra metadata changes, stop and investigate; never substitute
   hashes, remove unknown files, or enlarge the list automatically.

## What is accepted

The updater discovers a direct child of `/local_apps` by its config
`slug: dji_audio_archive`; the folder name is not assumed to match the installed
identifier. Exactly one matching app and one config are required. Target source
must have the exact reviewed source whitelist/hashes/modes, plus only explicitly
approved preserved regular extras described above. Missing/unlisted files or
directories, unsupported versions, hardlinks, symlinks,
FIFOs/devices, oversized files, unexpected configs, and ambiguous targets refuse
without deleting unrelated files. An unexpected file is a reason to investigate,
not permission to remove it. Other app files are never modified.

Every source access uses directory-relative file descriptors and no-follow
opens. Preflight fingerprints bind target directory device/inode, every file's
hash/mode, baseline manifest, and effective payload. Apply checks them again.
A cooperative directory lock prevents a second updater. Snapshot comparisons
before and after each change detect inode/content/metadata changes.

## Exact public or customized 0.2.5 baseline

The schema 2 bundle includes the exact reviewed **public** 0.2.5 config as
`baseline-config.blob`. Its hash and mode must match the baseline manifest, and
its slug/version must match the baseline identity. Both public baseline and
payload configs must have export disabled and an empty verifier. Public files
must also be independently reviewed for unrelated private information; these
export-default checks are not a general secret detector.

With an empty `transcript_export_token_sha256`, only the exact public baseline
is accepted and export stays disabled. To preserve an already customized 0.2.5
baseline, privately supply its **existing SHA-256 export verifier**, exactly 64
lowercase hexadecimal characters. Never supply or request the usable raw token.
The helper derives both the expected baseline and the 0.2.6 payload by applying
only the original helper's two deterministic assignments in memory:

- `transcript_export_enabled`: `false` to `true`
- `transcript_export_token_sha256`: empty to the supplied verifier

Serialization remains exactly `json.dumps(config, indent=2) + "\n"`. No other
option override is available. No observed live config bytes or learned hashes
are accepted as an alternative baseline. Any other config edit, whitespace
change, missing/wrong verifier, unexpected mode or source drift refuses.

The private baseline and payload hashes both enter the effective transaction
manifest and its ID. `public_bundle_sha256` identifies the reviewed generic
manifest; `bundle_sha256` identifies the effective private transaction.
`baseline_config_sha256` and `config_sha256` expose only config hashes, not the
verifier or config contents. Verify the baseline config hash against the last
accepted source evidence before apply. Public bundle bytes are never modified.

Use the identical verifier and preserved-extra list for inspect, apply and
rollback, including after reinstalling the helper. Rollback restores the exact
backed-up 0.2.5 config bytes, not a public placeholder or any earlier config. Different
transactions use distinct recovery directories, leaving older backups intact.
Do not use helper 1.1.0 or any older helper for this transaction: they target
earlier baselines and cannot roll this update back to 0.2.5.

Installed app options are separate from source defaults. Preserve every existing
effective option explicitly when the platform submits its update request; never
replace installed options with this public config's placeholder defaults.

## Preflight and rollout

1. Confirm the original app identity is `local_dji_audio_archive` and the running
   version is 0.2.5. Verify recording/transcription OFF, capture stopped, queue
   zero and labeling idle. Securely retain all nine effective options for comparison
   and preserve them explicitly in the platform update. Never log secrets.
2. Obtain authorization for publication, a protected local backup, temporary
   helper installation with whole-`local_apps` write scope, and the ordinary
   app update/restart. Install this exact reviewed helper revision. Leave manual
   startup and protection mode enabled. Do not install a second recorder app.
3. Set `mode: inspect`, blank `expected_fingerprint`, and, if authorized, the
   private digest above. Start the updater and read its single status result.
   It exits. Inspection performs no source or recovery writes.
4. Proceed only with `status: baseline`, reviewed public payload hash, expected
   baseline and payload effective config hashes, and the printed `source_fingerprint`. A refusal means
   stop and investigate. Never substitute a newly learned baseline hash.
5. Set `mode: apply` and `expected_fingerprint` to that exact preflight value;
   keep the same digest. Start once. The updater verifies a durable backup,
   stages and verifies files on the same filesystem, changes only differing or
   newly added whitelisted files, and commits config/version last.
6. Require `status: applied` (or verified `already_applied`). Run another
   `inspect` with the same digest and require `status: payload`. Preserve the
   bundle/config hashes for rollback. Do not refresh/build after any refusal.
7. Refresh the Supervisor app store so it sees source 0.2.6. Use the original
   app's ordinary **Update** action to 0.2.6. On Supervisor 2026.10.5 this builds
   the replacement image first, then stops/restarts the original app, preserving
   identity, options and private `/data`. **Never use Rebuild:** it removes the
   existing image/container before building.
8. Verify the original app is healthy at 0.2.6, all nine saved effective
   options are unchanged, both controls remain OFF, capture is stopped, the
   queue is empty, labeling is idle, and there is no runtime error. Verify the
   unchanged authenticated transcript-only endpoint. Source verification alone
   proves none of these runtime properties.
9. Reset updater mode to `inspect` and immediately uninstall the temporary
   helper in the same rollout session after successful runtime verification.
   Do this **before** waiting for browser/UI QA; never leave its whole-source-tree
   write access installed for later testing. Verify that it is uninstalled.
   Recovery storage remains available. Preserve the existing separate export
   credential; this update does not rotate or revoke it.
10. After helper removal, verify floor/date-filtered audio and transcript ZIP
    downloads through authenticated ingress, existing pagination and individual
    downloads, and denied non-ingress access without enabling capture. Retention
    cleanup continues; this update cannot restore expired files.

## Durable backup and interruption behavior

Private recovery data lives under the local source root:

`/local_apps/.dji-source-updater/<effective-bundle-sha256>/`

This location survives updater uninstall and does not add `/share`, another
app's `/data`, or an external backup destination. The recovery directories have
mode 0700; original backup blobs have mode 0600. A source config can contain
private defaults, so **do not publish or upload recovery storage**.

- `review.json` records the exact hash/mode whitelist, not original source text.
- `original/<sha256-of-filename>.blob` contains each original source file.
- `extras.json` records approved extra filenames/stat metadata only, never their
  contents. It is required for preserved-extra recovery; old empty-extra backups
  remain compatible.
- `ready.json` proves completed/verified backup preparation.
- `journal.json` records apply/rollback phase and bundle hash.
- `replacement-*.blob` are staged or atomically displaced files.
- `retired/<sha256-of-filename>_<operation-id>.blob` retains every replaced or
  removed inode, including possible late writes by another process.
- `*.pending` are non-authoritative interrupted writes. Only these exact names
  may be discarded/recreated during recovery; unknown entries are refused.

No backup/staging directory contains a discoverable app config filename.
Original code is stored as opaque `.blob` names, not another executable app
registration. The included public archive payload also uses opaque `.blob` filenames inside
this updater's bundle; its config exists only as bytes, never as another
`config.json`/`config.yaml`/`config.yml` in the repository. It cannot register
a second recorder even with recursive config discovery.

Each file change is atomic, but the whole multi-file update is **not** one
atomic transaction. All writes and directory transitions are fsynced. Normal
write failures automatically restore the baseline when snapshot checks permit.
After a process/power interruption, inspect the same reviewed bundle/digest:

- `baseline`: nothing active changed, or rollback completed; apply can retry.
- `mixed` with a verified backup: explicit `rollback` restores baseline. An
  `apply` on this state also rolls back and exits; it does not silently continue.
- `payload`: all reviewed target bytes are in place. Inspect and proceed with
  the normal versioned update, or explicitly roll back.
- `refused`: preserve all recovery data and investigate. Never force overwrite
  or delete unknown bytes, corrupt committed backups, or conflict markers.

Committed backup corruption refuses. A partial pending backup write can be
recreated from the still-unchanged verified baseline. Recovery never treats a
partially written object as a valid backup.

## Source rollback and running-image rollback

1. Keep the same reviewed updater image and private digest. If removed, reinstall
   that exact reviewed version; the hidden backup survives independently.
2. Stop all source writers/builds/refreshes. Set `mode: inspect` and run once.
3. Require a verified recovery backup and recognized baseline/payload/mixed
   source. Set `mode: rollback` and the new preflight fingerprint, then run.
4. Require `rolled_back` or `already_baseline`. Inspect again and require exact
   `baseline`. Changed original files are restored byte-for-byte with original
   modes; added payload files are removed from the app by moving them into
   retained recovery storage; config/version is restored last.
5. If the running app has **not** been updated, its original 0.2.5 image is still
   running: source rollback needs no recorder restart.
6. If 0.2.6 has already been activated, refresh the app store, then use the
   original app's ordinary versioned **Update to 0.2.5**. Supervisor 2026.10.5
   permits a requested version different from the installed version, including
   this downgrade. It builds first and retains the existing image on build
   failure. Do not use Rebuild. Verify original version, health, saved options and both controls OFF.
   Source restoration by itself does **not** switch the running image.
   After rollback runtime verification, reset the helper to inspect and uninstall
   it immediately in that same session, before later browser/UI QA.
7. Retain recovery data until the rollback window closes. This updater never
   automatically deletes backups or retired files. Remove them later only with
   explicit authorization and after verifying no needed edits remain.

If a concurrent edit is caught in the final check-to-rename window, atomic
exchange preserves the exact displaced bytes in private recovery storage and
writes a conflict marker. Source may contain some reviewed new files. Stop; do
not build, refresh, or force automatic rollback. An operator must reconcile
those retained bytes with original source and intended edits before any further
update. A conflicting addition is never clobbered. Directory descriptors keep
writes bound to the originally opened directory, never a substituted symlink.

This is not a security boundary against a hostile process with the same broad
filesystem authority. Such a process can rename directories or write after the
last check. Advisory locks do not prevent that. Atomic exchange prevents silent
loss of displaced bytes, but cannot guarantee that the live path never briefly
changes in a malicious race. Single-writer operation remains mandatory.

## Development and assembly

Install `requirements-dev.txt` from the official package registry and run:

`python -m unittest discover -s tests -v`

The tests use temporary synthetic trees only. They cover exact write scope,
config-last ordering, inspect-only behavior, duplicate slugs, strict manifests,
symlink/hardlink/FIFO rejection, root/app swaps, final-syscall races, retained
late-writer bytes, corrupted backups/journals, every mid-write power cut,
interrupted rollback, idempotence, private-default customization, bounded
diagnostic pagination, and exact preserve-only extras across interruption.

`tools/assemble.py` accepts a reviewed baseline hash/mode manifest, the exact
public baseline config, a public payload source directory, an explicit JSON
filename whitelist, and an empty output directory. It copies **only** whitelisted public payload bytes. The
baseline input format is `{"version":"0.2.5","files":{"filename":
{"sha256":"64-lowercase-hex","mode":420}}}`. It includes only the exact public baseline config template, never live/private
baseline bytes. Full original source backups are created only locally at apply.

Example with generic local inputs:

`python tools/assemble.py --baseline-manifest baseline-hashes.json --baseline-config public-baseline/config.json --payload-source public-source --payload-files public-files.json --output bundle`

The assembler is a packaging tool, not authority to trust arbitrary code.
Review every payload file and secret-scan the final folder before publication.
Exclude real credentials, recordings, transcripts, original private config,
settings, machine/user paths, internal reports, caches and virtual environments.
The image build runs synthetic tests and verifies the fixed payload manifest.
Actual container build, Supervisor installation, live update, OFF-state
preservation and HA rollback must be separately verified in the intended runtime.
