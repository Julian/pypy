from rpython.jit.backend.llsupport.regalloc import (RegisterManager, FrameManager,
                                                    TempVar, compute_vars_longevity,
                                                    BaseRegalloc, NoVariableToSpill)
from rpython.jit.backend.llsupport.jump import remap_frame_layout_mixed
from rpython.jit.backend.zarch.arch import WORD
from rpython.jit.codewriter import longlong
from rpython.jit.backend.zarch.locations import imm, get_fp_offset, imm0, imm1
from rpython.jit.metainterp.history import (Const, ConstInt, ConstFloat, ConstPtr,
                                            INT, REF, FLOAT, VOID)
from rpython.jit.metainterp.history import JitCellToken, TargetToken
from rpython.jit.metainterp.resoperation import rop
from rpython.jit.backend.zarch import locations as l
from rpython.jit.backend.llsupport import symbolic
from rpython.jit.backend.llsupport.descr import ArrayDescr
from rpython.jit.backend.llsupport.descr import unpack_arraydescr
from rpython.jit.backend.llsupport.descr import unpack_fielddescr
from rpython.jit.backend.llsupport.descr import unpack_interiorfielddescr
from rpython.jit.backend.llsupport.gcmap import allocate_gcmap
import rpython.jit.backend.zarch.registers as r
import rpython.jit.backend.zarch.conditions as c
import rpython.jit.backend.zarch.helper.regalloc as helper
from rpython.jit.backend.zarch.helper.regalloc import (check_imm,)
from rpython.jit.codewriter.effectinfo import EffectInfo
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.debug import debug_print
from rpython.rlib import rgc
from rpython.rlib.rarithmetic import r_uint
from rpython.rtyper.lltypesystem import rffi, lltype, rstr, llmemory
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.annlowlevel import cast_instance_to_gcref

LIMIT_LOOP_BREAK = 15000      # should be much smaller than 32 KB

def force_int(intvalue):
    # a hack before transaction: force the intvalue argument through
    # rffi.cast(), to turn Symbolics into real values
    return rffi.cast(lltype.Signed, intvalue)

class TempInt(TempVar):
    type = INT

    def __repr__(self):
        return "<TempInt at %s>" % (id(self),)

class TempPtr(TempVar):
    type = REF

    def __repr__(self):
        return "<TempPtr at %s>" % (id(self),)

class TempFloat(TempVar):
    type = FLOAT

    def __repr__(self):
        return "<TempFloat at %s>" % (id(self),)


class FPRegisterManager(RegisterManager):
    all_regs              = r.MANAGED_FP_REGS
    box_types             = [FLOAT]
    save_around_call_regs = r.VOLATILES_FLOAT
    assert set(save_around_call_regs).issubset(all_regs)
    pool = None

    def convert_to_adr(self, c):
        assert isinstance(c, ConstFloat)
        adr = self.assembler.datablockwrapper.malloc_aligned(8, 8)
        x = c.getfloatstorage()
        rffi.cast(rffi.CArrayPtr(longlong.FLOATSTORAGE), adr)[0] = x
        return adr

    def convert_to_imm(self, c):
        off = self.pool.get_offset(c)
        return l.pool(off, float=True)

    def __init__(self, longevity, frame_manager=None, assembler=None):
        RegisterManager.__init__(self, longevity, frame_manager, assembler)

    def call_result_location(self, v):
        return r.FPR_RETURN

    def place_in_pool(self, var):
        offset = self.assembler.pool.get_offset(var)
        return l.pool(offset, float=True)

    def ensure_reg(self, box, force_in_reg):
        if isinstance(box, Const):
            poolloc = self.place_in_pool(box)
            if force_in_reg:
                tmp = TempVar()
                self.temp_boxes.append(tmp)
                reg = self.force_allocate_reg(tmp)
                self.assembler.mc.LD(reg, poolloc)
                return reg
            return poolloc
        else:
            assert box in self.temp_boxes
            loc = self.make_sure_var_in_reg(box,
                    forbidden_vars=self.temp_boxes)
        return loc

    def get_scratch_reg(self,):
        box = TempFloat()
        reg = self.force_allocate_reg(box, forbidden_vars=self.temp_boxes)
        self.temp_boxes.append(box)
        return reg


