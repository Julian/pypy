r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, \
    r13, r14, r15, r16, r17, r18, r19, r20, r21, r22, \
    r23, r24, r25, r26, r27, r28, r29, r30, r31 = range(32)
rSCRATCH = r0
rSP = r1
rFP = r2 # the ABI doesn't specify a frame pointer.  however, we want one

class AllocationSlot(object):
    pass

class _StackSlot(AllocationSlot):
    is_register = False
    def __init__(self, offset):
        self.offset = offset
    def __repr__(self):
        return "stack@%s"%(self.offset,)

_stack_slot_cache = {}
def stack_slot(offset):
    # because stack slots are put into dictionaries which compare by
    # identity, it is important that there's a unique _StackSlot
    # object for each offset, at least per function generated or
    # something.  doing the caching here is easier, though.
    if offset in _stack_slot_cache:
        return _stack_slot_cache[offset]
    _stack_slot_cache[offset] = res = _StackSlot(offset)
    return res

NO_REGISTER = -1
GP_REGISTER = 0
FP_REGISTER = 1
CR_FIELD = 2
CT_REGISTER = 3

class Register(AllocationSlot):
    is_register = True

class GPR(Register):
    regclass = GP_REGISTER
    def __init__(self, number):
        self.number = number
    def __repr__(self):
        return 'r' + str(self.number)
gprs = map(GPR, range(32))

class FPR(Register):
    regclass = FP_REGISTER
    def __init__(self, number):
        self.number = number

fprs = map(GPR, range(32))

class CRF(Register):
    regclass = CR_FIELD
    def __init__(self, number):
        self.number = number
    def move_to_gpr(self, allocator, gpr):
        bit, negated = allocator.crfinfo[self.number]
        return _CRF2GPR(gpr, self.number*4 + bit, negated)

crfs = map(CRF, range(8))

class CTR(Register):
    regclass = CT_REGISTER
    def move_from_gpr(self, allocator, gpr):
        return _GPR2CTR(gpr)

ctr = CTR()

_insn_index = [0]

class Insn(object):
    '''
    result is the Var instance that holds the result, or None
    result_regclass is the class of the register the result goes into

    reg_args is the vars that need to have registers allocated for them
    reg_arg_regclasses is the type of register that needs to be allocated
    '''
    def __init__(self):
        self._magic_index = _insn_index[0]
        _insn_index[0] += 1
    def __repr__(self):
        return "<%s %d>" % (self.__class__.__name__, self._magic_index)

class Insn_GPR__GPR_GPR(Insn):
    def __init__(self, methptr, result, args):
        Insn.__init__(self)
        self.methptr = methptr

        self.result = result
        self.result_regclass = GP_REGISTER
        self.reg_args = args
        self.reg_arg_regclasses = [GP_REGISTER, GP_REGISTER]

    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
        self.arg_reg1 = allocator.loc_of(self.reg_args[0])
        self.arg_reg2 = allocator.loc_of(self.reg_args[1])

    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.result_reg.number,
                     self.arg_reg1.number,
                     self.arg_reg2.number)

class Insn_GPR__GPR_IMM(Insn):
    def __init__(self, methptr, result, args):
        Insn.__init__(self)
        self.methptr = methptr
        self.imm = args[1]

        self.result = result
        self.result_regclass = GP_REGISTER
        self.reg_args = [args[0]]
        self.reg_arg_regclasses = [GP_REGISTER]
    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
        self.arg_reg = allocator.loc_of(self.reg_args[0])
    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.result_reg.number,
                     self.arg_reg.number,
                     self.imm.value)

class Insn_GPR__GPR(Insn):
    def __init__(self, methptr, result, arg):
        Insn.__init__(self)
        self.methptr = methptr

        self.result = result
        self.result_regclass = GP_REGISTER
        self.reg_args = [arg]
        self.reg_arg_regclasses = [GP_REGISTER]
    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
        self.arg_reg = allocator.loc_of(self.reg_args[0])
    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.result_reg.number,
                     self.arg_reg.number)

class Insn_GPR__IMM(Insn):
    def __init__(self, methptr, result, args):
        Insn.__init__(self)
        self.methptr = methptr
        self.imm = args[0]

        self.result = result
        self.result_regclass = GP_REGISTER
        self.reg_args = []
        self.reg_arg_regclasses = []
    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.result_reg.number,
                     self.imm.value)

class Insn_None__GPR_GPR_IMM(Insn):
    def __init__(self, methptr, args):
        Insn.__init__(self)
        self.methptr = methptr
        self.imm = args[2]

        self.result = None
        self.result_regclass = NO_REGISTER
        self.reg_args = args[:2]
        self.reg_arg_regclasses = [GP_REGISTER, GP_REGISTER]
    def allocate(self, allocator):
        self.reg1 = allocator.loc_of(self.reg_args[0])
        self.reg2 = allocator.loc_of(self.reg_args[1])
    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.reg1.number,
                     self.reg2.number,
                     self.imm.value)

