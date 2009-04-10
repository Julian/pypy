
from pypy.rpython.ootypesystem import ootype
from pypy.objspace.flow.model import Constant, Variable
from pypy.rlib.objectmodel import we_are_translated
from pypy.conftest import option

from pypy.jit.metainterp.resoperation import ResOperation, rop
from pypy.jit.metainterp.history import TreeLoop, log, Box, History
from pypy.jit.metainterp.history import AbstractDescr, BoxInt, BoxPtr
from pypy.jit.metainterp.specnode import NotSpecNode
from pypy.rlib.debug import debug_print

def compile_new_loop(metainterp, old_loops, greenkey):
    """Try to compile a new loop by closing the current history back
    to the first operation.
    """
    if we_are_translated():
        return compile_fresh_loop(metainterp, old_loops, greenkey)
    else:
        return _compile_new_loop_1(metainterp, old_loops, greenkey)

def compile_new_bridge(metainterp, old_loops, resumekey):
    """Try to compile a new bridge leading from the beginning of the history
    to some existing place.
    """
    if we_are_translated():
        return compile_fresh_bridge(metainterp, old_loops, resumekey)
    else:
        return _compile_new_bridge_1(metainterp, old_loops, resumekey)

class BridgeInProgress(Exception):
    pass


# the following is not translatable
def _compile_new_loop_1(metainterp, old_loops, greenkey):
    old_loops_1 = old_loops[:]
    try:
        loop = compile_fresh_loop(metainterp, old_loops, greenkey)
    except Exception, exc:
        show_loop(metainterp, error=exc)
        raise
    else:
        if loop in old_loops_1:
            log.info("reusing loop at %r" % (loop,))
        else:
            show_loop(metainterp, loop)
    loop.check_consistency()
    return loop

def _compile_new_bridge_1(metainterp, old_loops, resumekey):
    try:
        target_loop = compile_fresh_bridge(metainterp, old_loops,
                                           resumekey)
    except Exception, exc:
        show_loop(metainterp, error=exc)
        raise
    else:
        if target_loop is not None:
            show_loop(metainterp, target_loop)
    if target_loop is not None and target_loop not in map_loop2descr:
        target_loop.check_consistency()
    return target_loop

def show_loop(metainterp, loop=None, error=None):
    # debugging
    if option.view:
        if error:
            errmsg = error.__class__.__name__
            if str(error):
                errmsg += ': ' + str(error)
        else:
            errmsg = None
        if loop is None or loop in map_loop2descr:
            extraloops = []
        else:
            extraloops = [loop]
        metainterp.staticdata.stats.view(errmsg=errmsg, extraloops=extraloops)

def create_empty_loop(metainterp):
    if we_are_translated():
        name = 'Loop'
    else:
        name = 'Loop #%d' % len(metainterp.staticdata.stats.loops)
    return TreeLoop(name)

# ____________________________________________________________

def compile_fresh_loop(metainterp, old_loops, greenkey):
    history = metainterp.history
    loop = create_empty_loop(metainterp)
    loop.greenkey = greenkey
    loop.inputargs = history.inputargs
    loop.operations = history.operations
    loop.operations[-1].jump_target = loop
    metainterp_sd = metainterp.staticdata
    old_loop = metainterp_sd.optimize_loop(metainterp_sd.options, old_loops,
                                           loop, metainterp.cpu)
    if old_loop is not None:
        if we_are_translated():
            debug_print("reusing old loop")
        return old_loop
    history.source_link = loop
    send_loop_to_backend(metainterp, loop, "loop")
    metainterp.staticdata.stats.loops.append(loop)
    old_loops.append(loop)
    return loop

def send_loop_to_backend(metainterp, loop, type):
    metainterp.cpu.compile_operations(loop)
    if not we_are_translated():
        if type != "entry bridge":
            metainterp.staticdata.stats.compiled_count += 1
        else:
            loop._ignore_during_counting = True
        log.info("compiled new " + type)
    else:
        debug_print("compiled new " + type)

# ____________________________________________________________

