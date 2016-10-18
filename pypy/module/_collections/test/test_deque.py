
class AppTestBasic:
    spaceconfig = dict(usemodules=['_collections', 'struct'])

    def test_basics(self):
        from _collections import deque
        assert deque.__module__ == 'collections'

        d = deque(range(-5125, -5000))
        d.__init__(range(200))
        for i in range(200, 400):
            d.append(i)
        for i in reversed(range(-200, 0)):
            d.appendleft(i)
        assert list(d) == list(range(-200, 400))
        assert len(d) == 600

        left = [d.popleft() for i in range(250)]
        assert left == list(range(-200, 50))
        assert list(d) == list(range(50, 400))

        right = [d.pop() for i in range(250)]
        right.reverse()
        assert right == list(range(150, 400))
        assert list(d) == list(range(50, 150))

    def test_maxlen(self):
        from _collections import deque
        raises(ValueError, deque, 'abc', -1)
        raises(ValueError, deque, 'abc', -2)
        it = iter(range(10))
        d = deque(it, maxlen=3)
        assert list(it) == []
        assert repr(d) == 'deque([7, 8, 9], maxlen=3)'
        assert list(d) == list(range(7, 10))
        d.appendleft(3)
        assert list(d) == [3, 7, 8]
        d.extend([20, 21])
        assert list(d) == [8, 20, 21]
        d.extendleft([-7, -6])
        assert list(d) == [-6, -7, 8]

    def test_maxlen_zero(self):
        from _collections import deque
        it = iter(range(100))
        d = deque(it, maxlen=0)
        assert list(d) == []
        assert list(it) == []
        d.extend(range(100))
        assert list(d) == []
        d.extendleft(range(100))
        assert list(d) == []

    def test_maxlen_attribute(self):
        from _collections import deque
        assert deque().maxlen is None
        assert deque('abc').maxlen is None
        assert deque('abc', maxlen=4).maxlen == 4
        assert deque('abc', maxlen=0).maxlen == 0
        raises((AttributeError, TypeError), "deque('abc').maxlen = 10")

    def test_runtimeerror(self):
        from _collections import deque
        d = deque('abcdefg')
        it = iter(d)
        d.pop()
        raises(RuntimeError, next, it)
        #
        d = deque('abcdefg')
        it = iter(d)
        d.append(d.pop())
        raises(RuntimeError, next, it)
        #
        d = deque()
        it = iter(d)
        d.append(10)
        raises(RuntimeError, next, it)

    def test_count(self):
        from _collections import deque
        for s in ('', 'abracadabra', 'simsalabim'*50+'abc'):
            s = list(s)
            d = deque(s)
            for letter in 'abcdeilmrs':
                assert s.count(letter) == d.count(letter)
        class MutatingCompare:
            def __eq__(self, other):
                d.pop()
                return True
        m = MutatingCompare()
        d = deque([1, 2, 3, m, 4, 5])
        raises(RuntimeError, d.count, 3)

    def test_comparisons(self):
        from _collections import deque
        d = deque('xabc'); d.popleft()
        for e in [d, deque('abc'), deque('ab'), deque(), list(d)]:
            assert (d==e) == (type(d)==type(e) and list(d)==list(e))
            assert (d!=e) == (not(type(d)==type(e) and list(d)==list(e)))

        args = map(deque, ('', 'a', 'b', 'ab', 'ba', 'abc', 'xba', 'xabc', 'cba'))
        for x in args:
            for y in args:
                assert (x == y) == (list(x) == list(y))
                assert (x != y) == (list(x) != list(y))
                assert (x <  y) == (list(x) <  list(y))
                assert (x <= y) == (list(x) <= list(y))
                assert (x >  y) == (list(x) >  list(y))
                assert (x >= y) == (list(x) >= list(y))

    def test_extend(self):
        from _collections import deque
        d = deque('a')
        d.extend('bcd')
        assert list(d) == list('abcd')
        d.extend(d)
        assert list(d) == list('abcdabcd')

    def test_add(self):
        from _collections import deque
        d1 = deque([1,2,3])
        d2 = deque([3,4,5])
        assert d1 + d2 == deque([1,2,3,3,4,5])

    def test_iadd(self):
        from _collections import deque
        d = deque('a')
        original_d = d
        d += 'bcd'
        assert list(d) == list('abcd')
        d += d
        assert list(d) == list('abcdabcd')
        assert original_d is d

    def test_extendleft(self):
        from _collections import deque
        d = deque('a')
        d.extendleft('bcd')
        assert list(d) == list(reversed('abcd'))
        d.extendleft(d)
        assert list(d) == list('abcddcba')

    def test_getitem(self):
        from _collections import deque
        n = 200
        l = range(1000, 1000 + n)
        d = deque(l)
        for j in range(-n, n):
            assert d[j] == l[j]
        raises(IndexError, "d[-n-1]")
        raises(IndexError, "d[n]")

    def test_setitem(self):
        from _collections import deque
        n = 200
        d = deque(range(n))
        for i in range(n):
            d[i] = 10 * i
        assert list(d) == [10*i for i in range(n)]
        l = list(d)
        for i in range(1-n, 0, -3):
            d[i] = 7*i
            l[i] = 7*i
        assert list(d) == l

    def test_delitem(self):
        from _collections import deque
        d = deque("abcdef")
        del d[-2]
        assert list(d) == list("abcdf")

    def test_reverse(self):
        from _collections import deque
        d = deque(range(1000, 1200))
        d.reverse()
        assert list(d) == list(reversed(range(1000, 1200)))
        #
        n = 100
        data = list(map(str, range(n)))
        for i in range(n):
            d = deque(data[:i])
            r = d.reverse()
            assert list(d) == list(reversed(data[:i]))
            assert r is None
            d.reverse()
            assert list(d) == data[:i]

    def test_rotate(self):
        from _collections import deque
        s = tuple('abcde')
        n = len(s)

        d = deque(s)
        d.rotate(1)             # verify rot(1)
        assert ''.join(d) == 'eabcd'

        d = deque(s)
        d.rotate(-1)            # verify rot(-1)
        assert ''.join(d) == 'bcdea'
        d.rotate()              # check default to 1
        assert tuple(d) == s

        d.rotate(500000002)
        assert tuple(d) == tuple('deabc')
        d.rotate(-5000002)
        assert tuple(d) == tuple(s)

    def test_len(self):
        from _collections import deque
        d = deque('ab')
        assert len(d) == 2
        d.popleft()
        assert len(d) == 1
        d.pop()
        assert len(d) == 0
        raises(IndexError, d.pop)
        raises(IndexError, d.popleft)
        assert len(d) == 0
        d.append('c')
        assert len(d) == 1
        d.appendleft('d')
        assert len(d) == 2
        d.clear()
        assert len(d) == 0
        assert list(d) == []

    def test_remove(self):
        from _collections import deque
        d = deque('abcdefghcij')
        d.remove('c')
        assert d == deque('abdefghcij')
        d.remove('c')
        assert d == deque('abdefghij')
        raises(ValueError, d.remove, 'c')
        assert d == deque('abdefghij')

    def test_repr(self):
        from _collections import deque
        d = deque(range(20))
        e = eval(repr(d))
        assert d == e
        d.append(d)
        assert '...' in repr(d)

    def test_hash(self):
        from _collections import deque
        raises(TypeError, hash, deque('abc'))

    def test_roundtrip_iter_init(self):
        from _collections import deque
        d = deque(range(200))
        e = deque(d)
        assert d is not e
        assert d == e
        assert list(d) == list(e)

    def test_reduce(self):
        from _collections import deque
        #
        d = deque('hello world')
        r = d.__reduce__()
        assert r == (deque, (list('hello world'),))
        #
        d = deque('hello world', 42)
        r = d.__reduce__()
        assert r == (deque, (list('hello world'), 42))
        #
        class D(deque):
            pass
        d = D('hello world')
        d.a = 5
        r = d.__reduce__()
        assert r == (D, (list('hello world'), None), {'a': 5})
        #
        class D(deque):
            pass
        d = D('hello world', 42)
        d.a = 5
        r = d.__reduce__()
        assert r == (D, (list('hello world'), 42), {'a': 5})

    def test_copy(self):
        from _collections import deque
        import copy
        mut = [10]
        d = deque([mut])
        e = copy.copy(d)
        assert d is not e
        assert d == e
        mut[0] = 11
        assert d == e

    def test_index(self):
        from _collections import deque
        d = deque([1,2,'a',1,2])
        assert d.index(1) is 0
        assert d.index('a') is 2
        assert d.index(1,2) is 3
        assert d.index('a',-3) is 2
        assert d.index('a',-3,-1) is 2
        assert d.index('a',-9) is 2
        raises(ValueError, d.index, 2, 2, -1)
        raises(ValueError, d.index, 1, 1, 3)
        raises(ValueError, d.index, 'a', -3, -3)
        raises(ValueError, d.index, 'a', 1, -3)
        raises(ValueError, d.index, 'a', -3, -9)

    def test_reversed(self):
        from _collections import deque
        for s in ('abcd', range(200)):
            assert list(reversed(deque(s))) == list(reversed(s))

    def test_free(self):
        import gc
        from _collections import deque
        class X(object):
            freed = False
            def __del__(self):
                X.freed = True
        d = deque()
        d.append(X())
        d.pop()
        gc.collect(); gc.collect(); gc.collect()
        assert X.freed

    def test_DequeIter_pickle(self):
        from _collections import deque
        import pickle
        d = deque([1,2,3])
        iterator = iter(d)
        copy = pickle.loads(pickle.dumps(iterator))
        assert list(iterator) == list(copy)

    def test_DequeRevIter_pickle(self):
        from _collections import deque
        import pickle
        d = deque([1,2,3])
        iterator = reversed(d)
        copy = pickle.loads(pickle.dumps(iterator))
        assert list(iterator) == list(copy)

    def test_deque_mul(self):
        from _collections import deque
        d = deque([1,2,3])
        assert d*3 == deque([1,2,3]*3)

    def test_deque_imul(self):
        from _collections import deque
        d = deque([1,2,3])
        d *= 3
        assert d == deque([1,2,3]*3)
        assert d is not deque([1,2,3]*3)
        d = deque('a')
        for n in (-10, -1, 0, 1, 2, 10, 1000):
            d = deque('a')
            d *= n
            assert d == deque('a' * n)
            assert d.maxlen is None

    def test_deque_insert(self):
        from _collections import deque
        for i in range(0,11):
            d = deque(range(10))
            d.insert(i, 'a')
            assert 'a' in d
            assert 'b' not in d
            assert d.index('a') == i
        d = deque(range(10))
        d.insert(-1, 500)
        assert d.index(500) == 9

    def test_deque_raises_runtimeerror(self):
        from _collections import deque
        n = 200
        class MutateCmp:
            def __init__(self, deque, result):
                self.deque = deque
                self.result = result
            def __eq__(self, other):
                self.deque.clear()
                return self.result
        d = deque(range(n))
        d[n//2] = MutateCmp(d, False)
        try:
            d.index(n)
            assert 0, "must raise!"
        except RuntimeError:
            pass

    def test_deque_rmul(self):
        from _collections import deque
        d = deque([1,2])
        assert 2 * d == deque([1,2,1,2])
        assert -5 * d == deque()

    def test_deque_maxlen(self):
        from _collections import deque
        g = deque('abcdef', maxlen=4)
        assert len(g) == 4 and g == deque('cdef')
        h = deque('gh')
        assert ''.join(g + h) == 'efgh'
        assert g + h == deque('efgh')

    def test_deque_insert2(self):
        from _collections import deque
        elements = 'ABCDEFGHI'
        for i in range(-5 - len(elements)*2, 5 + len(elements) * 2):
            d = deque('ABCDEFGHI')
            s = list('ABCDEFGHI')
            d.insert(i, 'Z')
            s.insert(i, 'Z')
            assert list(d) == s

    def test_deque_index_overflow_start_end(self):
        from _collections import deque
        import sys
        elements = 'ABCDEFGHI'
        d = deque([-2, -1, 0, 0, 1, 2])
        assert a.index(0, -4*sys.maxsize, 4*sys.maxsize) == 2
