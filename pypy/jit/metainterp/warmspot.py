import sys
from pypy.rpython.lltypesystem import lltype, llmemory, rclass
from pypy.rpython.annlowlevel import llhelper, MixLevelHelperAnnotator,\
     cast_base_ptr_to_instance
from pypy.annotation import model as annmodel
from pypy.rpython.llinterp import LLException
from pypy.rpython.test.test_llinterp import get_interpreter, clear_tcache
from pypy.objspace.flow.model import SpaceOperation, Variable, Constant
from pypy.objspace.flow.model import checkgraph, Link, copygraph
from pypy.rlib.objectmodel import we_are_translated, UnboxedValue, specialize
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib.jit import PARAMETERS
from pypy.rlib.rarithmetic import r_uint
from pypy.rlib.debug import debug_print
from pypy.rpython.lltypesystem.lloperation import llop

from pypy.jit.metainterp import support, history, pyjitpl
from pypy.jit.metainterp.pyjitpl import OOMetaInterp, Options
from pypy.jit.backend.llgraph import runner
from pypy.jit.metainterp.policy import JitPolicy

# ____________________________________________________________
# Bootstrapping

def apply_jit(translator, **kwds):
    from pypy.jit.backend.detect_cpu import getcpuclass
    warmrunnerdesc = WarmRunnerDesc(translator, CPUClass=getcpuclass(),
                                    translate_support_code=True,
                                    listops=True,
                                    **kwds)
    warmrunnerdesc.finish()

def ll_meta_interp(function, args, backendopt=False, **kwds):
    interp, graph = get_interpreter(function, args, backendopt=backendopt,
                                    inline_threshold=0)
    clear_tcache()
    return jittify_and_run(interp, graph, args, **kwds)

def jittify_and_run(interp, graph, args, **kwds):
    translator = interp.typer.annotator.translator
    warmrunnerdesc = WarmRunnerDesc(translator, **kwds)
    warmrunnerdesc.state.set_param_threshold(3)          # for tests
    warmrunnerdesc.state.set_param_trace_eagerness(2)    # for tests
    warmrunnerdesc.finish()
    return interp.eval_graph(graph, args)

def rpython_ll_meta_interp(function, args, backendopt=True,
                           loops='not used right now', **kwds):
    return ll_meta_interp(function, args, backendopt=backendopt,
                          translate_support_code=True, **kwds)

def find_can_enter_jit(graphs):
    results = []
    for graph in graphs:
        for block in graph.iterblocks():
            for i in range(len(block.operations)):
                op = block.operations[i]
                if (op.opname == 'jit_marker' and
                    op.args[0].value == 'can_enter_jit'):
                    results.append((graph, block, i))
    if not results:
        raise Exception("no can_enter_jit found!")
    return results

def find_jit_merge_point(graphs):
    results = []
    for graph in graphs:
        for block in graph.iterblocks():
            for i in range(len(block.operations)):
                op = block.operations[i]
                if (op.opname == 'jit_marker' and
                    op.args[0].value == 'jit_merge_point'):
                    results.append((graph, block, i))
    if len(results) != 1:
        raise Exception("found %d jit_merge_points, need exactly one!" %
                        (len(results),))
    return results[0]

def get_stats():
    return pyjitpl._warmrunnerdesc.stats

def debug_checks():
    stats = get_stats()
    stats.maybe_view()
    stats.check_consistency()

class JitException(Exception):
    _go_through_llinterp_uncaught_ = True     # ugh

# ____________________________________________________________

