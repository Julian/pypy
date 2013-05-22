from pypy.interpreter.error import OperationError
from pypy.objspace.std.tupleobject import W_AbstractTupleObject
from pypy.objspace.std.util import negate
from rpython.rlib.objectmodel import compute_hash
from rpython.rlib.rarithmetic import intmask
from rpython.rlib.unroll import unrolling_iterable
from rpython.tool.sourcetools import func_with_new_name


class NotSpecialised(Exception):
    pass


def make_specialised_class(typetuple):
    assert type(typetuple) == tuple

    nValues = len(typetuple)
    iter_n = unrolling_iterable(range(nValues))

    class cls(W_AbstractTupleObject):
        def __init__(self, space, *values_w):
            self.space = space
            assert len(values_w) == nValues
            for i in iter_n:
                w_obj = values_w[i]
                val_type = typetuple[i]
                if val_type == int:
                    unwrapped = space.int_w(w_obj)
                elif val_type == float:
                    unwrapped = space.float_w(w_obj)
                elif val_type == str:
                    unwrapped = space.str_w(w_obj)
                elif val_type == object:
                    unwrapped = w_obj
                else:
                    raise AssertionError
                setattr(self, 'value%s' % i, unwrapped)

        def length(self):
            return nValues

        def tolist(self):
            list_w = [None] * nValues
            for i in iter_n:
                value = getattr(self, 'value%s' % i)
                if typetuple[i] != object:
                    value = self.space.wrap(value)
                list_w[i] = value
            return list_w

        # same source code, but builds and returns a resizable list
        getitems_copy = func_with_new_name(tolist, 'getitems_copy')

        def descr_hash(self, space):
            mult = 1000003
            x = 0x345678
            z = nValues
            for i in iter_n:
                value = getattr(self, 'value%s' % i)
                if typetuple[i] == object:
                    y = space.int_w(space.hash(value))
                elif typetuple[i] == float:
                    # get the correct hash for float which is an
                    # integer & other less frequent cases
                    from pypy.objspace.std.floatobject import _hash_float
                    y = _hash_float(space, value)
                else:
                    y = compute_hash(value)
                x = (x ^ y) * mult
                z -= 1
                mult += 82520 + z + z
            x += 97531
            return space.wrap(intmask(x))

        def descr_eq(self, space, w_other):
            if not isinstance(w_other, W_AbstractTupleObject):
                return space.w_NotImplemented
            if not isinstance(w_other, cls):
                if nValues != w_other.length():
                    return space.w_False
                for i in iter_n:
                    myval = getattr(self, 'value%s' % i)
                    otherval = w_other.getitem(space, i)
                    if typetuple[i] != object:
                        myval = space.wrap(myval)
                    if not space.eq_w(myval, otherval):
                        return space.w_False
                return space.w_True

            for i in iter_n:
                myval = getattr(self, 'value%s' % i)
                otherval = getattr(w_other, 'value%s' % i)
                if typetuple[i] == object:
                    if not self.space.eq_w(myval, otherval):
                        return space.w_False
                else:
                    if myval != otherval:
                        return space.w_False
            return space.w_True

        descr_ne = negate(descr_eq)

        def getitem(self, space, index):
            if index < 0:
                index += nValues
            for i in iter_n:
                if index == i:
                    value = getattr(self, 'value%s' % i)
                    if typetuple[i] != object:
                        value = space.wrap(value)
                    return value
            raise OperationError(space.w_IndexError,
                                 space.wrap("tuple index out of range"))

    cls.__name__ = ('W_SpecialisedTupleObject_' +
                    ''.join([t.__name__[0] for t in typetuple]))
    _specialisations.append(cls)
    return cls

# ---------- current specialized versions ----------

_specialisations = []
Cls_ii = make_specialised_class((int, int))
Cls_oo = make_specialised_class((object, object))
Cls_ff = make_specialised_class((float, float))

def makespecialisedtuple(space, list_w):
    if len(list_w) == 2:
        w_arg1, w_arg2 = list_w
        w_type1 = space.type(w_arg1)
        if w_type1 is space.w_int:
            w_type2 = space.type(w_arg2)
            if w_type2 is space.w_int:
                return Cls_ii(space, w_arg1, w_arg2)
        elif w_type1 is space.w_float:
            w_type2 = space.type(w_arg2)
            if w_type2 is space.w_float:
                return Cls_ff(space, w_arg1, w_arg2)
        return Cls_oo(space, w_arg1, w_arg2)
    else:
        raise NotSpecialised
