import sys

def pytest_ignore_collect(path):
    if '__pypy__' not in sys.builtin_module_names:
        return True
