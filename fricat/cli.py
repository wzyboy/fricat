import click
from pathlib import Path
from fricat.concat import frigate
from fricat.concat import rtsp_record


@click.group()
def cli():
    pass


@cli.command()
@click.argument('src_root', type=click.Path(path_type=Path))
@click.argument('dst_root', type=click.Path(path_type=Path))
@click.option('--layout', default='frigate')
def concat(src_root: Path, dst_root: Path, layout: str):
    if layout == 'frigate':
        frigate(src_root, dst_root)
    elif layout == 'rtsp_record':
        rtsp_record(src_root, dst_root)
