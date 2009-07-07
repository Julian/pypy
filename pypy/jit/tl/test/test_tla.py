import py
from pypy.jit.tl import tla

def test_stack():
    f = tla.Frame('')
    f.push(1)
    f.push(2)
    f.push(3)
    assert f.pop() == 3
    assert f.pop() == 2
    assert f.pop() == 1
    py.test.raises(AssertionError, f.pop)


def test_W_IntObject():
    w_a = tla.W_IntObject(0)
    w_b = tla.W_IntObject(10)
    w_c = tla.W_IntObject(32)
    assert not w_a.is_true()
    assert w_b.is_true()
    assert w_c.is_true()
    assert w_b.add(w_c).intvalue == 42


def assemble(mylist):
    return ''.join([chr(x) for x in mylist])

def interp(mylist, w_arg):
    bytecode = assemble(mylist)
    return tla.run(bytecode, w_arg)

def test_interp():
    code = [
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(42))
    assert res.intvalue == 42

def test_pop():
    code = [
        tla.CONST_INT, 99,
        tla.POP,
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(42))
    assert res.intvalue == 42

def test_dup():
    code = [
        tla.DUP,
        tla.ADD,
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(41))
    assert res.intvalue == 2 * 41

def test_bogus_return():
    code = [
        tla.CONST_INT, 123,
        tla.RETURN # stack depth == 2 here, error!
        ]
    py.test.raises(AssertionError, "interp(code, tla.W_IntObject(234))")
    
def test_add():
    code = [
        tla.CONST_INT, 20,
        tla.ADD,
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(22))
    assert res.intvalue == 42

def test_sub():
    code = [
        tla.CONST_INT, 20,
        tla.SUB,
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(22))
    assert res.intvalue == 2

def test_jump_if():
    code = [
        tla.JUMP_IF, 5,   # jump to target
        tla.CONST_INT, 123,
        tla.RETURN,
        tla.CONST_INT, 234,  # target
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(0))
    assert res.intvalue == 123
    
    res = interp(code, tla.W_IntObject(1))
    assert res.intvalue == 234


def test_newstr():
    code = [
        tla.POP,
        tla.NEWSTR, ord('x'),
        tla.RETURN
        ]
    res = interp(code, tla.W_IntObject(0))
    assert isinstance(res, tla.W_StringObject)
    assert res.strvalue == 'x'

def test_add_strings():
    code = [
        tla.NEWSTR, ord('d'),
        tla.ADD,
        tla.NEWSTR, ord('!'),
        tla.ADD,
        tla.RETURN
        ]
    res = interp(code, tla.W_StringObject('Hello worl'))
    assert res.strvalue == 'Hello world!'

# ____________________________________________________________ 

