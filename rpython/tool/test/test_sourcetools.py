# -*- encoding: utf-8 -*-
import py
from rpython.tool.sourcetools import (
    func_with_new_name, func_renamer, rpython_wrapper, with_unicode_literals)

def test_rename():
    def f(x, y=5):
        return x + y
    f.prop = int

    g = func_with_new_name(f, "g")
    assert g(4, 5) == 9
    assert g.func_name == "g"
    assert f.func_defaults == (5,)
    assert g.prop is int

def test_rename_decorator():
    @func_renamer("g")
    def f(x, y=5):
        return x + y
    f.prop = int

    assert f(4, 5) == 9

    assert f.func_name == "g"
    assert f.func_defaults == (5,)
    assert f.prop is int

def test_func_rename_decorator():
    def bar():
        'doc'

    bar2 = func_with_new_name(bar, 'bar2')
    assert bar.func_doc == bar2.func_doc == 'doc'

    bar.func_doc = 'new doc'
    bar3 = func_with_new_name(bar, 'bar3')
    assert bar3.func_doc == 'new doc'
    assert bar2.func_doc != bar3.func_doc


def test_rpython_wrapper():
    calls = []

    def bar(a, b):
        calls.append(('bar', a, b))
        return a+b

    template = """
        def {name}({arglist}):
            calls.append(('decorated', {arglist}))
            return {original}({arglist})
    """
    bar = rpython_wrapper(bar, template, calls=calls)
    assert bar(40, 2) == 42
    assert calls == [
        ('decorated', 40, 2),
        ('bar', 40, 2),
        ]

        
def test_with_unicode_literals():
    @with_unicode_literals()
    def foo():
        return 'hello'
    assert type(foo()) is unicode
    #
    @with_unicode_literals
    def foo():
        return 'hello'
    assert type(foo()) is unicode
    #
    def foo():
        return 'hello àèì'
    py.test.raises(UnicodeDecodeError, "with_unicode_literals(foo)")
    #
    @with_unicode_literals(encoding='utf-8')
    def foo():
        return 'hello àèì'
    assert foo() == u'hello àèì'
    #
    @with_unicode_literals
    def foo():
        return ('a', 'b')
    assert type(foo()[0]) is unicode

