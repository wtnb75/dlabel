import docker
import re
import io
import tarfile
import yaml
import toml
from dictknife import deepmerge
from typing import Any
from logging import getLogger
_log = getLogger(__name__)


def tflabel2dict(labels: dict[str, str], prefix: str) -> dict[str, dict[str, str]]:
    res = {}
    for k, v in labels.items():
        if k.startswith(prefix):
            last = k[len(prefix):].lstrip(".")
            name, k2 = last.split(".", 1)
            if name not in res:
                res[name] = {}
            res[name][k2] = v
    return res


def find_block(conf: list[dict], directive: str):
    for c in conf:
        if c.get("directive") == directive:
            _log.debug("found directive %s: %s", directive, c)
            yield c


def find_server_block(conf: dict, server_name: str) -> list:
    for conf in conf.get("config", []):
        for http in find_block(conf.get("parsed", []), "http"):
            for srv in find_block(http.get("block", []), "server"):
                for name in find_block(srv.get("block", []), "server_name"):
                    if server_name in name.get("args", []):
                        return srv.get("block", [])


def middleware_prefix(mdlconf: dict[str, str]) -> list[dict]:
    res = []
    del_prefix = ""
    add_prefix = "/"
    if mdlconf.get("stripprefix.prefixes"):
        del_prefix = mdlconf["stripprefix.prefixes"]
    elif mdlconf.get("stripprefixregex.regex"):
        del_prefix = mdlconf.get("stripprefixregex.regex")
    if mdlconf.get("addprefix.prefix"):
        add_prefix = mdlconf["addprefix.prefix"]
    if del_prefix or add_prefix != "/":
        res.append({
            "directive": "rewrite",
            "args": [f"{del_prefix}(.*)", f"{add_prefix}$1", "break"],
        })
    return res


def middleware_headers(mdlconf: dict[str, str]) -> list[dict]:
    res = []
    for k, v in mdlconf.items():
        if k.startswith("headers.customrequestheaders."):
            hdr = k[len("headers.customrequestheaders."):]
            res.append({
                "directive": "proxy_set_header",
                "args": [hdr, v],
            })
        elif k.startswith("headers.customresponseheaders."):
            hdr = k[len("headers.customresponseheaders."):]
            res.append({
                "directive": "add_header",
                "args": [hdr, v],
            })
        elif k.split(".", 1)[0] not in ("stripprefix", "addprefix", "headers"):
            _log.info("not supported middleware: %s", k)
            res.append({
                "directive": "#",
                "comment": f" not supported middleware: {k}",
                "line": 1,
            })
    return res


def middleware_compress(mdlconf: dict[str, str]) -> list[dict]:
    res = []
    if mdlconf.get("compress"):
        res.append({
            "directive": "gzip",
            "args": ["on"],
        })
        if mdlconf.get("compress.includedcontenttypes"):
            res.append({
                "directive": "gzip_types",
                "args": [mdlconf["compress.includedcontenttypes"]],
            })
        if mdlconf.get("compress.minresponsebodybytes"):
            res.append({
                "directive": "gzip_min_length",
                "args": [mdlconf["compress.minresponsebodybytes"]],
            })
    return res


def middleware2nginx(mdlconf: dict[str, str]) -> list[dict]:
    res = []
    res.extend(middleware_prefix(mdlconf))
    res.extend(middleware_headers(mdlconf))
    res.extend(middleware_compress(mdlconf))
    return res


def get_middlewares(middlewares: dict[str, dict[str, str]], names: list[str], blk: list[dict]) -> dict:
    mdlconf = {}
    for idx, mname in enumerate(names, start=1):
        if not mname:
            continue
        if mname not in middlewares:
            _log.info("middleware not found: %s", mname)
            blk.append({
                "directive": "#",
                "comment": f" middleware {mname} not found",
                "line": idx,
            })
        _log.debug("middleware[%s]: %s", names, mname)
        mdlconf.update(middlewares.get(mname, {}))
    return mdlconf


def rule2locationkey(rule: str) -> list[str]:
    m = re.match(r"^PathPrefix\(`(?P<prefix>[^`]+)`\)$", rule)
    location_key = []
    if m:
        location_key = [m.group("prefix")]
    else:
        m = re.match(r"^Path\(`(?P<path>[^`]+)`\)$", rule)
        if m:
            location_key = ["=", m.group('path')]
    return location_key


def traefik_config_update(name: str, val: Any, res: dict):
    tgt = res
    if val == "true":
        val = {}
    elif val == "false":
        val = False
    for k in name.split(".")[:-1]:
        if k not in tgt:
            tgt[k] = {}
        if isinstance(tgt[k], (str, bool)):
            _log.debug("overwriting %s(%s) -> %s", name, tgt[k], val)
            tgt[k] = {}
        tgt = tgt[k]
    _log.debug("setting %s(%s) -> %s", name, tgt, val)
    tgt[name.split(".")[-1]] = val


