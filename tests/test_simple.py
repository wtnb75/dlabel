import unittest
import yaml
import tomllib
import tempfile
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from dlabel.main import cli


class TestSimpleCLI(unittest.TestCase):
    def _container(self, name, labels, image_labels):
        container = MagicMock()
        container.name = name
        container.labels = labels
        container.image.labels = image_labels
        return container

    def test_help(self):
        res = CliRunner().invoke(cli)
        if res.exception:
            raise res.exception
        self.assertEqual(0, res.exit_code)
        self.assertIn("traefik-dump", res.output)

    @patch("docker.from_env")
    def test_labels(self, dcl):
        ctn1 = self._container(
            "ctn1",
            {"label1": "value1", "label2": "value2.1", "label3": "value3"},
            {"label1": "value1", "label2": "value2"})
        ctn2 = self._container("ctn2", {}, {})
        dcl.return_value.containers.list.return_value = [ctn1, ctn2]
        res = CliRunner().invoke(cli, ["labels"])
        if res.exception:
            raise res.exception
        self.assertEqual(0, res.exit_code)
        output = yaml.safe_load(res.output)
        expected = [{
            "name": "ctn1",
            "labels": {"label2": "value2.1", "label3": "value3"},
            "image_labels": {"label1": "value1", "label2": "value2"}
        }, {
            "name": "ctn2",
            "labels": {},
            "image_labels": {}
        }]
        self.maxDiff = 100000
        self.assertEqual(expected, output)

    def test_load(self):
        data = {
            "http": {
                "middlewares": {},
                "services": {
                    "svc1": {
                        "loadbalancer": {
                            "servers": [{
                                "url": "http://localhost",
                            }]
                        }
                    }
                },
                "routers": {
                    "svc1": {
                        "service": "svc1",
                    }
                }
            }
        }
        with tempfile.NamedTemporaryFile("r+") as tf:
            yaml.dump(data, tf)
            tf.flush()
            res = CliRunner().invoke(cli, ["traefik-load", tf.name, "--format", "toml"])
            self.assertEqual(data, tomllib.loads(res.output))
