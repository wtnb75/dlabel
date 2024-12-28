import unittest
import yaml
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
