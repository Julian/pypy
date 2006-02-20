
import py
from pypy.rpython.lltypesystem.lltype import typeOf, pyobjectptr, Ptr, PyObject, Void
from pypy.rpython.llinterp import LLInterpreter, LLException,log
from pypy.rpython.rmodel import inputconst
from pypy.translator.translator import TranslationContext
from pypy.rpython.rlist import *
from pypy.rpython.rint import signed_repr
from pypy.rpython import rstr
from pypy.annotation.model import lltype_to_annotation
from pypy.rpython.rarithmetic import r_uint, ovfcheck
from pypy import conftest

# switch on logging of interp to show more info on failing tests

def setup_module(mod):
    mod.logstate = py.log._getstate()
    py.log.setconsumer("llinterp", py.log.STDOUT)

def teardown_module(mod):
    py.log._setstate(mod.logstate)

def find_exception(exc, interp):
    assert isinstance(exc, LLException)
    import exceptions
    klass, inst = exc.args[0], exc.args[1]
    # indirect way to invoke fn_pyexcclass2exc, for memory/test/test_llinterpsim
    f = typer.getexceptiondata().fn_pyexcclass2exc
    obj = typer.type_system.deref(f)
    ll_pyexcclass2exc_graph = obj.graph
    for cls in exceptions.__dict__.values():
        if type(cls) is type(Exception):
            if interp.eval_graph(ll_pyexcclass2exc_graph, [pyobjectptr(cls)]).typeptr == klass:
                return cls
    raise ValueError, "couldn't match exception"


def timelog(prefix, call, *args, **kwds): 
    #import time
    #print prefix, "...", 
    #start = time.time()
    res = call(*args, **kwds) 
    #elapsed = time.time() - start 
    #print "%.2f secs" %(elapsed,)
    return res 

def gengraph(func, argtypes=[], viewbefore='auto', policy=None,
             type_system="lltype"):
    t = TranslationContext()
    a = t.buildannotator(policy=policy)
    timelog("annotating", a.build_types, func, argtypes)
    if viewbefore == 'auto':
        viewbefore = conftest.option.view
    if viewbefore:
        a.simplify()
        t.view()
    global typer # we need it for find_exception
    typer = t.buildrtyper(type_system=type_system)
    timelog("rtyper-specializing", typer.specialize) 
    #t.view()
    timelog("checking graphs", t.checkgraphs) 
    desc = t.annotator.bookkeeper.getdesc(func)
    graph = desc.specialize(argtypes)
    return t, typer, graph

_lastinterpreted = []
_tcache = {}
def get_interpreter(func, values, view='auto', viewbefore='auto', policy=None,
                    someobjects=False, type_system="lltype"):
    key = (func,) + tuple([typeOf(x) for x in values])+ (someobjects,)
    try: 
        (t, interp, graph) = _tcache[key]
    except KeyError:
        def annotation(x):
            T = typeOf(x)
            if T == Ptr(PyObject) and someobjects:
                return object
            elif T == Ptr(rstr.STR):
                return str
            else:
                return lltype_to_annotation(T)

        t, typer, graph = gengraph(func, [annotation(x) for x in values],
                                   viewbefore, policy, type_system=type_system)
        interp = LLInterpreter(typer)
        _tcache[key] = (t, interp, graph)
        # keep the cache small 
        _lastinterpreted.append(key) 
        if len(_lastinterpreted) >= 4: 
            del _tcache[_lastinterpreted.pop(0)]
    if view == 'auto':
        view = conftest.option.view
    if view:
        t.view()
    return interp, graph

def interpret(func, values, view='auto', viewbefore='auto', policy=None,
              someobjects=False, type_system="lltype"):
    interp, graph = get_interpreter(func, values, view, viewbefore, policy,
                                    someobjects, type_system=type_system)
    return interp.eval_graph(graph, values)

def interpret_raises(exc, func, values, view='auto', viewbefore='auto',
                     policy=None, someobjects=False, type_system="lltype"):
    interp, graph  = get_interpreter(func, values, view, viewbefore, policy,
                             someobjects, type_system=type_system)
    info = py.test.raises(LLException, "interp.eval_graph(graph, values)")
    assert find_exception(info.value, interp) is exc, "wrong exception type"

#__________________________________________________________________
# tests

def test_int_ops():
    res = interpret(number_ops, [3])
    assert res == 4

def test_invert():
    def f(x):
        return ~x
    res = interpret(f, [3])
    assert res == ~3
    assert interpret(f, [r_uint(3)]) == ~r_uint(3)

