import click
from fricat import concat
from fricat import prune


@click.group()
def cli():
    pass


cli.add_command(concat.main, 'concat')
cli.add_command(prune.main, 'prune')