class DoneWithThisFrameDescr0(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        raise metainterp_sd.DoneWithThisFrame(None)

class DoneWithThisFrameDescr1(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        resultbox = fail_op.args[0]
        raise metainterp_sd.DoneWithThisFrame(resultbox)

class ExitFrameWithExceptionDescr(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert len(fail_op.args) == 1
        valuebox = fail_op.args[0]
        raise metainterp_sd.ExitFrameWithException(valuebox)

done_with_this_frame_descr_0 = DoneWithThisFrameDescr0()
done_with_this_frame_descr_1 = DoneWithThisFrameDescr1()
exit_frame_with_exception_descr = ExitFrameWithExceptionDescr()
map_loop2descr = {}

# pseudo-loops to make the life of optimize.py easier
_loop = TreeLoop('done_with_this_frame_int')
_loop.specnodes = [NotSpecNode()]
_loop.inputargs = [BoxInt()]
loops_done_with_this_frame_int = [_loop]
map_loop2descr[_loop] = done_with_this_frame_descr_1

_loop = TreeLoop('done_with_this_frame_ptr')
_loop.specnodes = [NotSpecNode()]
_loop.inputargs = [BoxPtr()]
loops_done_with_this_frame_ptr = [_loop]
map_loop2descr[_loop] = done_with_this_frame_descr_1

_loop = TreeLoop('done_with_this_frame_void')
_loop.specnodes = []
_loop.inputargs = []
loops_done_with_this_frame_void = [_loop]
map_loop2descr[_loop] = done_with_this_frame_descr_0

_loop = TreeLoop('exit_frame_with_exception')
_loop.specnodes = [NotSpecNode()]
_loop.inputargs = [BoxPtr()]
loops_exit_frame_with_exception = [_loop]
map_loop2descr[_loop] = exit_frame_with_exception_descr
del _loop


class ResumeGuardDescr(AbstractDescr):
    def __init__(self, resume_info, history, history_guard_index):
        self.resume_info = resume_info
        self.counter = 0
        self.history = history
        assert history_guard_index >= 0
        self.history_guard_index = history_guard_index

    def handle_fail_op(self, metainterp_sd, fail_op):
        from pypy.jit.metainterp.pyjitpl import MetaInterp
        metainterp = MetaInterp(metainterp_sd)
        return metainterp.handle_guard_failure(fail_op, self)

    def get_guard_op(self):
        guard_op = self.history.operations[self.history_guard_index]
        assert guard_op.is_guard()
        if guard_op.optimized is not None:   # should always be the case,
            return guard_op.optimized        # except if not optimizing at all
        else:
            return guard_op

    def compile_and_attach(self, metainterp, new_loop):
        # We managed to create a bridge.  Attach the new operations
        # to the existing source_loop and recompile the whole thing.
        source_loop = self.find_source_loop()
        metainterp.history.source_link = self.history
        metainterp.history.source_guard_index = self.history_guard_index
        guard_op = self.get_guard_op()
        guard_op.suboperations = new_loop.operations
        send_loop_to_backend(metainterp, source_loop, "bridge")

    def find_source_loop(self):
        # Find the TreeLoop object that contains this guard operation.
        source_loop = self.history.source_link
        while not isinstance(source_loop, TreeLoop):
            source_loop = source_loop.source_link
        return source_loop

    def find_toplevel_history(self):
        # Find the History that describes the start of the loop containing this
        # guard operation.
        history = self.history
        prevhistory = history.source_link
        while isinstance(prevhistory, History):
            history = prevhistory
            prevhistory = history.source_link
        return history


class ResumeFromInterpDescr(AbstractDescr):
    def __init__(self, original_boxes):
        self.original_boxes = original_boxes

    def compile_and_attach(self, metainterp, new_loop):
        # We managed to create a bridge going from the interpreter
        # to previously-compiled code.  We keep 'new_loop', which is not
        # a loop at all but ends in a jump to the target loop.  It starts
        # with completely unoptimized arguments, as in the interpreter.
        metainterp_sd = metainterp.staticdata
        num_green_args = metainterp_sd.num_green_args
        greenkey = self.original_boxes[:num_green_args]
        redkey = self.original_boxes[num_green_args:]
        metainterp.history.source_link = new_loop
        metainterp.history.inputargs = redkey
        new_loop.greenkey = greenkey
        new_loop.inputargs = redkey
        send_loop_to_backend(metainterp, new_loop, "entry bridge")
        metainterp_sd.stats.loops.append(new_loop)
        # send the new_loop to warmspot.py, to be called directly the next time
        metainterp_sd.state.attach_unoptimized_bridge_from_interp(greenkey,
                                                                  new_loop)
        # store the new_loop in compiled_merge_points too
        # XXX it's probably useless to do so when optimizing
        glob = metainterp_sd.globaldata
        old_loops = glob.compiled_merge_points.setdefault(greenkey, [])
        old_loops.append(new_loop)


def compile_fresh_bridge(metainterp, old_loops, resumekey):
    # The history contains new operations to attach as the code for the
    # failure of 'resumekey.guard_op'.
    #
    # Attempt to use optimize_bridge().  This may return None in case
    # it does not work -- i.e. none of the existing old_loops match.
    new_loop = create_empty_loop(metainterp)
    new_loop.operations = metainterp.history.operations
    metainterp_sd = metainterp.staticdata
    target_loop = metainterp_sd.optimize_bridge(metainterp_sd.options,
                                                old_loops, new_loop,
                                                metainterp.cpu)
    # Did it work?  If not, prepare_loop_from_bridge() will probably be used.
    if target_loop is not None:
        # Yes, we managed to create a bridge.  Dispatch to resumekey to
        # know exactly what we must do (ResumeGuardDescr/ResumeFromInterpDescr)
        prepare_last_operation(new_loop, target_loop)
        resumekey.compile_and_attach(metainterp, new_loop)
    return target_loop

def prepare_last_operation(new_loop, target_loop):
    op = new_loop.operations[-1]
    if target_loop not in map_loop2descr:
        # normal case
        op.jump_target = target_loop
    else:
        # The target_loop is a pseudo-loop done_with_this_frame.  Replace
        # the operation with the real operation we want, i.e. a FAIL.
        descr = map_loop2descr[target_loop]
        new_op = ResOperation(rop.FAIL, op.args, None, descr=descr)
        new_loop.operations[-1] = new_op


def prepare_loop_from_bridge(metainterp, resumekey):
    # To handle this case, we prepend to the history the unoptimized
    # operations coming from the loop, in order to make a (fake) complete
    # unoptimized trace.  (Then we will just compile this loop normally.)
    raise PrepareLoopFromBridgeIsDisabled
    if not we_are_translated():
        log.info("completing the bridge into a stand-alone loop")
    else:
        debug_print("completing the bridge into a stand-alone loop")
    operations = metainterp.history.operations
    metainterp.history.operations = []
    assert isinstance(resumekey, ResumeGuardDescr)
    append_full_operations(metainterp.history,
                           resumekey.history,
                           resumekey.history_guard_index)
    metainterp.history.operations.extend(operations)

def append_full_operations(history, sourcehistory, guard_index):
    prev = sourcehistory.source_link
    if isinstance(prev, History):
        append_full_operations(history, prev, sourcehistory.source_guard_index)
    history.operations.extend(sourcehistory.operations[:guard_index])
    op = inverse_guard(sourcehistory.operations[guard_index])
    history.operations.append(op)

def inverse_guard(guard_op):
    suboperations = guard_op.suboperations
    assert guard_op.is_guard()
    if guard_op.opnum == rop.GUARD_TRUE:
        guard_op = ResOperation(rop.GUARD_FALSE, guard_op.args, None)
    elif guard_op.opnum == rop.GUARD_FALSE:
        guard_op = ResOperation(rop.GUARD_TRUE, guard_op.args, None)
    else:
        # XXX other guards have no inverse so far
        raise InverseTheOtherGuardsPlease(guard_op)
    #
    guard_op.suboperations = suboperations
    return guard_op

class InverseTheOtherGuardsPlease(Exception):
    pass

class PrepareLoopFromBridgeIsDisabled(Exception):
    pass