def test_float_ops():
    res = interpret(number_ops, [3.5])
    assert res == 4.5

def test_ifs():
    res = interpret(simple_ifs, [0])
    assert res == 43
    res = interpret(simple_ifs, [1])
    assert res == 42

def test_raise():
    res = interpret(raise_exception, [41])
    assert res == 41
    interpret_raises(IndexError, raise_exception, [42])
    interpret_raises(ValueError, raise_exception, [43])

def test_call_raise():
    res = interpret(call_raise, [41])
    assert res == 41
    interpret_raises(IndexError, call_raise, [42])
    interpret_raises(ValueError, call_raise, [43])

def test_call_raise_twice():
    res = interpret(call_raise_twice, [6, 7])
    assert res == 13
    interpret_raises(IndexError, call_raise_twice, [6, 42])
    res = interpret(call_raise_twice, [6, 43])
    assert res == 1006
    interpret_raises(IndexError, call_raise_twice, [42, 7])
    interpret_raises(ValueError, call_raise_twice, [43, 7])

def test_call_raise_intercept():
    res = interpret(call_raise_intercept, [41])
    assert res == 41
    res = interpret(call_raise_intercept, [42])
    assert res == 42
    interpret_raises(TypeError, call_raise_intercept, [43])

def test_while_simple():
    res = interpret(while_simple, [3])
    assert res == 6

def test_number_comparisons():
    for t in float, int:
        val1 = t(3)
        val2 = t(4)
        gcres = interpret(comparisons, [val1, val2])
        res = [getattr(gcres, x) for x in typeOf(gcres).TO._names]
        assert res == [True, True, False, True, False, False]

def test_some_builtin():
    def f(i, j):
        x = range(i)
        return x[j-1]
    res = interpret(f, [10, 7])
    assert res == 6

def test_recursion_does_not_overwrite_my_variables():
    def f(i):
        j = i + 1
        if i > 0:
            f(i-1)
        return j

    res = interpret(f, [4])
    assert res == 5

#
#__________________________________________________________________
#
#  Test lists
def test_list_creation():
    def f():
        return [1,2,3]
    res = interpret(f,[])
    assert len(res.ll_items()) == len([1,2,3])
    for i in range(3):
        assert res.ll_items()[i] == i+1

def test_list_itemops():
    def f(i):
        l = [1, i]
        l[0] = 0
        del l[1]
        return l[-1]
    res = interpret(f, [3])
    assert res == 0

def test_list_append():
    def f(i):
        l = [1]
        l.append(i)
        return l[0] + l[1]
    res = interpret(f, [3])
    assert res == 4

def test_list_extend():
    def f(i):
        l = [1]
        l.extend([i])
        return l[0] + l[1]
    res = interpret(f, [3])
    assert res == 4

def test_list_multiply():
    def f(i):
        l = [i]
        l = l * i  # uses alloc_and_set for len(l) == 1
        return len(l)
    res = interpret(f, [3])
    assert res == 3

def test_unicode():
    def f():
        return u'Hello world'
    res = interpret(f,[])
    
    assert res._obj.value == u'Hello world'
    
##def test_unicode_split():
##    def f():
##        res = u'Hello world'.split()
##        return u' '.join(res)
##    res = interpret(f,[],True)
##    
##    assert res == u'Hello world'

def test_list_reverse():
    def f():
        l = [1,2,3]
        l.reverse()
        return l
    res = interpret(f,[])
    assert len(res.ll_items()) == len([3,2,1])
    print res
    for i in range(3):
        assert res.ll_items()[i] == 3-i
        
def test_list_pop():
    def f():
        l = [1,2,3]
        l1 = l.pop(2)
        l2 = l.pop(1)
        l3 = l.pop(-1)
        return [l1,l2,l3]
    res = interpret(f,[])
    assert len(res.ll_items()) == 3

def test_obj_obj_add():
    def f(x,y):
        return x+y
    _1L = pyobjectptr(1L)
    _2L = pyobjectptr(2L)
    res = interpret(f, [_1L, _2L], someobjects=True)
    assert res._obj.value == 3L

def test_ovf():
    import sys
    def f(x):
        try:
            return ovfcheck(sys.maxint + x)
        except OverflowError:
            return 1
    res = interpret(f, [1])
    assert res == 1
    res = interpret(f, [0])
    assert res == sys.maxint
    def g(x):
        try:
            return ovfcheck(abs(x))
        except OverflowError:
            return 42
    res = interpret(g, [-sys.maxint - 1])
    assert res == 42
    res = interpret(g, [-15])
    assert res == 15

