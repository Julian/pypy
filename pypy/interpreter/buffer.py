from rpython.rlib.rgc import nonmoving_raw_ptr_for_resizable_list
from rpython.rlib.signature import signature
from rpython.rlib import types
from rpython.rlib.rstruct.error import StructError

from pypy.interpreter.error import oefmt


class Buffer(object):
    """Abstract base class for buffers."""
    _attrs_ = ['readonly']
    _immutable_ = True

    def getlength(self):
        """Returns the size in bytes (even if getitemsize() > 1)."""
        raise NotImplementedError

    def as_str(self):
        "Returns an interp-level string with the whole content of the buffer."
        return ''.join(self._copy_buffer())

    def getbytes(self, start, stop, step, size):
        # May be overridden.  No bounds checks.
        return ''.join([self.getitem(i) for i in range(start, stop, step)])

    def setbytes(self, start, string):
        # May be overridden.  No bounds checks.
        for i in range(len(string)):
            self.setitem(start + i, string[i])

    def get_raw_address(self):
        raise ValueError("no raw buffer")

    def as_binary(self):
        # Inefficient. May be overridden.
        return StringBuffer(self.as_str())

    def getformat(self):
        raise NotImplementedError

    def getitemsize(self):
        raise NotImplementedError

    def getndim(self):
        raise NotImplementedError

    def getshape(self):
        raise NotImplementedError

    def getstrides(self):
        raise NotImplementedError

    def releasebuffer(self):
        pass

    def _copy_buffer(self):
        if self.getndim() == 0:
            itemsize = self.getitemsize()
            return [self.getbytes(0, itemsize, 1, itemsize)]
        data = []
        self._copy_rec(0, data, 0)
        return data

    def _copy_rec(self, idim, data, off):
        shapes = self.getshape()
        shape = shapes[idim]
        strides = self.getstrides()

        if self.getndim() - 1 == idim:
            self._copy_base(data, off)
            return

        for i in range(shape):
            self._copy_rec(idim + 1, data, off)
            off += strides[idim]

    def _copy_base(self, data, off):
        shapes = self.getshape()
        step = shapes[0]
        strides = self.getstrides()
        itemsize = self.getitemsize()
        bytesize = self.getlength()
        copiedbytes = 0
        for i in range(step):
            bytes = self.getbytes(off, off+itemsize, 1, itemsize)
            data.append(bytes)
            copiedbytes += len(bytes)
            off += strides[0]
            # do notcopy data if the sub buffer is out of bounds
            if copiedbytes >= bytesize:
                break

    def get_offset(self, space, dim, index):
        "Convert index at dimension `dim` into a byte offset"
        shape = self.getshape()
        nitems = shape[dim]
        if index < 0:
            index += nitems
        if index < 0 or index >= nitems:
            raise oefmt(space.w_IndexError,
                "index out of bounds on dimension %d", dim+1)
        # TODO suboffsets?
        strides = self.getstrides()
        return strides[dim] * index


    def w_getitem(self, space, idx):
        from pypy.module.struct.formatiterator import UnpackFormatIterator
        offset = self.get_offset(space, 0, idx)
        itemsize = self.getitemsize()
        if itemsize == 1:
            ch = self.as_binary()[offset]
            return space.newint(ord(ch))
        else:
            # TODO: this probably isn't very fast
            buf = SubBuffer(self.as_binary(), offset, itemsize)
            fmtiter = UnpackFormatIterator(space, buf)
            fmtiter.length = buf.getlength()
            fmtiter.interpret(self.getformat())
            return fmtiter.result_w[0]

    def setitem_w(self, space, idx, w_obj):
        from pypy.module.struct.formatiterator import PackFormatIterator
        offset = self.get_offset(space, 0, idx)
        itemsize = self.getitemsize()
        if itemsize == 1:
            self.as_binary()[offset] = space.byte_w(w_obj)
        else:
            # TODO: this probably isn't very fast
            fmtiter = PackFormatIterator(space, [w_obj], itemsize)
            try:
                fmtiter.interpret(self.getformat())
            except StructError as e:
                raise oefmt(space.w_TypeError,
                            "memoryview: invalid type for format '%s'",
                            self.getformat())
            byteval = fmtiter.result.build()
            self.setbytes(offset, byteval)

    def w_tolist(self, space):
        dim = self.getndim()
        fmt = self.getformat()
        if dim == 0:
            raise NotImplementedError
        elif dim == 1:
            n = self.getshape()[0]
            values_w = [self.w_getitem(space, i) for i in range(n)]
            return space.newlist(values_w)
        else:
            return self._tolist_rec(space, self.as_binary(), 0, 0, fmt)

    def _tolist(self, space, buf, bytecount, itemsize, fmt, strides=None):
        from pypy.module.struct.formatiterator import UnpackFormatIterator
        # TODO: this probably isn't very fast
        count = bytecount // itemsize
        fmtiter = UnpackFormatIterator(space, buf)
        # patch the length, necessary buffer might have offset
        # which leads to wrong length calculation if e.g. the
        # memoryview is reversed
        fmtiter.length = bytecount
        fmtiter.strides = strides
        fmtiter.interpret(fmt * count)
        return space.newlist(fmtiter.result_w)

    def _tolist_rec(self, space, buf, start, idim, fmt):
        strides = self.getstrides()
        shape = self.getshape()
        #
        dim = idim + 1
        stride = strides[idim]
        itemsize = self.getitemsize()
        dimshape = shape[idim]
        #
        if dim >= self.getndim():
            bytecount = (stride * dimshape)
            return self._tolist(space, buf, bytecount, itemsize, fmt, [stride])
        items = [None] * dimshape

        orig_buf = buf
        for i in range(dimshape):
            buf = SubBuffer(orig_buf, start, stride)
            item = self._tolist_rec(space, buf, start, idim + 1, fmt)
            items[i] = item
            start += stride

        return space.newlist(items)


