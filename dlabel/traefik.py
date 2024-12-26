import docker
import re
import io
import tarfile
import yaml
import toml
from logging import getLogger
from .traefik_conf import TraefikConfig, HttpMiddleware, HttpService

_log = getLogger(__name__)


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


def middleware_compress(mdl: HttpMiddleware) -> list[dict]:
    res = []
    if mdl.compress:
        res.append({
            "directive": "gzip",
            "args": ["on"],
        })
        if mdl.compress.includedcontenttypes:
            res.append({
                "directive": "gzip_types",
                "args": mdl.compress.includedcontenttypes,
            })
        if mdl.compress.minresponsebodybytes:
            res.append({
                "directive": "gzip_min_length",
                "args": [mdl.compress.minresponsebodybytes],
            })
    return res


def middleware_headers(mdl: HttpMiddleware) -> list[dict]:
    res = []
    if mdl.headers:
        if mdl.headers.customrequestheaders:
            for k, v in mdl.headers.customrequestheaders.items():
                res.append({
                    "directive": "proxy_set_header",
                    "args": [k, v],
                })
        if mdl.headers.customresponseheaders:
            for k, v in mdl.headers.customresponseheaders.items():
                res.append({
                    "directive": "add_header",
                    "args": [k, v],
                })
    return res


def middleware2nginx(mdlconf: list[HttpMiddleware]) -> list[dict]:
    res = []
    del_prefix = []
    add_prefix = "/"
    for mdl in mdlconf:
        res.extend(middleware_compress(mdl))
        res.extend(middleware_headers(mdl))
        if mdl.stripprefix:
            del_prefix.extend([re.escape(x) for x in mdl.stripprefix.prefixes])
        if mdl.stripprefixregex:
            del_prefix.extend(mdl.stripprefixregex.regex)
        if mdl.addprefix:
            add_prefix = mdl.addprefix.prefix
    if del_prefix or add_prefix != "/":
        res.append({
            "directive": "rewrite",
            "args": [f"{'|'.join(del_prefix)}(.*)", f"{add_prefix}$1", "break"],
        })
    _log.debug("middleware2nginx result: %s -> %s", mdlconf, res)
    return res


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


def traefik_label_config(labels: dict[str, str], host: str | None):
    res = TraefikConfig()
    for k, v in labels.items():
        if k == "traefik.enable":
            continue
        if k.startswith("traefik."):
            _, k1 = k.split(".", 1)
            m = re.match(r"http\.services\.([^\.]+)\.loadbalancer\.server\.port", k1)
            if m:
                res = res.setbyaddr(f"http.services.{m.group(1)}.loadbalancer.server.host", host)
                res = res.setbyaddr(k1, int(v))
            else:
                res = res.setbyaddr(k1, v)
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
    from_args = TraefikConfig()
    from_conf = TraefikConfig()
    for arg in ctn.attrs.get("Args", []):
        if arg.startswith("--") and "=" in arg:
            k, v = arg.split("=", 1)
            from_args = from_args.setbyaddr(k[2:], v)
    if from_args.providers and from_args.providers.file:
        to_load = from_args.providers.file.filename or from_args.providers.file.directory
        if to_load:
            for fn, bin in download_files(ctn, to_load):
                if fn.endswith(".yml") or fn.endswith(".yaml"):
                    loaded = TraefikConfig.model_validate(yaml.safe_load(bin))
                elif fn.endswith(".toml"):
                    loaded = TraefikConfig.model_validate(toml.loads(bin))
                from_conf = from_conf.merge(loaded)
    return from_args, from_conf


def traefik_dump(client: docker.DockerClient, ipaddr: bool):
    """extract traefik configuration"""
    from_conf = TraefikConfig()
    from_args = TraefikConfig()
    from_label = TraefikConfig()
    for ctn in client.containers.list():
        if "traefik" in ctn.image.tags[0]:
            _log.debug("traefik container: %s", ctn.name)
            from_args, from_conf = traefik_container_config(ctn)
            _log.debug("loaded: args=%s, conf=%s", from_args.model_dump(), from_conf.model_dump())
        if ctn.labels.get("traefik.enable") in ("true",):
            _log.debug("traefik enabled container: %s", ctn.name)
            host = ctn.name
            if ipaddr:
                addrs = [x["IPAddress"] for x in ctn.attrs["NetworkSettings"]["Networks"].values()]
                if len(addrs) != 0:
                    host = addrs[0]
            ctn_label = traefik_label_config(ctn.labels, host)
            from_label = from_label.merge(ctn_label)
    _log.debug("conf: %s", from_conf)
    _log.debug("arg: %s", from_args)
    _log.debug("label: %s", from_label)
    res = from_conf.merge(from_args)
    res = res.merge(from_label)
    return res.model_dump(exclude_unset=True, exclude_defaults=True, exclude_none=True)


