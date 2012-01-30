from pypy.config.pypyoption import get_pypy_config
from pypy.jit.metainterp.history import TargetToken, ConstInt, History, Stats
from pypy.jit.metainterp.history import BoxInt, INT
from pypy.jit.metainterp.compile import compile_loop
from pypy.jit.metainterp.compile import ResumeGuardDescr
from pypy.jit.metainterp.compile import ResumeGuardCountersInt
from pypy.jit.metainterp.compile import compile_tmp_callback
from pypy.jit.metainterp import jitprof, typesystem, compile
from pypy.jit.metainterp.optimizeopt.test.test_util import LLtypeMixin
from pypy.jit.tool.oparser import parse
from pypy.jit.metainterp.optimizeopt import ALL_OPTS_DICT

class FakeCPU(object):
    ts = typesystem.llhelper
    def __init__(self):
        self.seen = []
    def compile_loop(self, inputargs, operations, token, name=''):
        self.seen.append((inputargs, operations, token))

class FakeLogger(object):
    def log_loop(self, inputargs, operations, number=0, type=None, ops_offset=None, name=''):
        pass

    def repr_of_resop(self, op):
        return repr(op)

class FakeState(object):
    enable_opts = ALL_OPTS_DICT.copy()
    enable_opts.pop('unroll')

    def attach_unoptimized_bridge_from_interp(*args):
        pass

    def get_location_str(self, args):
        return 'location'

class FakeGlobalData(object):
    loopnumbering = 0

class FakeMetaInterpStaticData(object):
    
    logger_noopt = FakeLogger()
    logger_ops = FakeLogger()
    config = get_pypy_config(translating=True)

    stats = Stats()
    profiler = jitprof.EmptyProfiler()
    warmrunnerdesc = None
    def log(self, msg, event_kind=None):
        pass

class FakeMetaInterp:
    call_pure_results = {}
    class jitdriver_sd:
        warmstate = FakeState()
        virtualizable_info = None

def test_compile_loop():
    cpu = FakeCPU()
    staticdata = FakeMetaInterpStaticData()
    staticdata.cpu = cpu
    staticdata.globaldata = FakeGlobalData()
    staticdata.globaldata.loopnumbering = 1
    #
    loop = parse('''
    [p1]
    i1 = getfield_gc(p1, descr=valuedescr)
    i2 = int_add(i1, 1)
    p2 = new_with_vtable(ConstClass(node_vtable))
    setfield_gc(p2, i2, descr=valuedescr)
    jump(p2)
    ''', namespace=LLtypeMixin.__dict__.copy())
    #
    metainterp = FakeMetaInterp()
    metainterp.staticdata = staticdata
    metainterp.cpu = cpu
    metainterp.history = History()
    metainterp.history.operations = loop.operations[:-1]
    metainterp.history.inputargs = loop.inputargs[:]
    cpu._all_size_descrs_with_vtable = (
        LLtypeMixin.cpu._all_size_descrs_with_vtable)
    #
    greenkey = 'faked'
    target_token = compile_loop(metainterp, greenkey, 0,
                                loop.inputargs,
                                loop.operations[-1].getarglist(),
                                None)
    jitcell_token = target_token.targeting_jitcell_token
    assert jitcell_token == target_token.original_jitcell_token
    assert jitcell_token.target_tokens == [target_token]
    assert jitcell_token.number == 1
    assert staticdata.globaldata.loopnumbering == 2
    #
    assert len(cpu.seen) == 1
    assert cpu.seen[0][2] == jitcell_token
    #
    del cpu.seen[:]

def test_resume_guard_counters():
    rgc = ResumeGuardCountersInt()
    # fill in the table
    for i in range(5):
        count = rgc.see_int(100+i)
        assert count == 1
        count = rgc.see_int(100+i)
        assert count == 2
        assert rgc.counters == [0] * (4-i) + [2] * (1+i)
    for i in range(5):
        count = rgc.see_int(100+i)
        assert count == 3
    # make a distribution:  [5, 4, 7, 6, 3]
    assert rgc.counters == [3, 3, 3, 3, 3]
    count = rgc.see_int(101)
    assert count == 4
    count = rgc.see_int(101)
    assert count == 5
    count = rgc.see_int(101)
    assert count == 6
    count = rgc.see_int(102)
    assert count == 4
    count = rgc.see_int(102)
    assert count == 5
    count = rgc.see_int(102)
    assert count == 6
    count = rgc.see_int(102)
    assert count == 7
    count = rgc.see_int(103)
    assert count == 4
    count = rgc.see_int(104)
    assert count == 4
    count = rgc.see_int(104)
    assert count == 5
    assert rgc.counters == [5, 4, 7, 6, 3]
    # the next new item should throw away 104, as 5 is the middle counter
    count = rgc.see_int(190)
    assert count == 1
    assert rgc.counters == [1, 4, 7, 6, 3]
    # the next new item should throw away 103, as 4 is the middle counter
    count = rgc.see_int(191)
    assert count == 1
    assert rgc.counters == [1, 1, 7, 6, 3]
    # the next new item should throw away 100, as 3 is the middle counter
    count = rgc.see_int(192)
    assert count == 1
    assert rgc.counters == [1, 1, 7, 6, 1]


def test_compile_tmp_callback():
    from pypy.jit.codewriter import heaptracker
    from pypy.jit.backend.llgraph import runner
    from pypy.rpython.lltypesystem import lltype, llmemory
    from pypy.rpython.annlowlevel import llhelper
    from pypy.rpython.llinterp import LLException
    #
    cpu = runner.LLtypeCPU(None)
    FUNC = lltype.FuncType([lltype.Signed]*4, lltype.Signed)
    def ll_portal_runner(g1, g2, r3, r4):
        assert (g1, g2, r3, r4) == (12, 34, -156, -178)
        if raiseme:
            raise raiseme
        else:
            return 54321
    #
    class FakeJitDriverSD:
        portal_runner_ptr = llhelper(lltype.Ptr(FUNC), ll_portal_runner)
        portal_runner_adr = llmemory.cast_ptr_to_adr(portal_runner_ptr)
        portal_calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT, None)
        portal_finishtoken = compile.DoneWithThisFrameDescrInt()
        num_red_args = 2
        result_type = INT
    #
    loop_token = compile_tmp_callback(cpu, FakeJitDriverSD(),
                                      [ConstInt(12), ConstInt(34)], "ii")
    #
    raiseme = None
    # only two arguments must be passed in
    fail_descr = cpu.execute_token(loop_token, -156, -178)
    assert fail_descr is FakeJitDriverSD().portal_finishtoken
    #
    EXC = lltype.GcStruct('EXC')
    llexc = lltype.malloc(EXC)
    raiseme = LLException("exception class", llexc)
    fail_descr = cpu.execute_token(loop_token, -156, -178)
    assert isinstance(fail_descr, compile.PropagateExceptionDescr)
    got = cpu.grab_exc_value()
    assert lltype.cast_opaque_ptr(lltype.Ptr(EXC), got) == llexc
    #
    class FakeMetaInterpSD:
        class ExitFrameWithExceptionRef(Exception):
            pass
    FakeMetaInterpSD.cpu = cpu
    fail_descr = cpu.execute_token(loop_token, -156, -178)
    try:
        fail_descr.handle_fail(FakeMetaInterpSD(), None)
    except FakeMetaInterpSD.ExitFrameWithExceptionRef, e:
        assert lltype.cast_opaque_ptr(lltype.Ptr(EXC), e.args[1]) == llexc
    else:
        assert 0, "should have raised"
