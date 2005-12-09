from pypy.translator.llvm.node import ConstantLLVMNode
from pypy.translator.llvm.log import log 
from pypy.translator.c.extfunc import EXTERNALS

log = log.extfuncnode

class ExtFuncSig(object):
    def __init__(self, retval, args):
        self.args = args
        self.rettype = retval

# signature of external functions differ from C's implementation
ext_func_sigs = {
    "%LL_os_isatty" : ExtFuncSig("int", None),
    "%LL_stack_too_big" : ExtFuncSig("int", None),
    "%LL_os_lseek" : ExtFuncSig("int", None),
    "%LL_thread_acquirelock" : ExtFuncSig("int", [None, "int"]),
    "%LL_thread_start" : ExtFuncSig(None, ["sbyte*", "sbyte*"])}
    
class ExternalFuncNode(ConstantLLVMNode):

    def __init__(self, db, value):
        self.db = db
        self.value = value
        name = value._callable.__name__
        assert name.startswith("ll")

        mapped_name = EXTERNALS[value._callable]
        self.ref = self.make_ref("%", mapped_name)
        self.wrapper = ext_func_sigs.get(self.ref, None)
        
    def __str__(self):
        return "<ExternalFuncNode %r>" % self.ref

    def getdecl_parts(self):
        T = self.value._TYPE
        rettype = self.db.repr_type(T.RESULT)
        argtypes = [self.db.repr_type(a) for a in T.ARGS]
        return rettype, argtypes
    
    def getdecl(self):
        rettype, argtypes = self.getdecl_parts()
        return "%s %s(%s)" % (rettype, self.ref, ", ".join(argtypes))

    def writedecl(self, codewriter): 
        codewriter.declare(self.getdecl())

    def writeimpl(self, codewriter):
                                   
        if self.wrapper is None:
            return
        
        rettype, argtypes = self.getdecl_parts()
        argrefs = [self.db.repr_tmpvar() for ii in argtypes]
        arg_desription = ", ".join([
            "%s %s" % (typ_, name)
            for typ_, name in zip(argtypes, argrefs)])
        
        open_decl = "%s %s(%s)" % (rettype, self.ref, arg_desription)
        codewriter.openfunc(open_decl)
        
        returnval = self.db.repr_tmpvar()
        
        # call function with this
        expected_argrefs = []
        expected_argtypes = []

        # find out what the args/types should be
        if self.wrapper.args is not None:
            assert len(self.wrapper.args) == len(argtypes)
            
            for expected_typ, typ, ref in zip(self.wrapper.args,
                                              argtypes,
                                              argrefs):
                if expected_typ is not None:

                    # cast to desired arg type
                    expected_ref = self.db.repr_tmpvar()
                    codewriter.cast(expected_ref, typ, ref, expected_typ)

                else:
                    expected_ref = ref
                    expected_typ = typ

                expected_argrefs.append(expected_ref)
                expected_argtypes.append(expected_typ)
        else:
            expected_argrefs = argrefs
            expected_argtypes = argtypes

        # find out what the return type should be 
        expected_rettype = self.wrapper.rettype or rettype

        # call
        codewriter.call(returnval, expected_rettype, self.ref,
                        expected_argrefs, expected_argtypes)

        if self.wrapper.rettype:
            # cast to desired return type
            tmpval = returnval
            returnval = self.db.repr_tmpvar()
            codewriter.cast(returnval, self.wrapper.rettype,
                            tmpval, rettype)
            
        codewriter.ret(rettype, returnval)
        codewriter.closefunc()

    def writeglobalconstants(self, codewriter):
        pass