class ZARCHRegisterManager(RegisterManager):
    all_regs              = r.MANAGED_REGS
    box_types             = None       # or a list of acceptable types
    no_lower_byte_regs    = all_regs
    save_around_call_regs = r.VOLATILES
    frame_reg             = r.SPP
    assert set(save_around_call_regs).issubset(all_regs)
    pool = None

    def __init__(self, longevity, frame_manager=None, assembler=None):
        RegisterManager.__init__(self, longevity, frame_manager, assembler)

    def call_result_location(self, v):
        return r.GPR_RETURN

    def convert_to_int(self, c):
        if isinstance(c, ConstInt):
            return rffi.cast(lltype.Signed, c.value)
        else:
            assert isinstance(c, ConstPtr)
            return rffi.cast(lltype.Signed, c.value)

    def convert_to_imm(self, c):
        off = self.pool.get_offset(c)
        return l.pool(off)

    def ensure_reg(self, box, force_in_reg, selected_reg=None):
        if isinstance(box, Const):
            offset = self.assembler.pool.get_offset(box)
            poolloc = l.pool(offset)
            if force_in_reg:
                if selected_reg is None:
                    tmp = TempVar()
                    self.temp_boxes.append(tmp)
                    selected_reg = self.force_allocate_reg(tmp)
                self.assembler.mc.LG(selected_reg, poolloc)
                return selected_reg
            return poolloc
        else:
            assert box in self.temp_boxes
            loc = self.make_sure_var_in_reg(box,
                    forbidden_vars=self.temp_boxes,
                    selected_reg=selected_reg)
        return loc

    def get_scratch_reg(self):
        box = TempVar()
        reg = self.force_allocate_reg(box, forbidden_vars=self.temp_boxes)
        self.temp_boxes.append(box)
        return reg

    def ensure_even_odd_pair(self, var, bindvar, bind_first=True,
                             must_exist=True, load_loc_odd=True,
                             move_regs=True):
        self._check_type(var)
        prev_loc = self.loc(var, must_exist=must_exist)
        var2 = TempVar()
        if bind_first:
            loc, loc2 = self.force_allocate_reg_pair(bindvar, var2, self.temp_boxes)
        else:
            loc, loc2 = self.force_allocate_reg_pair(var2, bindvar, self.temp_boxes)
        self.temp_boxes.append(var2)
        assert loc.is_even() and loc2.is_odd()
        if move_regs and prev_loc is not loc2:
            if load_loc_odd:
                self.assembler.regalloc_mov(prev_loc, loc2)
            else:
                self.assembler.regalloc_mov(prev_loc, loc)
        return loc, loc2

    def force_allocate_reg_pair(self, var, var2, forbidden_vars=[], selected_reg=None):
        """ Forcibly allocate a register for the new variable var.
        var will have an even register (var2 will have an odd register).
        """
        self._check_type(var)
        self._check_type(var2)
        if isinstance(var, TempVar):
            self.longevity[var] = (self.position, self.position)
        if isinstance(var2, TempVar):
            self.longevity[var2] = (self.position, self.position)
        even, odd = None, None
        REGS = r.registers
        i = len(self.free_regs)-1
        candidates = {}
        while i >= 0:
            even = self.free_regs[i]
            if even.is_even():
                # found an even registers that is actually free
                odd = REGS[even.value+1]
                if odd not in r.MANAGED_REGS:
                    # makes no sense to use this register!
                    i -= 1
                    continue
                if odd not in self.free_regs:
                    # sadly odd is not free, but for spilling
                    # we found a candidate
                    candidates[odd] = True
                    i -= 1
                    continue
                assert var not in self.reg_bindings
                assert var2 not in self.reg_bindings
                self.reg_bindings[var] = even
                self.reg_bindings[var2] = odd
                del self.free_regs[i]
                i = self.free_regs.index(odd)
                del self.free_regs[i]
                assert even.is_even() and odd.is_odd()
                return even, odd
            else:
                # an odd free register, maybe the even one is
                # a candidate?
                odd = even
                even = REGS[odd.value-1]
                if even not in r.MANAGED_REGS:
                    # makes no sense to use this register!
                    i -= 1
                    continue
                if even not in self.free_regs:
                    # yes even might be a candidate
                    # this means that odd is free, but not even
                    candidates[even] = True
            i -= 1

        if len(candidates) != 0:
            cur_max_age = -1
            candidate = None
            # pseudo step to find best spilling candidate
            # similar to _pick_variable_to_spill, but tailored
            # to take the even/odd register allocation in consideration
            for next in self.reg_bindings:
                if next in forbidden_vars:
                    continue
                reg = self.reg_bindings[next]
                if reg in candidates:
                    reg2 = None
                    if reg.is_even():
                        reg2 = REGS[reg.value+1]
                    else:
                        reg2 = REGS[reg.value-1]
                    if reg2 not in r.MANAGED_REGS:
                        continue
                    max_age = self.longevity[next][1]
                    if cur_max_age < max_age:
                        cur_max_age = max_age
                        candidate = next
            if candidate is not None:
                # well, we got away with a single spill :)
                reg = self.reg_bindings[candidate]
                self._sync_var(candidate)
                del self.reg_bindings[candidate]
                if reg.is_even():
                    assert var is not candidate
                    self.reg_bindings[var] = reg
                    rmfree = REGS[reg.value+1]
                    self.reg_bindings[var2] = rmfree
                    self.free_regs = [fr for fr in self.free_regs if fr is not rmfree]
                    return reg, rmfree
                else:
                    assert var2 is not candidate
                    self.reg_bindings[var2] = reg
                    rmfree = REGS[reg.value-1]
                    self.reg_bindings[var] = rmfree
                    self.free_regs = [fr for fr in self.free_regs if fr is not rmfree]
                    return rmfree, reg

        # there is no candidate pair that only would
        # require one spill, thus we need to spill two!
        # this is a rare case!
        reverse_mapping = {}
        for v, reg in self.reg_bindings.items():
            reverse_mapping[reg] = v
        # always take the first
        for i, reg in enumerate(r.MANAGED_REGS):
            if i % 2 == 1:
                continue
            if i+1 < len(r.MANAGED_REGS):
                reg2 = r.MANAGED_REGS[i+1]
                assert reg.is_even() and reg2.is_odd()
                ovar = reverse_mapping[reg]
                if ovar in forbidden_vars:
                    continue
                ovar2 = reverse_mapping.get(reg2, None)
                if ovar2 is not None and ovar2 in forbidden_vars:
                    # blocked, try other register pair
                    continue
                even = reg
                odd = reg2
                self._sync_var(ovar)
                self._sync_var(ovar2)
                del self.reg_bindings[ovar]
                if ovar2 is not None:
                    del self.reg_bindings[ovar2]
                # both are not added to free_regs! no need to do so
                self.reg_bindings[var] = even
                self.reg_bindings[var2] = odd
                break
        else:
            # no break! this is bad. really bad
            raise NoVariableToSpill()

        reverse_mapping = None

        return even, odd

    def force_result_in_even_reg(self, result_v, loc, forbidden_vars=[]):
        pass

    def force_result_in_odd_reg(self, result_v, loc, forbidden_vars=[]):
        pass

