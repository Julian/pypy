import py
from pypy.jit.backend.minimal.runner import LLtypeCPU, OOtypeCPU
from pypy.jit.metainterp.test import test_basic

class LLJitMixin(test_basic.LLJitMixin):
    CPUClass = LLtypeCPU

class OOJitMixin(test_basic.OOJitMixin):
    CPUClass = OOtypeCPU

class BasicTests(test_basic.BasicTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_basic.py

    def _skip(self):
        py.test.skip("call not supported in non-translated version")

    test_stopatxpolicy = _skip
    test_print = _skip
    test_bridge_from_interpreter_2 = _skip
    test_bridge_from_interpreter_3 = _skip
    test_instantiate_classes = _skip
    test_zerodivisionerror = _skip
    test_free_object = _skip


class TestOOtype(OOJitMixin, BasicTests):
    test_isinstance = BasicTests._skip
    test_r_dict = BasicTests._skip

class TestLLtype(LLJitMixin, BasicTests):
    pass

