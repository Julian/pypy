# -*- encoding: utf-8 -*-
import py
from pypy.objspace.std import longobject as lobj
from rpython.rlib.rbigint import rbigint


class TestW_LongObject:
    def test_bigint_w(self):
        space = self.space
        fromlong = lobj.W_LongObject.fromlong
        assert isinstance(space.bigint_w(fromlong(42)), rbigint)
        assert space.bigint_w(fromlong(42)).eq(rbigint.fromint(42))
        assert space.bigint_w(fromlong(-1)).eq(rbigint.fromint(-1))
        w_obj = space.wrap("hello world")
        space.raises_w(space.w_TypeError, space.bigint_w, w_obj)
        w_obj = space.wrap(123.456)
        space.raises_w(space.w_TypeError, space.bigint_w, w_obj)

        w_obj = fromlong(42)
        assert space.unwrap(w_obj) == 42

    def test_rint_variants(self):
        py.test.skip("XXX broken!")
        from rpython.rtyper.tool.rfficache import platform
        space = self.space
        for r in platform.numbertype_to_rclass.values():
            if r is int:
                continue
            print r
            values = [0, -1, r.MASK>>1, -(r.MASK>>1)-1]
            for x in values:
                if not r.SIGNED:
                    x &= r.MASK
                w_obj = space.wrap(r(x))
                assert space.bigint_w(w_obj).eq(rbigint.fromint(x))


