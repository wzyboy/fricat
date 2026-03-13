import os
from pathlib import Path

import click
import uvicorn


@click.command()
@click.option('--root', 'root_path', type=click.Path(path_type=Path), required=True)
@click.option('--host', default='127.0.0.1', show_default=True)
@click.option('--port', default=8000, show_default=True, type=int)
def main(root_path: Path, host: str, port: int) -> None:
    """Serve the archive browser web UI."""
    os.environ['FRICAT_ARCHIVE_ROOT'] = str(root_path.resolve())
    uvicorn.run('fricat.webapp:app', host=host, port=port, reload=False)