class Insn_None__GPR_GPR_GPR(Insn):
    def __init__(self, methptr, args):
        Insn.__init__(self)
        self.methptr = methptr

        self.result = None
        self.result_regclass = NO_REGISTER
        self.reg_args = args
        self.reg_arg_regclasses = [GP_REGISTER, GP_REGISTER, GP_REGISTER]
    def allocate(self, allocator):
        self.reg1 = allocator.loc_of(self.reg_args[0])
        self.reg2 = allocator.loc_of(self.reg_args[1])
        self.reg3 = allocator.loc_of(self.reg_args[2])
    def __repr__(self):
        return "<%s %s %d>" % (self.__class__.__name__, self.methptr.im_func.func_name, self._magic_index)

    def emit(self, asm):
        self.methptr(asm,
                     self.reg1.number,
                     self.reg2.number,
                     self.reg3.number)

class CMPInsn(Insn):
    info = (0,0) # please the annotator for tests that don't use CMPW/CMPWI
    pass

class CMPW(CMPInsn):
    def __init__(self, info, result, args):
        Insn.__init__(self)
        self.info = info

        self.result = result
        self.result_regclass = CR_FIELD

        self.reg_args = args
        self.reg_arg_regclasses = [GP_REGISTER, GP_REGISTER]

    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
        self.arg_reg1 = allocator.loc_of(self.reg_args[0])
        self.arg_reg2 = allocator.loc_of(self.reg_args[1])

    def emit(self, asm):
        asm.cmpw(self.result_reg.number, self.arg_reg1.number, self.arg_reg2.number)

class CMPWL(CMPW):
    def emit(self, asm):
        asm.cmpwl(self.result_reg.number, self.arg_reg1.number, self.arg_reg2.number)

class CMPWI(CMPInsn):
    def __init__(self, info, result, args):
        Insn.__init__(self)
        self.info = info
        self.imm = args[1]

        self.result = result
        self.result_regclass = CR_FIELD

        self.reg_args = [args[0]]
        self.reg_arg_regclasses = [GP_REGISTER]

    def allocate(self, allocator):
        self.result_reg = allocator.loc_of(self.result)
        self.arg_reg = allocator.loc_of(self.reg_args[0])

    def emit(self, asm):
        asm.cmpwi(self.result_reg.number, self.arg_reg.number, self.imm.value)

class CMPWLI(CMPW):
    def emit(self, asm):
        asm.cmpwli(self.result_reg.number, self.arg_reg.number, self.imm.value)


## class MTCTR(Insn):
##     def __init__(self, result, args):
##         Insn.__init__(self)
##         self.result = result
##         self.result_regclass = CT_REGISTER

##         self.reg_args = args
##         self.reg_arg_regclasses = [GP_REGISTER]

##     def allocate(self, allocator):
##         self.arg_reg = allocator.loc_of(self.reg_args[0])

##     def emit(self, asm):
##         asm.mtctr(self.arg_reg.number)

class Jump(Insn):
    def __init__(self, gv_cond, targetbuilder, jump_if_true, jump_args_gv):
        Insn.__init__(self)
        self.gv_cond = gv_cond
        self.jump_if_true = jump_if_true

        self.result = None
        self.result_regclass = NO_REGISTER
        self.reg_args = [gv_cond]
        self.reg_arg_regclasses = [CR_FIELD]

        self.jump_args_gv = jump_args_gv
        self.targetbuilder = targetbuilder
    def allocate(self, allocator):
        self.crf = allocator.loc_of(self.reg_args[0])
        self.bit, self.negated = allocator.crfinfo[self.crf.number]

        assert self.targetbuilder.initial_var2loc is None
        self.targetbuilder.initial_var2loc = {}
        for gv_arg in self.jump_args_gv:
            self.targetbuilder.initial_var2loc[gv_arg] = allocator.var2loc[gv_arg]
        allocator.builders_to_tell_spill_offset_to.append(self.targetbuilder)
    def emit(self, asm):
        if self.targetbuilder.start:
            asm.load_word(rSCRATCH, self.targetbuilder.start)
        else:
            self.targetbuilder.patch_start_here = asm.mc.tell()
            asm.load_word(rSCRATCH, 0)
        asm.mtctr(rSCRATCH)
        if self.negated ^ self.jump_if_true:
            BO = 12 # jump if relavent bit is set in the CR
        else:
            BO = 4  # jump if relavent bit is NOT set in the CR
        asm.bcctr(BO, self.crf.number*4 + self.bit)

class SpillCalleeSaves(Insn):
    def __init__(self):
        Insn.__init__(self)
        self.reg_args = []
        self.reg_arg_regclasses = []
        self.result = None
        self.result_regclass = NO_REGISTER
    def allocate(self, allocator):
        # cough cough cough
        callersave = gprs[3:13]
        for v in allocator.var2loc:
            loc = allocator.loc_of(v)
            if loc in callersave:
                #print "spilling", v, "from", loc, "to",
                allocator.spill(loc, v)
                #print allocator.loc_of(v)
                allocator.freeregs[GP_REGISTER].append(loc)
    def emit(self, asm):
        pass