class ZARCHFrameManager(FrameManager):
    def __init__(self, base_ofs):
        FrameManager.__init__(self)
        self.used = []
        self.base_ofs = base_ofs

    def frame_pos(self, loc, box_type):
        return l.StackLocation(loc, get_fp_offset(self.base_ofs, loc), box_type)

    @staticmethod
    def frame_size(type):
        return 1

    @staticmethod
    def get_loc_index(loc):
        assert isinstance(loc, l.StackLocation)
        return loc.position


class Regalloc(BaseRegalloc):

    def __init__(self, assembler=None):
        self.cpu = assembler.cpu
        self.assembler = assembler
        self.jump_target_descr = None
        self.final_jump_op = None

    def _prepare(self,  inputargs, operations, allgcrefs):
        cpu = self.assembler.cpu
        self.fm = ZARCHFrameManager(cpu.get_baseofs_of_frame_field())
        operations = cpu.gc_ll_descr.rewrite_assembler(cpu, operations,
                                                       allgcrefs)
        # compute longevity of variables
        longevity, last_real_usage = compute_vars_longevity(
                                                    inputargs, operations)
        self.longevity = longevity
        self.last_real_usage = last_real_usage
        self.rm = ZARCHRegisterManager(self.longevity,
                                     frame_manager = self.fm,
                                     assembler = self.assembler)
        self.rm.pool = self.assembler.pool
        self.fprm = FPRegisterManager(self.longevity, frame_manager = self.fm,
                                      assembler = self.assembler)
        self.fprm.pool = self.assembler.pool
        return operations

    def prepare_loop(self, inputargs, operations, looptoken, allgcrefs):
        operations = self._prepare(inputargs, operations, allgcrefs)
        self._set_initial_bindings(inputargs, looptoken)
        # note: we need to make a copy of inputargs because possibly_free_vars
        # is also used on op args, which is a non-resizable list
        self.possibly_free_vars(list(inputargs))
        self.min_bytes_before_label = 4    # for redirect_call_assembler()
        return operations

    def prepare_bridge(self, inputargs, arglocs, operations, allgcrefs,
                       frame_info):
        operations = self._prepare(inputargs, operations, allgcrefs)
        self._update_bindings(arglocs, inputargs)
        self.min_bytes_before_label = 0
        return operations

    def ensure_next_label_is_at_least_at_position(self, at_least_position):
        self.min_bytes_before_label = max(self.min_bytes_before_label,
                                          at_least_position)

    def _update_bindings(self, locs, inputargs):
        # XXX this should probably go to llsupport/regalloc.py
        used = {}
        i = 0
        for loc in locs:
            if loc is None: # xxx bit kludgy
                loc = r.SPP
            arg = inputargs[i]
            i += 1
            if loc.is_core_reg():
                if loc is r.SPP:
                    self.rm.bindings_to_frame_reg[arg] = None
                else:
                    self.rm.reg_bindings[arg] = loc
                    used[loc] = None
            elif loc.is_fp_reg():
                self.fprm.reg_bindings[arg] = loc
                used[loc] = None
            else:
                assert loc.is_stack()
                self.fm.bind(arg, loc)
        self.rm.free_regs = []
        for reg in self.rm.all_regs:
            if reg not in used:
                self.rm.free_regs.append(reg)
        self.fprm.free_regs = []
        for reg in self.fprm.all_regs:
            if reg not in used:
                self.fprm.free_regs.append(reg)
        self.possibly_free_vars(list(inputargs))
        self.fm.finish_binding()
        self.rm._check_invariants()
        self.fprm._check_invariants()

    def get_final_frame_depth(self):
        return self.fm.get_frame_depth()

    def possibly_free_var(self, var):
        if var is not None:
            if var.type == FLOAT:
                self.fprm.possibly_free_var(var)
            else:
                self.rm.possibly_free_var(var)

    def possibly_free_vars(self, vars):
        for var in vars:
            self.possibly_free_var(var)

    def possibly_free_vars_for_op(self, op):
        for i in range(op.numargs()):
            var = op.getarg(i)
            self.possibly_free_var(var)

    def force_result_in_reg(self, var, loc):
        if var.type == FLOAT:
            forbidden_vars = self.fprm.temp_boxes
            return self.fprm.force_result_in_reg(var, loc, forbidden_vars)
        else:
            forbidden_vars = self.rm.temp_boxes
            return self.rm.force_result_in_reg(var, loc, forbidden_vars)

    def force_allocate_reg(self, var):
        if var.type == FLOAT:
            forbidden_vars = self.fprm.temp_boxes
            return self.fprm.force_allocate_reg(var, forbidden_vars)
        else:
            forbidden_vars = self.rm.temp_boxes
            return self.rm.force_allocate_reg(var, forbidden_vars)

    def force_allocate_reg_or_cc(self, var):
        assert var.type == INT
        if self.next_op_can_accept_cc(self.operations, self.rm.position):
            # hack: return the SPP location to mean "lives in CC".  This
            # SPP will not actually be used, and the location will be freed
            # after the next op as usual.
            self.rm.force_allocate_frame_reg(var)
            return r.SPP
        else:
            # else, return a regular register (not SPP).
            if self.rm.reg_bindings.get(var, None) is not None:
                return self.rm.loc(var, must_exist=True)
            return self.rm.force_allocate_reg(var)

    def walk_operations(self, inputargs, operations):
        from rpython.jit.backend.zarch.assembler import (
                asm_operations)
        i = 0
        self.limit_loop_break = (self.assembler.mc.get_relative_pos() +
                                     LIMIT_LOOP_BREAK)
        self.operations = operations
        while i < len(operations):
            op = operations[i]
            self.assembler.mc.mark_op(op)
            self.rm.position = i
            self.fprm.position = i
            if op.has_no_side_effect() and op not in self.longevity:
                i += 1
                self.possibly_free_vars_for_op(op)
                continue
            #
            for j in range(op.numargs()):
                box = op.getarg(j)
                if box.type != FLOAT:
                    self.rm.temp_boxes.append(box)
                else:
                    self.fprm.temp_boxes.append(box)
            #
            opnum = op.getopnum()
            if not we_are_translated() and opnum == -127:
                self._consider_force_spill(op)
            else:
                arglocs = prepare_oplist[opnum](self, op)
                asm_operations[opnum](self.assembler, op, arglocs, self)
            self.free_op_vars()
            self.possibly_free_var(op)
            self.rm._check_invariants()
            self.fprm._check_invariants()
            if self.assembler.mc.get_relative_pos() > self.limit_loop_break:
                self.assembler.break_long_loop()
                self.limit_loop_break = (self.assembler.mc.get_relative_pos() +
                                             LIMIT_LOOP_BREAK)
            i += 1
        assert not self.rm.reg_bindings
        assert not self.fprm.reg_bindings
        self.flush_loop()
        self.assembler.mc.mark_op(None) # end of the loop
        self.operations = None
        for arg in inputargs:
            self.possibly_free_var(arg)

    def flush_loop(self):
        # Emit a nop in the rare case where we have a guard_not_invalidated
        # immediately before a label
        mc = self.assembler.mc
        while self.min_bytes_before_label > mc.get_relative_pos():
            mc.nop()

    def get_gcmap(self, forbidden_regs=[], noregs=False):
        frame_depth = self.fm.get_frame_depth()
        gcmap = allocate_gcmap(self.assembler, frame_depth,
                               r.JITFRAME_FIXED_SIZE)
        for box, loc in self.rm.reg_bindings.iteritems():
            if loc in forbidden_regs:
                continue
            if box.type == REF and self.rm.is_still_alive(box):
                assert not noregs
                assert loc.is_core_reg()
                val = self.assembler.cpu.all_reg_indexes[loc.value]
                gcmap[val // WORD // 8] |= r_uint(1) << (val % (WORD * 8))
        for box, loc in self.fm.bindings.iteritems():
            if box.type == REF and self.rm.is_still_alive(box):
                assert isinstance(loc, l.StackLocation)
                val = loc.get_position() + r.JITFRAME_FIXED_SIZE
                gcmap[val // WORD // 8] |= r_uint(1) << (val % (WORD * 8))
        return gcmap

    def loc(self, var):
        if var.type == FLOAT:
            return self.fprm.loc(var)
        else:
            return self.rm.loc(var)

    def next_instruction(self):
        self.rm.next_instruction()
        self.fprm.next_instruction()

    def force_spill_var(self, var):
        if var.type == FLOAT:
            self.fprm.force_spill_var(var)
        else:
            self.rm.force_spill_var(var)

    def _consider_force_spill(self, op):
        # This operation is used only for testing
        self.force_spill_var(op.getarg(0))

    def before_call(self, force_store=[], save_all_regs=False):
        self.rm.before_call(force_store, save_all_regs)
        self.fprm.before_call(force_store, save_all_regs)

    def after_call(self, v):
        if v.type == FLOAT:
            return self.fprm.after_call(v)
        else:
            return self.rm.after_call(v)

    def call_result_location(self, v):
        if v.type == FLOAT:
            return self.fprm.call_result_location(v)
        else:
            return self.rm.call_result_location(v)

    def ensure_reg(self, box, force_in_reg=False):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box, force_in_reg)
        else:
            return self.rm.ensure_reg(box, force_in_reg)

    def ensure_reg_or_16bit_imm(self, box):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box, True)
        else:
            if helper.check_imm(box):
                return imm(box.getint())
            return self.rm.ensure_reg(box, force_in_reg=True)

    def ensure_reg_or_any_imm(self, box):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box, True)
        else:
            if isinstance(box, Const):
                return imm(box.getint())
            return self.rm.ensure_reg(box, force_in_reg=True)

    def get_scratch_reg(self, type):
        if type == FLOAT:
            return self.fprm.get_scratch_reg()
        else:
            return self.rm.get_scratch_reg()

    def free_op_vars(self):
        # free the boxes in the 'temp_boxes' lists, which contain both
        # temporary boxes and all the current operation's arguments
        self.rm.free_temp_vars()
        self.fprm.free_temp_vars()

    def compute_hint_frame_locations(self, operations):
        # optimization only: fill in the 'hint_frame_locations' dictionary
        # of rm and xrm based on the JUMP at the end of the loop, by looking
        # at where we would like the boxes to be after the jump.
        op = operations[-1]
        if op.getopnum() != rop.JUMP:
            return
        self.final_jump_op = op
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        if descr._ll_loop_code != 0:
            # if the target LABEL was already compiled, i.e. if it belongs
            # to some already-compiled piece of code
            self._compute_hint_frame_locations_from_descr(descr)
        #else:
        #   The loop ends in a JUMP going back to a LABEL in the same loop.
        #   We cannot fill 'hint_frame_locations' immediately, but we can
        #   wait until the corresponding prepare_op_label() to know where the
        #   we would like the boxes to be after the jump.

    def _compute_hint_frame_locations_from_descr(self, descr):
        arglocs = self.assembler.target_arglocs(descr)
        jump_op = self.final_jump_op
        assert len(arglocs) == jump_op.numargs()
        for i in range(jump_op.numargs()):
            box = jump_op.getarg(i)
            if not isinstance(box, Const):
                loc = arglocs[i]
                if loc is not None and loc.is_stack():
                    self.fm.hint_frame_pos[box] = self.fm.get_loc_index(loc)

    def convert_to_int(self, c):
        if isinstance(c, ConstInt):
            return rffi.cast(lltype.Signed, c.value)
        else:
            assert isinstance(c, ConstPtr)
            return rffi.cast(lltype.Signed, c.value)

    # ******************************************************
    # *         P R E P A R E  O P E R A T I O N S         * 
    # ******************************************************

    def prepare_increment_debug_counter(self, op):
        #poolloc = self.ensure_reg(op.getarg(0))
        immvalue = self.convert_to_int(op.getarg(0))
        base_loc = r.SCRATCH
        self.assembler.mc.load_imm(base_loc, immvalue)
        scratch = r.SCRATCH2
        return [base_loc, scratch]

    prepare_int_add = helper.prepare_int_add
    prepare_int_add_ovf = helper.prepare_int_add
    prepare_int_sub = helper.prepare_int_sub
    prepare_int_sub_ovf = helper.prepare_int_sub
    prepare_int_mul = helper.prepare_int_mul
    prepare_int_mul_ovf = helper.prepare_int_mul_ovf
    prepare_int_floordiv = helper.prepare_int_div
    prepare_uint_floordiv = helper.prepare_int_div
    prepare_int_mod = helper.prepare_int_mod

    prepare_int_and = helper.prepare_int_logic
    prepare_int_or  = helper.prepare_int_logic
    prepare_int_xor = helper.prepare_int_logic

    prepare_int_rshift  = helper.prepare_int_shift
    prepare_int_lshift  = helper.prepare_int_shift
    prepare_uint_rshift = helper.prepare_int_shift

    prepare_int_le = helper.generate_cmp_op()
    prepare_int_lt = helper.generate_cmp_op()
    prepare_int_ge = helper.generate_cmp_op()
    prepare_int_gt = helper.generate_cmp_op()
    prepare_int_eq = helper.generate_cmp_op()
    prepare_int_ne = helper.generate_cmp_op()

    prepare_ptr_eq = prepare_int_eq
    prepare_ptr_ne = prepare_int_ne

    prepare_instance_ptr_eq = prepare_ptr_eq
    prepare_instance_ptr_ne = prepare_ptr_ne

    prepare_uint_le = helper.generate_cmp_op(signed=False)
    prepare_uint_lt = helper.generate_cmp_op(signed=False)
    prepare_uint_ge = helper.generate_cmp_op(signed=False)
    prepare_uint_gt = helper.generate_cmp_op(signed=False)

    prepare_int_is_zero = helper.prepare_unary_cmp
    prepare_int_is_true = helper.prepare_unary_cmp

    prepare_int_neg     = helper.prepare_unary_op
    prepare_int_invert  = helper.prepare_unary_op
    prepare_int_signext = helper.prepare_unary_op

    prepare_int_force_ge_zero = helper.prepare_unary_op


    prepare_float_add = helper.generate_prepare_float_binary_op(allow_swap=True)
    prepare_float_sub = helper.generate_prepare_float_binary_op()
    prepare_float_mul = helper.generate_prepare_float_binary_op(allow_swap=True)
    prepare_float_truediv = helper.generate_prepare_float_binary_op()

    prepare_float_lt = helper.prepare_float_cmp_op
    prepare_float_le = helper.prepare_float_cmp_op
    prepare_float_eq = helper.prepare_float_cmp_op
    prepare_float_ne = helper.prepare_float_cmp_op
    prepare_float_gt = helper.prepare_float_cmp_op
    prepare_float_ge = helper.prepare_float_cmp_op

    prepare_float_neg = helper.prepare_unary_op
    prepare_float_abs = helper.prepare_unary_op


    prepare_cast_ptr_to_int = helper.prepare_same_as
    prepare_cast_int_to_ptr = helper.prepare_same_as

    prepare_same_as_i = helper.prepare_same_as
    prepare_same_as_r = helper.prepare_same_as
    prepare_same_as_f = helper.prepare_same_as

    def void(self, op):
        return []

    prepare_debug_merge_point = void
    prepare_jit_debug = void
    prepare_keepalive = void
    prepare_enter_portal_frame = void
    prepare_leave_portal_frame = void

    def _prepare_call(self, op):
        oopspecindex = self.get_oopspecindex(op)
        if oopspecindex == EffectInfo.OS_MATH_SQRT:
            return self._prepare_math_sqrt(op)
        if oopspecindex == EffectInfo.OS_THREADLOCALREF_GET:
            return self._prepare_threadlocalref_get(op)
        return self._prepare_call_default(op)

    prepare_call_i = _prepare_call
    prepare_call_r = _prepare_call
    prepare_call_f = _prepare_call
    prepare_call_n = _prepare_call

    def prepare_call_malloc_gc(self, op):
        return self._prepare_call_default(op)

    def prepare_call_malloc_nursery(self, op):
        self.rm.force_allocate_reg(op, selected_reg=r.RES)
        self.rm.temp_boxes.append(op)
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        return []

    def prepare_call_malloc_nursery_varsize_frame(self, op):
        sizeloc = self.ensure_reg(op.getarg(0))
        # sizeloc must be in a register, but we can free it now
        # (we take care explicitly of conflicts with r.RES or r.RSZ)
        self.free_op_vars()
        # the result will be in r.RES
        self.rm.force_allocate_reg(op, selected_reg=r.RES)
        self.rm.temp_boxes.append(op)
        # we need r.RSZ as a temporary
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        return [sizeloc]

    def prepare_call_malloc_nursery_varsize(self, op):
        # the result will be in r.RES
        self.rm.force_allocate_reg(op, selected_reg=r.RES)
        self.rm.temp_boxes.append(op)
        # we need r.RSZ as a temporary
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        # length_box always survives: it's typically also present in the
        # next operation that will copy it inside the new array.  Make
        # sure it is in a register different from r.RES and r.RSZ.  (It
        # should not be a ConstInt at all.)
        length_box = op.getarg(2)
        lengthloc = self.ensure_reg(length_box, force_in_reg=True)
        return [lengthloc]


    def _prepare_gc_load(self, op):
        base_loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        size_box = op.getarg(2)
        assert isinstance(size_box, ConstInt)
        size = abs(size_box.value)
        sign_loc = imm0
        if size_box.value < 0:
            sign_loc = imm1
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op)
        return [result_loc, base_loc, index_loc, imm(size), sign_loc]

    prepare_gc_load_i = _prepare_gc_load
    prepare_gc_load_f = _prepare_gc_load
    prepare_gc_load_r = _prepare_gc_load

    def _prepare_gc_load_indexed(self, op):
        base_loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        scale_box = op.getarg(2)
        offset_box = op.getarg(3)
        size_box = op.getarg(4)
        assert isinstance(scale_box, ConstInt)
        assert isinstance(offset_box, ConstInt)
        assert isinstance(size_box, ConstInt)
        scale = scale_box.value
        assert scale == 1
        offset = offset_box.value
        size = size_box.value
        size_loc = imm(abs(size))
        if size < 0:
            sign_loc = imm1
        else:
            sign_loc = imm0
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op)
        return [result_loc, base_loc, index_loc, imm(offset), size_loc, sign_loc]

    prepare_gc_load_indexed_i = _prepare_gc_load_indexed
    prepare_gc_load_indexed_f = _prepare_gc_load_indexed
    prepare_gc_load_indexed_r = _prepare_gc_load_indexed

    def prepare_gc_store(self, op):
        base_loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        size_box = op.getarg(3)
        assert isinstance(size_box, ConstInt)
        size = abs(size_box.value)
        self.free_op_vars()
        return [base_loc, index_loc, value_loc, imm(size)]

    def prepare_gc_store_indexed(self, op):
        args = op.getarglist()
        base_loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        scale_box = op.getarg(3)
        offset_box = op.getarg(4)
        size_box = op.getarg(5)
        assert isinstance(scale_box, ConstInt)
        assert isinstance(offset_box, ConstInt)
        assert isinstance(size_box, ConstInt)
        factor = scale_box.value
        assert factor == 1
        offset = offset_box.value
        size = size_box.value
        return [base_loc, index_loc, value_loc, imm(offset), imm(abs(size))]

    def get_oopspecindex(self, op):
        descr = op.getdescr()
        assert descr is not None
        effectinfo = descr.get_extra_info()
        if effectinfo is not None:
            return effectinfo.oopspecindex
        return EffectInfo.OS_NONE

    def prepare_convert_float_bytes_to_longlong(self, op):
        loc1 = self.ensure_reg(op.getarg(0), force_in_reg=True)
        res = self.force_allocate_reg(op)
        return [loc1, res]

    def prepare_convert_longlong_bytes_to_float(self, op):
        loc1 = self.ensure_reg(op.getarg(0), force_in_reg=True)
        res = self.force_allocate_reg(op)
        return [loc1, res]

    def _spill_before_call(self, save_all_regs=False):
        # spill variables that need to be saved around calls
        self.fprm.before_call(save_all_regs=save_all_regs)
        if not save_all_regs:
            gcrootmap = self.assembler.cpu.gc_ll_descr.gcrootmap
            if gcrootmap and gcrootmap.is_shadow_stack:
                save_all_regs = 2
        self.rm.before_call(save_all_regs=save_all_regs)

    def _prepare_call_default(self, op, save_all_regs=False):
        args = [None]
        for i in range(op.numargs()):
            args.append(self.loc(op.getarg(i)))
        self._spill_before_call(save_all_regs)
        if op.type != VOID:
            resloc = self.after_call(op)
            args[0] = resloc
        return args

    def _prepare_call_may_force(self, op):
        return self._prepare_call_default(op, save_all_regs=True)

    prepare_call_may_force_i = _prepare_call_may_force
    prepare_call_may_force_r = _prepare_call_may_force
    prepare_call_may_force_f = _prepare_call_may_force
    prepare_call_may_force_n = _prepare_call_may_force

    def _prepare_call_release_gil(self, op):
        errno_box = op.getarg(0)
        assert isinstance(errno_box, ConstInt)
        args = [None, l.imm(errno_box.value)]
        for i in range(1,op.numargs()):
            args.append(self.loc(op.getarg(i)))
        self._spill_before_call(save_all_regs=True)
        if op.type != VOID:
            resloc = self.after_call(op)
            args[0] = resloc
        return args

    prepare_call_release_gil_i = _prepare_call_release_gil
    prepare_call_release_gil_f = _prepare_call_release_gil
    prepare_call_release_gil_n = _prepare_call_release_gil

    def prepare_force_token(self, op):
        res_loc = self.force_allocate_reg(op)
        return [res_loc]

    def _prepare_call_assembler(self, op):
        locs = self.locs_for_call_assembler(op)
        self._spill_before_call(save_all_regs=True)
        if op.type != VOID:
            resloc = self.after_call(op)
        else:
            resloc = None
        return [resloc] + locs

    prepare_call_assembler_i = _prepare_call_assembler
    prepare_call_assembler_r = _prepare_call_assembler
    prepare_call_assembler_f = _prepare_call_assembler
    prepare_call_assembler_n = _prepare_call_assembler

    def _prepare_threadlocalref_get(self, op):
        if self.cpu.translate_support_code:
            res = self.force_allocate_reg(op)
            return [res]
        else:
            return self._prepare_call_default(op)

    def prepare_zero_array(self, op):
        itemsize, ofs, _ = unpack_arraydescr(op.getdescr())
        base_loc, length_loc = self.rm.ensure_even_odd_pair(op.getarg(0), op,
              bind_first=True, must_exist=False, load_loc_odd=False)
        tempvar = TempInt()
        self.rm.temp_boxes.append(tempvar)
        pad_byte, _ = self.rm.ensure_even_odd_pair(tempvar, tempvar,
                              bind_first=True, must_exist=False, move_regs=False)
        startindex_loc = self.ensure_reg_or_16bit_imm(op.getarg(1))

        length_box = op.getarg(2)
        ll = self.rm.loc(length_box)
        if length_loc is not ll:
            self.assembler.regalloc_mov(ll, length_loc)
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        return [base_loc, startindex_loc, length_loc, ofs_loc, imm(itemsize), pad_byte]

    def prepare_cond_call(self, op):
        self.load_condition_into_cc(op.getarg(0))
        locs = []
        # support between 0 and 4 integer arguments
        assert 2 <= op.numargs() <= 2 + 4
        for i in range(1, op.numargs()):
            loc = self.loc(op.getarg(i))
            assert loc.type != FLOAT
            locs.append(loc)
        return locs

    def prepare_cond_call_gc_wb(self, op):
        arglocs = [self.ensure_reg(op.getarg(0))]
        return arglocs

    def prepare_cond_call_gc_wb_array(self, op):
        arglocs = [self.ensure_reg(op.getarg(0)),
                   self.ensure_reg_or_16bit_imm(op.getarg(1)),
                   None]
        if arglocs[1].is_reg():
            arglocs[2] = self.get_scratch_reg(INT)
        return arglocs

    def _prepare_math_sqrt(self, op):
        loc = self.ensure_reg(op.getarg(1), force_in_reg=True)
        self.free_op_vars()
        res = self.fprm.force_allocate_reg(op)
        return [loc, res]

    def prepare_cast_int_to_float(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        res = self.fprm.force_allocate_reg(op)
        return [loc1, res]

    def prepare_cast_float_to_int(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        self.free_op_vars()
        res = self.rm.force_allocate_reg(op)
        return [loc1, res]

    def _prepare_guard(self, op, args=None):
        if args is None:
            args = []
        args.append(imm(self.fm.get_frame_depth()))
        for arg in op.getfailargs():
            if arg:
                args.append(self.loc(arg))
            else:
                args.append(None)
        self.possibly_free_vars(op.getfailargs())
        #
        # generate_quick_failure() produces up to 14 instructions per guard
        self.limit_loop_break -= 14 * 4
        #
        return args

    def load_condition_into_cc(self, box):
        if self.assembler.guard_success_cc == c.cond_none:
            loc = self.ensure_reg(box)
            mc = self.assembler.mc
            mc.cmp_op(loc, l.imm(0), imm=True)
            self.assembler.guard_success_cc = c.NE

    def _prepare_guard_cc(self, op):
        self.load_condition_into_cc(op.getarg(0))
        return self._prepare_guard(op)

    prepare_guard_true = _prepare_guard_cc
    prepare_guard_false = _prepare_guard_cc
    prepare_guard_nonnull = _prepare_guard_cc
    prepare_guard_isnull = _prepare_guard_cc
    prepare_guard_overflow = _prepare_guard_cc

    def prepare_guard_class(self, op):
        x = self.ensure_reg(op.getarg(0), force_in_reg=True)
        y_val = force_int(op.getarg(1).getint())
        arglocs = self._prepare_guard(op, [x, imm(y_val)])
        return arglocs

    prepare_guard_nonnull_class = prepare_guard_class
    prepare_guard_gc_type = prepare_guard_class
    prepare_guard_subclass = prepare_guard_class

    def prepare_guard_no_exception(self, op):
        arglocs = self._prepare_guard(op)
        return arglocs

    prepare_guard_no_overflow = prepare_guard_no_exception
    prepare_guard_overflow = prepare_guard_no_exception
    prepare_guard_not_forced = prepare_guard_no_exception

    def prepare_guard_not_forced_2(self, op):
        self.rm.before_call(op.getfailargs(), save_all_regs=True)
        arglocs = self._prepare_guard(op)
        return arglocs

    def prepare_guard_value(self, op):
        l0 = self.ensure_reg(op.getarg(0))
        l1 = self.ensure_reg_or_16bit_imm(op.getarg(1))
        arglocs = self._prepare_guard(op, [l0, l1])
        return arglocs

    def prepare_guard_not_invalidated(self, op):
        pos = self.assembler.mc.get_relative_pos()
        self.ensure_next_label_is_at_least_at_position(pos + 4)
        locs = self._prepare_guard(op)
        return locs

    def prepare_guard_exception(self, op):
        loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        if op in self.longevity:
            resloc = self.force_allocate_reg(op)
        else:
            resloc = None
        arglocs = self._prepare_guard(op, [loc, resloc])
        return arglocs

    def prepare_guard_is_object(self, op):
        loc_object = self.ensure_reg(op.getarg(0), force_in_reg=True)
        arglocs = self._prepare_guard(op, [loc_object])
        return arglocs

    def prepare_save_exception(self, op):
        res = self.rm.force_allocate_reg(op)
        return [res]
    prepare_save_exc_class = prepare_save_exception

    def prepare_restore_exception(self, op):
        loc0 = self.ensure_reg(op.getarg(0), force_in_reg=True)
        loc1 = self.ensure_reg(op.getarg(1), force_in_reg=True)
        return [loc0, loc1]

    def prepare_copystrcontent(self, op):
        src_ptr_loc = self.ensure_reg(op.getarg(0), force_in_reg=True)
        dst_ptr_loc = self.ensure_reg(op.getarg(1), force_in_reg=True)
        src_ofs_loc = self.ensure_reg_or_any_imm(op.getarg(2))
        dst_ofs_loc = self.ensure_reg_or_any_imm(op.getarg(3))
        length_loc  = self.ensure_reg_or_any_imm(op.getarg(4))
        self._spill_before_call(save_all_regs=False)
        return [src_ptr_loc, dst_ptr_loc,
                src_ofs_loc, dst_ofs_loc, length_loc]

    prepare_copyunicodecontent = prepare_copystrcontent

    def prepare_label(self, op):
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        inputargs = op.getarglist()
        arglocs = [None] * len(inputargs)
        #
        # we use force_spill() on the boxes that are not going to be really
        # used any more in the loop, but that are kept alive anyway
        # by being in a next LABEL's or a JUMP's argument or fail_args
        # of some guard
        position = self.rm.position
        for arg in inputargs:
            assert not isinstance(arg, Const)
            if self.last_real_usage.get(arg, -1) <= position:
                self.force_spill_var(arg)
        #
        # we need to make sure that no variable is stored in spp (=r31)
        for arg in inputargs:
            assert self.loc(arg) is not r.SPP, (
                "variable stored in spp in prepare_label")
        self.rm.bindings_to_frame_reg.clear()
        #
        for i in range(len(inputargs)):
            arg = inputargs[i]
            assert not isinstance(arg, Const)
            loc = self.loc(arg)
            assert loc is not r.SPP
            arglocs[i] = loc
            if loc.is_core_reg():
                self.fm.mark_as_free(arg)
        #
        # if we are too close to the start of the loop, the label's target may
        # get overridden by redirect_call_assembler().  (rare case)
        self.flush_loop()
        #
        descr._zarch_arglocs = arglocs
        descr._ll_loop_code = self.assembler.mc.currpos()
        descr._zarch_clt = self.assembler.current_clt
        self.assembler.target_tokens_currently_compiling[descr] = None
        self.possibly_free_vars_for_op(op)
        #
        # if the LABEL's descr is precisely the target of the JUMP at the
        # end of the same loop, i.e. if what we are compiling is a single
        # loop that ends up jumping to this LABEL, then we can now provide
        # the hints about the expected position of the spilled variables.
        jump_op = self.final_jump_op
        if jump_op is not None and jump_op.getdescr() is descr:
            self._compute_hint_frame_locations_from_descr(descr)

    def prepare_jump(self, op):
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        self.jump_target_descr = descr
        arglocs = self.assembler.target_arglocs(descr)

        # get temporary locs
        tmploc = r.SCRATCH
        fptmploc = r.FP_SCRATCH

        # Part about non-floats
        src_locations1 = []
        dst_locations1 = []
        src_locations2 = []
        dst_locations2 = []

        # Build the four lists
        for i in range(op.numargs()):
            box = op.getarg(i)
            src_loc = self.loc(box)
            dst_loc = arglocs[i]
            if box.type != FLOAT:
                src_locations1.append(src_loc)
                dst_locations1.append(dst_loc)
            else:
                src_locations2.append(src_loc)
                dst_locations2.append(dst_loc)

        remap_frame_layout_mixed(self.assembler,
                                 src_locations1, dst_locations1, tmploc,
                                 src_locations2, dst_locations2, fptmploc, WORD)
        return []

    def prepare_finish(self, op):
        descr = op.getdescr()
        fail_descr = cast_instance_to_gcref(descr)
        # we know it does not move, but well
        rgc._make_sure_does_not_move(fail_descr)
        fail_descr = rffi.cast(lltype.Signed, fail_descr)
        assert fail_descr > 0
        if op.numargs() > 0:
            loc = self.ensure_reg(op.getarg(0))
            locs = [loc, imm(fail_descr)]
        else:
            locs = [imm(fail_descr)]
        return locs

def notimplemented(self, op):
    msg = '[S390X/regalloc] %s not implemented\n' % op.getopname()
    if we_are_translated():
        llop.debug_print(lltype.Void, msg)
    raise NotImplementedError(msg)

prepare_oplist = [notimplemented] * (rop._LAST + 1)

implemented_count = 0
total_count = 0
missing = []
for key, value in rop.__dict__.items():
    key = key.lower()
    if key.startswith('_'):
        continue
    total_count += 1
    methname = 'prepare_%s' % key
    if hasattr(Regalloc, methname):
        func = getattr(Regalloc, methname).im_func
        prepare_oplist[value] = func
        implemented_count += 1
    else:
        missing.append(methname)

if __name__ == '__main__':
    for m in missing:
        print(" " * 4 + m)
    print
    print("regalloc implements %d of %d = %.2f%% of all resops" % \
          (implemented_count, total_count, (100.0 * implemented_count / total_count)))

del implemented_count
del total_count
del missing
