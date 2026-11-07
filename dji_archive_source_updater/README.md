# DJI Archive Source Updater

A temporary, manually started Home Assistant app that makes one narrow source
change, then exits. It does **not** update a running container, stop recording,
call Home Assistant/Supervisor APIs, create credentials, or access audio/private
app data. The fixed payload upgrades the original reviewed DJI Audio Archive
0.2.2 source to 0.2.4. Unrecognized source is refused, including 0.2.3.

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
  quiescent during apply/recovery. The running recorder may continue recording:
  this changes its source tree, not its running image or `/data`.

## Read-only diagnosis of unexpected entries

If `inspect` refuses because the target contains unexpected files/directories,
set `mode: diagnose` and start the updater once. This is diagnostic only: it
never approves source, produces an apply fingerprint, changes the whitelist,
reads unknown contents, follows unknown symlinks, enters unknown directories,
or writes source/recovery data. Target discovery still requires the unique
archive slug and the existing no-follow checks.

After the existing bounded config reads needed for unique-slug discovery, the
diagnostic scans at most 256 target-directory entries and reports up to 8
unexpected direct-child names and their lstat type/mode. A scan reaching 256
entries conservatively reports a truncated listing and lower-bound counts. Regular files also have size and link count. No symlink targets,
file contents, unknown content hashes, owners, timestamps, or child listings are
reported. Names are JSON-escaped, limited to 96 characters and 256 escaped
bytes; truncated names include a filename-only SHA-256 for identification.
Every reported entry includes its exact filename bytes encoded as base64, even
when the display name is truncated or contains invalid UTF-8.
Total output is limited to 8192 bytes; omitted-entry counts are explicit.
The final consistency recheck has the same 256-entry target-directory bound.
Diagnostic discovery refuses more than 256 local-root entries (using one
lookahead entry). A five-second cooperative budget is checked between filesystem
operations; it cannot interrupt a blocked kernel filesystem call. The existing
config reads identify the target; no unexpected-entry contents are read.
Concurrent directory/reported-entry changes cause refusal rather than a stale
success result. Filename metadata is private runtime information: do not copy
it into a public issue, source bundle or repository.

A diagnostic result is not permission to remove an entry or accept different
source. Investigate the result, then retain the original strict `inspect` and
fingerprint-gated `apply` workflow. The default mode remains `inspect`.

## What is accepted

The updater discovers a direct child of `/local_apps` by its config
`slug: dji_audio_archive`; the folder name is not assumed to match the installed
identifier. Exactly one matching app and one config are required. Target source
must be exactly the manifest whitelist, with reviewed SHA-256 and permissions.
Missing/extra files or directories, unsupported versions, hardlinks, symlinks,
FIFOs/devices, oversized files, unexpected configs, and ambiguous targets refuse
without deleting unrelated files. An unexpected file is a reason to investigate,
not permission to remove it. Other app files are never modified.

Every source access uses directory-relative file descriptors and no-follow
opens. Preflight fingerprints bind target directory device/inode, every file's
hash/mode, baseline manifest, and effective payload. Apply checks them again.
A cooperative directory lock prevents a second updater. Snapshot comparisons
before and after each change detect inode/content/metadata changes.

## Optional private export activation

The public payload defaults are export disabled and empty token digest. To
activate export at the first updated startup, supply only the **SHA-256 digest**
of the separately created export credential in the updater option
`transcript_export_token_sha256`. Never put the raw token here.

The value must be exactly 64 lowercase hexadecimal characters. After verifying
all public payload hashes, the updater privately changes only these two config
options in memory:

- `transcript_export_enabled`: `false` to `true`
- `transcript_export_token_sha256`: empty to the supplied digest

No other override fields are accepted. The effective config hash and payload
fingerprint include this exact derived config. Logs report hashes/status, not
the digest or source contents. Public image/repository files remain unchanged.
The same digest must remain configured for inspect, apply, and rollback;
changing or omitting it refuses the customized source. Preserve it privately
through the rollback window. It is a verifier, not the usable export token.
Existing installed options remain separate and override defaults; this route
is for the original baseline that has no export options yet.

