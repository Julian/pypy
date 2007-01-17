import operator, weakref
from pypy.annotation import model as annmodel
from pypy.rpython.lltypesystem import lltype, lloperation, llmemory
from pypy.jit.hintannotator.model import originalconcretetype
from pypy.jit.timeshifter import rvalue, rcontainer
from pypy.rlib.unroll import unrolling_iterable
from pypy.rpython.annlowlevel import cachedtype, base_ptr_lltype
from pypy.rpython.annlowlevel import cast_instance_to_base_ptr
from pypy.rpython.annlowlevel import cast_base_ptr_to_instance

FOLDABLE_OPS = dict.fromkeys(lloperation.enum_foldable_ops())

FOLDABLE_GREEN_OPS = FOLDABLE_OPS.copy()
FOLDABLE_GREEN_OPS['getfield'] = None
FOLDABLE_GREEN_OPS['getarrayitem'] = None

debug_view = lloperation.llop.debug_view
debug_print = lloperation.llop.debug_print

# ____________________________________________________________
# emit ops


class OpDesc(object):
    """
    Description of a low-level operation
    that can be passed around to low level helpers
    to inform op generation
    """
    
    def _freeze_(self):
        return True

    def __init__(self, RGenOp, opname, ARGS, RESULT):
        self.RGenOp = RGenOp
        self.opname = opname
        self.llop = lloperation.LL_OPERATIONS[opname]
        self.nb_args = len(ARGS)
        self.ARGS = ARGS
        self.RESULT = RESULT
        self.result_kind = RGenOp.kindToken(RESULT)
        self.redboxcls = rvalue.ll_redboxcls(RESULT)
        self.canfold = opname in FOLDABLE_OPS

    def __getattr__(self, name): # .ARGx -> .ARGS[x]
        if name.startswith('ARG'):
            index = int(name[3:])
            return self.ARGS[index]
        else:
            raise AttributeError("don't know about %r in OpDesc" % name)

    def compact_repr(self): # goes in ll helper names
        return self.opname.upper()

_opdesc_cache = {}

def make_opdesc(hop):
    hrtyper = hop.rtyper
    op_key = (hrtyper.RGenOp, hop.spaceop.opname,
              tuple([originalconcretetype(s_arg) for s_arg in hop.args_s]),
              originalconcretetype(hop.s_result))
    try:
        return _opdesc_cache[op_key]
    except KeyError:
        opdesc = OpDesc(*op_key)
        _opdesc_cache[op_key] = opdesc
        return opdesc

def ll_gen1(opdesc, jitstate, argbox):
    ARG0 = opdesc.ARG0
    RESULT = opdesc.RESULT
    opname = opdesc.name
    if opdesc.canfold and argbox.is_constant():
        arg = rvalue.ll_getvalue(argbox, ARG0)
        res = opdesc.llop(RESULT, arg)
        return rvalue.ll_fromvalue(jitstate, res)
    gv_arg = argbox.getgenvar(jitstate)
    genvar = jitstate.curbuilder.genop1(opdesc.opname, gv_arg)
    return opdesc.redboxcls(opdesc.result_kind, genvar)

def ll_gen2(opdesc, jitstate, argbox0, argbox1):
    ARG0 = opdesc.ARG0
    ARG1 = opdesc.ARG1
    RESULT = opdesc.RESULT
    opname = opdesc.name
    if opdesc.canfold and argbox0.is_constant() and argbox1.is_constant():
        # const propagate
        arg0 = rvalue.ll_getvalue(argbox0, ARG0)
        arg1 = rvalue.ll_getvalue(argbox1, ARG1)
        res = opdesc.llop(RESULT, arg0, arg1)
        return rvalue.ll_fromvalue(jitstate, res)
    gv_arg0 = argbox0.getgenvar(jitstate)
    gv_arg1 = argbox1.getgenvar(jitstate)
    genvar = jitstate.curbuilder.genop2(opdesc.opname, gv_arg0, gv_arg1)
    return opdesc.redboxcls(opdesc.result_kind, genvar)

def ll_genmalloc_varsize(jitstate, contdesc, sizebox):
    gv_size = sizebox.getgenvar(jitstate)
    alloctoken = contdesc.varsizealloctoken
    genvar = jitstate.curbuilder.genop_malloc_varsize(alloctoken, gv_size)
    return rvalue.PtrRedBox(contdesc.ptrkind, genvar)

def ll_gengetfield(jitstate, deepfrozen, fielddesc, argbox):
    if (fielddesc.immutable or deepfrozen) and argbox.is_constant():
        res = getattr(rvalue.ll_getvalue(argbox, fielddesc.PTRTYPE),
                      fielddesc.fieldname)
        return rvalue.ll_fromvalue(jitstate, res)
    return argbox.op_getfield(jitstate, fielddesc)

def ll_gensetfield(jitstate, fielddesc, destbox, valuebox):
    destbox.op_setfield(jitstate, fielddesc, valuebox)

def ll_gengetsubstruct(jitstate, fielddesc, argbox):
    if argbox.is_constant():
        res = getattr(rvalue.ll_getvalue(argbox, fielddesc.PTRTYPE),
                      fielddesc.fieldname)
        return rvalue.ll_fromvalue(jitstate, res)
    return argbox.op_getsubstruct(jitstate, fielddesc)

