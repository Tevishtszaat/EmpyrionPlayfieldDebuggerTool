# Empyrion Playfield Studio

Batch diagnostics for Empyrion: Galactic Survival playfields.

Scan is read-only. Apply, Safe-fix, and Safe-fix all write playfields and append every change to `logs/epd-edits.log`. A full issue list from the last scan is in `logs/epd-last-scan.txt`. Backups go in `.epd_backups` next to the file that changed.

Point it at:

1. Scenario `Playfields`
2. Scenario `Prefabs`
3. Base-game `Content/Playfields` (biome inheritance)
4. Base-game `Content/Prefabs`

Random POIs are checked by blueprint group name (the string in the `.epb` header / F2 group), not by a made-up DefPrefabs list. Fixed POIs are checked by `.epb` filename. `UseFixed: true` is required for fixed POIs in Survival. Biome filters are checked against `BiomeClusterData` names.