class SimpleBuffer(Buffer):
    _attrs_ = ['readonly', 'data']
    _immutable_ = True

    def __init__(self, data):
        self.data = data
        self.readonly = self.data.readonly

    def getlength(self):
        return self.data.getlength()

    def as_str(self):
        return self.data.as_str()

    def getbytes(self, start, stop, step, size):
        assert step == 1
        return self.data[start:stop]

    def setbytes(self, offset, s):
        self.data.setslice(offset, s)

    def get_raw_address(self):
        return self.data.get_raw_address()

    def as_binary(self):
        return self.data

    def getformat(self):
        return 'B'

    def getitemsize(self):
        return 1

    def getndim(self):
        return 1

    def getshape(self):
        return [self.getlength()]

    def getstrides(self):
        return [1]

    def get_offset(self, space, dim, index):
        "Convert index at dimension `dim` into a byte offset"
        assert dim == 0
        nitems = self.getlength()
        if index < 0:
            index += nitems
        if index < 0 or index >= nitems:
            raise oefmt(space.w_IndexError,
                "index out of bounds on dimension %d", dim + 1)
        return index

    def w_getitem(self, space, idx):
        idx = self.get_offset(space, 0, idx)
        ch = self.data[idx]
        return space.newint(ord(ch))

    def setitem_w(self, space, idx, w_obj):
        idx = self.get_offset(space, 0, idx)
        self.data[idx] = space.byte_w(w_obj)