def ll_gengetarrayitem(jitstate, deepfrozen, fielddesc, argbox, indexbox):
    if ((fielddesc.immutable or deepfrozen) and argbox.is_constant()
                                            and indexbox.is_constant()):
        array = rvalue.ll_getvalue(argbox, fielddesc.PTRTYPE)
        res = array[rvalue.ll_getvalue(indexbox, lltype.Signed)]
        return rvalue.ll_fromvalue(jitstate, res)
    genvar = jitstate.curbuilder.genop_getarrayitem(
        fielddesc.arraytoken,
        argbox.getgenvar(jitstate),
        indexbox.getgenvar(jitstate))
                                                    
    return fielddesc.makebox(jitstate, genvar)

def ll_gengetarraysubstruct(jitstate, fielddesc, argbox, indexbox):
    if argbox.is_constant() and indexbox.is_constant():
        array = rvalue.ll_getvalue(argbox, fielddesc.PTRTYPE)
        res = array[rvalue.ll_getvalue(indexbox, lltype.Signed)]
        return rvalue.ll_fromvalue(jitstate, res)
    genvar = jitstate.curbuilder.genop_getarraysubstruct(
        fielddesc.arraytoken,
        argbox.getgenvar(jitstate),
        indexbox.getgenvar(jitstate))
                                                    
    return fielddesc.makebox(jitstate, genvar)


def ll_gensetarrayitem(jitstate, fielddesc, destbox, indexbox, valuebox):
    genvar = jitstate.curbuilder.genop_setarrayitem(
        fielddesc.arraytoken,
        destbox.getgenvar(jitstate),
        indexbox.getgenvar(jitstate),
        valuebox.getgenvar(jitstate)
        )

def ll_gengetarraysize(jitstate, fielddesc, argbox):
    if argbox.is_constant():
        array = rvalue.ll_getvalue(argbox, fielddesc.PTRTYPE)
        res = len(array)
        return rvalue.ll_fromvalue(jitstate, res)
    genvar = jitstate.curbuilder.genop_getarraysize(
        fielddesc.arraytoken,
        argbox.getgenvar(jitstate))
    return rvalue.IntRedBox(fielddesc.indexkind, genvar)

def ll_genptrnonzero(jitstate, argbox, reverse):
    if argbox.is_constant():
        addr = rvalue.ll_getvalue(argbox, llmemory.Address)
        return rvalue.ll_fromvalue(jitstate, bool(addr) ^ reverse)
    builder = jitstate.curbuilder
    if argbox.content is None:
        gv_addr = argbox.getgenvar(jitstate)
        if reverse:
            gv_res = builder.genop1("ptr_iszero", gv_addr)
        else:
            gv_res = builder.genop1("ptr_nonzero", gv_addr)
    else:
        gv_res = builder.rgenop.genconst(True ^ reverse)
    return rvalue.IntRedBox(builder.rgenop.kindToken(lltype.Bool), gv_res)

def ll_genptreq(jitstate, argbox0, argbox1, reverse):
    builder = jitstate.curbuilder
    if argbox0.content is not None or argbox1.content is not None:
        equal = argbox0.content is argbox1.content
        return rvalue.ll_fromvalue(jitstate, equal ^ reverse)
    elif argbox0.is_constant() and argbox1.is_constant():
        addr0 = rvalue.ll_getvalue(argbox0, llmemory.Address)
        addr1 = rvalue.ll_getvalue(argbox1, llmemory.Address)
        return rvalue.ll_fromvalue(jitstate, (addr0 == addr1) ^ reverse)
    gv_addr0 = argbox0.getgenvar(jitstate)
    gv_addr1 = argbox1.getgenvar(jitstate)
    if reverse:
        gv_res = builder.genop2("ptr_ne", gv_addr0, gv_addr1)
    else:
        gv_res = builder.genop2("ptr_eq", gv_addr0, gv_addr1)
    return rvalue.IntRedBox(builder.rgenop.kindToken(lltype.Bool), gv_res)

# ____________________________________________________________
# other jitstate/graph level operations

def enter_next_block(jitstate, incoming):
    linkargs = []
    kinds = []
    for redbox in incoming:
        linkargs.append(redbox.genvar)
        kinds.append(redbox.kind)
    newblock = jitstate.curbuilder.enter_next_block(kinds, linkargs)
    for i in range(len(incoming)):
        incoming[i].genvar = linkargs[i]
    return newblock

def return_marker(jitstate):
    raise AssertionError("shouldn't get here")

def start_new_block(states_dic, jitstate, key, global_resumer, index=-1):
    memo = rvalue.freeze_memo()
    frozen = jitstate.freeze(memo)
    memo = rvalue.exactmatch_memo()
    outgoingvarboxes = []
    res = frozen.exactmatch(jitstate, outgoingvarboxes, memo)
    assert res, "exactmatch() failed"
    cleanup_partial_data(memo.partialdatamatch)
    newblock = enter_next_block(jitstate, outgoingvarboxes)
    if index < 0:
        states_dic[key].append((frozen, newblock))
    else:
        states_dic[key][index] = (frozen, newblock)
        
    if global_resumer is not None and global_resumer is not return_marker:
        jitstate.curbuilder.log('start_new_block %s' % (key,))
        greens_gv = jitstate.greens
        rgenop = jitstate.curbuilder.rgenop
        node = PromotionPathRoot(greens_gv, rgenop,
                                 frozen, newblock,
                                 global_resumer)
        jitstate.frame.dispatchqueue.mergecounter = 0
        jitstate.promotion_path = PromotionPathMergesToSee(node, 0)
        #debug_print(lltype.Void, "PROMOTION ROOT")
