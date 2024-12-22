import functools
import docker
import click
from logging import getLogger
import sys
from .traefik import traefik2nginx, traefik_dump
from .compose import compose
from .version import VERSION

_log = getLogger(__name__)


@click.group(invoke_without_command=True)
@click.version_option(VERSION)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def verbose_option(func):
    @click.option("--verbose/--quiet", default=None)
    @functools.wraps(func)
    def _(verbose, **kwargs):
        from logging import basicConfig
        fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
        if verbose is None:
            basicConfig(level="INFO", format=fmt)
        elif verbose is False:
            basicConfig(level="WARNING", format=fmt)
        else:
            basicConfig(level="DEBUG", format=fmt)
        return func(**kwargs)
    return _


def format_option(func):
    @click.option("--format", default="yaml", type=click.Choice(["yaml", "json", "toml"]), show_default=True)
    @functools.wraps(func)
    def _(format, **kwargs):
        res = func(**kwargs)
        if format == "json":
            import json
            json.dump(res, indent=2, fp=sys.stdout, ensure_ascii=False)
        elif format == "yaml":
            import yaml
            yaml.dump(res, stream=sys.stdout, allow_unicode=True, encoding="utf-8", sort_keys=False)
        elif format == "toml":
            import toml
            toml.dump(res, sys.stdout)
        return res
    return _


def docker_option(func):
    @click.option("-H", "--host", envvar="DOCKER_HOST", help="Daemon socket(s) to connect to", show_envvar=True)
    @functools.wraps(func)
    def _(host, **kwargs):
        if not host:
            cl = docker.from_env()
        else:
            cl = docker.DockerClient(base_url=host)
        return func(client=cl, **kwargs)

    return _


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@verbose_option
@docker_option
@format_option
def summary(client: docker.DockerClient, output):
    """show name and labels of containers"""
    res: list[dict] = []
    for ctn in client.containers.list():
        res.append({
            "name": ctn.name,
            "labels": ctn.attrs["Config"]["Labels"]
        })
    return res


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@verbose_option
@docker_option
@format_option
def attrs(client: docker.DockerClient, output):
    """show name and attributes of containers"""
    res: list[dict] = []
    for ctn in client.containers.list():
        res.append({
            "name": ctn.name,
            "attrs": ctn.attrs
        })
    return res


@cli.command(compose.__name__, help=compose.__doc__)
@click.option("--output", type=click.Path(file_okay=False, dir_okay=True, exists=True, writable=True))
@click.option("--all/--compose", default=False, show_default=True)
@click.option("--volume/--no-volume", default=True, show_default=True)
@click.option("--project", default="*", show_default=True)
@verbose_option
@docker_option
@format_option
def _compose(*args, **kwargs):
    return compose(*args, **kwargs)


@cli.command(traefik2nginx.__name__, help=traefik2nginx.__doc__)
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@click.option("--baseconf", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
              default=None, show_default=True)
@click.option("--server-name", default="localhost", show_default=True)
@click.option("--ipaddr/--hostname", default=False, show_default=True)
@verbose_option
@docker_option
def _traefik2nginx(*args, **kwargs):
    return traefik2nginx(*args, **kwargs)


@cli.command(traefik_dump.__name__.replace("_", "-"), help=traefik_dump.__doc__)
@click.option("--ipaddr/--hostname", default=False, show_default=True)
@verbose_option
@docker_option
@format_option
def _traefik_dump(*args, **kwargs):
    return traefik_dump(*args, **kwargs)


@cli.command()
@docker_option
@verbose_option
@format_option
def list_volume(client: docker.DockerClient):
    """list volumes"""
    return [x.attrs for x in client.volumes.list()]


@cli.command()
@docker_option
@verbose_option
@click.option("--image", default='hello-world', show_default=True)
@click.option("--output", type=click.File("wb"), default="-", show_default=True)
@click.option("-z", is_flag=True, help="compress with gzip")
@click.argument("volume")
def tar_volume(client: docker.DockerClient, volume, image, output, z):
    """get volume content as tar"""
    mount = "/" + volume.strip("/")
    vol = client.volumes.get(volume)
    _log.debug("Volume %s found with ID %s", volume, vol.id)

    try:
        img = client.images.get(image)
        _log.debug("Image %s found locally", image)
    except docker.errors.ImageNotFound:
        img = client.images.pull(image)
        _log.debug("Image %s pulled successfully", image)

    mnt = docker.types.Mount(target=mount, source=vol.id, read_only=True)
    cl = client.containers.create(img, mounts=[mnt])
    _log.debug("Container created with image %s and volume %s mounted at %s", image, volume, mount)

    try:
        bin, _ = cl.get_archive(mount, encode_stream=z)
        _log.debug("Starting to archive volume %s", volume)
        for b in bin:
            output.write(b)
        _log.debug("Volume %s archived successfully", volume)
    finally:
        cl.remove(force=True)
        _log.debug("Container removed")


if __name__ == "__main__":
    cli()