def get_backend(svc: HttpService) -> list[str]:
    backend_urls = []
    if svc.loadbalancer and svc.loadbalancer.servers:
        backend_urls.extend([x.get("url").removeprefix("http://") for x in svc.loadbalancer.servers])
    if svc.loadbalancer.server and svc.loadbalancer.server.port:
        backend_urls.append(f"{svc.loadbalancer.server.host}:{svc.loadbalancer.server.port}")
    return backend_urls


def traefik2nginx(traefik_file, output, baseconf, server_name):
    """generate nginx configuration from traefik configuration"""
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
    traefik_config = TraefikConfig.model_validate(yaml.safe_load(traefik_file))
    services: dict[str, HttpService] | None = traefik_config.http.services
    routers = traefik_config.http.routers
    middlewares = traefik_config.http.middlewares
    _log.debug("all middlewares: %s", middlewares)
    for location in set(traefik_config.http.services.keys()) & set(traefik_config.http.routers.keys()):
        route, svc = routers[location], services[location]
        rule = route.rule
        middleware_names = route.middlewares
        _log.debug("middleware_names: %s", middleware_names)
        location_keys = [rule2locationkey(x) for x in rule.split("||")]
        middles: list[HttpMiddleware] = [middlewares.get(x) for x in middleware_names]
        _log.debug("middles: %s", middles)
        backend_urls = get_backend(svc)
        target.append({
            "directive": "#",
            "comment": f" {location}: {', '.join([' '.join(x) for x in location_keys])} -> {', '.join(backend_urls)}",
            "line": 1
        })
        if len(backend_urls) > 1:
            _log.info("multiple backend urls: %s", backend_urls)
            target.append({
                "directive": "upstream",
                "args": [location],
                "block": [{"directive": "server", "args": [x]} for x in backend_urls],
            })
            backend = location
        else:
            backend = backend_urls[0]
        blk = [{"directive": "proxy_pass", "args": [f"http://{backend}"]}]
        blk.extend(middleware2nginx(middles))
        for lk in location_keys:
            target.append({
                "directive": "location",
                "args": lk,
                "block": blk,
            })
    for conf in nginx_confs.get("config", []):
        output.write(crossplane.build(conf.get("parsed", [])))
        output.write("\n")


def traefik2apache(traefik_file, output, baseconf, server_name):
    """generate apache configuration from traefik configuration"""
    traefik_config = TraefikConfig.model_validate(yaml.safe_load(traefik_file))
    services = traefik_config.http.services
    routers = traefik_config.http.routers
    middlewares = traefik_config.http.middlewares
    _log.debug("all middlewares: %s", middlewares)
    res = []
    for location in set(services.keys()) & set(routers.keys()):
        route, svc = routers[location], services[location]
        rule = route.rule
        _log.debug("rules: %s", rule)
        middleware_names = route.middlewares
        _log.debug("middleware_names: %s", middleware_names)
        location_keys = [rule2locationkey(x) for x in rule[0].split("||")]
        backend_urls = get_backend(svc)
        if len(backend_urls) == 1:
            backend_to = f"http://{backend_urls[0]}"
        else:
            res.append(f"<Proxy balancer://{location}>")
            for b in backend_urls:
                res.append(f"  BalancerMember http://{b}")
            res.append("</Proxy>")
            backend_to = f"balancer://{location}"
        for loc in location_keys:
            if len(loc) == 1:
                res.append(f"<Location {loc[0]}>")
            elif loc[0] == "=":
                res.append(f"<Location ~ \"{re.escape(loc[1])}$\">")
            res.append(f"  ProxyPass {backend_to}")
            res.append(f"  ProxyPassReverse {backend_to}")
            res.append("</Location>")
    print("\n".join(res), file=output)