class AppTestLong:

    def test_trunc(self):
        import math
        assert math.trunc(1) == 1
        assert math.trunc(-1) == -1

    def test_add(self):
        x = 123
        assert int(x + 12443) == 123 + 12443
        x = -20
        assert x + 2 + 3 + True == -14

    def test_sub(self):
        x = 58543
        assert int(x - 12332) == 58543 - 12332
        x = 237123838281233
        assert x * 12 == x * 12

    def test_mul(self):
        x = 363
        assert x * 2 ** 40 == x << 40

    def test_truediv(self):
        a = 31415926 / 10000000
        assert a == 3.1415926

    def test_floordiv(self):
        x = 31415926
        a = x // 10000000
        assert a == 3

    def test_numerator_denominator(self):
        assert (1).numerator == 1
        assert (1).denominator == 1
        assert (42).numerator == 42
        assert (42).denominator == 1

    def test_compare(self):
        Z = 0
        ZL = 0
        for BIG in (1, 1 << 62, 1 << 9999):
            assert Z == ZL
            assert not (Z != ZL)
            assert ZL == Z
            assert not (ZL != Z)
            assert not (Z == BIG)
            assert Z != BIG
            assert not (BIG == Z)
            assert BIG != Z
            assert not (ZL == BIG)
            assert ZL != BIG
            assert Z <= ZL
            assert not (Z < ZL)
            assert Z <= BIG
            assert Z < BIG
            assert not (BIG <= Z)
            assert not (BIG < Z)
            assert ZL <= ZL
            assert not (ZL < ZL)
            assert ZL <= BIG
            assert ZL < BIG
            assert not (BIG <= ZL)
            assert not (BIG < ZL)
            assert not (Z <= -BIG)
            assert not (Z < -BIG)
            assert -BIG <= Z
            assert -BIG < Z
            assert not (ZL <= -BIG)
            assert not (ZL < -BIG)
            assert -BIG <= ZL
            assert -BIG < ZL
            #
            assert not (BIG <  int(BIG))
            assert     (BIG <= int(BIG))
            assert     (BIG == int(BIG))
            assert not (BIG != int(BIG))
            assert not (BIG >  int(BIG))
            assert     (BIG >= int(BIG))
            #
            assert     (BIG <  int(BIG)+1)
            assert     (BIG <= int(BIG)+1)
            assert not (BIG == int(BIG)+1)
            assert     (BIG != int(BIG)+1)
            assert not (BIG >  int(BIG)+1)
            assert not (BIG >= int(BIG)+1)
            #
            assert not (BIG <  int(BIG)-1)
            assert not (BIG <= int(BIG)-1)
            assert not (BIG == int(BIG)-1)
            assert     (BIG != int(BIG)-1)
            assert     (BIG >  int(BIG)-1)
            assert     (BIG >= int(BIG)-1)
            #
            assert not (int(BIG) <  BIG)
            assert     (int(BIG) <= BIG)
            assert     (int(BIG) == BIG)
            assert not (int(BIG) != BIG)
            assert not (int(BIG) >  BIG)
            assert     (int(BIG) >= BIG)
            #
            assert not (int(BIG)+1 <  BIG)
            assert not (int(BIG)+1 <= BIG)
            assert not (int(BIG)+1 == BIG)
            assert     (int(BIG)+1 != BIG)
            assert     (int(BIG)+1 >  BIG)
            assert     (int(BIG)+1 >= BIG)
            #
            assert     (int(BIG)-1 <  BIG)
            assert     (int(BIG)-1 <= BIG)
            assert not (int(BIG)-1 == BIG)
            assert     (int(BIG)-1 != BIG)
            assert not (int(BIG)-1 >  BIG)
            assert not (int(BIG)-1 >= BIG)

    def test_conversion(self):
        class long2(int):
            pass
        x = 1
        x = long2(x<<100)
        y = int(x)
        assert type(y) == int
        assert type(+long2(5)) is int
        assert type(long2(5) << 0) is int
        assert type(long2(5) >> 0) is int
        assert type(long2(5) + 0) is int
        assert type(long2(5) - 0) is int
        assert type(long2(5) * 1) is int
        assert type(1 * long2(5)) is int
        assert type(0 + long2(5)) is int
        assert type(-long2(0)) is int
        assert type(long2(5) // 1) is int

    def test_pow(self):
        x = 0
        assert pow(x, 0, 1) == 0

    def test_getnewargs(self):
        assert  0 .__getnewargs__() == (0,)
        assert  (-1) .__getnewargs__() == (-1,)

    def test_divmod(self):
        def check_division(x, y):
            q, r = divmod(x, y)
            pab, pba = x*y, y*x
            assert pab == pba
            assert q == x//y
            assert r == x%y
            assert x == q*y + r
            if y > 0:
                assert 0 <= r < y
            else:
                assert y < r <= 0
        for x in [-1, 0, 1, 2 ** 100 - 1, -2 ** 100 - 1]:
            for y in [-105566530, -1, 1, 1034522340]:
                print("checking division for %s, %s" % (x, y))
                check_division(x, y)
        # special case from python tests:
        s1 = 33
        s2 = 2
        x = 16565645174462751485571442763871865344588923363439663038777355323778298703228675004033774331442052275771343018700586987657790981527457655176938756028872904152013524821759375058141439
        x >>= s1*16
        y = 10953035502453784575
        y >>= s2*16
        x = 0x3FE0003FFFFC0001FFF
        y = 0x9800FFC1
        check_division(x, y)
        raises(ZeroDivisionError, "x // 0")

    def test_format(self):
        assert repr(12345678901234567890) == '12345678901234567890'
        assert str(12345678901234567890) == '12345678901234567890'
        assert hex(0x1234567890ABCDEF) == '0x1234567890abcdef'
        assert oct(0o1234567012345670) == '0o1234567012345670'

    def test_bits(self):
        x = 0xAAAAAAAA
        assert x | 0x55555555 == 0xFFFFFFFF
        assert x & 0x55555555 == 0x00000000
        assert x ^ 0x55555555 == 0xFFFFFFFF
        assert -x | 0x55555555 == -0xAAAAAAA9
        assert x | 0x555555555 == 0x5FFFFFFFF
        assert x & 0x555555555 == 0x000000000
        assert x ^ 0x555555555 == 0x5FFFFFFFF

    def test_hash(self):
        # ints have the same hash as equal longs
        for i in range(-4, 14):
            assert hash(i) == hash(int(i))
        # might check too much -- it's ok to change the hashing algorithm
        assert hash(123456789) == 123456789
        assert hash(1234567890123456789) in (
            -1895067127,            # with 32-bit platforms
            1234567890123456789)    # with 64-bit platforms

    def test_math_log(self):
        import math
        raises(ValueError, math.log, 0)
        raises(ValueError, math.log, -1)
        raises(ValueError, math.log, -2)
        raises(ValueError, math.log, -(1 << 10000))
        #raises(ValueError, math.log, 0)
        raises(ValueError, math.log, -1)
        raises(ValueError, math.log, -2)

    def test_long(self):
        import sys
        n = -sys.maxsize-1
        assert int(n) == n
        assert str(int(n)) == str(n)

    def test_huge_longs(self):
        import operator
        x = 1
        huge = x << 40000
        raises(OverflowError, float, huge)
        raises(OverflowError, operator.truediv, huge, 3)
        raises(OverflowError, operator.truediv, huge, 3)

    def test_just_trunc(self):
        class myint(object):
            def __trunc__(self):
                return 42
        assert int(myint()) == 42

    def test_override___int__(self):
        class myint(int):
            def __int__(self):
                return 42
        assert int(myint(21)) == 42
        class myotherint(int):
            pass
        assert int(myotherint(21)) == 21

    def test___int__(self):
        class A(object):
            def __int__(self):
                return 42
        assert int(A()) == 42
        # but!: (blame CPython 2.7)
        class Integral(object):
            def __int__(self):
                return 42
        class TruncReturnsNonInt(object):
            def __trunc__(self):
                return Integral()
        assert int(TruncReturnsNonInt()) == 42

    def test_conjugate(self):
        assert (7).conjugate() == 7
        assert (-7).conjugate() == -7

        class L(int):
            pass

        assert type(L(7).conjugate()) is int

        class L(int):
            def __pos__(self):
                return 43
        assert L(7).conjugate() == 7

    def test_bit_length(self):
        assert (8).bit_length() == 4
        assert (-1<<40).bit_length() == 41
        assert ((2**31)-1).bit_length() == 31

    def test_from_bytes(self):
        assert int.from_bytes(b'c', 'little') == 99
        assert int.from_bytes(b'\x01\x01', 'little') == 257
        assert int.from_bytes(b'\x01\x00', 'big') == 256
        assert int.from_bytes(b'\x00\x80', 'little', signed=True) == -32768
        assert int.from_bytes([255, 0, 0], 'big', signed=True) == -65536
        raises(TypeError, int.from_bytes, 0, 'big')
        raises(TypeError, int.from_bytes, '', 'big')
        raises(ValueError, int.from_bytes, b'c', 'foo')

    def test_to_bytes(self):
        assert 65535 .to_bytes(2, 'big') == b'\xff\xff'
        assert (-8388608).to_bytes(3, 'little', signed=True) == b'\x00\x00\x80'
        raises(OverflowError, (-5).to_bytes, 1, 'big')
        raises(ValueError, (-5).to_bytes, 1, 'foo')

    def test_negative_zero(self):
        x = eval("-0")
        assert x == 0

    def test_long_real(self):
        class A(int): pass
        b = A(5).real
        assert type(b) is int

    def test_long_from_unicode(self):
        raises(ValueError, int, '123L')
        assert int('L', 22) == 21
        s = '\U0001D7CF\U0001D7CE' # 𝟏𝟎
        assert int(s) == 10

    def test_long_from_bytes(self):
        assert int(b'1234') == 1234

    def test_invalid_literal_message(self):
        try:
            int('hello àèìò')
        except ValueError as e:
            assert 'hello àèìò' in str(e)
        else:
            assert False, 'did not raise'

    def test_base_overflow(self):
        raises(ValueError, int, '42', 2**63)

    def test_long_real(self):
        class A(int): pass
        b = A(5).real
        assert type(b) is int

    def test__int__(self):
        class A(int):
            def __int__(self):
                return 42

        assert int(int(3)) == int(3)
        assert int(A(13)) == 42

    def test_large_identity(self):
        import sys
        a = sys.maxsize + 1
        b = sys.maxsize + 2
        assert a is not b
