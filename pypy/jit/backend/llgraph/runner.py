"""
Minimal-API wrapper around the llinterpreter to run operations.
"""

import sys
from pypy.rpython.lltypesystem import lltype, llmemory, rclass
from pypy.rpython.llinterp import LLInterpreter
from pypy.jit.metainterp import history
from pypy.jit.metainterp.resoperation import ResOperation, rop
from pypy.jit.backend.llgraph import llimpl, symbolic


class MiniStats:
    pass


class Descr(history.AbstractDescr):
    name = None
    
    def __init__(self, ofs, type='?'):
        self.ofs = ofs
        self.type = type

    def __hash__(self):
        return hash((self.ofs, self.type))

    def __eq__(self, other):
        if not isinstance(other, Descr):
            return NotImplemented
        return self.ofs == other.ofs and self.type == other.type

    def __ne__(self, other):
        if not isinstance(other, Descr):
            return NotImplemented
        return self.ofs != other.ofs or self.type != other.type

    def sort_key(self):
        return self.ofs

    def __lt__(self, other):
        raise TypeError("cannot use comparison on Descrs")
    def __le__(self, other):
        raise TypeError("cannot use comparison on Descrs")
    def __gt__(self, other):
        raise TypeError("cannot use comparison on Descrs")
    def __ge__(self, other):
        raise TypeError("cannot use comparison on Descrs")

    def __repr__(self):
        if self.name is not None:
            return '<Descr %r, %r, %r>' % (self.ofs, self.type, self.name)
        return '<Descr %r, %r>' % (self.ofs, self.type)

