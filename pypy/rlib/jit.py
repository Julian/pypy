import py
import sys
from pypy.rpython.extregistry import ExtRegistryEntry
from pypy.rlib.objectmodel import CDefinedIntSymbolic
from pypy.rlib.objectmodel import keepalive_until_here
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib.nonconst import NonConstant

def purefunction(func):
    """ Decorate a function as pure. Pure means precisely that:

    (1) the result of the call should not change if the arguments are
        the same (same numbers or same pointers)
    (2) it's fine to remove the call completely if we can guess the result
    according to rule 1

    Most importantly it doesn't mean that pure function has no observable
    side effect, but those side effects can be ommited (ie caching).
    For now, such a function should never raise an exception.
    """
    func._pure_function_ = True
    return func

def hint(x, **kwds):
    """ Hint for the JIT

    possible arguments are:
    XXX
    """
    return x

def dont_look_inside(func):
    """ Make sure the JIT does not trace inside decorated function
    (it becomes a call instead)
    """
    func._jit_look_inside_ = False
    return func

def unroll_safe(func):
    """ JIT can safely unroll loops in this function and this will
    not lead to code explosion
    """
    func._jit_unroll_safe_ = True
    return func

def loop_invariant(func):
    """ Describes a function with no argument that returns an object that
    is always the same in a loop.

    Use it only if you know what you're doing.
    """
    dont_look_inside(func)
    func._jit_loop_invariant_ = True
    return func

def purefunction_promote(promote_args='all'):
    """ A decorator that promotes all arguments and then calls the supplied
    function
    """
    def decorator(func):
        import inspect
        purefunction(func)
        args, varargs, varkw, defaults = inspect.getargspec(func)
        args = ["v%s" % (i, ) for i in range(len(args))]
        assert varargs is None and varkw is None
        assert not defaults
        argstring = ", ".join(args)
        code = ["def f(%s):\n" % (argstring, )]
        if promote_args != 'all':
            args = [('v%d' % int(i)) for i in promote_args.split(",")]
        for arg in args:
            code.append("    %s = hint(%s, promote=True)\n" % (arg, arg))
        code.append("    return func(%s)\n" % (argstring, ))
        d = {"func": func, "hint": hint}
        exec py.code.Source("\n".join(code)).compile() in d
        result = d["f"]
        result.func_name = func.func_name + "_promote"
        return result
    return decorator

def oopspec(spec):
    def decorator(func):
        func.oopspec = spec
        return func
    return decorator

class Entry(ExtRegistryEntry):
    _about_ = hint

    def compute_result_annotation(self, s_x, **kwds_s):
        from pypy.annotation import model as annmodel
        s_x = annmodel.not_const(s_x)
        access_directly = 's_access_directly' in kwds_s
        fresh_virtualizable = 's_fresh_virtualizable' in kwds_s
        if  access_directly or fresh_virtualizable:
            assert access_directly, "lone fresh_virtualizable hint"
            if isinstance(s_x, annmodel.SomeInstance):
                from pypy.objspace.flow.model import Constant
                classdesc = s_x.classdef.classdesc
                virtualizable = classdesc.read_attribute('_virtualizable2_',
                                                         Constant(None)).value
                if virtualizable is not None:
                    flags = s_x.flags.copy()
                    flags['access_directly'] = True
                    if fresh_virtualizable:
                        flags['fresh_virtualizable'] = True
                    s_x = annmodel.SomeInstance(s_x.classdef,
                                                s_x.can_be_None,
                                                flags)        
        return s_x

    def specialize_call(self, hop, **kwds_i):
        from pypy.rpython.lltypesystem import lltype
        hints = {}
        for key, index in kwds_i.items():
            s_value = hop.args_s[index]
            if not s_value.is_constant():
                from pypy.rpython.error import TyperError
                raise TyperError("hint %r is not constant" % (key,))
            assert key.startswith('i_')
            hints[key[2:]] = s_value.const
        v = hop.inputarg(hop.args_r[0], arg=0)
        c_hint = hop.inputconst(lltype.Void, hints)
        hop.exception_cannot_occur()
        return hop.genop('hint', [v, c_hint], resulttype=v.concretetype)


def we_are_jitted():
    """ Considered as true during tracing and blackholing,
    so its consquences are reflected into jitted code """
    return False

_we_are_jitted = CDefinedIntSymbolic('0 /* we are not jitted here */',
                                     default=0)

class Entry(ExtRegistryEntry):
    _about_ = we_are_jitted

    def compute_result_annotation(self):
        from pypy.annotation import model as annmodel
        return annmodel.SomeInteger(nonneg=True)

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        hop.exception_cannot_occur()
        return hop.inputconst(lltype.Signed, _we_are_jitted)


