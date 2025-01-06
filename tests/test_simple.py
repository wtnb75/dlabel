import unittest
import yaml
import tomllib
import tempfile
from click.testing import CliRunner
from unittest.mock import patch, MagicMock, ANY
from docker.errors import ImageNotFound
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

    @patch("docker.from_env")
    def test_tar_volume(self, dcl):
        dcl.return_value.volumes.get.return_value.id = "volid1"
        dcl.return_value.images.get.side_effect = ImageNotFound("image not found")
        dcl.return_value.images.pull.return_value = "img"
        dcl.return_value.containers.create.return_value.get_archive.return_value = ([b"binary data"], None)
        res = CliRunner().invoke(cli, ["tar-volume", "vol1", "--verbose"])
        if res.exception:
            raise res.exception
        self.assertEqual("binary data", res.output)
        dcl.assert_called_once_with()
        dcl.return_value.images.get.assert_called_once_with("hello-world")
        dcl.return_value.images.pull.assert_called_once_with("hello-world")
        dcl.return_value.containers.create.assert_called_once_with("img", mounts=ANY)
        dcl.return_value.containers.create.return_value.get_archive.assert_called_once_with(ANY, encode_stream=False)
