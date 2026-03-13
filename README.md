# fricat

Small CLI utilities for managing Frigate-style recordings and dated backup directories, with Prometheus textfile metrics output.

## What it does

- `concat`: concatenate per-hour/per-camera MP4 segments into a single MKV per hour.
- `prune`: apply a GFS (daily/weekly/monthly/yearly) retention policy to `YYYY-MM-DD` directories.

## Requirements

- Python 3.13+
- `ffmpeg` on PATH (for `concat`)
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

## Metrics

Both commands write Prometheus textfile metrics via `prometheus-client`:

- `concat`: processed bytes/files, duration, last run timestamp
- `prune`: input/kept/removed counts, removed bytes, duration, last run timestamp

Override the output path with `--metrics-file` on each command.

## Development

Run the CLI from the repo:

```bash
python -m fricat --help
```
