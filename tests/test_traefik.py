import unittest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
import yaml

from dlabel.main import cli


class TestTraefikDump(unittest.TestCase):

    def test_traefik_dump_help(self):
        result = CliRunner().invoke(cli, ["traefik-dump", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--verbose", result.output)
        self.assertIn("--quiet", result.output)
        self.assertIn("--format", result.output)
        self.assertIn("--host", result.output)
        self.assertIn("-H", result.output)
        self.assertIn("DOCKER_HOST", result.output)

    def _container(self, name, image_name, labels: dict[str, str], args: list[str],
                   env: list[str], ipaddr: str | None = None):
        container = MagicMock()
        container.name = name
        container.labels = labels
        container.image.tags = [image_name]
        container.attrs = {
            "Config": {
                "Labels": labels,
                "Image": image_name,
                "Env": env,
                "Cmd": args,
            },
            "NetworkSettings": {
                "Networks": {},
            },
            "Args": args[1:],
        }
        if ipaddr:
            container.attrs["NetworkSettings"]["Networks"] = {"xyz": {"IPAddress": "1.2.3.4"}}
        return container

    @patch("docker.from_env")
    def test_no_container(self, dcl):
        ctn1 = self._container(
            "proj1_ctn1", "alpine:3",
            {
                "key2": "value2",
                "image-label1": "image-value1",
                "image-label2": "container-value"}, [], [])
        ctn2 = self._container(
            "proj1_ctn2", "alpine:3",
            {
                "traefik.enable": "false",
                "traefik.http.services.hello": "blabla",
            }, [], [])
        dcl.return_value.containers.list.return_value = [ctn1, ctn2]
        result = CliRunner().invoke(cli, ["traefik-dump"])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)
        res = yaml.safe_load(result.output)
        self.assertEqual({}, res)
        dcl.assert_called_once_with()
        dcl.return_value.containers.list.assert_called_once_with()

    @patch("docker.from_env")
    def test_label(self, dcl):
        ctn1 = self._container(
            "proj1_ctn1", "alpine:3",
            {
                "label123": "valule123",
                "traefik.enable": "true",
                "traefik.http.routers.ctn1.entrypoints": "web",
                "traefik.http.routers.ctn1.middlewares": "mdl",
                "traefik.http.routers.ctn1.rule": "Path(`/`)",
                "traefik.http.services.ctn1.loadbalancer.server.port": "8080",
            }, [], [], "1.2.3.4")
        ctn2 = self._container(
            "proj1_ctn2", "alpine:3",
            {
                "label234": "valule234",
                "traefik.enable": "true",
                "traefik.http.routers.ctn2.entrypoints": "web",
                "traefik.http.routers.ctn2.middlewares": "mdl",
                "traefik.http.routers.ctn2.rule": "PathPrefix(`/ctn2`)",
                "traefik.http.services.ctn2.loadbalancer.server.port": "9999",
                "traefik.api": "true",
            }, [], [])
        dcl.return_value.containers.list.return_value = [ctn1, ctn2]
        result = CliRunner().invoke(cli, ["traefik-dump"])
        if result.exception:
            raise result.exception
        self.assertEqual(0, result.exit_code)
        res = yaml.safe_load(result.output)
        expected = {
            'api': {},
            'http': {
                'routers': {
                    'ctn1': {
                        'entrypoints': ['web'],
                        'rule': 'Path(`/`)',
                        'middlewares': ['mdl']},
                    'ctn2': {
                        'entrypoints': ['web'],
                        'rule': 'PathPrefix(`/ctn2`)',
                        'middlewares': ['mdl']}},
                'services': {
                    'ctn1': {
                        'loadbalancer': {
                            'server': {'host': 'proj1_ctn1', 'ipaddress': '1.2.3.4', 'port': 8080}}},
                    'ctn2': {
                        'loadbalancer': {
                            'server': {'host': 'proj1_ctn2', 'ipaddress': '', 'port': 9999}}}}}}
        self.assertEqual(expected, res)


if __name__ == '__main__':
    unittest.main()