class LoadArg(Insn):
    def __init__(self, argnumber, arg):
        Insn.__init__(self)
        self.reg_args = []
        self.reg_arg_regclasses = []
        self.result = None
        self.result_regclass = NO_REGISTER

        self.argnumber = argnumber
        self.arg = arg
    def allocate(self, allocator):
        from pypy.jit.codegen.ppc.rgenop import Var
        if isinstance(self.arg, Var):
            self.loc = allocator.loc_of(self.arg)
        else:
            self.loc = None
    def emit(self, asm):
        targetreg = 3+self.argnumber
        if self.loc is None:
            self.arg.load_now(asm, gprs[targetreg])
        elif self.loc.is_register:
            asm.mr(targetreg, self.loc.number)
        else:
            asm.lwz(targetreg, rFP, self.loc.offset)

class CALL(Insn):
    def __init__(self, result, target):
        Insn.__init__(self)
        from pypy.jit.codegen.ppc.rgenop import Var
        if isinstance(target, Var):
            self.reg_args = [target]
            self.reg_arg_regclasses = [CT_REGISTER]
        else:
            self.reg_args = []
            self.reg_arg_regclasses = []
            self.target = target
        self.result = result
        self.result_regclass = GP_REGISTER
    def allocate(self, allocator):
        if self.reg_args:
            assert allocator.loc_of(self.reg_args[0]) is ctr
        self.resultreg = allocator.loc_of(self.result)
    def emit(self, asm):
        if not self.reg_args:
            self.target.load_now(asm, gprs[0])
            asm.mtctr(0)
        asm.bctrl()
        asm.lwz(rFP, rSP, 0)
        if self.resultreg != gprs[3]:
            asm.mr(self.resultreg.number, 3)


class AllocTimeInsn(Insn):
    def __init__(self):
        Insn.__init__(self)
        self.reg_args = []
        self.reg_arg_regclasses = []
        self.result_regclass =  NO_REGISTER
        self.result = None

class Move(AllocTimeInsn):
    def __init__(self, dest, src):
        self.dest = dest
        self.src = src
    def emit(self, asm):
        asm.mr(self.dest.number, self.src.number)

class Load(AllocTimeInsn):
    def __init__(self, dest, const):
        self.dest = dest
        self.const = const
    def emit(self, asm):
        self.const.load_now(asm, self.dest)

class Unspill(AllocTimeInsn):
    """ A special instruction inserted by our register "allocator."  It
    indicates that we need to load a value from the stack into a register
    because we spilled a particular value. """
    def __init__(self, var, reg, stack):
        """
        var --- the var we spilled (a Var)
        reg --- the reg we spilled it from (an integer)
        offset --- the offset on the stack we spilled it to (an integer)
        """
        AllocTimeInsn.__init__(self)
        self.var = var
        assert isinstance(reg, GPR)
        self.reg = reg
        self.stack = stack
    def emit(self, asm):
        asm.lwz(self.reg.number, rFP, self.stack.offset)

class Spill(AllocTimeInsn):
    """ A special instruction inserted by our register "allocator."
    It indicates that we need to store a value from the register into
    the stack because we spilled a particular value."""
    def __init__(self, var, reg, stack):
        """
        var --- the var we are spilling (a Var)
        reg --- the reg we are spilling it from (an integer)
        offset --- the offset on the stack we are spilling it to (an integer)
        """
        AllocTimeInsn.__init__(self)
        self.var = var
        assert isinstance(reg, GPR)
        self.reg = reg
        self.stack = stack
    def emit(self, asm):
        asm.stw(self.reg.number, rFP, self.stack.offset)

class _CRF2GPR(AllocTimeInsn):
    def __init__(self, targetreg, bit, negated):
        AllocTimeInsn.__init__(self)
        self.targetreg = targetreg
        self.bit = bit
        self.negated = negated
    def emit(self, asm):
        asm.mfcr(self.targetreg)
        asm.extrwi(self.targetreg, self.targetreg, 1, self.bit)
        if self.negated:
            asm.xori(self.targetreg, self.targetreg, 1)

class _GPR2CTR(AllocTimeInsn):
    def __init__(self, fromreg):
        AllocTimeInsn.__init__(self)
        self.fromreg = fromreg
    def emit(self, asm):
        asm.mtctr(self.fromreg)

class Return(Insn):
    """ Ensures the return value is in r3 """
    def __init__(self, var):
        Insn.__init__(self)
        self.var = var
        self.reg_args = [self.var]
        self.reg_arg_regclasses = [GP_REGISTER]
        self.result = None
        self.result_regclass = NO_REGISTER
        self.reg = None
    def allocate(self, allocator):
        self.reg = allocator.loc_of(self.reg_args[0])
    def emit(self, asm):
        if self.reg.number != 3:
            asm.mr(r3, self.reg.number)

class FakeUse(Insn):
    """ A fake use of a var to get it into a register.  And reserving
    a condition register field."""
    def __init__(self, rvar, var):
        Insn.__init__(self)
        self.var = var
        self.reg_args = [self.var]
        self.reg_arg_regclasses = [GP_REGISTER]
        self.result = rvar
        self.result_regclass = CR_FIELD
    def allocate(self, allocator):
        pass
    def emit(self, asm):
        pass