def test_div_ovf_zer():
    import sys
    def f(x):
        try:
            return ovfcheck((-sys.maxint - 1) // x)
        except OverflowError:
            return 1
        except ZeroDivisionError:
            return 0
    res = interpret(f, [0])
    assert res == 0
    res = interpret(f, [-1])
    assert res == 1
    res = interpret(f, [30])
    assert res == (-sys.maxint - 1) // 30

def test_mod_ovf_zer():
    import sys
    def f(x):
        try:
            return ovfcheck((-sys.maxint - 1) % x)
        except OverflowError:
            return 1
        except ZeroDivisionError:
            return 0
    res = interpret(f, [0])
    assert res == 0
    res = interpret(f, [-1])
    assert res == 1
    res = interpret(f, [30])
    assert res == (-sys.maxint - 1) % 30


def test_obj_obj_is():
    def f(x,y):
        return x is y
    o = pyobjectptr(object())
    res = interpret(f, [o, o], someobjects=True)
    assert res is True
    

def test_funny_links():
    from pypy.objspace.flow.model import Block, FunctionGraph, \
         SpaceOperation, Variable, Constant, Link
    for i in range(2):
        v_i = Variable("i")
        v_case = Variable("case")
        block = Block([v_i])
        g = FunctionGraph("is_one", block)
        block.operations.append(SpaceOperation("eq", [v_i, Constant(1)], v_case))
        block.exitswitch = v_case
        tlink = Link([Constant(1)], g.returnblock, True)
        flink = Link([Constant(0)], g.returnblock, False)
        links = [tlink, flink]
        if i:
            links.reverse()
        block.closeblock(*links)
        t = TranslationContext()
        a = t.buildannotator()
        a.build_graph_types(g, [annmodel.SomeInteger()])
        rtyper = t.buildrtyper()
        rtyper.specialize()
        interp = LLInterpreter(rtyper)
        assert interp.eval_graph(g, [1]) == 1
        assert interp.eval_graph(g, [0]) == 0

#__________________________________________________________________
#
#  Test objects and instances

class ExampleClass:
    def __init__(self, x):
        self.x = x + 1

def test_basic_instantiation():
    def f(x):
        return ExampleClass(x).x
    res = interpret(f, [4])
    assert res == 5

def test_id():
    def getids(i, j):
        e1 = ExampleClass(1)
        e2 = ExampleClass(2)
        a = [e1, e2][i]
        b = [e1, e2][j]
        return (id(a) == id(b)) == (a is b)
    for i in [0, 1]:
        for j in [0, 1]:
            result = interpret(getids, [i, j])
            assert result

def test_stack_malloc():
    class A(object):
        pass
    def f():
        a = A()
        a.i = 1
        return a.i
    interp, graph = get_interpreter(f, [])
    graph.startblock.operations[0].opname = "flavored_malloc"
    graph.startblock.operations[0].args.insert(0, inputconst(Void, "stack"))
    result = interp.eval_graph(graph, [])
    assert result == 1

def test_invalid_stack_access():
    class A(object):
        pass
    globala = A()
    globala.next = None
    globala.i = 1
    def g(a):
        globala.next = a
    def f():
        a = A()
        a.i = 2
        g(a)
    def h():
        f()
        return globala.next.i
    interp, graph = get_interpreter(h, [])
    fgraph = graph.startblock.operations[0].args[0].value._obj.graph
    fgraph.startblock.operations[0].opname = "flavored_malloc"
    fgraph.startblock.operations[0].args.insert(0, inputconst(Void, "stack"))
    py.test.raises(AttributeError, "interp.eval_graph(graph, [])")
#__________________________________________________________________
# example functions for testing the LLInterpreter
_snap = globals().copy()

def number_ops(i):
    j = i + 2
    k = j * 2
    m = k / 2
    return m - 1

def comparisons(x, y):
    return (x < y,
            x <= y,
            x == y,
            x != y,
            #x is None,
            #x is not None,
            x >= y,
            x > y,
            )

def simple_ifs(i):
    if i:
        return 42
    else:
        return 43

def while_simple(i):
    sum = 0
    while i > 0:
        sum += i
        i -= 1
    return sum

def raise_exception(i):
    if i == 42:
        raise IndexError
    elif i == 43:
        raise ValueError
    return i

def call_raise(i):
    return raise_exception(i)

def call_raise_twice(i, j):
    x = raise_exception(i)
    try:
        y = raise_exception(j)
    except ValueError:
        y = 1000
    return x + y

def call_raise_intercept(i):
    try:
        return raise_exception(i)
    except IndexError:
        return i
    except ValueError:
        raise TypeError

