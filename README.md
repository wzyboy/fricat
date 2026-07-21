# fricat

Small CLI utilities for managing Frigate-style recordings and dated backup directories, with Prometheus textfile metrics output.

## What it does

- `concat`: concatenate per-hour/per-camera MP4 segments into a single MKV per hour.
- `repair`: scan and stream-copy repair hourly archives with malformed duration metadata.
- `prune`: apply a GFS (daily/weekly/monthly/yearly) retention policy to `YYYY-MM-DD` directories.
- `web`: serve a local browser UI for reviewing archived recordings by camera, date, and hour.

## Requirements

- Python 3.13+
- `ffmpeg` and `ffprobe` on PATH (for `concat` and `repair`)
- Permissions to write metrics files (defaults under `/var/lib/node_exporter/`)

## Install

From this repo:

```bash
uv sync
```

Or run as a module without installing:

```bash
python -m fricat --help
```

## Usage

### concat

Input layout (example, each segment is ~10 seconds):

```
/media/frigate/recordings/2025-11-18/14/CAM2/56.31.mp4
                          YYYY-MM-DD/HH/CAM_/MM.SS.mp4
```

Output layout (example):

```
/media/frigate/archive/2025-11-18/14_CAM2.mkv
```

Command:

```bash
fricat concat /media/frigate/recordings /media/frigate/archive
```

Notes:

- Only processes fully-finished hours: if now is 10:00 UTC, it only processes up to `08`.
- Skips output files that already exist.
- Writes Prometheus metrics to `/var/lib/node_exporter/fricat_concat.prom` by default.
- Validates each completed archive and stream-copy remuxes files with malformed durations before publishing them.

### repair

Scan existing hourly archives for malformed container durations. Scanning is the default and does not modify files:

```bash
fricat repair /media/frigate/archive
```

Repair malformed files in place with an atomic stream-copy remux:

```bash
fricat repair /media/frigate/archive --apply
```

The default maximum duration is 3700 seconds and can be changed with `--max-duration`. Run this command on the machine that stores the archive to avoid transferring media over the network. JSON sidecars are not modified. A repaired file keeps its ownership and mode, while its modification time records when the repair occurred.

### prune

Prunes date-named directories inside a base path using a GFS policy.

```bash
fricat prune /path/to/backups --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --keep-yearly 2
```

Dry run:

```bash
fricat prune /path/to/backups -d 7 -w 4 -m 6 -y 2 --dry-run
```

Notes:

- Only directories named `YYYY-MM-DD` are considered.
- Rules are applied in order: daily, weekly, monthly, yearly.
- Writes Prometheus metrics to `/var/lib/node_exporter/fricat_prune.prom` by default.

### web

Serve the archive browser at `http://127.0.0.1:8000`:

```bash
fricat web --root /media/frigate/archive
```

## Metrics

Both commands write Prometheus textfile metrics via `prometheus-client`:

- `concat`: processed bytes/files, duration, last run timestamp
- `prune`: input/kept/removed counts, removed bytes, duration, last run timestamp

Override the output path with `--metrics-file` on each command.
