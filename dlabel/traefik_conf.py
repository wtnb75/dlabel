from dictknife import deepmerge
from pydantic import BaseModel, model_validator, BeforeValidator, ConfigDict
from typing import Any, Annotated
from logging import getLogger

_log = getLogger(__name__)

excludes = dict(exclude_none=True, exclude_defaults=True, exclude_unset=True)


class Model(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    def __lowercase_property_keys__(cls, values: Any) -> Any:
        def __lower__(value: Any) -> Any:
            if isinstance(value, dict):
                return {k.lower(): __lower__(v) for k, v in value.items()}
            return value

        return __lower__(values)

    def merge(self, other: BaseModel):
        _log.debug("merge: %s +  %s", self, other)
        obj = deepmerge(
            self.model_dump(**excludes), other.model_dump(**excludes))
        _log.debug("merged: %s", obj)
        return self.model_validate(obj)

    def setbyaddr(self, address: str, value):
        _log.debug("set(addr) %s -> %s", address, value)
        res = {}
        tgt = res
        for k in address.split(".")[:-1]:
            tgt[k] = {}
            tgt = tgt[k]
        if value == "true":
            value = {}
        tgt[address.rsplit(".", 1)[-1]] = value
        _log.debug("set-dict: %s", res)
        return self.merge(self.model_validate(res))

    def __str__(self) -> str:
        return self.model_dump_json(**excludes)


def csv_list(v: Any) -> list[str]:
    if isinstance(v, str):
        return v.split(",")
    return v


ListStr = Annotated[list[str], BeforeValidator(csv_list)]


class CertFile(Model):
    certfile: str | None = None
    keyfile: str | None = None


class StoreCert(Model):
    defaultcertificate: CertFile | None = None
    defaultgeneratedcert: dict[str, Any] | None = None


class TlsCert(CertFile):
    stores: list[str] | None = None


class TlsConfig(Model):
    certificates: list[TlsCert] | None = None
    stores: dict[str, StoreCert] | None = None
    options: dict[str, Any] | None = None


class HttpRouter(Model):
    entrypoints: ListStr | None = None
    rule: str | None = None
    rulesyntax: str | None = None
    middlewares: ListStr | None = None
    service: str | None = None
    priority: int | None = None
    tls: dict[str, Any] | bool | None = None


class HttpLoadBalancerServer(Model):
    # incompatible with traefik configuration (used by docker label)
    host: str | None = None
    ipaddress: str | None = None
    port: int | None = None


class HttpLoadBalancer(Model):
    servers: list[dict] | None = None
    server: HttpLoadBalancerServer | None = None
    sticky: dict[str, Any] | None = None
    healthcheck: dict[str, str] | None = None
    passhostheader: bool | None = None
    serverstransport: str | None = None
    responseforwarding: dict | None = None


class HttpService(Model):
    loadbalancer: HttpLoadBalancer | None = None
    weighted: dict[str, Any] | None = None
    mirroring: dict[str, Any] | None = None
    failover: dict[str, Any] | None = None


class CompressMiddleware(Model):
    excludedcontenttypes: list[str] | None = None
    includedcontenttypes: list[str] | None = None
    minresponsebodybytes: int | None = None
    defaultencoding: str | None = None
    encodings: ListStr | None = None


class HeadersMiddleware(Model):
    customrequestheaders: dict[str, str] | None = None
    customresponseheaders: dict[str, str] | None = None


class StripprefixMiddleware(Model):
    prefixes: ListStr | None = None
    forceslash: bool | None = None


class StripprefixregexMiddleware(Model):
    regex: ListStr | None = None


class AddprefixMiddleware(Model):
    prefix: str | None = None


class HttpMiddleware(Model):
    addprefix: AddprefixMiddleware | None = None
    basicauth: dict[str, Any] | None = None
    buffering: dict[str, Any] | None = None
    chain: dict[str, Any] | None = None
    circuitbreaker: dict[str, Any] | None = None
    compress: CompressMiddleware | bool | None = None
    contenttype: dict[str, Any] | bool | None = None
    digestauth: dict[str, Any] | None = None
    errors: dict[str, Any] | None = None
    forwardauth: dict[str, Any] | None = None
    grpcweb: dict[str, Any] | None = None
    headers: HeadersMiddleware | None = None
    ipwhitelist: dict[str, Any] | None = None
    ipallowlist: dict[str, Any] | None = None
    inflightreq: dict[str, Any] | None = None
    passtlsclientcert: dict[str, Any] | None = None
    ratelimit: dict[str, Any] | None = None
    redirectregex: dict[str, Any] | None = None
    redirectscheme: dict[str, Any] | None = None
    replacepath: dict[str, Any] | None = None
    replacepathregex: dict[str, Any] | None = None
    retry: dict[str, Any] | None = None
    stripprefix: StripprefixMiddleware | None = None
    stripprefixregex: StripprefixregexMiddleware | None = None


class HttpConfig(Model):
    middlewares: dict[str, HttpMiddleware] | None = None
    routers: dict[str, HttpRouter] | None = None
    services: dict[str, HttpService] | None = None
    serverstransports: dict[str, dict] | None = None


class AccessLogField(Model):
    defaultmode: str | None = None
    names: dict[str, str] | None = None
    headers: dict[str, Any] | None = None


class AccessLogConfig(Model):
    addinternals: bool = False
    filepath: str | None = None
    format: str | None = None
    bufferingsize: int | None = None
    filters: dict[str, Any] | None = None
    fields: AccessLogField | None = None


class EntrypointHttp(Model):
    redirections: dict[str, Any] | None = None
    encodequerysemicolons: bool | None = None
    middlewares: list[str] | None = None
    tls: dict[str, Any] | None = None


class EntrypointHttp2(Model):
    maxconcurrentstreams: int | None = None


class EntrypointHttp3(Model):
    advertisedport: int | None = None


class EntrypointConfig(Model):
    address: str | None = None
    http: EntrypointHttp | None = None
    http2: EntrypointHttp2 | None = None
    http3: EntrypointHttp3 | bool | None = None
    udp: dict[str, Any] | None = None
    allowacmebypass: bool | None = None
    reuseport: bool | None = None
    asdefault: bool | None = None
    forwardedheaders: dict[str, Any] | None = None
    transport: dict[str, Any] | None = None
    proxyprotocol: dict[str, Any] | None = None


class FileProvider(Model):
    filename: str | None = None
    directory: str | None = None
    watch: bool | None = None


class ProviderConfig(Model):
    docker: dict[str, Any] | bool | None = None
    file: FileProvider | None = None
    swarm: dict[str, Any] | None = None
    kubernetescrd: dict[str, Any] | None = None
    kubernetesingress: dict[str, Any] | bool | None = None
    kubernetesgateway: dict[str, Any] | bool | None = None
    consulcatalog: dict[str, Any] | bool | None = None
    nomad: dict[str, Any] | bool | None = None
    ecs: dict[str, Any] | bool | None = None
    consul: dict[str, Any] | None = None
    etcd: dict[str, Any] | None = None
    zookeeper: dict[str, Any] | None = None
    redis: dict[str, Any] | None = None
    http: dict[str, Any] | None = None


class TraefikConfig(Model):
    tls: TlsConfig | None = None
    http: HttpConfig | None = None
    tcp: dict[str, Any] | None = None
    udp: dict[str, Any] | None = None
    entrypoints: dict[str, EntrypointConfig] | None = None
    providers: ProviderConfig | None = None
    api: dict[str, Any] | None = None
    accesslog: dict[str, Any] | None = None
    experimental: dict[str, Any] | None = None
    log: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    tracing: dict[str, Any] | None = None
    certificatesresolvers: dict[str, Any] | None = None
    spiffe: dict[str, Any] | None = None
