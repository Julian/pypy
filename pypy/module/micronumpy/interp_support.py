from pypy.interpreter.error import OperationError
from pypy.interpreter.gateway import unwrap_spec
from pypy.module.micronumpy.interp_dtype import get_dtype_cache
from pypy.rlib.rstruct.runpack import runpack
from pypy.rpython.lltypesystem import lltype, rffi


FLOAT_SIZE = rffi.sizeof(lltype.Float)

@unwrap_spec(s=str)
def fromstring(space, s):
    from pypy.module.micronumpy.interp_numarray import W_NDimArray
    length = len(s)

    if length % FLOAT_SIZE == 0:
        number = length/FLOAT_SIZE
    else:
        raise OperationError(space.w_ValueError, space.wrap(
            "string length %d not divisable by %d" % (length, FLOAT_SIZE)))

    dtype = get_dtype_cache(space).w_float64dtype
    a = W_NDimArray(number, [number], dtype=dtype)

    start = 0
    end = FLOAT_SIZE
    i = 0
    while i < number:
        part = s[start:end]
        a.dtype.setitem(a.storage, i, dtype.box(runpack('d', part)))
        i += 1
        start += FLOAT_SIZE
        end += FLOAT_SIZE

    return space.wrap(a)
