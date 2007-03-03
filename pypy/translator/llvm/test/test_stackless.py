import os

from pypy.annotation.listdef import s_list_of_strings
from pypy.rlib.rstack import stack_unwind, stack_frames_depth, stack_too_big
from pypy.rlib.rstack import yield_current_frame_to_caller

from pypy.translator.llvm.genllvm import genllvm_compile
import py
py.test.skip("not there yet!")


class StacklessTest(object):
    def wrap_stackless_function(self, fn):
        def entry_point(argv):
            os.write(1, str(fn()) + "\n")
            return 0

        # XXX run this with interactive
        #exe_path = genllvm_compile(entry_point, [s_list_of_strings], optimize=False,
        #                           exe_name="stacktest", standalone=True,
        #                           stackless=True, logging=False)

        res = os.system(exe_path)
        return int(res.strip())

# ____________________________________________________________


class TestStackless(StacklessTest):

    def test_stack_depth(self):
        def g1():
            "just to check Void special cases around the code"
        def g2(ignored):
            g1()
        def f(n):
            g1()
            if n > 0:
                res = f(n-1) + 0 # make sure it is not a tail call
            else:
                res = stack_frames_depth()
            g2(g1)
            return res

        def fn():
            count0 = f(0)
            count10 = f(10)
            return count10 - count0

        res = self.wrap_stackless_function(fn)
        assert res == 10

    def test_stack_withptr(self):
        def f(n):
            if n > 0:
                res, dummy = f(n-1)
            else:
                res, dummy = stack_frames_depth(), 1
            return res, dummy

        def fn():
            count0, _ = f(0)
            count10, _ = f(10)
            return count10 - count0

        res = self.wrap_stackless_function(fn)
        assert res == 10

    def test_stackless_manytimes(self):
        def f(n):
            if n > 0:
                stack_frames_depth()
                res, dummy = f(n-1)
            else:
                res, dummy = stack_frames_depth(), 1
            return res, dummy

        def fn():
            count0, _ = f(0)
            count10, _ = f(100)
            return count10 - count0

        res = self.wrap_stackless_function(fn)
        assert res == 100

    def test_stackless_arguments(self):
        def f(n, d, t):
            if n > 0:
                a, b, c = f(n-1, d, t)
            else:
                a, b, c = stack_frames_depth(), d, t
            return a, b, c

        def fn():
            count0, d, t = f(0, 5.5, (1, 2))
            count10, d, t = f(10, 5.5, (1, 2))
            result = (count10 - count0) * 1000000
            result += t[0]              * 10000
            result += t[1]              * 100
            result += int(d*10)
            return result

        res = self.wrap_stackless_function(fn)
        assert res == 10010255


    def test_stack_too_big(self):
        def f1():
            return stack_too_big()
        def f2():
            return lst[1]()
        def f3():
            return lst[2]()
        def f4():
            return lst[3]()
        def f5():
            return lst[4]()
        lst = [None,f1,f2,f3,f4,f5]

        def f(n):
            if lst[5]():
                return n
            return f(n)+1

        def fn():
            return f(0)
        res = self.wrap_stackless_function(fn)
        assert res > 500


    def test_stack_unwind(self):
        def f():
            stack_unwind()
            return 42

        res = self.wrap_stackless_function(f)
        assert res == 42

    def test_auto_stack_unwind(self):
        def f(n):
            if n == 1:
                return 1
            return (n+f(n-1)) % 1291

        def fn():
            return f(10**6)
        res = self.wrap_stackless_function(fn)
        assert res == 704

    def test_yield_frame(self):

        def g(lst):
            lst.append(2)
            frametop_before_5 = yield_current_frame_to_caller()
            lst.append(4)
            frametop_before_7 = frametop_before_5.switch()
            lst.append(6)
            return frametop_before_7

        def f():
            lst = [1]
            frametop_before_4 = g(lst)
            lst.append(3)
            frametop_before_6 = frametop_before_4.switch()
            lst.append(5)
            frametop_after_return = frametop_before_6.switch()
            lst.append(7)
            assert frametop_after_return is None
            n = 0
            for i in lst:
                n = n*10 + i
            return n

        res = self.wrap_stackless_function(f)
        assert res == 1234567

    def test_foo(self):
        def f():
            c = g()
            c.switch()
            return 1
        def g():
            d = yield_current_frame_to_caller()
            return d
        res = self.wrap_stackless_function(f)
        assert res == 1
        

    def test_yield_noswitch_frame(self):
        # this time we make sure that function 'g' does not
        # need to switch and even does not need to be stackless

        def g(lst):
            lst.append(2)
            frametop_before_5 = yield_current_frame_to_caller()
            lst.append(4)
            return frametop_before_5

        def f():
            lst = [1]
            frametop_before_4 = g(lst)
            lst.append(3)
            frametop_after_return = frametop_before_4.switch()
            lst.append(5)
            assert frametop_after_return is None
            n = 0
            for i in lst:
                n = n*10 + i
            return n

        res = self.wrap_stackless_function(f)
        assert res == 12345

    # tested with refcounting too for sanity checking
    def test_yield_frame_mem_pressure(self):

        class A:
            def __init__(self, value):
                self.lst = [0] * 10000
                self.lst[5000] = value

            def inc(self, delta):
                self.lst[5000] += delta
                return self.lst[5000]

        def g(lst):
            a = A(1)
            lst.append(a.inc(1))
            frametop_before_5 = yield_current_frame_to_caller()
            malloc_a_lot()
            lst.append(a.inc(2))
            frametop_before_7 = frametop_before_5.switch()
            malloc_a_lot()
            lst.append(a.inc(2))
            return frametop_before_7

        def f():
            lst = [1]
            frametop_before_4 = g(lst)
            lst.append(3)
            malloc_a_lot()
            frametop_before_6 = frametop_before_4.switch()
            lst.append(5)
            malloc_a_lot()
            frametop_after_return = frametop_before_6.switch()
            lst.append(7)
            assert frametop_after_return is None
            n = 0
            for i in lst:
                n = n*10 + i
            return n

        res = self.wrap_stackless_function(f)
        assert res == 1234567


# ____________________________________________________________

def malloc_a_lot():
    i = 0
    while i < 10:
        i += 1
        a = [1] * 10
        j = 0
        while j < 20:
            j += 1
            a.append(j)
    from pypy.rpython.lltypesystem.lloperation import llop
    from pypy.rpython.lltypesystem import lltype
    llop.gc__collect(lltype.Void)
