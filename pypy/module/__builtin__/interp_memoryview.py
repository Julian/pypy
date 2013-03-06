"""
Implementation of the 'buffer' and 'memoryview' types.
"""
from pypy.interpreter.baseobjspace import Wrappable
from pypy.interpreter import buffer
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.interpreter.typedef import TypeDef, GetSetProperty
from pypy.interpreter.error import OperationError
import operator

class W_MemoryView(Wrappable):
    """Implement the built-in 'memoryview' type as a thin wrapper around
    an interp-level buffer.
    """

    def __init__(self, buf):
        assert isinstance(buf, buffer.Buffer)
        self.buf = buf

    def _make_descr__cmp(name):
        def descr__cmp(self, space, w_other):
            other = space.interpclass_w(w_other)
            if self.buf is None:
                return space.wrap(getattr(operator, name)(self, other))
            if isinstance(other, W_MemoryView):
                # xxx not the most efficient implementation
                str1 = self.as_str()
                str2 = other.as_str()
                return space.wrap(getattr(operator, name)(str1, str2))

            try:
                w_buf = space.buffer(w_other)
            except OperationError, e:
                if not e.match(space, space.w_TypeError):
                    raise
                return space.w_NotImplemented
            else:
                str1 = self.as_str()
                str2 = space.buffer_w(w_buf).as_str()
                return space.wrap(getattr(operator, name)(str1, str2))
        descr__cmp.func_name = name
        return descr__cmp

    descr_eq = _make_descr__cmp('eq')
    descr_ne = _make_descr__cmp('ne')

    def as_str(self):
        return self.buf.as_str()

    def getlength(self):
        return self.buf.getlength()

    def getslice(self, start, stop):
        if start < 0:
            start = 0
        size = stop - start
        if size < 0:
            size = 0
        buf = self.buf
        if isinstance(buf, buffer.RWBuffer):
            buf = buffer.RWSubBuffer(buf, start, size)
        else:
            buf = buffer.SubBuffer(buf, start, size)
        return W_MemoryView(buf)

    def descr_buffer(self, space):
        """Note that memoryview() objects in PyPy support buffer(), whereas
        not in CPython; but CPython supports passing memoryview() to most
        built-in functions that accept buffers, with the notable exception
        of the buffer() built-in."""
        self._check_released(space)
        return space.wrap(self.buf)

    def descr_tobytes(self, space):
        self._check_released(space)
        return space.wrapbytes(self.as_str())

    def descr_tolist(self, space):
        self._check_released(space)
        buf = self.buf
        if buf.format != 'B':
            raise OperationError(space.w_NotImplementedError, space.wrap(
                "tolist() only supports byte views"))
        result = []
        for i in range(buf.getlength()):
            result.append(space.wrap(ord(buf.getitem(i)[0])))
        return space.newlist(result)

    def descr_getitem(self, space, w_index):
        self._check_released(space)
        start, stop, step = space.decode_index(w_index, self.getlength())
        if step == 0:  # index only
            return space.wrapbytes(self.buf.getitem(start))
        elif step == 1:
            res = self.getslice(start, stop)
            return space.wrap(res)
        else:
            raise OperationError(space.w_ValueError,
                space.wrap("memoryview object does not support"
                           " slicing with a step"))

    @unwrap_spec(newstring='bufferstr')
    def descr_setitem(self, space, w_index, newstring):
        self._check_released(space)
        buf = self.buf
        if isinstance(buf, buffer.RWBuffer):
            buf.descr_setitem(space, w_index, newstring)
        else:
            raise OperationError(space.w_TypeError,
                                 space.wrap("cannot modify read-only memory"))

    def descr_len(self, space):
        self._check_released(space)
        return self.buf.descr_len(space)

    def w_get_format(self, space):
        self._check_released(space)
        return space.wrap(self.buf.format)
    def w_get_itemsize(self, space):
        self._check_released(space)
        return space.wrap(self.buf.itemsize)
    def w_get_ndim(self, space):
        self._check_released(space)
        return space.wrap(1)
    def w_is_readonly(self, space):
        self._check_released(space)
        return space.wrap(not isinstance(self.buf, buffer.RWBuffer))
    def w_get_shape(self, space):
        self._check_released(space)
        return space.newtuple([space.wrap(self.getlength())])
    def w_get_strides(self, space):
        self._check_released(space)
        return space.newtuple([space.wrap(self.buf.itemsize)])
    def w_get_suboffsets(self, space):
        self._check_released(space)
        # I've never seen anyone filling this field
        return space.w_None

    def descr_repr(self, space):
        if self.buf is None:
            return self.getrepr(space, 'released memory')
        else:
            return self.getrepr(space, 'memory')

    def descr_release(self, space):
        self.buf = None

    def _check_released(self, space):
        if self.buf is None:
            raise OperationError(space.w_ValueError, space.wrap(
                    "operation forbidden on released memoryview object"))

    def descr_enter(self, space):
        self._check_released(space)
        return self

    def descr_exit(self, space, __args__):
        self.buf = None
        return space.w_None


def descr_new(space, w_subtype, w_object):
    memoryview = W_MemoryView(space.buffer(w_object))
    return space.wrap(memoryview)

W_MemoryView.typedef = TypeDef(
    "memoryview",
    __doc__ = """\
Create a new memoryview object which references the given object.
""",
    __new__ = interp2app(descr_new),
    __buffer__  = interp2app(W_MemoryView.descr_buffer),
    __eq__      = interp2app(W_MemoryView.descr_eq),
    __getitem__ = interp2app(W_MemoryView.descr_getitem),
    __len__     = interp2app(W_MemoryView.descr_len),
    __ne__      = interp2app(W_MemoryView.descr_ne),
    __setitem__ = interp2app(W_MemoryView.descr_setitem),
    __repr__    = interp2app(W_MemoryView.descr_repr),
    __enter__   = interp2app(W_MemoryView.descr_enter),
    __exit__    = interp2app(W_MemoryView.descr_exit),
    tobytes     = interp2app(W_MemoryView.descr_tobytes),
    tolist      = interp2app(W_MemoryView.descr_tolist),
    release     = interp2app(W_MemoryView.descr_release),
    format      = GetSetProperty(W_MemoryView.w_get_format),
    itemsize    = GetSetProperty(W_MemoryView.w_get_itemsize),
    ndim        = GetSetProperty(W_MemoryView.w_get_ndim),
    readonly    = GetSetProperty(W_MemoryView.w_is_readonly),
    shape       = GetSetProperty(W_MemoryView.w_get_shape),
    strides     = GetSetProperty(W_MemoryView.w_get_strides),
    suboffsets  = GetSetProperty(W_MemoryView.w_get_suboffsets),
    )
W_MemoryView.typedef.acceptable_as_base_class = False
