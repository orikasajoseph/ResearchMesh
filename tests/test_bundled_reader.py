"""Run Zhang's unchanged bundled Reader unit tests in the normal free suite."""
import importlib


def load_tests(loader, tests, pattern):
    module = importlib.import_module("agents.paper_reader.tests.test_researchmesh_paper_reader")
    return loader.loadTestsFromModule(module)
