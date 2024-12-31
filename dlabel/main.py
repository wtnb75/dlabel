import functools
import docker
import click
from logging import getLogger
import sys
import time
import subprocess
from .traefik import traefik2nginx, traefik2apache, traefik_dump
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


def webserver_option(func):
    @click.option("--baseconf", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
                  default=None, show_default=True)
    @click.option("--server-url", default="http://localhost", show_default=True)
    @click.option("--ipaddr/--hostname", default=False, show_default=True)
    @functools.wraps(func)
    def _(**kwargs):
        return func(**kwargs)
    return _


@cli.command()
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@verbose_option
@docker_option
@format_option
def labels(client: docker.DockerClient, output):
    """show labels"""
    res: list[dict] = []
    for ctn in client.containers.list():
        image_labels = ctn.image.labels
        res.append({
            "name": ctn.name,
            "labels": {k: v for k, v in ctn.labels.items() if image_labels.get(k) != v},
            "image_labels": image_labels,
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
@click.option("--traefik-file", type=click.File("r"), default="-", show_default=True)
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@webserver_option
@verbose_option
def _traefik2nginx(*args, **kwargs):
    return traefik2nginx(*args, **kwargs)


@cli.command(traefik2apache.__name__, help=traefik2apache.__doc__)
@click.option("--traefik-file", type=click.File("r"), default="-", show_default=True)
@click.option("--output", type=click.File("w"), default="-", show_default=True)
@webserver_option
@verbose_option
def _traefik2apache(*args, **kwargs):
    return traefik2apache(*args, **kwargs)


@cli.command(traefik_dump.__name__.replace("_", "-"), help=traefik_dump.__doc__)
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


@cli.command()
@verbose_option
@format_option
@click.argument("input", type=click.File("r"))
@click.option("--strict/--no-strict", default=False, show_default=True)
def traefik_load(input, strict):
    """load traefik configuration"""
    import yaml
    from .traefik_conf import TraefikConfig
    res = TraefikConfig.model_validate(yaml.safe_load(input), strict=strict)
    return res.model_dump(exclude_none=True, exclude_defaults=True, exclude_unset=True)


def srun(title: str, args: list[str]):
    _log.debug("run %s: %s", title, args)
    cmdresult = subprocess.run(args, capture_output=True, check=True)
    _log.debug("result %s: stdout=%s, stderr=%s", title, cmdresult.stdout, cmdresult.stderr)


def webserver_run(client: docker.DockerClient, conv_fn, conffile: str, baseconf: str | None,
                  server_url: str, ipaddr: bool, daemon: bool, interval: int,
                  test_cmd: list[str], boot_cmd: list[str], stop_cmd: list[str], reload_cmd: list[str]):
    import dictknife
    import atexit
    config = traefik_dump(client)
    with open(conffile, "w") as ngc:
        conv_fn(config, ngc, baseconf, server_url, ipaddr)
    # test config
    srun("test", test_cmd)
    # boot
    srun("boot", boot_cmd)

    if daemon:
        @atexit.register
        def _():
            srun("exit", stop_cmd)

    while daemon:
        time.sleep(interval)
        newconfig = traefik_dump(client)
        if newconfig != config:
            for d in dictknife.diff(config, newconfig):
                _log.info("diff: %s", d)
            _log.info("generate config")
            with open(conffile, "w") as ngc:
                conv_fn(newconfig, ngc, baseconf, server_url, ipaddr)
            srun("test", test_cmd)
            srun("reload", reload_cmd)
        else:
            _log.debug("not changed")


@cli.command()
@docker_option
@webserver_option
@click.option("--conffile", type=click.Path(), required=True)
@click.option("--nginx", default="nginx", show_default=True)
@click.option("--daemon/--foreground", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True)
@verbose_option
def traefik_nginx_monitor(client: docker.DockerClient, baseconf: str, conffile: str, nginx: str,
                          server_url: str, ipaddr: bool, daemon: bool, interval: int):
    webserver_run(client, traefik2nginx, conffile, baseconf, server_url,
                  ipaddr, daemon, interval,
                  [nginx, "-c", conffile, "-t"],
                  [nginx, "-c", conffile],
                  [nginx, "-s", "quit"],
                  [nginx, "-s", "reload"]
                  )


@cli.command()
@docker_option
@webserver_option
@click.option("--conffile", type=click.Path(), required=True)
@click.option("--apache", default="httpd", show_default=True)
@click.option("--daemon/--foreground", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True)
@verbose_option
def traefik_apache_monitor(client: docker.DockerClient, baseconf: str, conffile: str, apache: str,
                           server_url: str, ipaddr: bool, daemon: bool, interval: int):
    webserver_run(client, traefik2apache, conffile, baseconf, server_url,
                  ipaddr, daemon, interval,
                  [apache, "-t"],
                  [apache],
                  [apache, "-k", "graceful-stop"],
                  [apache, "-k", "graceful"]
                  )


if __name__ == "__main__":
    cli()
