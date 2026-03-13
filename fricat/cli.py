import click
from fricat import concat
from fricat import prune
from fricat import web


@click.group()
def cli():
    pass


cli.add_command(concat.main, 'concat')
cli.add_command(prune.main, 'prune')
cli.add_command(web.main, 'web')