start_new_block._annspecialcase_ = "specialize:arglltype(2)"

def retrieve_jitstate_for_merge(states_dic, jitstate, key, global_resumer,
                                force_merge=False):
    if jitstate.virtualizables:
        jitstate.enter_block_sweep_virtualizables()
    if key not in states_dic:
        states_dic[key] = []
        start_new_block(states_dic, jitstate, key, global_resumer)
        return False   # continue

    states = states_dic[key]
    for i in range(len(states) -1, -1, -1):
        frozen, oldblock =  states[i]
        memo = rvalue.exactmatch_memo(force_merge)
        outgoingvarboxes = []
        
        try:
            match = frozen.exactmatch(jitstate, outgoingvarboxes, memo)
        except rvalue.DontMerge:
            continue
        if match:
            linkargs = []
            for box in outgoingvarboxes:
                linkargs.append(box.getgenvar(jitstate))
            jitstate.curbuilder.finish_and_goto(linkargs, oldblock)
            return True    # finished
        # A mergable blook found
        # We need a more general block.  Do it by generalizing all the
        # redboxes from outgoingvarboxes, by making them variables.
        # Then we make a new block based on this new state.
        cleanup_partial_data(memo.partialdatamatch)
        replace_memo = rvalue.copy_memo()
        for box in outgoingvarboxes:
            box.forcevar(jitstate, replace_memo)
        if replace_memo.boxes:
            jitstate.replace(replace_memo)
        start_new_block(states_dic, jitstate, key, global_resumer, index=i)
        if global_resumer is None:
            merge_generalized(jitstate)
        return False       # continue

    # No mergable states found, make a new.
    start_new_block(states_dic, jitstate, key, global_resumer)
    return False   

retrieve_jitstate_for_merge._annspecialcase_ = "specialize:arglltype(2)"

def cleanup_partial_data(partialdatamatch):
    # remove entries from PartialDataStruct unless they matched
    # their frozen equivalent
    for box, keep in partialdatamatch.iteritems():
        content = box.content
        if isinstance(content, rcontainer.PartialDataStruct):
            box.content = content.cleanup_partial_data(keep)

def merge_generalized(jitstate):
    resuming = jitstate.resuming
    if resuming is None:
        node = jitstate.promotion_path
        while not node.cut_limit:
            node = node.next
        dispatchqueue = jitstate.frame.dispatchqueue
        count = dispatchqueue.mergecounter + 1
        dispatchqueue.mergecounter = count
        node = PromotionPathMergesToSee(node, count)
        #debug_print(lltype.Void, "MERGE", count)
        jitstate.promotion_path = node
    else:
        if resuming.mergesleft != MC_IGNORE_UNTIL_RETURN:
            assert resuming.mergesleft > 0
            resuming.mergesleft -= 1

def guard_global_merge(jitstate, resumepoint):
    jitstate.pause()
    dispatchqueue = jitstate.frame.dispatchqueue
    jitstate.next = dispatchqueue.global_merge_chain
    dispatchqueue.global_merge_chain = jitstate
    jitstate.resumepoint = resumepoint

def split(jitstate, switchredbox, resumepoint, *greens_gv):
    exitgvar = switchredbox.getgenvar(jitstate)
    if exitgvar.is_const:
        return exitgvar.revealconst(lltype.Bool)
    else:
        resuming = jitstate.resuming
        if resuming is not None and resuming.mergesleft == 0:
            node = resuming.path.pop()
            assert isinstance(node, PromotionPathSplit)
            return node.answer
        false_gv = jitstate.get_locals_gv() # alive gvs on the false path
        later_builder = jitstate.curbuilder.jump_if_false(exitgvar, false_gv)
        jitstate2 = jitstate.split(later_builder, resumepoint, list(greens_gv))
        if resuming is None:
            node = jitstate.promotion_path
            jitstate2.promotion_path = PromotionPathNo(node)
            jitstate .promotion_path = PromotionPathYes(node)
        return True

def collect_split(jitstate_chain, resumepoint, *greens_gv):
    # assumes that the head of the jitstate_chain is ready for writing,
    # and all the other jitstates in the chain are paused
    greens_gv = list(greens_gv)
    pending = jitstate_chain
    resuming = jitstate_chain.resuming
    if resuming is not None and resuming.mergesleft == 0:
        node = resuming.path.pop()
        assert isinstance(node, PromotionPathCollectSplit)
        for i in range(node.n):
            pending = pending.next
        pending.greens.extend(greens_gv)
        if pending.returnbox is not None:
            pending.frame.local_boxes.insert(0, getreturnbox(pending))
        pending.next = None
        return pending

    n = 0
    while True:
        jitstate = pending
        pending = pending.next
        jitstate.greens.extend(greens_gv)   # item 0 is the return value
        if jitstate.returnbox is not None:
            jitstate.frame.local_boxes.insert(0, getreturnbox(jitstate))
        jitstate.resumepoint = resumepoint
        if resuming is None:
            node = jitstate.promotion_path
            jitstate.promotion_path = PromotionPathCollectSplit(node, n)
            n += 1
        if pending is None:
            break

    dispatchqueue = jitstate_chain.frame.dispatchqueue
    jitstate.next = dispatchqueue.split_chain
    dispatchqueue.split_chain = jitstate_chain.next
    jitstate_chain.next = None
    return jitstate_chain
    # XXX obscurity++ above

