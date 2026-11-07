# Presence View

A standalone, local Home Assistant app for live radar-target maps, sampled historical tracks and time-weighted heatmaps. It does not create or modify dashboards.

## Security and access

The app uses authenticated Home Assistant ingress, no published host port, no host networking, and no Supervisor management API access. Its fixed API endpoints only read configured sensor states/history. It has no service-call, arbitrary-file or arbitrary-URL proxy. Home Assistant's `homeassistant_api` permission is nevertheless administrator-capable at the platform level; it is not a platform-enforced read-only grant. Review this before installation.

The Supervisor supplies the Core credential internally. No user token is requested, logged or saved. Sensor mappings, observations and private settings are not part of this repository.

## Data interpretation

Coordinates are independent sensor-local frames, not a calibrated shared floorplan. Radar target slots are detections, not identified people. A target slot can be reassigned; a line is not proof of one person's path. No coordinates or height are fabricated for occupancy-only sensors. Installation height and configured Z bounds are never treated as measured person height.

Home Assistant recorder history is used before local collection coverage. X/Y axes are joined as-of two-second samples. The app records observed snapshots every approximately two seconds, independent of whether the UI is open. Local heatmaps count time only between consecutive observations of a target; gaps longer than six seconds are excluded. Missing data, offline periods, unknown presence/count and inconsistent slot counts do not establish vacancy and do not produce active dots. Historic recorder changes may lack a device heartbeat, so historical reconstructions remain approximate.

Default local retention is 30 days with a 512 MiB SQLite page budget. Journal overhead is additional. Oldest observations can be pruned earlier at the size limit. Collection pauses when less than 1 GiB free space remains. Coverage bounds do not imply uninterrupted observations. The database stays in app-private storage and can be included in encrypted app backups. No observations are uploaded by this app.

## Installation and configuration

Add this repository through Home Assistant's app store repository menu, install Presence View, then configure its sensor mappings before starting it. Use existing enabled entities; this app does not discover new hardware or enable sensor reporting itself. Do not place credentials in the options.

Each sensor has a unique short `id`, display `name` and `model`, exact `presence` and `count` entity IDs, plot `bounds`, `targets`, and a `notes` list. The optional `unit` must match the actual coordinate entities. Each target mapping contains an anonymous string `id`, exact `x` and `y` entities, optional measured `height` entity, and a `zero_pair_absent` boolean. Set that flag only when the device uses (0,0) as an absent-slot placeholder. A legitimate X=0 alone is not rejected. For occupancy-only sensors, use an empty targets list. Plot bounds are a viewing extent, not proof of a room boundary.

Open Settings > Apps > Presence View > Open Web UI. Choose Live, Tracks or Heatmap. Historical filters support the last 1, 6, 12 or 24 hours and a day/hour in Home Assistant's configured time zone. Repeated daylight-saving hours are labeled separately. No sidebar or dashboard modifications are required.

## Validation and recovery

This is pre-release software. Unit tests run during the image build. Tests do not replace validation with the actual device firmware, recorded data and viewing device. Verify live coordinates against the sensor's own view; distinguish missing history from a verified empty interval. No historical coordinate backfill is implied for sensors that previously recorded only counts.

Stopping this app stops its collection without modifying sensors or dashboards. Preserve/export its history before uninstalling. Full Home Assistant backup restoration can overwrite unrelated later changes and should not be a routine rollback. Keep encrypted backups and recovery keys separate.

Run the local tests with: python -m unittest -v test_server test_http test_history_store test_observation_tracks
