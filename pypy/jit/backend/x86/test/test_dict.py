
from pypy.jit.backend.x86.test.test_basic import Jit386Mixin
from pypy.jit.metainterp.test.test_dict import DictTests


class TestDict(Jit386Mixin, DictTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_dict.py
    pass
