import functools
import docker
import click
from logging import getLogger
import sys
import time
import subprocess
from pathlib import Path
from .traefik import traefik2nginx, traefik2apache, traefik_dump
from .compose import compose
from .version import VERSION
from .util import get_diff, get_archives
from .dockerfile import get_dockerfile

_log = getLogger(__name__)


@click.group(invoke_without_command=True)
@click.version_option(VERSION)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def verbose_option(func):
    @click.option("--verbose/--quiet", default=None, help="INFO(default)/DEBUG(verbose)/WARNING(quiet)")
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
    @click.option("--format", default="yaml", type=click.Choice(["yaml", "json", "toml"]), show_default=True,
                  help="output format")
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


def container_option(func):
    @docker_option
    @click.option("--name", help="container name")
    @click.option("--id", help="container id")
    @functools.wraps(func)
    def _(client: docker.DockerClient, name: str, id: str, **kwargs):
        if not name and not id:
            click.echo("id name image")
            for ctn in client.containers.list():
                click.echo(f"{ctn.short_id} {ctn.name} {ctn.image.tags}")
            return
        if id:
            ctn = client.containers.get(id)
        elif name:
            ctnlist = client.containers.list(filters={"name": name})
            if len(ctnlist) != 1:
                raise FileNotFoundError(f"container named {name} not found({len(ctnlist)})")
            ctn = ctnlist[0]
        return func(client=client, container=ctn, **kwargs)

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


class ComposeGen:
    def __init__(self, **kwargs):
        self.gen = compose(**kwargs)

    def __iter__(self):
        self.value = yield from self.gen
        return self.value


@cli.command(compose.__name__, help=compose.__doc__)
@click.option("--output", type=click.Path(file_okay=False, dir_okay=True, exists=True, writable=True))
@click.option("--volume/--no-volume", default=True, show_default=True, help="copy volume content")
@click.option("--project", help="project name of compose")
@verbose_option
@docker_option
@format_option
def _compose(client, output, volume, project):
    if not output:
        volume = False
    cgen = ComposeGen(client=client, volume=volume, project=project)
    for path, bin in cgen:
        if output:
            out = Path(output) / path
            if out.is_relative_to(output):
                _log.debug("output %s -> %s (%s bytes)", path, out, len(bin))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(bin)
            else:
                _log.debug("is not relative: pass %s -> %s (%s bytes)", path, out, len(bin))
    return cgen.value


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
    return traefik_dump(*args, **kwargs).to_dict()


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
@click.option("--image", default='hello-world', show_default=True, help="container image name")
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


def srun(title: str, args: list[str], capture_output=True):
    _log.info("run %s: %s", title, args)
    cmdresult = subprocess.run(args, capture_output=capture_output, check=True)
    _log.info("result %s: stdout=%s, stderr=%s", title, cmdresult.stdout, cmdresult.stderr)


def webserver_run(client: docker.DockerClient, conv_fn, conffile: str, baseconf: str | None,
                  server_url: str, ipaddr: bool, interval: int, oneshot: bool,
                  test_cmd: list[str], boot_cmd: list[str], stop_cmd: list[str], reload_cmd: list[str]):
    import dictknife
    import atexit
    config = traefik_dump(client)
    with open(conffile, "w") as ngc:
        conv_fn(config, ngc, baseconf, server_url, ipaddr)
    # test config
    srun("test", test_cmd)
    # boot
    srun("boot", boot_cmd, capture_output=False)

    if not oneshot:
        @atexit.register
        def _():
            srun("exit", stop_cmd)
    else:
        return

    while True:
        _log.debug("sleep %s", interval)
        time.sleep(interval)
        newconfig = traefik_dump(client)
        if newconfig != config:
            _log.info("change detected")
            for d in dictknife.diff(config.to_dict(), newconfig.to_dict()):
                _log.info("diff: %s", d)
            _log.info("generate config")
            with open(conffile, "w") as ngc:
                conv_fn(newconfig, ngc, baseconf, server_url, ipaddr)
            srun("test", test_cmd)
            srun("reload", reload_cmd)
            config = newconfig
        else:
            _log.debug("not changed")


@cli.command()
@docker_option
@webserver_option
@click.option("--conffile", type=click.Path(), required=True)
@click.option("--nginx", default="nginx", show_default=True, help="nginx binary filepath")
@click.option("--oneshot/--forever", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True, help="check interval")
@verbose_option
def traefik_nginx_monitor(client: docker.DockerClient, baseconf: str, conffile: str, nginx: str,
                          server_url: str, ipaddr: bool, interval: int, oneshot: bool):
    """boot nginx with configuration from labels"""
    webserver_run(client, traefik2nginx, conffile, baseconf, server_url,
                  ipaddr, interval, oneshot,
                  [nginx, "-c", conffile, "-t"],
                  [nginx, "-c", conffile],
                  [nginx, "-s", "quit"],
                  [nginx, "-s", "reload"]
                  )


