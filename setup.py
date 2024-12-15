import os
import glob
from setuptools import setup

extras_require = {
    os.path.splitext(x)[0].split("-", 1)[-1]: open(x).readlines() for x in glob.glob(
        os.path.join(os.path.dirname(__file__), "requirements-*.txt"))}

setup(
    install_requires=open("requirements.txt").readlines(),
    extras_require=extras_require,
)