def traefik_label_config(labels: dict[str, str], host: str | None):
    res: list[tuple[str, Any]] = []
    for k, v in labels.items():
        if k == "traefik.enable":
            continue
        if k.startswith("traefik."):
            _, k1 = k.split(".", 1)
            res.append((k1, v))
            m = re.match(r"http\.services\.([^\.]+)\.loadbalancer\.server\.port", k1)
            if m:
                res.append((f"http.services.{m.group(1)}.loadbalancer.server.host", host))
    return res


def download_files(ctn: docker.models.containers.Container, filename: str):
    bins, stat = ctn.get_archive(filename)
    _log.debug("download %s: %s", filename, stat)
    fp = io.BytesIO()
    for chunk in bins:
        fp.write(chunk)
    fp.seek(0)
    with tarfile.open(fileobj=fp) as tar:
        for member in tar.getmembers():
            if member.isfile():
                _log.debug("extract %s", member.name)
                yield member.name, tar.extractfile(member).read()


def traefik_container_config(ctn: docker.models.containers.Container):
    from_args = []
    from_conf = {}
    for arg in ctn.attrs.get("Args", []):
        if arg.startswith("--") and "=" in arg:
            k, v = arg.split("=", 1)
            from_args.append((k[2:], v))
            if k[2:] in ("providers.file.filename", "providers.file.directory"):
                for fn, bin in download_files(ctn, v):
                    if fn.endswith(".yml") or fn.endswith(".yaml"):
                        from_conf = deepmerge(from_conf, yaml.safe_load(bin))
                    elif fn.endswith(".toml"):
                        from_conf = deepmerge(from_conf, toml.loads(bin))
    return from_args, from_conf


def traefik_dump(client: docker.DockerClient, ipaddr: bool):
    """extract traefik configuration"""
    from_conf = {}
    from_args: list[tuple[str, Any]] = []
    from_label: list[tuple[str, Any]] = []
    for ctn in client.containers.list():
        if "traefik" in ctn.image.tags[0]:
            _log.debug("traefik container: %s", ctn.name)
            from_args, from_conf = traefik_container_config(ctn)
        if ctn.labels.get("traefik.enable") in ("true",):
            _log.debug("traefik enabled container: %s", ctn.name)
            host = ctn.name
            if ipaddr:
                addrs = [x["IPAddress"] for x in ctn.attrs["NetworkSettings"]["Networks"].values()]
                if len(addrs) != 0:
                    host = addrs[0]
            ctn_label = traefik_label_config(ctn.labels, host)
            from_label.extend(ctn_label)
    res = from_conf.copy()
    for arg, val in from_label:
        traefik_config_update(arg, val, res)
    for arg, val in from_args:
        traefik_config_update(arg, val, res)
    return res


def traefik2nginx(client: docker.DockerClient, output, ipaddr, baseconf, server_name):
    """generate nginx configuration from traefik labels"""
    import crossplane
    if baseconf:
        nginx_confs = crossplane.parse(baseconf)
    else:
        import tempfile
        minconf = """
user nginx;
worker_processes auto;
error_log /dev/stderr notice;
events {worker_connections 512;}
http {server {server_name %s;}}
""" % (server_name)
        with tempfile.NamedTemporaryFile("r+") as tf:
            tf.write(minconf)
            tf.seek(0)
            nginx_confs = crossplane.parse(tf.name, combine=True)
    target = find_server_block(nginx_confs, server_name)
    _log.debug("target: %s", target)
    assert target is not None
    for ctn in client.containers.list():
        name = ctn.name
        labels = ctn.attrs["Config"]["Labels"]
        addresses = [x["IPAddress"] for x in ctn.attrs["NetworkSettings"]["Networks"].values()]
        pass_to = name
        if ipaddr:
            pass_to = addresses[0]
        if labels.get("traefik.enable") not in ("true",):
            _log.debug("traefik is not enabled: %s", labels)
            continue
        services = tflabel2dict(labels, "traefik.http.services.")
        routers = tflabel2dict(labels, "traefik.http.routers.")
        middlewares = tflabel2dict(labels, "traefik.http.middlewares.")
        _log.debug("services: %s", services)
        _log.debug("routers: %s", routers)
        _log.debug("middleware: %s", middlewares)
        for router_name, router_config in routers.items():
            rule = router_config.get("rule", "")
            destport = services.get(router_name).get("loadbalancer.server.port")
            if not destport:
                _log.debug("not match lbport: %s", router_config)
                continue
            location_key = rule2locationkey(rule)
            if not location_key:
                _log.info("not supported rule: %s", rule)
                continue
            blk = []
            blk.append({
                "directive": "proxy_pass",
                "args": [f"http://{pass_to}:{destport}"],
            })
            mdls = router_config.get("middlewares", "").split(",")
            mdlconf = get_middlewares(middlewares, mdls, blk)
            _log.debug("middleware configurations: %s", mdlconf)
            blk.extend(middleware2nginx(mdlconf))
            target.append({
                "directive": "#",
                "comment": f" {name}: {' '.join(location_key)} -> {addresses[0]}:{destport}",
                "line": 1
            })
            location = {
                "directive": "location",
                "args": location_key,
                "block": blk,
            }
            target.append(location)
    for conf in nginx_confs.get("config", []):
        output.write(crossplane.build(conf.get("parsed", [])))
        output.write("\n")