def current_trace_length():
    """During JIT tracing, returns the current trace length (as a constant).
    If not tracing, returns -1."""
    if NonConstant(False):
        return 73
    return -1
current_trace_length.oopspec = 'jit.current_trace_length()'

def jit_debug(string, arg1=-sys.maxint-1, arg2=-sys.maxint-1,
                      arg3=-sys.maxint-1, arg4=-sys.maxint-1):
    """When JITted, cause an extra operation JIT_DEBUG to appear in
    the graphs.  Should not be left after debugging."""
    keepalive_until_here(string) # otherwise the whole function call is removed
jit_debug.oopspec = 'jit.debug(string, arg1, arg2, arg3, arg4)'

def assert_green(value):
    """Very strong assert: checks that 'value' is a green
    (a JIT compile-time constant)."""
    keepalive_until_here(value)
assert_green._annspecialcase_ = 'specialize:argtype(0)'
assert_green.oopspec = 'jit.assert_green(value)'

class AssertGreenFailed(Exception):
    pass


##def force_virtualizable(virtualizable):
##    pass

##class Entry(ExtRegistryEntry):
##    _about_ = force_virtualizable

##    def compute_result_annotation(self):
##        from pypy.annotation import model as annmodel
##        return annmodel.s_None

##    def specialize_call(self, hop):
##        [vinst] = hop.inputargs(hop.args_r[0])
##        cname = inputconst(lltype.Void, None)
##        cflags = inputconst(lltype.Void, {})
##        hop.exception_cannot_occur()
##        return hop.genop('jit_force_virtualizable', [vinst, cname, cflags],
##                         resulttype=lltype.Void)

# ____________________________________________________________
# VRefs

def virtual_ref(x):
    
    """Creates a 'vref' object that contains a reference to 'x'.  Calls
    to virtual_ref/virtual_ref_finish must be properly nested.  The idea
    is that the object 'x' is supposed to be JITted as a virtual between
    the calls to virtual_ref and virtual_ref_finish, but the 'vref'
    object can escape at any point in time.  If at runtime it is
    dereferenced (by the call syntax 'vref()'), it returns 'x', which is
    then forced."""
    return DirectJitVRef(x)
virtual_ref.oopspec = 'virtual_ref(x)'

def virtual_ref_finish(x):
    """See docstring in virtual_ref(x).  Note that virtual_ref_finish
    takes as argument the real object, not the vref."""
    keepalive_until_here(x)   # otherwise the whole function call is removed
virtual_ref_finish.oopspec = 'virtual_ref_finish(x)'

def non_virtual_ref(x):
    """Creates a 'vref' that just returns x when called; nothing more special.
    Used for None or for frames outside JIT scope."""
    return DirectVRef(x)

# ---------- implementation-specific ----------

class DirectVRef(object):
    def __init__(self, x):
        self._x = x
    def __call__(self):
        return self._x

class DirectJitVRef(DirectVRef):
    def __init__(self, x):
        assert x is not None, "virtual_ref(None) is not allowed"
        DirectVRef.__init__(self, x)

class Entry(ExtRegistryEntry):
    _about_ = (non_virtual_ref, DirectJitVRef)

    def compute_result_annotation(self, s_obj):
        from pypy.rlib import _jit_vref
        return _jit_vref.SomeVRef(s_obj)

    def specialize_call(self, hop):
        return hop.r_result.specialize_call(hop)

class Entry(ExtRegistryEntry):
    _type_ = DirectVRef

    def compute_annotation(self):
        from pypy.rlib import _jit_vref
        assert isinstance(self.instance, DirectVRef)
        s_obj = self.bookkeeper.immutablevalue(self.instance())
        return _jit_vref.SomeVRef(s_obj)

vref_None = non_virtual_ref(None)

# ____________________________________________________________
# User interface for the hotpath JIT policy

class JitHintError(Exception):
    """Inconsistency in the JIT hints."""

OPTIMIZER_SIMPLE = 0
OPTIMIZER_NO_UNROLL = 1
OPTIMIZER_FULL = 2

PARAMETERS = {'threshold': 1000,
              'trace_eagerness': 200,
              'trace_limit': 10000,
              'inlining': False,
              'optimizer': OPTIMIZER_FULL,
              'loop_longevity': 1000,
              }
unroll_parameters = unrolling_iterable(PARAMETERS.keys())

# ____________________________________________________________

