import functools
import docker
import click
from logging import getLogger
import sys
import time
import subprocess
import fnmatch
import io
import tarfile
from pathlib import Path
from .traefik import traefik2nginx, traefik2apache, traefik_dump
from .compose import compose
from .version import VERSION
from .util import download_files, special_modes, nonreg

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


def container_option(func):
    @docker_option
    @click.option("--name")
    @click.option("--id")
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
            for d in dictknife.diff(config, newconfig):
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
@click.option("--nginx", default="nginx", show_default=True)
@click.option("--oneshot/--forever", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True)
@verbose_option
def traefik_nginx_monitor(client: docker.DockerClient, baseconf: str, conffile: str, nginx: str,
                          server_url: str, ipaddr: bool, interval: int, oneshot: bool):
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
@click.option("--apache", default="httpd", show_default=True)
@click.option("--oneshot/--forever", default=True, show_default=True)
@click.option("--interval", type=int, default=10, show_default=True)
@verbose_option
def traefik_apache_monitor(client: docker.DockerClient, baseconf: str, conffile: str, apache: str,
                           server_url: str, ipaddr: bool, interval: int, oneshot: bool):
    webserver_run(client, traefik2apache, conffile, baseconf, server_url,
                  ipaddr, interval, oneshot,
                  [apache, "-t"],
                  [apache],
                  [apache, "-k", "graceful-stop"],
                  [apache, "-k", "graceful"]
                  )


def get_archives(container: docker.models.containers.Container, names: set[str], outpath: str, ignore: list[str]):
    outarchive = tarfile.open(outpath, "w:gz")
    for fn in sorted(names):
        _log.debug("extract: %s", fn)
        for is_dir, tinfo, bin in download_files(container, fn):
            if is_dir:
                tinfo.name = str(Path(fn) / tinfo.name).lstrip("/")
            else:
                tinfo.name = fn.lstrip("/")
            if is_match(ignore, tinfo.name):
                _log.debug("ignore: %s", tinfo.name)
                continue
            outarchive.addfile(tinfo, io.BytesIO(bin))
    outarchive.close()


def is_match(patterns: list[str], target: str) -> bool:
    for p in patterns:
        if fnmatch.fnmatch(target, p):
            return True
    return False


def is_already(prev: set[str], target: str) -> bool:
    for p in prev:
        if Path(p) in Path(target).parents:
            return True
    return False


def do_kind0(modified: set[str], path: str, link: dict[str, str],
             container: docker.models.containers.Container):   # modified
    _, stats = container.get_archive(path)
    _log.debug("stats %s: %s", path, stats)
    special, _ = special_modes(stats["mode"])
    if nonreg & special:
        _log.debug("skip: %s %s", path, special)
    elif "symlink" in special and stats["linkTarget"]:
        link[path] = stats["linkTarget"]
    else:
        modified.add(path)


def do_kind1(added: set[str], path: str, link: dict[str, str],
             container: docker.models.containers.Container):   # added
    if is_already(added, path):
        _log.debug("skip(parent-exists): %s", path)
    else:
        _, stats = container.get_archive(path)
        _log.debug("stats %s: %s", path, stats)
        special, _ = special_modes(stats["mode"])
        if (nonreg-{"dir"}) & special:
            _log.debug("skip: %s %s", path, special)
        elif "symlink" in special and stats["linkTarget"]:
            link[path] = stats["linkTarget"]
        else:
            added.add(path)


def do_kind2(deleted: set[str], path: str):  # deleted
    if is_already(deleted, path):
        _log.debug("skip(parent-exists): %s", path)
    else:
        deleted.add(path)


def get_diff(container: docker.models.containers.Container, ignore: list[str]) -> \
        tuple[set[str], set[str], set[str], dict[str, str]]:
    deleted: set[str] = set()
    added: set[str] = set()
    modified: set[str] = set()
    link: dict[str, str] = {}
    for pathkind in container.diff():
        path = pathkind["Path"]
        kind = pathkind["Kind"]
        if is_match(ignore, path):
            _log.debug("ignore: %s", path)
            continue
        if kind == 2:  # deleted
            do_kind2(deleted, path)
        elif kind == 1:  # added
            do_kind1(added, path, link, container)
        elif kind == 0:  # modified
            do_kind0(modified, path, link, container)
    _log.debug("deleted: %s", deleted)
    _log.debug("added: %s", added)
    _log.debug("modified: %s", modified)
    _log.debug("link: %s", link)
    return deleted, added, modified, link


@cli.command()
@verbose_option
@container_option
@click.option("--output", type=click.Path(dir_okay=True))
@click.option("--ignore", multiple=True)
def make_dockerfile(client: docker.DockerClient, container: docker.models.containers.Container, output, ignore):
    import shlex
    deleted, added, modified, link = get_diff(container, ignore)
    if output:
        Path(output).mkdir(exist_ok=True)
        ofp = (Path(output) / "Dockerfile").open("w")
        (Path(output) / ".dockerignore").write_text("""
*
!added.tar.gz
!modified.tar.gz
""")
    else:
        ofp = sys.stdout
    print(f"FROM {container.image.tags[0]}", file=ofp)
    if deleted:
        print("RUN rm -rf " + shlex.join(deleted), file=ofp)
    if added:
        if output:
            get_archives(container, added, Path(output) / "added.tar.gz", ignore)
        print("ADD added.tar.gz /", file=ofp)
    if modified:
        if output:
            get_archives(container, modified, Path(output) / "modified.tar.gz", ignore)
        print("ADD modified.tar.gz /", file=ofp)
    if link:
        for k, v in link.items():
            print(f"RUN ln -sf {shlex.quote(v)} {shlex.quote(k)}", file=ofp)


if __name__ == "__main__":
    cli()
