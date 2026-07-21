import click

from fricat import web
from fricat import prune
from fricat import concat
from fricat import repair
from fricat import backfill
from fricat import check_segments


@click.group()
def cli():
    pass


cli.add_command(concat.main, 'concat')
cli.add_command(backfill.main, 'backfill')
cli.add_command(prune.main, 'prune')
cli.add_command(repair.main, 'repair')
cli.add_command(check_segments.main)
cli.add_command(web.main, 'web')