class JitDriver:    
    """Base class to declare fine-grained user control on the JIT.  So
    far, there must be a singleton instance of JitDriver.  This style
    will allow us (later) to support a single RPython program with
    several independent JITting interpreters in it.
    """

    active = True          # if set to False, this JitDriver is ignored
    virtualizables = []

    def __init__(self, greens=None, reds=None, virtualizables=None,
                 get_jitcell_at=None, set_jitcell_at=None,
                 get_printable_location=None, confirm_enter_jit=None,
                 can_never_inline=None):
        if greens is not None:
            self.greens = greens
        if reds is not None:
            self.reds = reds
        if not hasattr(self, 'greens') or not hasattr(self, 'reds'):
            raise AttributeError("no 'greens' or 'reds' supplied")
        if virtualizables is not None:
            self.virtualizables = virtualizables
        for v in self.virtualizables:
            assert v in self.reds
        self._alllivevars = dict.fromkeys(
            [name for name in self.greens + self.reds if '.' not in name])
        self._make_extregistryentries()
        self.get_jitcell_at = get_jitcell_at
        self.set_jitcell_at = set_jitcell_at
        self.get_printable_location = get_printable_location
        self.confirm_enter_jit = confirm_enter_jit
        self.can_never_inline = can_never_inline

    def _freeze_(self):
        return True

    def jit_merge_point(_self, **livevars):
        # special-cased by ExtRegistryEntry
        assert dict.fromkeys(livevars) == _self._alllivevars

    def can_enter_jit(_self, **livevars):
        # special-cased by ExtRegistryEntry
        assert dict.fromkeys(livevars) == _self._alllivevars

    def loop_header(self):
        # special-cased by ExtRegistryEntry
        pass

    def _set_param(self, name, value):
        # special-cased by ExtRegistryEntry
        # (internal, must receive a constant 'name')
        assert name in PARAMETERS

    def set_param(self, name, value):
        """Set one of the tunable JIT parameter."""
        for name1 in unroll_parameters:
            if name1 == name:
                self._set_param(name1, value)
                return
        raise ValueError("no such parameter")
    set_param._annspecialcase_ = 'specialize:arg(0)'

    def set_user_param(self, text):
        """Set the tunable JIT parameters from a user-supplied string
        following the format 'param=value,param=value'.  For programmatic
        setting of parameters, use directly JitDriver.set_param().
        """
        for s in text.split(','):
            s = s.strip(' ')
            parts = s.split('=')
            if len(parts) != 2:
                raise ValueError
            try:
                value = int(parts[1])
            except ValueError:
                raise    # re-raise the ValueError (annotator hint)
            name = parts[0]
            self.set_param(name, value)
    set_user_param._annspecialcase_ = 'specialize:arg(0)'

    def _make_extregistryentries(self):
        # workaround: we cannot declare ExtRegistryEntries for functions
        # used as methods of a frozen object, but we can attach the
        # bound methods back to 'self' and make ExtRegistryEntries
        # specifically for them.
        self.jit_merge_point = self.jit_merge_point
        self.can_enter_jit = self.can_enter_jit
        self.loop_header = self.loop_header
        self._set_param = self._set_param

        class Entry(ExtEnterLeaveMarker):
            _about_ = (self.jit_merge_point, self.can_enter_jit)

        class Entry(ExtLoopHeader):
            _about_ = self.loop_header

        class Entry(ExtSetParam):
            _about_ = self._set_param

# ____________________________________________________________
#
# Annotation and rtyping of some of the JitDriver methods

class BaseJitCell(object):
    __slots__ = ()


