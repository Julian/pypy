from pypy.jit.backend.arm.assembler import AssemblerARM
from pypy.jit.backend.arm.registers import all_regs, all_vfp_regs
from pypy.jit.backend.llsupport.llmodel import AbstractLLCPU
from pypy.rpython.llinterp import LLInterpreter
from pypy.rpython.lltypesystem import lltype, rffi, llmemory
from pypy.rlib.jit_hooks import LOOP_RUN_CONTAINER
from pypy.jit.backend.arm.arch import FORCE_INDEX_OFS


class AbstractARMCPU(AbstractLLCPU):

    supports_floats = True
    supports_longlong = False # XXX requires an implementation of
                              # read_timestamp that works in user mode
    
    use_hf_abi = False        # use hard float abi flag

    def __init__(self, rtyper, stats, opts=None, translate_support_code=False,
                 gcdescr=None):
        if gcdescr is not None:
            gcdescr.force_index_ofs = FORCE_INDEX_OFS
        AbstractLLCPU.__init__(self, rtyper, stats, opts,
                               translate_support_code, gcdescr)

    def set_debug(self, flag):
        return self.assembler.set_debug(flag)

    def setup(self):
        if self.opts is not None:
            failargs_limit = self.opts.failargs_limit
        else:
            failargs_limit = 1000
        self.assembler = AssemblerARM(self, failargs_limit=failargs_limit)

    def setup_once(self):
        self.assembler.setup_once()

    def finish_once(self):
        self.assembler.finish_once()

    def compile_loop(self, inputargs, operations, looptoken,
                                                    log=True, name=''):
        return self.assembler.assemble_loop(name, inputargs, operations,
                                                    looptoken, log=log)

    def compile_bridge(self, faildescr, inputargs, operations,
                                       original_loop_token, log=True):
        clt = original_loop_token.compiled_loop_token
        clt.compiling_a_bridge()
        return self.assembler.assemble_bridge(faildescr, inputargs, operations,
                                                original_loop_token, log=log)

    def get_latest_value_float(self, index):
        return self.assembler.fail_boxes_float.getitem(index)

    def get_latest_value_int(self, index):
        return self.assembler.fail_boxes_int.getitem(index)

    def get_latest_value_ref(self, index):
        return self.assembler.fail_boxes_ptr.getitem(index)

    def get_latest_value_count(self):
        return self.assembler.fail_boxes_count

    def get_latest_force_token(self):
        return self.assembler.fail_force_index

    def get_on_leave_jitted_hook(self):
        return self.assembler.leave_jitted_hook

    def clear_latest_values(self, count):
        setitem = self.assembler.fail_boxes_ptr.setitem
        null = lltype.nullptr(llmemory.GCREF.TO)
        for index in range(count):
            setitem(index, null)

    def make_execute_token(self, *ARGS):
        FUNCPTR = lltype.Ptr(lltype.FuncType(ARGS, lltype.Signed))

        def execute_token(executable_token, *args):
            clt = executable_token.compiled_loop_token
            assert len(args) == clt._debug_nbargs
            #
            addr = executable_token._arm_func_addr
            assert addr % 8 == 0
            func = rffi.cast(FUNCPTR, addr)
            #llop.debug_print(lltype.Void, ">>>> Entering", addr)
            prev_interpreter = None   # help flow space
            if not self.translate_support_code:
                prev_interpreter = LLInterpreter.current_interpreter
                LLInterpreter.current_interpreter = self.debug_ll_interpreter
            try:
                fail_index = func(*args)
            finally:
                if not self.translate_support_code:
                    LLInterpreter.current_interpreter = prev_interpreter
            #llop.debug_print(lltype.Void, "<<<< Back")
            return self.get_fail_descr_from_number(fail_index)
        return execute_token

    def cast_ptr_to_int(x):
        adr = llmemory.cast_ptr_to_adr(x)
        return ArmCPU.cast_adr_to_int(adr)
    cast_ptr_to_int._annspecialcase_ = 'specialize:arglltype(0)'
    cast_ptr_to_int = staticmethod(cast_ptr_to_int)

    all_null_registers = lltype.malloc(rffi.LONGP.TO,
                        len(all_vfp_regs) * 2 + len(all_regs),
                        flavor='raw', zero=True, immortal=True)

    def force(self, addr_of_force_index):
        TP = rffi.CArrayPtr(lltype.Signed)
        fail_index = rffi.cast(TP, addr_of_force_index)[0]
        assert fail_index >= 0, "already forced!"
        faildescr = self.get_fail_descr_from_number(fail_index)
        rffi.cast(TP, addr_of_force_index)[0] = ~fail_index
        bytecode = self.assembler._find_failure_recovery_bytecode(faildescr)
        addr_all_null_regsiters = rffi.cast(rffi.LONG, self.all_null_registers)
        # start of "no gc operation!" block
        fail_index_2 = self.assembler.failure_recovery_func(
            bytecode,
            addr_of_force_index,
            addr_all_null_regsiters)
        self.assembler.leave_jitted_hook()
        # end of "no gc operation!" block
        assert fail_index == fail_index_2
        return faildescr

    def redirect_call_assembler(self, oldlooptoken, newlooptoken):
        self.assembler.redirect_call_assembler(oldlooptoken, newlooptoken)

    def invalidate_loop(self, looptoken):
        """Activate all GUARD_NOT_INVALIDATED in the loop and its attached
        bridges.  Before this call, all GUARD_NOT_INVALIDATED do nothing;
        after this call, they all fail.  Note that afterwards, if one such
        guard fails often enough, it has a bridge attached to it; it is
        possible then to re-call invalidate_loop() on the same looptoken,
        which must invalidate all newer GUARD_NOT_INVALIDATED, but not the
        old one that already has a bridge attached to it."""
        from pypy.jit.backend.arm.codebuilder import ARMv7Builder

        for jmp, tgt  in looptoken.compiled_loop_token.invalidate_positions:
            mc = ARMv7Builder()
            mc.B_offs(tgt)
            mc.copy_to_raw_memory(jmp)
        # positions invalidated
        looptoken.compiled_loop_token.invalidate_positions = []

    # should be combined with other ll backends
    def get_all_loop_runs(self):
        l = lltype.malloc(LOOP_RUN_CONTAINER,
                          len(self.assembler.loop_run_counters))
        for i, ll_s in enumerate(self.assembler.loop_run_counters):
            l[i].type = ll_s.type
            l[i].number = ll_s.number
            l[i].counter = ll_s.i
        return l

class CPU_ARM(AbstractARMCPU):
    """ARM v7 uses softfp ABI, requires vfp"""
    pass
ArmCPU = CPU_ARM

class CPU_ARMHF(AbstractARMCPU):
    """ARM v7 uses hardfp ABI, requires vfp"""
    use_hf_abi = True
    supports_floats = False