@cli.command()
@docker_option
@webserver_option
@click.option("--conffile", type=click.Path(), required=True)
@click.option("--apache", default="httpd", show_default=True, help="httpd binary filepath")
@click.option("--oneshot/--forever", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True, help="check interval")
@verbose_option
def traefik_apache_monitor(client: docker.DockerClient, baseconf: str, conffile: str, apache: str,
                           server_url: str, ipaddr: bool, interval: int, oneshot: bool):
    """boot apache httpd with configuration from labels"""
    webserver_run(client, traefik2apache, conffile, baseconf, server_url,
                  ipaddr, interval, oneshot,
                  [apache, "-t"],
                  [apache],
                  [apache, "-k", "graceful-stop"],
                  [apache, "-k", "graceful"]
                  )


@cli.command()
@verbose_option
@container_option
@click.option("--output", type=click.Path(dir_okay=True))
@click.option("--ignore", multiple=True)
@click.option("--labels/--no-labels", default=False, show_default=True)
def make_dockerfile(client: docker.DockerClient, container: docker.models.containers.Container, output, ignore, labels):
    """make Dockerfile from running container"""
    import tarfile
    import io
    tf: tarfile.TarFile | None = None
    if output == "-":
        _log.debug("stream output")
        tf = tarfile.open(mode="w|", fileobj=sys.stdout.buffer, format=tarfile.GNU_FORMAT)
    elif output:
        Path(output).mkdir(exist_ok=True)
    for name, bin in get_dockerfile(container, ignore, labels, bool(output)):
        if tf:
            ti = tarfile.TarInfo(name)
            ti.mode = 0o644
            ti.mtime = time.time()
            ti.size = len(bin)
            tf.addfile(ti, io.BytesIO(bin))
        elif bool(output):
            (Path(output) / name).write_bytes(bin)
        elif name == "Dockerfile":
            sys.stdout.buffer.write(bin)
    if tf:
        tf.close()


@cli.command()
@verbose_option
@container_option
@click.option("--sbom", type=click.Path(file_okay=True), help="output filename")
@click.option("--collector", default="syft", show_default=True, help="syft binary filepath")
@click.option("--checker", default="grype", show_default=True, help="grype binary filepath")
@click.option("--ignore", multiple=True)
def diff_sbom(client: docker.DockerClient, container: docker.models.containers.Container, ignore,
              collector, sbom, checker):
    """make SBOM and check Vulnerability of updated files in container"""
    import tempfile
    import tarfile
    import subprocess
    _log.info("get metadata: %s", container.name)
    deleted, added, modified, link = get_diff(container, ignore)
    with tempfile.TemporaryDirectory() as td:
        tarfn = Path(td) / "files.tar"
        rootdir = Path(td)/"root"
        if sbom:
            sbomfn = Path(sbom)
        else:
            sbomfn = Path(td) / "sbom.json"
        _log.info("get diffs: %s+%s file/dirs", len(added), len(modified))
        tfbin = get_archives(container, added | modified, ignore, "w")
        tarfn.write_bytes(tfbin)
        _log.info("extract files: size=%s", tarfn.stat().st_size)
        with tarfile.open(tarfn) as tf:
            tf.extractall(rootdir, filter='data')
        _log.info("generate sbom")
        subprocess.check_call([collector, "scan", f"dir:{rootdir}", "-o", f"json={sbomfn}"])
        _log.info("check vuln")
        subprocess.check_call([checker, f"sbom:{sbomfn}"])


@cli.command()
@verbose_option
@docker_option
@click.option("--listen", default="0.0.0.0", show_default=True)
@click.option("--port", type=int, default=8000, show_default=True)
@click.option("--schema/--no-schema", default=False, help="output openapi schema and exit")
@format_option
def server(client: docker.DockerClient, listen, port, schema):
    """start API server"""
    from fastapi import FastAPI
    from .api import ComposeRoute, TraefikRoute, NginxRoute, DockerfileRoute
    import uvicorn
    api = FastAPI()
    api.include_router(ComposeRoute(client).router, prefix="/compose")
    api.include_router(TraefikRoute(client).router, prefix="/traefik")
    api.include_router(NginxRoute(client).router, prefix="/nginx")
    api.include_router(DockerfileRoute(client).router, prefix="/dockerfile")
    if schema:
        return api.openapi()
    else:
        uvicorn.run(api, host=listen, port=port, log_config=None)


if __name__ == "__main__":
    cli()
