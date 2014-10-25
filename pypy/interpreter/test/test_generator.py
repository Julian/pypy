class AppTestGenerator:

    def test_generator(self):
        def f():
            yield 1
        assert next(f()) == 1

    def test_generator2(self):
        def f():
            yield 1
        g = f()
        assert next(g) == 1
        raises(StopIteration, next, g)

    def test_attributes(self):
        def f():
            yield 1
            assert g.gi_running
        g = f()
        assert g.gi_code is f.__code__
        assert g.__name__ == 'f'
        assert g.gi_frame is not None
        assert not g.gi_running
        next(g)
        assert not g.gi_running
        raises(StopIteration, next, g)
        assert not g.gi_running
        assert g.gi_frame is None
        assert g.gi_code is f.__code__
        assert g.__name__ == 'f'

    def test_generator3(self):
        def f():
            yield 1
        g = f()
        assert list(g) == [1]

    def test_generator4(self):
        def f():
            yield 1
        g = f()
        assert [x for x in g] == [1]

    def test_generator5(self):
        d = {}
        exec("""if 1:
        def f():
            v = (yield )
            yield v
        g = f()
        next(g)
        """, d, d)
        g = d['g']
        assert g.send(42) == 42

    def test_throw1(self):
        def f():
            yield 2
        g = f()
        raises(NameError, g.throw, NameError, "Error")

    def test_throw2(self):
        def f():
            yield 2
        g = f()
        raises(NameError, g.throw, NameError("Error"))

    def test_throw3(self):
        def f():
            try:
                yield 1
                yield 2
            except:
                yield 3
        g = f()
        assert next(g) == 1
        assert g.throw(NameError("Error")) == 3
        raises(StopIteration, next, g)

    def test_throw4(self):
        d = {}
        exec("""if 1:
        def f():
            try:
                yield 1
                v = (yield 2)
            except:
                yield 3
        g = f()
        """, d, d)
        g = d['g']
        assert next(g) == 1
        assert next(g) == 2
        assert g.throw(NameError("Error")) == 3
        raises(StopIteration, next, g)

    def test_throw5(self):
        def f():
            try:
                yield 1
            except:
                x = 3
            try:
                yield x
            except:
                pass
        g = f()
        next(g)
        # String exceptions are not allowed anymore
        raises(TypeError, g.throw, "Error")
        assert g.throw(Exception) == 3
        raises(StopIteration, g.throw, Exception)

    def test_throw6(self):
        def f():
            yield 2
        g = f()
        raises(NameError, g.throw, NameError, "Error", None)


    def test_throw_fail(self):
        def f():
            yield 1
        g = f()
        raises(TypeError, g.throw, NameError("Error"), "error")

    def test_throw_fail2(self):
        def f():
            yield 1
        g = f()
        raises(TypeError, g.throw, list())

    def test_throw_fail3(self):
        def f():
            yield 1
        g = f()
        raises(TypeError, g.throw, NameError("Error"), None, "not tb object")

    def test_throw_finishes_generator(self):
        def f():
            yield 1
        g = f()
        assert g.gi_frame is not None
        raises(ValueError, g.throw, ValueError)
        assert g.gi_frame is None

    def test_throw_bug(self):
        def f():
            try:
                x.throw(IndexError)     # => "generator already executing"
            except ValueError:
                yield 1
        x = f()
        res = list(x)
        assert res == [1]

    def test_throw_on_finished_generator(self):
        def f():
            yield 1
        g = f()
        res = next(g)
        assert res == 1
        raises(StopIteration, next, g)
        raises(NameError, g.throw, NameError)

    def test_throw_tb(self):
        def f():
            try:
                yield
            except:
                raise
        g = f()
        try:
            1/0
        except ZeroDivisionError as v:
            try:
                g.throw(v)
            except Exception as w:
                tb = w.__traceback__
        levels = 0
        while tb:
            levels += 1
            tb = tb.tb_next
        assert levels == 3

    def test_close(self):
        def f():
            yield 1
        g = f()
        assert g.close() is None

    def test_close2(self):
        def f():
            try:
                yield 1
            except GeneratorExit:
                raise StopIteration
        g = f()
        next(g)
        assert g.close() is None

    def test_close3(self):
        def f():
            try:
                yield 1
            except GeneratorExit:
                raise NameError
        g = f()
        next(g)
        raises(NameError, g.close)

    def test_close_fail(self):
        def f():
            try:
                yield 1
            except GeneratorExit:
                yield 2
        g = f()
        next(g)
        raises(RuntimeError, g.close)

    def test_close_on_collect(self):
        ## we need to exec it, else it won't run on python2.4
        d = {}
        exec("""
        def f():
            try:
                yield
            finally:
                f.x = 42
        """.strip(), d, d)

        g = d['f']()
        next(g)
        del g
        import gc
        gc.collect()
        assert d['f'].x == 42

    def test_generator_raises_typeerror(self):
        def f():
            yield 1
        g = f()
        raises(TypeError, g.send, 1)

    def test_generator_explicit_stopiteration(self):
        def f():
            yield 1
            raise StopIteration
        g = f()
        assert [x for x in g] == [1]

    def test_generator_propagate_stopiteration(self):
        def f():
            it = iter([1])
            while 1: yield next(it)
        g = f()
        assert [x for x in g] == [1]

    def test_generator_restart(self):
        def g():
            i = next(me)
            yield i
        me = g()
        raises(ValueError, next, me)

    def test_generator_expression(self):
        d = {}
        exec("res = sum(i*i for i in range(5))", d, d)
        assert d['res'] == 30

    def test_generator_expression_2(self):
        d = {}
        exec("""
def f():
    total = sum(i for i in [x for x in z])
    return total
z = [1, 2, 7]
res = f()
""", d, d)
        assert d['res'] == 10

    def test_repr(self):
        def myFunc():
            yield 1
        g = myFunc()
        r = repr(g)
        assert r.startswith("<generator object myFunc at 0x")
        assert list(g) == [1]
        assert repr(g) == r

    def test_unpackiterable_gen(self):
        g = (i*i for i in range(-5, 3))
        assert set(g) == set([0, 1, 4, 9, 16, 25])
        assert set(g) == set()
        assert set(i for i in range(0)) == set()

    def test_explicit_stop_iteration_unpackiterable(self):
        def f():
            yield 1
            raise StopIteration
        assert tuple(f()) == (1,)

    def test_exception_is_cleared_by_yield(self):
        def f():
            try:
                foobar
            except NameError:
                yield 5
                raise
        gen = f()
        next(gen)  # --> 5
        try:
            next(gen)
        except NameError:
            pass

    def test_yield_return(self):
        """
        def f():
            yield 1
            return 2
        g = f()
        assert next(g) == 1
        try:
            next(g)
        except StopIteration as e:
            assert e.value == 2
        else:
            assert False, 'Expected StopIteration'
            """

    def test_yield_from_return(self):
        """
        def f1():
            result = yield from f2()
            return result
        def f2():
            yield 1
            return 2
        g = f1()
        assert next(g) == 1
        try:
            next(g)
        except StopIteration as e:
            assert e.value == 2
        else:
            assert False, 'Expected StopIteration'
            """