class BinaryBuffer(object):
    """Base class for buffers of bytes"""
    _attrs_ = ['readonly']
    _immutable_ = True

    def getlength(self):
        """Returns the size in bytes (even if getitemsize() > 1)."""
        raise NotImplementedError

    def __len__(self):
        res = self.getlength()
        assert res >= 0
        return res

    def as_str(self):
        "Returns an interp-level string with the whole content of the buffer."
        # May be overridden.
        return self.getslice(0, self.getlength(), 1, self.getlength())

    def as_str_and_offset_maybe(self):
        """
        If the buffer is backed by a string, return a pair (string, offset),
        where offset is the offset inside the string where the buffer start.
        Else, return (None, 0).
        """
        return None, 0

    def getitem(self, index):
        "Returns the index'th character in the buffer."
        raise NotImplementedError   # Must be overriden.  No bounds checks.

    def __getitem__(self, i):
        return self.getitem(i)

    def getslice(self, start, stop, step, size):
        # May be overridden.  No bounds checks.
        return ''.join([self.getitem(i) for i in range(start, stop, step)])

    def __getslice__(self, start, stop):
        return self.getslice(start, stop, 1, stop - start)

    def setitem(self, index, char):
        "Write a character into the buffer."
        raise NotImplementedError   # Must be overriden.  No bounds checks.

    def __setitem__(self, i, char):
        return self.setitem(i, char)

    def setslice(self, start, string):
        # May be overridden.  No bounds checks.
        for i in range(len(string)):
            self.setitem(start + i, string[i])

class ByteBuffer(BinaryBuffer):
    _immutable_ = True

    def __init__(self, len):
        self.data = ['\x00'] * len
        self.readonly = False

    def getlength(self):
        return len(self.data)

    def getitem(self, index):
        return self.data[index]

    def setitem(self, index, char):
        self.data[index] = char

    def get_raw_address(self):
        return nonmoving_raw_ptr_for_resizable_list(self.data)

class StringBuffer(BinaryBuffer):
    _attrs_ = ['readonly', 'value']
    _immutable_ = True

    def __init__(self, value):
        self.value = value
        self.readonly = 1

    def getlength(self):
        return len(self.value)

    def as_str(self):
        return self.value

    def as_str_and_offset_maybe(self):
        return self.value, 0

    def getitem(self, index):
        return self.value[index]

    def getslice(self, start, stop, step, size):
        if size == 0:
            return ""
        if step == 1:
            assert 0 <= start <= stop
            if start == 0 and stop == len(self.value):
                return self.value
            return self.value[start:stop]
        return BinaryBuffer.getslice(self, start, stop, step, size)

    def get_raw_address(self):
        from rpython.rtyper.lltypesystem import rffi
        # may still raise ValueError on some GCs
        return rffi.get_raw_address_of_string(self.value)

class SubBuffer(BinaryBuffer):
    _attrs_ = ['buffer', 'offset', 'size', 'readonly']
    _immutable_ = True

    @signature(types.any(), types.instance(BinaryBuffer), types.int(), types.int(), returns=types.none())
    def __init__(self, buffer, offset, size):
        self.readonly = buffer.readonly
        if isinstance(buffer, SubBuffer):     # don't nest them
            # we want a view (offset, size) over a view
            # (buffer.offset, buffer.size) over buffer.buffer.
            # Note that either '.size' can be -1 to mean 'up to the end'.
            at_most = buffer.getlength() - offset
            if size > at_most or size < 0:
                if at_most < 0:
                    at_most = 0
                size = at_most
            offset += buffer.offset
            buffer = buffer.buffer
        #
        self.buffer = buffer
        self.offset = offset
        self.size = size

    def getlength(self):
        at_most = self.buffer.getlength() - self.offset
        if 0 <= self.size <= at_most:
            return self.size
        elif at_most >= 0:
            return at_most
        else:
            return 0

    def as_str_and_offset_maybe(self):
        string, offset = self.buffer.as_str_and_offset_maybe()
        if string is not None:
            return string, offset + self.offset
        return None, 0

    def getitem(self, index):
        return self.buffer.getitem(self.offset + index)

    def getslice(self, start, stop, step, size):
        if start == stop:
            return ''     # otherwise, adding self.offset might make them
                          # out of bounds
        return self.buffer.getslice(self.offset + start, self.offset + stop,
                                    step, size)

    def setitem(self, index, char):
        self.buffer.setitem(self.offset + index, char)

    def setslice(self, start, string):
        if len(string) == 0:
            return        # otherwise, adding self.offset might make 'start'
                          # out of bounds
        self.buffer.setslice(self.offset + start, string)

    def get_raw_address(self):
        from rpython.rtyper.lltypesystem import rffi
        ptr = self.buffer.get_raw_address()
        return rffi.ptradd(ptr, self.offset)