class CPU(object):

    def __init__(self, rtyper, stats=None, translate_support_code=False,
                 annmixlevel=None):
        self.rtyper = rtyper
        self.translate_support_code = translate_support_code
        self.stats = stats or MiniStats()
        self.stats.exec_counters = {}
        self.stats.exec_jumps = 0
        self.memo_cast = llimpl.new_memo_cast()
        llimpl._stats = self.stats
        llimpl._llinterp = LLInterpreter(self.rtyper)
        if translate_support_code:
            self.mixlevelann = annmixlevel
        self.fielddescrof_vtable = self.fielddescrof(rclass.OBJECT, 'typeptr')

    def compile_operations(self, loop):
        """In a real assembler backend, this should assemble the given
        list of operations.  Here we just generate a similar LoopOrBridge
        instance.  The code here is RPython, whereas the code in llimpl
        is not.
        """
        operations = loop.operations
        c = llimpl.compile_start()
        loop._compiled_version = c
        var2index = {}
        for i in range(len(operations[0].args)):
            box = operations[0].args[i]
            if isinstance(box, history.BoxInt):
                var2index[box] = llimpl.compile_start_int_var(c)
            elif isinstance(box, history.BoxPtr):
                var2index[box] = llimpl.compile_start_ptr_var(c)
            else:
                raise Exception("box is: %r" % (box,))
        self._compile_branch(c, operations[1:], var2index)

    def _compile_branch(self, c, operations, var2index):
        for op in operations:
            llimpl.compile_add(c, op.opnum)
            if op.descr is not None:
                llimpl.compile_add_descr(c, op.descr.ofs, op.descr.type)
            for x in op.args:
                if isinstance(x, history.Box):
                    llimpl.compile_add_var(c, var2index[x])
                elif isinstance(x, history.ConstInt):
                    llimpl.compile_add_int_const(c, x.value)
                elif isinstance(x, history.ConstPtr):
                    llimpl.compile_add_ptr_const(c, x.value)
                elif isinstance(x, history.ConstAddr):
                    llimpl.compile_add_int_const(c, x.getint())
                else:
                    raise Exception("%s args contain: %r" % (op.getopname(),
                                                             x))
            if op.is_guard():
                c2 = llimpl.compile_suboperations(c)
                self._compile_branch(c2, op.suboperations, var2index.copy())
            x = op.result
            if x is not None:
                if isinstance(x, history.BoxInt):
                    var2index[x] = llimpl.compile_add_int_result(c)
                elif isinstance(x, history.BoxPtr):
                    var2index[x] = llimpl.compile_add_ptr_result(c)
                else:
                    raise Exception("%s.result contain: %r" % (op.getopname(),
                                                               x))
        llimpl.compile_add_jump_target(c, op.target_jump._compiled_version)

    def execute_operations(self, loop, valueboxes):
        """Calls the assembler generated for the given loop.
        """
        frame = llimpl.new_frame(self.memo_cast)
        # setup the frame
        llimpl.frame_clear(frame, loop._compiled_version)
        for box in valueboxes:
            if isinstance(box, history.BoxInt):
                llimpl.frame_add_int(frame, box.value)
            elif isinstance(box, history.BoxPtr):
                llimpl.frame_add_ptr(frame, box.value)
            elif isinstance(box, history.ConstInt):
                llimpl.frame_add_int(frame, box.value)
            elif isinstance(box, history.ConstPtr):
                llimpl.frame_add_ptr(frame, box.value)
            else:
                raise Exception("bad box in valueboxes: %r" % (box,))
        # run the loop
        result = llimpl.frame_execute(frame)
        # get the exception to raise and really raise it, if any
        exception_addr = llimpl.frame_get_exception(frame)
        if exception_addr:
            exc_value_gcref = llimpl.frame_get_exc_value(frame)
            xxxx
        else:
            return result

    @staticmethod
    def sizeof(S):
        return Descr(symbolic.get_size(S))

    @staticmethod
    def numof(S):
        return 4

    addresssuffix = '4'

    @staticmethod
    def fielddescrof(S, fieldname):
        ofs, size = symbolic.get_field_token(S, fieldname)
        token = history.getkind(getattr(S, fieldname))
        res = Descr(ofs, token[0])
        res.name = fieldname
        return res

    @staticmethod
    def arraydescrof(A):
        assert isinstance(A, lltype.GcArray)
        size = symbolic.get_size(A)
        token = history.getkind(A.OF)
        return Descr(size, token[0])

    @staticmethod
    def calldescrof(ARGS, RESULT):
        token = history.getkind(RESULT)
        return Descr(0, token[0])

    def cast_adr_to_int(self, adr):
        return llimpl.cast_adr_to_int(self.memo_cast, adr)

    def cast_int_to_adr(self, int):
        return llimpl.cast_int_to_adr(self.memo_cast, int)

    # ---------- the backend-dependent operations ----------

    def do_arraylen_gc(self, args, arraydescr):
        array = args[0].getptr_base()
        return history.BoxInt(llimpl.do_arraylen_gc(arraydescr, array))

    def do_strlen(self, args, descr=None):
        string = args[0].getptr_base()
        return history.BoxInt(llimpl.do_strlen(0, string))

    def do_strgetitem(self, args, descr=None):
        string = args[0].getptr_base()
        index = args[1].getint()
        return history.BoxInt(llimpl.do_strgetitem(0, string, index))

    def do_getarrayitem_gc(self, args, arraydescr):
        array = args[0].getptr_base()
        index = args[1].getint()
        if arraydescr.type == 'p':
            return history.BoxPtr(llimpl.do_getarrayitem_gc_ptr(array, index))
        else:
            return history.BoxInt(llimpl.do_getarrayitem_gc_int(array, index,
                                                               self.memo_cast))

    def do_getfield_gc(self, args, fielddescr):
        struct = args[0].getptr_base()
        if fielddescr.type == 'p':
            return history.BoxPtr(llimpl.do_getfield_gc_ptr(struct,
                                                            fielddescr.ofs))
        else:
            return history.BoxInt(llimpl.do_getfield_gc_int(struct,
                                                            fielddescr.ofs,
                                                            self.memo_cast))

    def do_getfield_raw(self, args, fielddescr):
        struct = self.cast_int_to_adr(args[0].getint())
        if fielddescr.type == 'p':
            return history.BoxPtr(llimpl.do_getfield_raw_ptr(struct,
                                                             fielddescr.ofs))
        else:
            return history.BoxInt(llimpl.do_getfield_raw_int(struct,
                                                             fielddescr.ofs,
                                                             self.memo_cast))

    def do_new(self, args, size):
        return history.BoxPtr(llimpl.do_new(size.ofs))

    def do_new_with_vtable(self, args, size):
        vtable = args[0].getint()
        result = llimpl.do_new(size.ofs)
        llimpl.do_setfield_gc_int(result, self.fielddescrof_vtable.ofs,
                                  vtable, self.memo_cast)
        return history.BoxPtr(result)

    def do_new_array(self, args, size):
        count = args[0].getint()
        return history.BoxPtr(llimpl.do_new_array(size.ofs, count))

    def do_setarrayitem_gc(self, args, arraydescr):
        array = args[0].getptr_base()
        index = args[1].getint()
        if arraydescr.type == 'p':
            newvalue = args[2].getptr_base()
            llimpl.do_setarrayitem_gc_ptr(array, index, newvalue)
        else:
            newvalue = args[2].getint()
            llimpl.do_setarrayitem_gc_int(array, index, newvalue,
                                          self.memo_cast)

    def do_setfield_gc(self, args, fielddescr):
        struct = args[0].getptr_base()
        if fielddescr.type == 'p':
            newvalue = args[1].getptr_base()
            llimpl.do_setfield_gc_ptr(struct, fielddescr.ofs, newvalue)
        else:
            newvalue = args[1].getint()
            llimpl.do_setfield_gc_int(struct, fielddescr.ofs, newvalue,
                                      self.memo_cast)

    def do_setfield_raw(self, args, fielddescr):
        struct = self.cast_int_to_adr(args[0].getint())
        if fielddescr.type == 'p':
            newvalue = args[1].getptr_base()
            llimpl.do_setfield_raw_ptr(struct, fielddescr.ofs, newvalue)
        else:
            newvalue = args[1].getint()
            llimpl.do_setfield_raw_int(struct, fielddescr.ofs, newvalue,
                                       self.memo_cast)

    def do_newstr(self, args, descr=None):
        length = args[0].getint()
        return history.BoxPtr(llimpl.do_newstr(0, length))

    def do_strsetitem(self, args, descr=None):
        string = args[0].getptr_base()
        index = args[1].getint()
        newvalue = args[2].getint()
        llimpl.do_strsetitem(0, string, index, newvalue)

    def do_call(self, args, calldescr):
        func = args[0].getint()
        for arg in args[1:]:
            if (isinstance(arg, history.BoxPtr) or
                isinstance(arg, history.ConstPtr)):
                llimpl.do_call_pushptr(arg.getptr_base())
            else:
                llimpl.do_call_pushint(arg.getint())
        if calldescr.type == 'p':
            return history.BoxPtr(llimpl.do_call_ptr(func, self.memo_cast))
        elif calldescr.type == 'i':
            return history.BoxInt(llimpl.do_call_int(func, self.memo_cast))
        else:  # calldescr.type == 'v'  # void
            llimpl.do_call_void(func, self.memo_cast)

class GuardFailed(object):
    returns = False

    def __init__(self, frame, guard_op):
        self.frame = frame
        self.guard_op = guard_op

    def make_ready_for_return(self, retbox):
        self.returns = True
        self.retbox = retbox

    def make_ready_for_continuing_at(self, merge_point):
        llimpl.frame_clear(self.frame, merge_point._compiled,
                           merge_point._opindex)
        self.merge_point = merge_point

# ____________________________________________________________

import pypy.jit.metainterp.executor
pypy.jit.metainterp.executor.make_execute_list(CPU)
