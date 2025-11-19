import click
from pathlib import Path
from fricat.concat import concat_to_mkv


@click.group()
def cli():
    pass


@cli.command()
@click.argument('src_root', type=click.Path(path_type=Path))
@click.argument('dst_root', type=click.Path(path_type=Path))
def concat(src_root: Path, dst_root: Path):
    concat_to_mkv(src_root, dst_root)