def reverse_split_queue(dispatchqueue):
    newchain = None
    while dispatchqueue.split_chain:
        jitstate = dispatchqueue.split_chain
        dispatchqueue.split_chain = jitstate.next
        jitstate.next = newchain
        newchain = jitstate
    dispatchqueue.split_chain = newchain

def dispatch_next(oldjitstate, dispatchqueue):
    if dispatchqueue.split_chain is not None:
        jitstate = dispatchqueue.split_chain
        dispatchqueue.split_chain = jitstate.next
        jitstate.curbuilder.start_writing()
        return jitstate
    elif dispatchqueue.global_merge_chain is not None:
        jitstate = dispatchqueue.global_merge_chain
        dispatchqueue.global_merge_chain = jitstate.next
        jitstate.curbuilder.start_writing()
        return jitstate
    else:
        oldjitstate.resumepoint = -1
        return oldjitstate

def getresumepoint(jitstate):
    return jitstate.resumepoint

def pickjitstate(oldjitstate, newjitstate):
    if newjitstate is not None:
        return newjitstate
    else:
        return oldjitstate

def save_locals(jitstate, *redboxes):
    redboxes = list(redboxes)
    assert None not in redboxes
    jitstate.frame.local_boxes = redboxes

def save_greens(jitstate, *greens_gv):
    jitstate.greens = list(greens_gv)

def getlocalbox(jitstate, i):
    return jitstate.frame.local_boxes[i]

def ll_getgreenbox(jitstate, i, T):
    return jitstate.greens[i].revealconst(T)

def getreturnbox(jitstate):
    retbox = jitstate.returnbox
    jitstate.returnbox = None
    return retbox

def getexctypebox(jitstate):
    return jitstate.exc_type_box

def getexcvaluebox(jitstate):
    return jitstate.exc_value_box

def setexctypebox(jitstate, box):
    jitstate.exc_type_box = box

def setexcvaluebox(jitstate, box):
    jitstate.exc_value_box = box

def save_return(jitstate):
    # add 'jitstate' to the chain of return-jitstates
    jitstate.pause()
    dispatchqueue = jitstate.frame.dispatchqueue
    jitstate.next = dispatchqueue.return_chain
    dispatchqueue.return_chain = jitstate

##def ll_gvar_from_redbox(jitstate, redbox):
##    return redbox.getgenvar(jitstate)

##def ll_gvar_from_constant(jitstate, ll_value):
##    return jitstate.curbuilder.rgenop.genconst(ll_value)

class CallDesc:
    __metaclass__ = cachedtype

    def __init__(self, RGenOp, FUNCTYPE):
        self.sigtoken = RGenOp.sigToken(FUNCTYPE)
        self.result_kind = RGenOp.kindToken(FUNCTYPE.RESULT)
        # xxx what if the result is virtualizable?
        self.redboxbuilder = rvalue.ll_redboxbuilder(FUNCTYPE.RESULT)

    def _freeze_(self):
        return True

def ll_gen_residual_call(jitstate, calldesc, funcbox):
    builder = jitstate.curbuilder
    jitstate.prepare_for_residual_call()
    gv_funcbox = funcbox.getgenvar(jitstate)
    argboxes = jitstate.frame.local_boxes
    args_gv = [argbox.getgenvar(jitstate) for argbox in argboxes]
    gv_result = builder.genop_call(calldesc.sigtoken, gv_funcbox, args_gv)
    jitstate.after_residual_call()
    return calldesc.redboxbuilder(calldesc.result_kind, gv_result)


class ResumingInfo(object):
    def __init__(self, promotion_point, gv_value, path):
        node = PromotionPathPromote(promotion_point.promotion_path,
                                    promotion_point, gv_value)
        path[0] = node
        self.promotion_point = promotion_point
        self.path = path
        self.mergesleft = 0

    def merges_to_see(self):
        node = self.path[-1]
        if isinstance(node, PromotionPathMergesToSee):
            self.mergesleft = node.count
            del self.path[-1]
        else:
            self.mergesleft = MC_IGNORE_UNTIL_RETURN

    def leave_call(self, dispatchqueue):
        parent_mergesleft = dispatchqueue.mergecounter
        if parent_mergesleft == 0:
            node = self.path.pop()
            assert isinstance(node, PromotionPathBackFromReturn)
            self.merges_to_see()
        elif parent_mergesleft == MC_CALL_NOT_TAKEN:
            self.mergesleft = 0
        else:
            self.mergesleft = parent_mergesleft


class PromotionPoint(object):
    def __init__(self, flexswitch, incoming_gv, promotion_path):
        assert promotion_path is not None
        self.flexswitch = flexswitch
        self.incoming_gv = incoming_gv
        self.promotion_path = promotion_path

