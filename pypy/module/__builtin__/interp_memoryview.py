"""
Implementation of the 'buffer' and 'memoryview' types.
"""
import operator

from pypy.interpreter import buffer
from pypy.interpreter.baseobjspace import W_Root
from pypy.interpreter.error import OperationError
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.interpreter.typedef import TypeDef, GetSetProperty
from rpython.rlib.objectmodel import compute_hash
from rpython.rlib.rstring import StringBuilder


def _buffer_setitem(space, buf, w_index, newstring):
    start, stop, step, size = space.decode_index4(w_index, buf.getlength())
    if step == 0:  # index only
        if len(newstring) != 1:
            msg = 'buffer[index]=x: x must be a single character'
            raise OperationError(space.w_TypeError, space.wrap(msg))
        char = newstring[0]   # annotator hint
        buf.setitem(start, char)
    elif step == 1:
        if len(newstring) != size:
            msg = "right operand length must match slice length"
            raise OperationError(space.w_ValueError, space.wrap(msg))
        buf.setslice(start, newstring)
    else:
        raise OperationError(space.w_ValueError,
                             space.wrap("buffer object does not support"
                                        " slicing with a step"))


class W_Buffer(W_Root):
    """Implement the built-in 'buffer' type as a wrapper around
    an interp-level buffer.
    """

    def __init__(self, buf):
        self.buf = buf

    def buffer_w(self, space):
        return self.buf

    @staticmethod
    @unwrap_spec(offset=int, size=int)
    def descr_new_buffer(space, w_subtype, w_object, offset=0, size=-1):
        if space.isinstance_w(w_object, space.w_unicode):
            # unicode objects support the old buffer interface
            # but not the new buffer interface (change in python 2.7)
            from rpython.rlib.rstruct.unichar import pack_unichar, UNICODE_SIZE
            unistr = space.unicode_w(w_object)
            builder = StringBuilder(len(unistr) * UNICODE_SIZE)
            for unich in unistr:
                pack_unichar(unich, builder)
            from pypy.interpreter.buffer import StringBuffer
            buf = StringBuffer(builder.build())
        else:
            buf = space.buffer_w(w_object)

        if offset == 0 and size == -1:
            return W_Buffer(buf)
        # handle buffer slices
        if offset < 0:
            raise OperationError(space.w_ValueError,
                                 space.wrap("offset must be zero or positive"))
        if size < -1:
            raise OperationError(space.w_ValueError,
                                 space.wrap("size must be zero or positive"))
        if isinstance(buf, buffer.RWBuffer):
            buf = buffer.RWSubBuffer(buf, offset, size)
        else:
            buf = buffer.SubBuffer(buf, offset, size)
        return W_Buffer(buf)

    def descr_len(self, space):
        return space.wrap(self.buf.getlength())

    def descr_getitem(self, space, w_index):
        start, stop, step, size = space.decode_index4(w_index, self.buf.getlength())
        if step == 0:  # index only
            return space.wrap(self.buf.getitem(start))
        res = self.buf.getslice(start, stop, step, size)
        return space.wrap(res)

    @unwrap_spec(newstring='bufferstr')
    def descr_setitem(self, space, w_index, newstring):
        if not isinstance(self.buf, buffer.RWBuffer):
            raise OperationError(space.w_TypeError,
                                 space.wrap("buffer is read-only"))
        _buffer_setitem(space, self.buf, w_index, newstring)

    def descr_str(self, space):
        return space.wrap(self.buf.as_str())

    @unwrap_spec(other='bufferstr')
    def descr_add(self, space, other):
        return space.wrap(self.buf.as_str() + other)

    def _make_descr__cmp(name):
        def descr__cmp(self, space, w_other):
            if not isinstance(w_other, W_Buffer):
                return space.w_NotImplemented
            # xxx not the most efficient implementation
            str1 = self.buf.as_str()
            str2 = w_other.buf.as_str()
            return space.wrap(getattr(operator, name)(str1, str2))
        descr__cmp.func_name = name
        return descr__cmp

    descr_eq = _make_descr__cmp('eq')
    descr_ne = _make_descr__cmp('ne')
    descr_lt = _make_descr__cmp('lt')
    descr_le = _make_descr__cmp('le')
    descr_gt = _make_descr__cmp('gt')
    descr_ge = _make_descr__cmp('ge')

    def descr_hash(self, space):
        return space.wrap(compute_hash(self.buf.as_str()))

    def descr_mul(self, space, w_times):
        # xxx not the most efficient implementation
        w_string = space.wrap(self.buf.as_str())
        # use the __mul__ method instead of space.mul() so that we
        # return NotImplemented instead of raising a TypeError
        return space.call_method(w_string, '__mul__', w_times)

    def descr_repr(self, space):
        if isinstance(self.buf, buffer.RWBuffer):
            info = 'read-write buffer'
        else:
            info = 'read-only buffer'
        addrstring = self.getaddrstring(space)

        return space.wrap("<%s for 0x%s, size %d>" %
                          (info, addrstring, self.buf.getlength()))