class WarmRunnerDesc:

    def __init__(self, translator, policy=None, **kwds):
        pyjitpl._warmrunnerdesc = self   # this is a global for debugging only!
        if policy is None:
            policy = JitPolicy()
        self.translator = translator
        self.build_meta_interp(**kwds)
        self.make_args_specification()
        self.metainterp.generate_bytecode(policy)
        self.make_enter_function()
        self.rewrite_can_enter_jit()
        self.rewrite_jit_merge_point()
        self.metainterp.num_green_args = self.num_green_args
        self.metainterp.state = self.state

    def finish(self):
        if self.cpu.translate_support_code:
            self.annhelper.finish()

    def _freeze_(self):
        return True

    def build_meta_interp(self, CPUClass=runner.CPU, view="auto",
                          translate_support_code=False, **kwds):
        opt = Options(**kwds)
        self.stats = history.Stats()
        if translate_support_code:
            self.annhelper = MixLevelHelperAnnotator(self.translator.rtyper)
            annhelper = self.annhelper
        else:
            annhelper = None
        cpu = CPUClass(self.translator.rtyper, self.stats,
                       translate_support_code, annhelper)
        self.cpu = cpu
        graphs = self.translator.graphs
        self.jit_merge_point_pos = find_jit_merge_point(graphs)
        graph, block, pos = self.jit_merge_point_pos
        graph = copygraph(graph)
        graph.startblock.isstartblock = False
        graph.startblock = support.split_before_jit_merge_point(
            *find_jit_merge_point([graph]))
        graph.startblock.isstartblock = True
        checkgraph(graph)
        for v in graph.getargs():
            assert isinstance(v, Variable)
        assert len(dict.fromkeys(graph.getargs())) == len(graph.getargs())
        self.translator.graphs.append(graph)
        self.portal_graph = graph
        self.jitdriver = block.operations[pos].args[1].value
        self.metainterp = OOMetaInterp(graph, graphs, cpu, self.stats, opt)

    def make_enter_function(self):
        WarmEnterState = make_state_class(self)
        state = WarmEnterState()
        self.state = state

        def crash_in_jit(e):
            if not we_are_translated():
                print "~~~ Crash in JIT!"
                print '~~~ %s: %s' % (e.__class__, e)
                if sys.stdout == sys.__stdout__:
                    import pdb; pdb.post_mortem(sys.exc_info()[2])
                raise
            debug_print('~~~ Crash in JIT!')
            debug_print('~~~ %s' % (e,))
            raise history.CrashInJIT("crash in JIT")
        crash_in_jit._dont_inline_ = True

        def maybe_enter_jit(*args):
            try:
                state.maybe_compile_and_run(*args)
            except JitException:
                raise     # go through
            except Exception, e:
                crash_in_jit(e)
        maybe_enter_jit._always_inline_ = True

        self.maybe_enter_jit_fn = maybe_enter_jit

    def make_args_specification(self):
        graph, block, index = self.jit_merge_point_pos
        op = block.operations[index]
        args = op.args[2:]
        ALLARGS = []
        self.green_args_spec = []
        for i, v in enumerate(args):
            TYPE = v.concretetype
            ALLARGS.append(TYPE)
            if i < len(self.jitdriver.greens):
                self.green_args_spec.append(TYPE)
        RESTYPE = graph.getreturnvar().concretetype
        self.JIT_ENTER_FUNCTYPE = lltype.FuncType(ALLARGS, lltype.Void)
        self.PORTAL_FUNCTYPE = lltype.FuncType(ALLARGS, RESTYPE)

    def rewrite_can_enter_jit(self):
        FUNC = self.JIT_ENTER_FUNCTYPE
        FUNCPTR = lltype.Ptr(FUNC)
        jit_enter_fnptr = self.helper_func(FUNCPTR, self.maybe_enter_jit_fn)

        graphs = self.translator.graphs
        can_enter_jits = find_can_enter_jit(graphs)
        for graph, block, index in can_enter_jits:
            if graph is self.jit_merge_point_pos[0]:
                continue

            op = block.operations[index]
            greens_v, reds_v = decode_hp_hint_args(op)
            args_v = greens_v + reds_v

            vlist = [Constant(jit_enter_fnptr, FUNCPTR)] + args_v

            v_result = Variable()
            v_result.concretetype = lltype.Void
            newop = SpaceOperation('direct_call', vlist, v_result)
            block.operations[index] = newop

    def helper_func(self, FUNCPTR, func):
        if not self.cpu.translate_support_code:
            return llhelper(FUNCPTR, func)
        FUNC = FUNCPTR.TO
        args_s = [annmodel.lltype_to_annotation(ARG) for ARG in FUNC.ARGS]
        s_result = annmodel.lltype_to_annotation(FUNC.RESULT)
        return self.annhelper.delayedfunction(func, args_s, s_result)

    def rewrite_jit_merge_point(self):
        #
        # Mutate the original portal graph from this:
        #
        #       def original_portal(..):
        #           stuff
        #           while 1:
        #               jit_merge_point(*args)
        #               more stuff
        #
        # to that:
        #
        #       def original_portal(..):
        #           stuff
        #           return portal_runner(*args)
        #
        #       def portal_runner(*args):
        #           while 1:
        #               try:
        #                   return portal(*args)
        #               except ContinueRunningNormally, e:
        #                   *args = *e.new_args
        #               except DoneWithThisFrame, e:
        #                   return e.result
        #               except ExitFrameWithException, e:
        #                   raise e.type, e.value
        #
        #       def portal(*args):
        #           while 1:
        #               more stuff
        #
        origportalgraph = self.jit_merge_point_pos[0]
        portalgraph = self.portal_graph
        PORTALFUNC = self.PORTAL_FUNCTYPE

        # ____________________________________________________________
        # Prepare the portal_runner() helper
        #
        portal_ptr = lltype.functionptr(PORTALFUNC, 'portal',
                                        graph = portalgraph)

        class DoneWithThisFrame(JitException):
            def __init__(self, resultbox):
                self.resultbox = resultbox
            def __str__(self):
                return 'DoneWithThisFrame(%s)' % (self.result,)

        class ExitFrameWithException(JitException):
            def __init__(self, typebox, valuebox):
                self.typebox = typebox
                self.valuebox = valuebox
            def __str__(self):
                return 'ExitFrameWithException(%s, %s)' % (self.type,
                                                           self.value)

        class ContinueRunningNormally(JitException):
            def __init__(self, args):
                self.args = args

            def __str__(self):
                return 'ContinueRunningNormally(%s)' % (
                    ', '.join(map(str, self.args)),)

        self.DoneWithThisFrame = DoneWithThisFrame
        self.ExitFrameWithException = ExitFrameWithException
        self.ContinueRunningNormally = ContinueRunningNormally
        self.metainterp.DoneWithThisFrame = DoneWithThisFrame
        self.metainterp.ExitFrameWithException = ExitFrameWithException
        self.metainterp.ContinueRunningNormally = ContinueRunningNormally
        rtyper = self.translator.rtyper
        portalfunc_ARGS = unrolling_iterable(list(enumerate(PORTALFUNC.ARGS)))
        RESULT = PORTALFUNC.RESULT

        def unwrap(TYPE, box):
            if TYPE is lltype.Void:
                return None
            if isinstance(TYPE, lltype.Ptr):
                return box.getptr(TYPE)
            else:
                return lltype.cast_primitive(TYPE, box.getint())
        unwrap._annspecialcase_ = 'specialize:arg(0)'

        def ll_portal_runner(*args):
            while 1:
                try:
                    return support.maybe_on_top_of_llinterp(rtyper,
                                                      portal_ptr)(*args)
                except ContinueRunningNormally, e:
                    args = ()
                    for i, ARG in portalfunc_ARGS:
                        v = unwrap(ARG, e.args[i])
                        args = args + (v,)
                except DoneWithThisFrame, e:
                    return unwrap(RESULT, e.resultbox)
                except ExitFrameWithException, e:
                    value = e.valuebox.getptr(lltype.Ptr(rclass.OBJECT))
                    if not we_are_translated():
                        type = e.typebox.getaddr(self.metainterp.cpu)
                        type = llmemory.cast_adr_to_ptr(type, rclass.CLASSTYPE)
                        raise LLException(type, value)
                    else:
                        value = cast_base_ptr_to_instance(Exception, value)
                        raise Exception, value

        portal_runner_ptr = self.helper_func(lltype.Ptr(PORTALFUNC),
                                             ll_portal_runner)

        # ____________________________________________________________
        # Now mutate origportalgraph to end with a call to portal_runner_ptr
        #
        _, origblock, origindex = self.jit_merge_point_pos
        op = origblock.operations[origindex]
        assert op.opname == 'jit_marker'
        assert op.args[0].value == 'jit_merge_point'
        greens_v, reds_v = decode_hp_hint_args(op)
        vlist = [Constant(portal_runner_ptr, lltype.Ptr(PORTALFUNC))]
        vlist += greens_v
        vlist += reds_v
        v_result = Variable()
        v_result.concretetype = PORTALFUNC.RESULT
        newop = SpaceOperation('direct_call', vlist, v_result)
        del origblock.operations[origindex:]
        origblock.operations.append(newop)
        origblock.exitswitch = None
        origblock.recloseblock(Link([v_result], origportalgraph.returnblock))
        checkgraph(origportalgraph)