class AbstractPromotionPath(object):
    cut_limit = False

class PromotionPathRoot(AbstractPromotionPath):
    cut_limit = True

    def __init__(self, greens_gv, rgenop, frozen, replayableblock, global_resumer):
        self.greens_gv = greens_gv
        self.rgenop = rgenop
        self.frozen = frozen
        self.replayableblock = replayableblock
        self.global_resumer = global_resumer

    def follow_path(self, path):
        return self

    def continue_compilation(self, resuminginfo):
        incoming = []
        memo = rvalue.unfreeze_memo()
        jitstate = self.frozen.unfreeze(incoming, memo)
        kinds = [box.kind for box in incoming]
        builder, vars_gv = self.rgenop.replay(self.replayableblock, kinds)
        for i in range(len(incoming)):
            incoming[i].genvar = vars_gv[i]
        jitstate.curbuilder = builder
        jitstate.greens = self.greens_gv
        jitstate.resuming = resuminginfo
        assert jitstate.frame.backframe is None
        resuminginfo.merges_to_see()
        self.global_resumer(jitstate)
        builder.show_incremental_progress()

class PromotionPathNode(AbstractPromotionPath):
    def __init__(self, next):
        self.next = next
    def follow_path(self, path):
        path.append(self)
        return self.next.follow_path(path)

class PromotionPathSplit(PromotionPathNode):
    pass

class PromotionPathYes(PromotionPathSplit):
    answer = True

class PromotionPathNo(PromotionPathSplit):
    answer = False

class PromotionPathCollectSplit(PromotionPathNode):

    def __init__(self, next, n):
        self.next = next
        self.n = n

class PromotionPathCallNotTaken(PromotionPathNode):
    pass

class PromotionPathPromote(PromotionPathNode):
    cut_limit = True

    def __init__(self, next, promotion_point, gv_value):
        self.next = next
        self.promotion_point = promotion_point
        self.gv_value = gv_value

class PromotionPathCall(PromotionPathNode):
    cut_limit = True

class PromotionPathBackFromReturn(PromotionPathNode):
    cut_limit = True

class PromotionPathMergesToSee(PromotionPathNode):
    def __init__(self, next, count):
        self.next = next
        self.count = count

MC_IGNORE_UNTIL_RETURN = -1
MC_CALL_NOT_TAKEN      = -2


def ll_continue_compilation(promotion_point_ptr, value):
    try:
        promotion_point = cast_base_ptr_to_instance(PromotionPoint,
                                                    promotion_point_ptr)
        path = [None]
        root = promotion_point.promotion_path.follow_path(path)
        gv_value = root.rgenop.genconst(value)
        resuminginfo = ResumingInfo(promotion_point, gv_value, path)
        root.continue_compilation(resuminginfo)
    except Exception, e:
        lloperation.llop.debug_fatalerror(lltype.Void,
                                          "compilation-time error %s" % e)

class PromotionDesc:
    __metatype__ = cachedtype

    def __init__(self, ERASED, hrtyper):
##        (s_PromotionPoint,
##         r_PromotionPoint) = hrtyper.s_r_instanceof(PromotionPoint)
        fnptr = hrtyper.annhelper.delayedfunction(
            ll_continue_compilation,
            [annmodel.SomePtr(base_ptr_lltype()),
             annmodel.lltype_to_annotation(ERASED)],
            annmodel.s_None, needtype=True)
        RGenOp = hrtyper.RGenOp
        self.gv_continue_compilation = RGenOp.constPrebuiltGlobal(fnptr)
        self.sigtoken = RGenOp.sigToken(lltype.typeOf(fnptr).TO)
##        self.PROMOTION_POINT = r_PromotionPoint.lowleveltype

    def _freeze_(self):
        return True

def ll_promote(jitstate, promotebox, promotiondesc):
    builder = jitstate.curbuilder
    gv_switchvar = promotebox.getgenvar(jitstate)
    if gv_switchvar.is_const:
        return False
    else:
        incoming = jitstate.enter_block_sweep_virtualizables()
        switchblock = enter_next_block(jitstate, incoming)
        gv_switchvar = promotebox.genvar
        incoming_gv = [box.genvar for box in incoming]
        flexswitch, default_builder = builder.flexswitch(gv_switchvar,
                                                         incoming_gv)
        if jitstate.resuming is None:
            jitstate.curbuilder = default_builder
            # default case of the switch:
            pm = PromotionPoint(flexswitch, incoming_gv,
                                jitstate.promotion_path)
            #debug_print(lltype.Void, "PROMOTE")
            ll_pm = cast_instance_to_base_ptr(pm)
            gv_pm = default_builder.rgenop.genconst(ll_pm)
            gv_switchvar = promotebox.genvar
            default_builder.genop_call(promotiondesc.sigtoken,
                               promotiondesc.gv_continue_compilation,
                               [gv_pm, gv_switchvar])
            linkargs = []
            for box in incoming:
                linkargs.append(box.getgenvar(jitstate))
            default_builder.finish_and_goto(linkargs, switchblock)
            return True
        else:
            assert jitstate.promotion_path is None
            resuming = jitstate.resuming
            if resuming.mergesleft != 0:
                default_builder.pause_writing([])
                return True

            promotenode = resuming.path.pop()
            assert isinstance(promotenode, PromotionPathPromote)
            #debug_view(lltype.Void, promotenode, resuming, incoming)
            pm = promotenode.promotion_point
            assert pm.promotion_path is promotenode.next

            # clear the complete state of dispatch queues
            f = jitstate.frame
            while f is not None:
                f.dispatchqueue.clear()
                f = f.backframe

            if len(resuming.path) == 0:
                incoming_gv = pm.incoming_gv
                for i in range(len(incoming)):
                    incoming[i].genvar = incoming_gv[i]
                flexswitch = pm.flexswitch
                promotebox.genvar = promotenode.gv_value
                jitstate.resuming = None
                node = PromotionPathMergesToSee(promotenode, 0)
                jitstate.promotion_path = node
            else:
                resuming.merges_to_see()
                promotebox.genvar = promotenode.gv_value
                
            newbuilder = flexswitch.add_case(promotenode.gv_value)
            jitstate.curbuilder = newbuilder
            return False

