import click

from fricat import web
from fricat import prune
from fricat import concat
from fricat import backfill


@click.group()
def cli():
    pass


cli.add_command(concat.main, 'concat')
cli.add_command(backfill.main, 'backfill')
cli.add_command(prune.main, 'prune')
cli.add_command(web.main, 'web')