W_Buffer.typedef = TypeDef(
    "buffer",
    __doc__ = """\
buffer(object [, offset[, size]])

Create a new buffer object which references the given object.
The buffer will reference a slice of the target object from the
start of the object (or at the specified offset). The slice will
extend to the end of the target object (or with the specified size).
""",
    __new__ = interp2app(W_Buffer.descr_new_buffer),
    __len__ = interp2app(W_Buffer.descr_len),
    __getitem__ = interp2app(W_Buffer.descr_getitem),
    __setitem__ = interp2app(W_Buffer.descr_setitem),
    __str__ = interp2app(W_Buffer.descr_str),
    __add__ = interp2app(W_Buffer.descr_add),
    __eq__ = interp2app(W_Buffer.descr_eq),
    __ne__ = interp2app(W_Buffer.descr_ne),
    __lt__ = interp2app(W_Buffer.descr_lt),
    __le__ = interp2app(W_Buffer.descr_le),
    __gt__ = interp2app(W_Buffer.descr_gt),
    __ge__ = interp2app(W_Buffer.descr_ge),
    __hash__ = interp2app(W_Buffer.descr_hash),
    __mul__ = interp2app(W_Buffer.descr_mul),
    __rmul__ = interp2app(W_Buffer.descr_mul),
    __repr__ = interp2app(W_Buffer.descr_repr),
)
W_Buffer.typedef.acceptable_as_base_class = False


class W_MemoryView(W_Root):
    """Implement the built-in 'memoryview' type as a wrapper around
    an interp-level buffer.
    """

    def __init__(self, buf):
        self.buf = buf

    def buffer_w(self, space):
        return self.buf

    @staticmethod
    def descr_new_memoryview(space, w_subtype, w_object):
        w_memoryview = W_MemoryView(space.buffer_w(w_object))
        return w_memoryview

    def _make_descr__cmp(name):
        def descr__cmp(self, space, w_other):
            if isinstance(w_other, W_MemoryView):
                # xxx not the most efficient implementation
                str1 = self.as_str()
                str2 = w_other.as_str()
                return space.wrap(getattr(operator, name)(str1, str2))

            try:
                buf = space.buffer_w(w_other)
            except OperationError, e:
                if not e.match(space, space.w_TypeError):
                    raise
                return space.w_NotImplemented
            else:
                str1 = self.as_str()
                str2 = buf.as_str()
                return space.wrap(getattr(operator, name)(str1, str2))
        descr__cmp.func_name = name
        return descr__cmp

    descr_eq = _make_descr__cmp('eq')
    descr_ne = _make_descr__cmp('ne')
    descr_lt = _make_descr__cmp('lt')
    descr_le = _make_descr__cmp('le')
    descr_gt = _make_descr__cmp('gt')
    descr_ge = _make_descr__cmp('ge')

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
        """
        Note that memoryview() is very inconsistent in CPython: it does not
        support the buffer interface but does support the new buffer
        interface: as a result, it is possible to pass memoryview to
        e.g. socket.send() but not to file.write().  For simplicity and
        consistency, in PyPy memoryview DOES support buffer(), which means
        that it is accepted in more places than CPython.
        """
        return space.wrap(self.buf)

    def descr_tobytes(self, space):
        return space.wrap(self.as_str())

    def descr_tolist(self, space):
        buf = self.buf
        result = []
        for i in range(buf.getlength()):
            result.append(space.wrap(ord(buf.getitem(i))))
        return space.newlist(result)

    def descr_getitem(self, space, w_index):
        start, stop, step = space.decode_index(w_index, self.getlength())
        if step == 0:  # index only
            return space.wrap(self.buf.getitem(start))
        elif step == 1:
            res = self.getslice(start, stop)
            return space.wrap(res)
        else:
            raise OperationError(space.w_ValueError,
                space.wrap("memoryview object does not support"
                           " slicing with a step"))

    @unwrap_spec(newstring='bufferstr')
    def descr_setitem(self, space, w_index, newstring):
        if not isinstance(self.buf, buffer.RWBuffer):
            raise OperationError(space.w_TypeError,
                                 space.wrap("cannot modify read-only memory"))
        _buffer_setitem(space, self.buf, w_index, newstring)

    def descr_len(self, space):
        return space.wrap(self.buf.getlength())

    def w_get_format(self, space):
        return space.wrap("B")

    def w_get_itemsize(self, space):
        return space.wrap(1)

    def w_get_ndim(self, space):
        return space.wrap(1)

    def w_is_readonly(self, space):
        return space.wrap(not isinstance(self.buf, buffer.RWBuffer))

    def w_get_shape(self, space):
        return space.newtuple([space.wrap(self.getlength())])

    def w_get_strides(self, space):
        return space.newtuple([space.wrap(1)])

    def w_get_suboffsets(self, space):
        # I've never seen anyone filling this field
        return space.w_None

W_MemoryView.typedef = TypeDef(
    "memoryview",
    __doc__ = """\
Create a new memoryview object which references the given object.
""",
    __new__ = interp2app(W_MemoryView.descr_new_memoryview),
    __eq__      = interp2app(W_MemoryView.descr_eq),
    __ge__      = interp2app(W_MemoryView.descr_ge),
    __getitem__ = interp2app(W_MemoryView.descr_getitem),
    __gt__      = interp2app(W_MemoryView.descr_gt),
    __le__      = interp2app(W_MemoryView.descr_le),
    __len__     = interp2app(W_MemoryView.descr_len),
    __lt__      = interp2app(W_MemoryView.descr_lt),
    __ne__      = interp2app(W_MemoryView.descr_ne),
    __setitem__ = interp2app(W_MemoryView.descr_setitem),
    tobytes     = interp2app(W_MemoryView.descr_tobytes),
    tolist      = interp2app(W_MemoryView.descr_tolist),
    format      = GetSetProperty(W_MemoryView.w_get_format),
    itemsize    = GetSetProperty(W_MemoryView.w_get_itemsize),
    ndim        = GetSetProperty(W_MemoryView.w_get_ndim),
    readonly    = GetSetProperty(W_MemoryView.w_is_readonly),
    shape       = GetSetProperty(W_MemoryView.w_get_shape),
    strides     = GetSetProperty(W_MemoryView.w_get_strides),
    suboffsets  = GetSetProperty(W_MemoryView.w_get_suboffsets),
    )
W_MemoryView.typedef.acceptable_as_base_class = False
