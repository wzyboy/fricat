from pathlib import Path
from collections.abc import Mapping

import click
from prometheus_client import Gauge
from prometheus_client import CollectorRegistry
from prometheus_client import write_to_textfile


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
    write_to_textfile(str(metrics_path), registry)
    click.echo(f'Wrote metrics to {metrics_path}')
