import os
from pathlib import Path

import click
import uvicorn


@click.command()
@click.option('--root', 'root_path', type=click.Path(path_type=Path), required=True)
@click.option('--host', default='127.0.0.1', show_default=True)
@click.option('--port', default=8000, show_default=True, type=int)
@click.option('--reload', is_flag=True, help='Enable auto-reload for development.')
def main(root_path: Path, host: str, port: int, reload: bool) -> None:
    """Serve the archive browser web UI."""
    os.environ['FRICAT_ARCHIVE_ROOT'] = str(root_path.resolve())
    static_dir = Path(__file__).parent / 'static'
    reload_dirs = [str(Path(__file__).parent)] if reload else None
    uvicorn.run('fricat.webapp:app', host=host, port=port, reload=reload, reload_dirs=reload_dirs)
