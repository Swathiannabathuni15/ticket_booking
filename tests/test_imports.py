"""Every module in app/ must at least import. (A broken import in a file that no test touches,
like the job scheduler, would otherwise only show up when that program is started.)"""

import importlib
import pkgutil

import pytest

import app

MODULES = [m.name for m in pkgutil.walk_packages(app.__path__, "app.")]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