## Preflight and rollout

1. Confirm the original app identity is `local_dji_audio_archive` and the running
   version is 0.2.2. Record its recording state without copying private content.
2. Install this updater from the reviewed repository revision. Leave manual
   startup and protection mode enabled. Do not install a second recorder app.
3. Set `mode: inspect`, blank `expected_fingerprint`, and, if authorized, the
   private digest above. Start the updater and read its single status result.
   It exits. Inspection performs no source or recovery writes.
4. Proceed only with `status: baseline`, reviewed public payload hash, expected
   effective config hash, and the printed `source_fingerprint`. A refusal means
   stop and investigate. Never substitute a newly learned baseline hash.
5. Set `mode: apply` and `expected_fingerprint` to that exact preflight value;
   keep the same digest. Start once. The updater verifies a durable backup,
   stages and verifies files on the same filesystem, changes only differing or
   newly added whitelisted files, and commits config/version last.
6. Require `status: applied` (or verified `already_applied`). Run another
   `inspect` with the same digest and require `status: payload`. Preserve the
   bundle/config hashes for rollback. Do not refresh/build after any refusal.
7. Refresh the Supervisor app store so it sees source 0.2.4. Use the original
   app's ordinary **Update** action to 0.2.4. On Supervisor 2026.10.5 this builds
   the replacement image first, then stops/restarts the original app, preserving
   identity, options and private `/data`. **Never use Rebuild:** it removes the
   existing image/container before building.
8. Verify the original app is healthy, its version is 0.2.4, recording resumes,
   and the authenticated transcript-only endpoint behaves as reviewed. A source
   update alone does not prove any of these live properties.
9. Reset updater mode to `inspect` before removing it. Remove the temporary
   updater once live verification succeeds. Recovery storage remains available.
   Revoke the separate export credential when its authorized use ends.

## Durable backup and interruption behavior

Private recovery data lives under the local source root:

`/local_apps/.dji-source-updater/<effective-bundle-sha256>/`

This location survives updater uninstall and does not add `/share`, another
app's `/data`, or an external backup destination. The recovery directories have
mode 0700; original backup blobs have mode 0600. A source config can contain
private defaults, so **do not publish or upload recovery storage**.

- `review.json` records the exact hash/mode whitelist, not original source text.
- `original/<sha256-of-filename>.blob` contains each original source file.
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
5. If the running app has **not** been updated, its original 0.2.2 image is still
   running: source rollback needs no recorder restart.
6. If 0.2.4 has already been activated, refresh the app store, then use the
   original app's ordinary versioned **Update to 0.2.2**. Supervisor 2026.10.5
   permits a requested version different from the installed version, including
   this downgrade. It builds first and retains the existing image on build
   failure. Do not use Rebuild. Verify original version, health and recording.
   Source restoration by itself does **not** switch the running image.
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
interrupted rollback, idempotence and private-default customization.

`tools/assemble.py` accepts a reviewed baseline hash/mode manifest, a public
payload source directory, an explicit JSON filename whitelist, and an empty
output directory. It copies **only** whitelisted public payload bytes. The
baseline input format is `{"version":"0.2.2","files":{"filename":
{"sha256":"64-lowercase-hex","mode":420}}}`. It never copies baseline source.

Example with generic local inputs:

`python tools/assemble.py --baseline-manifest baseline-hashes.json --payload-source public-source --payload-files public-files.json --output bundle`

The assembler is a packaging tool, not authority to trust arbitrary code.
Review every payload file and secret-scan the final folder before publication.
Exclude real credentials, recordings, transcripts, original private config,
settings, machine/user paths, internal reports, caches and virtual environments.
The image build runs synthetic tests and verifies the fixed payload manifest.
Actual container build, Supervisor installation, live update, recording
continuity and HA rollback must be separately verified in the intended runtime.