# ____________________________________________________________

class BaseDispatchQueue(object):

    def __init__(self):
        self.split_chain = None
        self.global_merge_chain = None
        self.return_chain = None
        self.mergecounter = 0

    def clear(self):
        self.__init__()

def build_dispatch_subclass(attrnames):
    if len(attrnames) == 0:
        return BaseDispatchQueue
    attrnames = unrolling_iterable(attrnames)
    class DispatchQueue(BaseDispatchQueue):
        def __init__(self):
            BaseDispatchQueue.__init__(self)
            for name in attrnames:
                setattr(self, name, {})     # the new dicts have various types!
    return DispatchQueue


class FrozenVirtualFrame(object):
    fz_backframe = None
    #fz_local_boxes = ... set by freeze()

    def exactmatch(self, vframe, outgoingvarboxes, memo):
        self_boxes = self.fz_local_boxes
        live_boxes = vframe.local_boxes
        fullmatch = True
        for i in range(len(self_boxes)):
            if not self_boxes[i].exactmatch(live_boxes[i],
                                            outgoingvarboxes,
                                            memo):
                fullmatch = False
        if self.fz_backframe is not None:
            assert vframe.backframe is not None
            if not self.fz_backframe.exactmatch(vframe.backframe,
                                                outgoingvarboxes,
                                                memo):
                fullmatch = False
        else:
            assert vframe.backframe is None
        return fullmatch

    def unfreeze(self, incomingvarboxes, memo):
        local_boxes = []
        for fzbox in self.fz_local_boxes:
            local_boxes.append(fzbox.unfreeze(incomingvarboxes, memo))
        if self.fz_backframe is not None:
            backframe = self.fz_backframe.unfreeze(incomingvarboxes, memo)
        else:
            backframe = None
        vframe = VirtualFrame(backframe, None) # dispatch queue to be patched
        vframe.local_boxes = local_boxes
        return vframe


class FrozenJITState(object):
    #fz_frame = ...           set by freeze()
    #fz_exc_type_box = ...    set by freeze()
    #fz_exc_value_box = ...   set by freeze()
    #fz_virtualizables = ...  set by freeze()

    def exactmatch(self, jitstate, outgoingvarboxes, memo):
        fullmatch = True
        if not self.fz_frame.exactmatch(jitstate.frame,
                                        outgoingvarboxes,
                                        memo):
            fullmatch = False
        if not self.fz_exc_type_box.exactmatch(jitstate.exc_type_box,
                                               outgoingvarboxes,
                                               memo):
            fullmatch = False
        if not self.fz_exc_value_box.exactmatch(jitstate.exc_value_box,
                                                outgoingvarboxes,
                                                memo):
            fullmatch = False
        return fullmatch

    def unfreeze(self, incomingvarboxes, memo):
        frame         = self.fz_frame        .unfreeze(incomingvarboxes, memo)
        exc_type_box  = self.fz_exc_type_box .unfreeze(incomingvarboxes, memo)
        exc_value_box = self.fz_exc_value_box.unfreeze(incomingvarboxes, memo)
        virtualizables = []
        for fz_virtualizable_box in self.fz_virtualizables:
            virtualizable_box = fz_virtualizable_box.unfreeze(incomingvarboxes,
                                                              memo)
            assert isinstance(virtualizable_box, rvalue.PtrRedBox)
            virtualizables.append(virtualizable_box)
        return JITState(None, frame, exc_type_box, exc_value_box,
                        virtualizables=virtualizables)


class VirtualFrame(object):

    def __init__(self, backframe, dispatchqueue):
        self.backframe = backframe
        self.dispatchqueue = dispatchqueue
        #self.local_boxes = ... set by callers

    def enter_block(self, incoming, memo):
        for box in self.local_boxes:
            box.enter_block(incoming, memo)
        if self.backframe is not None:
            self.backframe.enter_block(incoming, memo)

    def freeze(self, memo):
        result = FrozenVirtualFrame()
        frozens = [box.freeze(memo) for box in self.local_boxes]
        result.fz_local_boxes = frozens
        if self.backframe is not None:
            result.fz_backframe = self.backframe.freeze(memo)
        return result

    def copy(self, memo):
        if self.backframe is None:
            newbackframe = None
        else:
            newbackframe = self.backframe.copy(memo)
        result = VirtualFrame(newbackframe, self.dispatchqueue)
        result.local_boxes = [box.copy(memo) for box in self.local_boxes]
        return result

    def replace(self, memo):
        local_boxes = self.local_boxes
        for i in range(len(local_boxes)):
            local_boxes[i] = local_boxes[i].replace(memo)
        if self.backframe is not None:
            self.backframe.replace(memo)