def test_should_not_inline(space):
    from pypy.interpreter.generator import should_not_inline
    w_co = space.appexec([], '''():
        def g(x):
            yield x + 5
        return g.__code__
    ''')
    assert should_not_inline(w_co) == False
    w_co = space.appexec([], '''():
        def g(x):
            yield x + 5
            yield x + 6
        return g.__code__
    ''')
    assert should_not_inline(w_co) == True

class AppTestYieldFrom:
    def test_delegating_close(self):
        """
        Test delegating 'close'
        """
        trace = []
        d = dict(trace=trace)
        exec('''if 1:
        def g1():
            try:
                trace.append("Starting g1")
                yield "g1 ham"
                yield from g2()
                yield "g1 eggs"
            finally:
                trace.append("Finishing g1")
        def g2():
            try:
                trace.append("Starting g2")
                yield "g2 spam"
                yield "g2 more spam"
            finally:
                trace.append("Finishing g2")
        ''', d)
        g1, g2 = d['g1'], d['g2']
        g = g1()
        for i in range(2):
            x = next(g)
            trace.append("Yielded %s" % (x,))
        g.close()
        assert trace == [
            "Starting g1",
            "Yielded g1 ham",
            "Starting g2",
            "Yielded g2 spam",
            "Finishing g2",
            "Finishing g1"
        ]

    def test_handing_exception_while_delegating_close(self):
        """
        Test handling exception while delegating 'close'
        """
        trace = []
        d = dict(trace=trace)
        exec('''if 1:
        def g1():
            try:
                trace.append("Starting g1")
                yield "g1 ham"
                yield from g2()
                yield "g1 eggs"
            finally:
                trace.append("Finishing g1")
        def g2():
            try:
                trace.append("Starting g2")
                yield "g2 spam"
                yield "g2 more spam"
            finally:
                trace.append("Finishing g2")
                raise ValueError("nybbles have exploded with delight")
        ''', d)
        g1, g2 = d['g1'], d['g2']
        g = g1()
        for i in range(2):
            x = next(g)
            trace.append("Yielded %s" % (x,))
        exc = raises(ValueError, g.close)
        assert exc.value.args[0] == "nybbles have exploded with delight"
        assert isinstance(exc.value.__context__, GeneratorExit)
        assert trace == [
            "Starting g1",
            "Yielded g1 ham",
            "Starting g2",
            "Yielded g2 spam",
            "Finishing g2",
            "Finishing g1",
        ]

    def test_delegating_throw(self):
        """
        Test delegating 'throw'
        """
        trace = []
        d = dict(trace=trace)
        exec('''if 1:
        def g1():
            try:
                trace.append("Starting g1")
                yield "g1 ham"
                yield from g2()
                yield "g1 eggs"
            finally:
                trace.append("Finishing g1")
        def g2():
            try:
                trace.append("Starting g2")
                yield "g2 spam"
                yield "g2 more spam"
            finally:
                trace.append("Finishing g2")
        ''', d)
        g1, g2 = d['g1'], d['g2']
        g = g1()
        for i in range(2):
            x = next(g)
            trace.append("Yielded %s" % (x,))
        e = ValueError("tomato ejected")
        exc = raises(ValueError, g.throw, e)
        assert exc.value.args[0] == "tomato ejected"
        assert trace == [
            "Starting g1",
            "Yielded g1 ham",
            "Starting g2",
            "Yielded g2 spam",
            "Finishing g2",
            "Finishing g1",
        ]

    def test_delegating_throw_to_non_generator(self):
        """
        Test delegating 'throw' to non-generator
        """
        trace = []
        d = dict(trace=trace)
        exec('''if 1:
        def g():
            try:
                trace.append("Starting g")
                yield from range(10)
            finally:
                trace.append("Finishing g")
        ''', d)
        g = d['g']
        gi = g()
        for i in range(5):
            x = next(gi)
            trace.append("Yielded %s" % (x,))
        exc = raises(ValueError, gi.throw, ValueError("tomato ejected"))
        assert exc.value.args[0] == "tomato ejected"
        assert trace == [
            "Starting g",
            "Yielded 0",
            "Yielded 1",
            "Yielded 2",
            "Yielded 3",
            "Yielded 4",
            "Finishing g",
        ]

    def test_broken_getattr_handling(self):
        """
        Test subiterator with a broken getattr implementation
        """
        class Broken:
            def __iter__(self):
                return self
            def __next__(self):
                return 1
            def __getattr__(self, attr):
                1/0

        d = dict(Broken=Broken)
        exec('''if 1:
        def g():
            yield from Broken()
        ''', d)
        g = d['g']

        gi = g()
        assert next(gi) == 1
        raises(ZeroDivisionError, gi.send, 1)

        gi = g()
        assert next(gi) == 1
        raises(ZeroDivisionError, gi.throw, RuntimeError)

        gi = g()
        assert next(gi) == 1
        import io, sys
        sys.stderr = io.StringIO()
        gi.close()
        assert 'ZeroDivisionError' in sys.stderr.getvalue()
    
    def test_returning_value_from_delegated_throw(self):
        """
        Test returning value from delegated 'throw'
        """
        trace = []
        class LunchError(Exception):
            pass
        d = dict(trace=trace, LunchError=LunchError)
        exec('''if 1:
        def g1():
            try:
                trace.append("Starting g1")
                yield "g1 ham"
                yield from g2()
                yield "g1 eggs"
            finally:
                trace.append("Finishing g1")
        def g2():
            try:
                trace.append("Starting g2")
                yield "g2 spam"
                yield "g2 more spam"
            except LunchError:
                trace.append("Caught LunchError in g2")
                yield "g2 lunch saved"
                yield "g2 yet more spam"
        ''', d)
        g1, g2 = d['g1'], d['g2']
        g = g1()
        for i in range(2):
            x = next(g)
            trace.append("Yielded %s" % (x,))
        e = LunchError("tomato ejected")
        g.throw(e)
        for x in g:
            trace.append("Yielded %s" % (x,))
        assert trace == [
            "Starting g1",
            "Yielded g1 ham",
            "Starting g2",
            "Yielded g2 spam",
            "Caught LunchError in g2",
            "Yielded g2 yet more spam",
            "Yielded g1 eggs",
            "Finishing g1",
        ]

    def test_catching_exception_from_subgen_and_returning(self):
        """
        Test catching an exception thrown into a
        subgenerator and returning a value
        """
        trace = []
        d = dict(trace=trace)
        exec('''if 1:
        def inner():
            try:
                yield 1
            except ValueError:
                trace.append("inner caught ValueError")
            return 2

        def outer():
            v = yield from inner()
            trace.append("inner returned %r to outer" % v)
            yield v
        ''', d)
        inner, outer = d['inner'], d['outer']
        g = outer()
        trace.append(next(g))
        trace.append(g.throw(ValueError))
        assert trace == [
            1,
            "inner caught ValueError",
            "inner returned 2 to outer",
            2,
        ]

