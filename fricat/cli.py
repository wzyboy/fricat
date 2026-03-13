import click
from fricat import concat
from fricat import index
from fricat import prune
from fricat import web


@click.group()
def cli():
    pass


cli.add_command(concat.main, 'concat')
cli.add_command(index.main, 'index')
cli.add_command(prune.main, 'prune')
cli.add_command(web.main, 'web')