class JITState(object):
    returnbox = None
    next      = None   # for linked lists
    promotion_path = None

    def __init__(self, builder, frame, exc_type_box, exc_value_box,
                 resumepoint=-1, newgreens=[], resuming=None,
                 virtualizables=None):
        self.curbuilder = builder
        self.frame = frame
        self.exc_type_box = exc_type_box
        self.exc_value_box = exc_value_box
        self.resumepoint = resumepoint
        self.greens = newgreens
        self.resuming = resuming   # None or a ResumingInfo

        # XXX can not be adictionary
        # it needs to be iterated in a deterministic order.
        if virtualizables is None:
            virtualizables = []
        self.virtualizables = virtualizables

    def add_virtualizable(self, virtualizable_box):
        assert isinstance(virtualizable_box, rvalue.PtrRedBox)
        if virtualizable_box not in self.virtualizables:
            self.virtualizables.append(virtualizable_box)

    def split(self, newbuilder, newresumepoint, newgreens):
        memo = rvalue.copy_memo()
        virtualizables = []
        for virtualizable_box in self.virtualizables:
            new_virtualizable_box = virtualizable_box.copy(memo)
            assert isinstance(new_virtualizable_box, rvalue.PtrRedBox)
            virtualizables.append(new_virtualizable_box)
        later_jitstate = JITState(newbuilder,
                                  self.frame.copy(memo),
                                  self.exc_type_box .copy(memo),
                                  self.exc_value_box.copy(memo),
                                  newresumepoint,
                                  newgreens,
                                  self.resuming,
                                  virtualizables)
        # add the later_jitstate to the chain of pending-for-dispatch_next()
        dispatchqueue = self.frame.dispatchqueue
        later_jitstate.next = dispatchqueue.split_chain
        dispatchqueue.split_chain = later_jitstate
        return later_jitstate

    def _enter_block(self, incoming, memo):
        self.frame.enter_block(incoming, memo)
        self.exc_type_box .enter_block(incoming, memo)
        self.exc_value_box.enter_block(incoming, memo)

    def enter_block_sweep_virtualizables(self):
        incoming = []
        memo = rvalue.enter_block_memo()
        self._enter_block(incoming, memo)
        virtualizables = self.virtualizables
        builder = self.curbuilder
        self.virtualizables = []
        for virtualizable_box in virtualizables:
            if virtualizable_box.content in memo.containers:
                self.virtualizables.append(virtualizable_box)
            else:
                content = virtualizable_box.content
                assert isinstance(content, rcontainer.VirtualizableStruct)
                content.store_back(self)
        return incoming

    def prepare_for_residual_call(self):
        virtualizables = self.virtualizables
        if virtualizables:
            builder = self.curbuilder            
            gv_base = builder.get_frame_base()
            memo = rvalue.make_vinfo_memo()
            for virtualizable_box in virtualizables:
                content = virtualizable_box.content
                assert isinstance(content, rcontainer.VirtualizableStruct)
                content.prepare_for_residual_call(self, gv_base, memo)
                
    def after_residual_call(self):
        virtualizables = self.virtualizables
        if virtualizables:
            builder = self.curbuilder            
            for virtualizable_box in virtualizables:
                content = virtualizable_box.content
                assert isinstance(content, rcontainer.VirtualizableStruct)
                content.after_residual_call(self)

    def freeze(self, memo):
        result = FrozenJITState()
        result.fz_frame = self.frame.freeze(memo)
        result.fz_exc_type_box  = self.exc_type_box .freeze(memo)
        result.fz_exc_value_box = self.exc_value_box.freeze(memo)
        fz_virtualizables = result.fz_virtualizables = []
        for virtualizable_box in self.virtualizables:
            assert virtualizable_box in memo.boxes
            fz_virtualizables.append(virtualizable_box.freeze(memo))
        return result

    def replace(self, memo):
        self.frame.replace(memo)
        self.exc_type_box  = self.exc_type_box .replace(memo)
        self.exc_value_box = self.exc_value_box.replace(memo)
        virtualizables = []
        for i in range(len(self.virtualizables)):
            virtualizable_box = self.virtualizables[i]
            new_virtualizable_box = virtualizable_box.replace(memo)
            assert isinstance(new_virtualizable_box, rvalue.PtrRedBox)
            self.virtualizables[i] = new_virtualizable_box
            
    def get_locals_gv(self): # xxx
        # get all the genvars that are "alive", i.e. stored in the JITState
        # or the VirtualFrames
        incoming = []
        memo = rvalue.enter_block_memo()
        self._enter_block(incoming, memo)
        for virtualizable_box in self.virtualizables:
            virtualizable_box.enter_block(incoming, memo)
        locals_gv = [redbox.genvar for redbox in incoming]
        return locals_gv

    def pause(self):
        locals_gv = self.get_locals_gv()
        self.curbuilder = self.curbuilder.pause_writing(locals_gv)


    def residual_ll_exception(self, ll_evalue):
        ll_etype  = ll_evalue.typeptr
        etypebox  = rvalue.ll_fromvalue(self, ll_etype)
        evaluebox = rvalue.ll_fromvalue(self, ll_evalue)
        setexctypebox (self, etypebox )
        setexcvaluebox(self, evaluebox)

    def residual_exception(self, e):
        self.residual_ll_exception(cast_instance_to_base_ptr(e))
        

