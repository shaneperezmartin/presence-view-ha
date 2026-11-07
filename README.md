# Presence View for Home Assistant

Standalone live radar-target maps, sampled tracks and local heatmaps, presented through authenticated Home Assistant ingress. No dashboard changes.

This repository contains generic application code only. Configure your own sensor entity mappings locally in Home Assistant. Do not commit credentials, sensor IDs, observations, backups or private configuration.

The app requests Home Assistant Core API access. Its implementation uses fixed read-only routes, but the platform permission is administrator-capable. Review the code and [app documentation](presence_view/DOCS.md) before installing.

Pre-release software. Supports existing X/Y sensor entities; sensors exposing only occupancy/counts cannot produce spatial maps until genuine coordinates are made available. No person identity, measured height or historical trajectory capability is assumed.