class ExtEnterLeaveMarker(ExtRegistryEntry):
    # Replace a call to myjitdriver.jit_merge_point(**livevars)
    # with an operation jit_marker('jit_merge_point', myjitdriver, livevars...)
    # Also works with can_enter_jit.

    def compute_result_annotation(self, **kwds_s):
        from pypy.annotation import model as annmodel

        if self.instance.__name__ == 'jit_merge_point':
            self.annotate_hooks(**kwds_s)

        driver = self.instance.im_self
        keys = kwds_s.keys()
        keys.sort()
        expected = ['s_' + name for name in driver.greens + driver.reds
                                if '.' not in name]
        expected.sort()
        if keys != expected:
            raise JitHintError("%s expects the following keyword "
                               "arguments: %s" % (self.instance,
                                                  expected))

        try:
            cache = self.bookkeeper._jit_annotation_cache[driver]
        except AttributeError:
            cache = {}
            self.bookkeeper._jit_annotation_cache = {driver: cache}
        except KeyError:
            cache = {}
            self.bookkeeper._jit_annotation_cache[driver] = cache
        for key, s_value in kwds_s.items():
            s_previous = cache.get(key, annmodel.s_ImpossibleValue)
            s_value = annmodel.unionof(s_previous, s_value)
            if annmodel.isdegenerated(s_value):
                raise JitHintError("mixing incompatible types in argument %s"
                                   " of jit_merge_point/can_enter_jit" %
                                   key[2:])
            cache[key] = s_value

        return annmodel.s_None

    def annotate_hooks(self, **kwds_s):
        driver = self.instance.im_self
        s_jitcell = self.bookkeeper.valueoftype(BaseJitCell)
        h = self.annotate_hook
        h(driver.get_jitcell_at, driver.greens, **kwds_s)
        h(driver.set_jitcell_at, driver.greens, [s_jitcell], **kwds_s)
        h(driver.get_printable_location, driver.greens, **kwds_s)

    def annotate_hook(self, func, variables, args_s=[], **kwds_s):
        if func is None:
            return
        bk = self.bookkeeper
        s_func = bk.immutablevalue(func)
        uniquekey = 'jitdriver.%s' % func.func_name
        args_s = args_s[:]
        for name in variables:
            if '.' not in name:
                s_arg = kwds_s['s_' + name]
            else:
                objname, fieldname = name.split('.')
                s_instance = kwds_s['s_' + objname]
                attrdef = s_instance.classdef.find_attribute(fieldname)
                position = self.bookkeeper.position_key
                attrdef.read_locations[position] = True
                s_arg = attrdef.getvalue()
                assert s_arg is not None
            args_s.append(s_arg)
        bk.emulate_pbc_call(uniquekey, s_func, args_s)

    def specialize_call(self, hop, **kwds_i):
        # XXX to be complete, this could also check that the concretetype
        # of the variables are the same for each of the calls.
        from pypy.rpython.error import TyperError
        from pypy.rpython.lltypesystem import lltype
        driver = self.instance.im_self
        greens_v = []
        reds_v = []
        for name in driver.greens:
            if '.' not in name:
                i = kwds_i['i_' + name]
                r_green = hop.args_r[i]
                v_green = hop.inputarg(r_green, arg=i)
            else:
                if hop.rtyper.type_system.name == 'ootypesystem':
                    py.test.skip("lltype only")
                objname, fieldname = name.split('.')   # see test_green_field
                assert objname in driver.reds
                i = kwds_i['i_' + objname]
                s_red = hop.args_s[i]
                r_red = hop.args_r[i]
                while True:
                    try:
                        mangled_name, r_field = r_red._get_field(fieldname)
                        break
                    except KeyError:
                        pass
                    assert r_red.rbase is not None, (
                        "field %r not found in %r" % (name,
                                                      r_red.lowleveltype.TO))
                    r_red = r_red.rbase
                GTYPE = r_red.lowleveltype.TO
                assert GTYPE._immutable_field(mangled_name), (
                    "field %r must be declared as immutable" % name)
                if not hasattr(driver, 'll_greenfields'):
                    driver.ll_greenfields = {}
                driver.ll_greenfields[name] = GTYPE, mangled_name
                #
                v_red = hop.inputarg(r_red, arg=i)
                c_llname = hop.inputconst(lltype.Void, mangled_name)
                v_green = hop.genop('getfield', [v_red, c_llname],
                                    resulttype = r_field)
                s_green = s_red.classdef.about_attribute(fieldname)
                assert s_green is not None
                hop.rtyper.annotator.setbinding(v_green, s_green)
            greens_v.append(v_green)
        for name in driver.reds:
            i = kwds_i['i_' + name]
            r_red = hop.args_r[i]
            v_red = hop.inputarg(r_red, arg=i)
            reds_v.append(v_red)
        hop.exception_cannot_occur()
        vlist = [hop.inputconst(lltype.Void, self.instance.__name__),
                 hop.inputconst(lltype.Void, driver)]
        vlist.extend(greens_v)
        vlist.extend(reds_v)
        return hop.genop('jit_marker', vlist,
                         resulttype=lltype.Void)

class ExtLoopHeader(ExtRegistryEntry):
    # Replace a call to myjitdriver.loop_header()
    # with an operation jit_marker('loop_header', myjitdriver).

    def compute_result_annotation(self, **kwds_s):
        from pypy.annotation import model as annmodel
        return annmodel.s_None

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        driver = self.instance.im_self
        hop.exception_cannot_occur()
        vlist = [hop.inputconst(lltype.Void, 'loop_header'),
                 hop.inputconst(lltype.Void, driver)]
        return hop.genop('jit_marker', vlist,
                         resulttype=lltype.Void)

class ExtSetParam(ExtRegistryEntry):

    def compute_result_annotation(self, s_name, s_value):
        from pypy.annotation import model as annmodel
        assert s_name.is_constant()
        assert annmodel.SomeInteger().contains(s_value)
        return annmodel.s_None

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        hop.exception_cannot_occur()
        driver = self.instance.im_self
        name = hop.args_s[0].const
        v_value = hop.inputarg(lltype.Signed, arg=1)
        vlist = [hop.inputconst(lltype.Void, "set_param"),
                 hop.inputconst(lltype.Void, driver),
                 hop.inputconst(lltype.Void, name),
                 v_value]
        return hop.genop('jit_marker', vlist,
                         resulttype=lltype.Void)