def decode_hp_hint_args(op):
    # Returns (list-of-green-vars, list-of-red-vars) without Voids.
    assert op.opname == 'jit_marker'
    jitdriver = op.args[1].value
    numgreens = len(jitdriver.greens)
    numreds = len(jitdriver.reds)
    greens_v = op.args[2:2+numgreens]
    reds_v = op.args[2+numgreens:]
    assert len(reds_v) == numreds
    return ([v for v in greens_v if v.concretetype is not lltype.Void],
            [v for v in reds_v if v.concretetype is not lltype.Void])

def cast_whatever_to_int(TYPE, x):
    if isinstance(TYPE, lltype.Ptr):
        return lltype.cast_ptr_to_int(x)
    else:
        return lltype.cast_primitive(lltype.Signed, x)
cast_whatever_to_int._annspecialcase_ = 'specialize:arg(0)'

# ____________________________________________________________

def make_state_class(warmrunnerdesc):
    jitdriver = warmrunnerdesc.jitdriver
    num_green_args = len(jitdriver.greens)
    warmrunnerdesc.num_green_args = num_green_args
    green_args_spec = unrolling_iterable(warmrunnerdesc.green_args_spec)
    green_args_names = unrolling_iterable(jitdriver.greens)
    if num_green_args:
        MAX_HASH_TABLE_BITS = 28
    else:
        MAX_HASH_TABLE_BITS = 0
    THRESHOLD_MAX = (sys.maxint-1) / 2

    class StateCell(object):
        __slots__ = []

    class Counter(StateCell, UnboxedValue):
        __slots__ = 'counter'

    class MachineCodeEntryPoint(StateCell):
        def __init__(self, mp, *greenargs):
            self.mp = mp
            self.next = Counter(0)
            i = 0
            for name in green_args_names:
                setattr(self, 'green_' + name, greenargs[i])
                i += 1
        def equalkey(self, *greenargs):
            i = 0
            for name in green_args_names:
                if getattr(self, 'green_' + name) != greenargs[i]:
                    return False
                i += 1
            return True

    class WarmEnterState:
        #NULL_MC = lltype.nullptr(hotrunnerdesc.RESIDUAL_FUNCTYPE)

        def __init__(self):
            # initialize the state with the default values of the
            # parameters specified in rlib/jit.py
            for name, default_value in PARAMETERS.items():
                meth = getattr(self, 'set_param_' + name)
                meth(default_value)

        def set_param_threshold(self, threshold):
            if threshold > THRESHOLD_MAX:
                threshold = THRESHOLD_MAX
            self.threshold = threshold

        def set_param_trace_eagerness(self, value):
            self.trace_eagerness = value

        def set_param_hash_bits(self, value):
            if value < 0:
                value = 0
            elif value > MAX_HASH_TABLE_BITS:
                value = MAX_HASH_TABLE_BITS
            self.cells = [Counter(0)] * (1 << value)
            self.hashtablemask = (1 << value) - 1

            # Only use the hash of the arguments as the profiling key.
            # Indeed, this is all a heuristic, so if things are designed
            # correctly, the occasional mistake due to hash collision is
            # not too bad.

        def maybe_compile_and_run(self, *args):
            greenargs = args[:num_green_args]
            argshash = self.getkeyhash(*greenargs)
            argshash &= self.hashtablemask
            cell = self.cells[argshash]
            if isinstance(cell, Counter):
                # update the profiling counter
                n = cell.counter + 1
                if n < self.threshold:
                    #if hotrunnerdesc.verbose_level >= 3:
                    #    interp.debug_trace("jit_not_entered", *args)
                    self.cells[argshash] = Counter(n)
                    return
                #interp.debug_trace("jit_compile", *greenargs)
                self.compile_and_run(argshash, *args)
            else:
                raise NotImplementedError("bridges to compiled code")
                # machine code was already compiled for these greenargs
                # (or we have a hash collision)
                assert isinstance(cell, MachineCodeEntryPoint)
                if cell.equalkey(*greenargs):
                    self.run(cell, *args)
                else:
                    xxx
                    self.handle_hash_collision(cell, argshash, *args)
        maybe_compile_and_run._dont_inline_ = True

        def handle_hash_collision(self, cell, argshash, *args):
            greenargs = args[:num_green_args]
            next = cell.next
            while not isinstance(next, Counter):
                assert isinstance(next, MachineCodeEntryPoint)
                if next.equalkey(*greenargs):
                    # found, move to the front of the linked list
                    cell.next = next.next
                    next.next = self.cells[argshash]
                    self.cells[argshash] = next
                    return next.mc
                cell = next
                next = cell.next
            # not found at all, do profiling
            interp = hotrunnerdesc.interpreter
            n = next.counter + 1
            if n < self.threshold:
                if hotrunnerdesc.verbose_level >= 3:
                    interp.debug_trace("jit_not_entered", *args)
                cell.next = Counter(n)
                return self.NULL_MC
            interp.debug_trace("jit_compile", *greenargs)
            return self.compile(argshash, *args)
        handle_hash_collision._dont_inline_ = True

        def getkeyhash(self, *greenargs):
            result = r_uint(0x345678)
            i = 0
            mult = r_uint(1000003)
            for TYPE in green_args_spec:
                if i > 0:
                    result = result * mult
                    mult = mult + 82520 + 2*len(greenargs)
                item = greenargs[i]
                result = result ^ cast_whatever_to_int(TYPE, item)
                i = i + 1
            return result
        getkeyhash._always_inline_ = True

        def compile_and_run(self, argshash, *args):
            loop, boxes = warmrunnerdesc.metainterp.compile_and_run_once(*args)
            if loop:
                cpu = warmrunnerdesc.metainterp.cpu
                operations = loop.operations
                box = cpu.execute_operations_in_new_frame('run_this_loop',
                                                          operations, boxes)
                raise warmrunnerdesc.DoneWithThisFrame(box)

        def must_compile_from_failure(self, guard_failure):
            guard_op = guard_failure.guard_op
            guard_op.counter += 1
            return guard_op.counter >= self.trace_eagerness

    return WarmEnterState