def start_writing(jitstate=None, prevopen=None):
    if jitstate is not prevopen:
        if prevopen is not None:
            prevopen.pause()
        jitstate.curbuilder.start_writing()
    return jitstate


def ensure_queue(jitstate, DispatchQueueClass):
    return DispatchQueueClass()
ensure_queue._annspecialcase_ = 'specialize:arg(1)'

def replayable_ensure_queue(jitstate, DispatchQueueClass):
    resuming = jitstate.resuming
    if resuming is None:
        return DispatchQueueClass()
    else:
        dispatchqueue = jitstate.frame.dispatchqueue
        assert isinstance(dispatchqueue, DispatchQueueClass)
        return dispatchqueue
replayable_ensure_queue._annspecialcase_ = 'specialize:arg(1)'

def enter_frame(jitstate, dispatchqueue):
    jitstate.frame = VirtualFrame(jitstate.frame, dispatchqueue)
    resuming = jitstate.resuming
    if resuming is None:
        node = PromotionPathCall(jitstate.promotion_path)
        node = PromotionPathMergesToSee(node, 0)
        jitstate.promotion_path = node
    else:
        parent_mergesleft = resuming.mergesleft
        resuming.mergesleft = MC_IGNORE_UNTIL_RETURN
        if parent_mergesleft == 0:
            node = resuming.path.pop()
            if isinstance(node, PromotionPathCall):
                resuming.merges_to_see()
            else:
                assert isinstance(node, PromotionPathCallNotTaken)
                parent_mergesleft = MC_CALL_NOT_TAKEN
        dispatchqueue.mergecounter = parent_mergesleft

def merge_returning_jitstates(jitstate, dispatchqueue, force_merge=False):
    return_chain = dispatchqueue.return_chain
    return_cache = {}
    still_pending = None
    opened = None
    while return_chain is not None:
        jitstate = return_chain
        return_chain = return_chain.next
        opened = start_writing(jitstate, opened)
        res = retrieve_jitstate_for_merge(return_cache, jitstate, (),
                                          return_marker,
                                          force_merge=force_merge)
        if res is False:    # not finished
            jitstate.next = still_pending
            still_pending = jitstate
        else:
            opened = None
    
    # Of the jitstates we have left some may be mergable to a later
    # more general one.
    return_chain = still_pending
    if return_chain is not None:
        return_cache = {}
        still_pending = None
        while return_chain is not None:
            jitstate = return_chain
            return_chain = return_chain.next
            opened = start_writing(jitstate, opened)
            res = retrieve_jitstate_for_merge(return_cache, jitstate, (),
                                              return_marker,
                                              force_merge=force_merge)
            if res is False:    # not finished
                jitstate.next = still_pending
                still_pending = jitstate
            else:
                opened = None
    start_writing(still_pending, opened)
    return still_pending

def leave_graph_red(jitstate, dispatchqueue, is_portal):
    resuming = jitstate.resuming
    return_chain = merge_returning_jitstates(jitstate, dispatchqueue,
                                             force_merge=is_portal)
    if resuming is not None:
        resuming.leave_call(dispatchqueue)
    jitstate = return_chain
    while jitstate is not None:
        myframe = jitstate.frame
        leave_frame(jitstate)
        jitstate.greens = []
        jitstate.returnbox = myframe.local_boxes[0]
        jitstate = jitstate.next
    return return_chain

def leave_graph_gray(jitstate, dispatchqueue):
    resuming = jitstate.resuming
    return_chain = merge_returning_jitstates(jitstate, dispatchqueue)
    if resuming is not None:
        resuming.leave_call(dispatchqueue)
    jitstate = return_chain
    while jitstate is not None:
        leave_frame(jitstate)
        jitstate.greens = []
        jitstate.returnbox = None
        jitstate = jitstate.next
    return return_chain

def leave_frame(jitstate):
    myframe = jitstate.frame
    backframe = myframe.backframe
    jitstate.frame = backframe    
    if jitstate.resuming is None:
        #debug_view(jitstate)
        node = jitstate.promotion_path
        while not node.cut_limit:
            node = node.next
        if isinstance(node, PromotionPathCall):
            node = PromotionPathCallNotTaken(node.next)
        else:
            node = PromotionPathBackFromReturn(node)
            node = PromotionPathMergesToSee(node, 0)
        jitstate.promotion_path = node


def leave_graph_yellow(jitstate, mydispatchqueue):
    resuming = jitstate.resuming
    if resuming is not None:
        resuming.leave_call(mydispatchqueue)
    return_chain = mydispatchqueue.return_chain
    jitstate = return_chain
    while jitstate is not None:
        leave_frame(jitstate)
        jitstate = jitstate.next
    # return the jitstate which is the head of the chain,
    # ready for further writing
    return_chain.curbuilder.start_writing()
    return return_chain
