import py
from pypy.jit.timeshifter.test import test_vlist
from pypy.jit.codegen.i386.test.test_interp_ts import I386LLInterpTimeshiftingTestMixin


class TestVList(I386LLInterpTimeshiftingTestMixin,
                test_vlist.TestVList):

    # for the individual tests see
    # ====> ../../../timeshifter/test/test_vlist.py

    pass
