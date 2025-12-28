from pathlib import Path
from collections.abc import Mapping
from tempfile import NamedTemporaryFile

import click
from prometheus_client import CollectorRegistry, Gauge, write_to_textfile


def write_metrics_file(
    metrics_path: Path,
    metrics: Mapping[str, float | int],
    labels: dict[str, str] | None = None,
) -> None:
    """Atomically write metrics for node_exporter textfile collector."""
    registry = CollectorRegistry()
    for name, value in metrics.items():
        if labels:
            gauge = Gauge(name, '', registry=registry, labelnames=tuple(labels.keys()))
            gauge.labels(**labels).set(float(value))
        else:
            gauge = Gauge(name, '', registry=registry)
            gauge.set(float(value))

    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=metrics_path.parent, prefix=metrics_path.name, suffix='.tmp') as tmp:
        tmp_path = Path(tmp.name)
        try:
            write_to_textfile(str(tmp_path), registry)
            tmp.flush()
            tmp_path.replace(metrics_path)
            click.echo(f'Wrote metrics to {metrics_path}')
        except Exception as exc:
            click.echo(f'Failed to write metrics to {metrics_path}: {exc}', err=True)
            tmp_path.unlink(missing_ok=True)
