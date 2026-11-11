# Temporary ingress route check (not installed)

Purpose: observe the actual Home Assistant ingress hop metadata from the user's iPhone HTTPS session before installing the independent password lock. This separate diagnostic does not rename or modify the original app and cannot access archive files.

Permissions: normal HA ingress only, internal port8099 (not published); no HA API, Supervisor API, host network, host PID, host IPC, Docker socket, special capabilities, or shared-directory mappings. Runs as UID/GID65534. Administrator-only panel. Manual boot. Stop and uninstall it after the check. Installation/start/uninstall require the user's deployment approval; no permission changes to existing apps are included.

The user opens Home Assistant at the verified HTTPS hostname through Tailscale, signs in normally, opens this diagnostic from Apps and views its JSON response. No password, setup code or token should be entered. The response contains the actual immediate socket peer, sanitized IP-only forwarded chain, HTTPS/HTTP enum, .ts.net hostname and ingress-prefix-valid boolean. It never reads or returns cookies, authorization, request bodies, arbitrary header text or ingress token. Request logging is disabled. No diagnostic result is persisted.

Expected structural chain for the reviewed versions is [tailnet client,127.0.0.1,actual Core peer], with immediate ingress peer172.30.32.2. Compare the observed hostname and protocol against the verified external HTTPS route. Do not auto-learn trust settings from an arbitrary request, assume a test fixture's Core peer is real, or treat this output as cryptographic TLS attestation. Trusted host processes remain inside the trust boundary. Record only these minimal noncredential observations, not screenshots containing unrelated private data.

Exact current hostname belongs in the private deployment plan, not this generic package. A valid observation does not authorize deployment: review it against the prepared app's pinned transport parser, then request the separate app cutover approval. Do not widen tailnet ACLs for cloud diagnostics; the iPhone is already an authorized member device.

Recovery is stop/uninstall of this newly installed diagnostic only. The original archive, its data, options and recording/transcription controls are untouched. The public source is generic; publication has not been authorized or performed.

Validation: six synthetic sanitizer/unit tests passed. A Docker build and live ingress test have not run. The current environment has no Docker executable or Docker socket.
