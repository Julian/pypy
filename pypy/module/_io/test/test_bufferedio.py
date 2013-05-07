from __future__ import with_statement
from pypy.interpreter.gateway import interp2app
from rpython.tool.udir import udir
from pypy.module._io import interp_bufferedio
from pypy.interpreter.error import OperationError
import py.test

class AppTestBufferedReader:
    spaceconfig = dict(usemodules=['_io'])

    def setup_class(cls):
        tmpfile = udir.join('tmpfile')
        tmpfile.write("a\nb\nc", mode='wb')
        cls.w_tmpfile = cls.space.wrap(str(tmpfile))

    def test_simple_read(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.read() == b"a\nb\nc"
        raises(ValueError, f.read, -2)
        f.close()
        #
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        r = f.read(4)
        assert r == b"a\nb\n"
        f.close()

    def test_read_pieces(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.read(3) == b"a\nb"
        assert f.read(3) == b"\nc"
        assert f.read(3) == b""
        assert f.read(3) == b""
        f.close()

    def test_slow_provider(self):
        import _io
        class MockIO(_io._IOBase):
            def readable(self):
                return True
            def readinto(self, buf):
                buf[:3] = b"abc"
                return 3
        bufio = _io.BufferedReader(MockIO())
        r = bufio.read(5)
        assert r == b"abcab"

    def test_read_past_eof(self):
        import _io
        class MockIO(_io._IOBase):
            stack = [b"abc", b"d", b"efg"]
            def readable(self):
                return True
            def readinto(self, buf):
                if self.stack:
                    data = self.stack.pop(0)
                    buf[:len(data)] = data
                    return len(data)
                else:
                    return 0
        bufio = _io.BufferedReader(MockIO())
        assert bufio.read(9000) == b"abcdefg"

    def test_buffering(self):
        import _io
        data = b"abcdefghi"
        dlen = len(data)
        class MockFileIO(_io.BytesIO):
            def __init__(self, data):
                self.read_history = []
                _io.BytesIO.__init__(self, data)

            def read(self, n=None):
                res = _io.BytesIO.read(self, n)
                self.read_history.append(None if res is None else len(res))
                return res

            def readinto(self, b):
                res = _io.BytesIO.readinto(self, b)
                self.read_history.append(res)
                return res


        tests = [
            [ 100, [ 3, 1, 4, 8 ], [ dlen, 0 ] ],
            [ 100, [ 3, 3, 3],     [ dlen ]    ],
            [   4, [ 1, 2, 4, 2 ], [ 4, 4, 1 ] ],
        ]

        for bufsize, buf_read_sizes, raw_read_sizes in tests:
            rawio = MockFileIO(data)
            bufio = _io.BufferedReader(rawio, buffer_size=bufsize)
            pos = 0
            for nbytes in buf_read_sizes:
                assert bufio.read(nbytes) == data[pos:pos+nbytes]
                pos += nbytes
            # this is mildly implementation-dependent
            assert rawio.read_history == raw_read_sizes

    def test_peek(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.read(2) == b'a\n'
        assert f.peek().startswith(b'b\nc')
        assert f.read(3) == b'b\nc'
        assert f.peek() == b''

    def test_read1(self):
        import _io
        class RecordingFileIO(_io.FileIO):
            def read(self, size=-1):
                self.nbreads += 1
                return _io.FileIO.read(self, size)
            def readinto(self, buf):
                self.nbreads += 1
                return _io.FileIO.readinto(self, buf)
        raw = RecordingFileIO(self.tmpfile)
        raw.nbreads = 0
        f = _io.BufferedReader(raw, buffer_size=3)
        assert f.read(1) == b'a'
        assert f.read1(1) == b'\n'
        assert raw.nbreads == 1
        assert f.read1(100) == b'b'
        assert raw.nbreads == 1
        assert f.read1(100) == b'\nc'
        assert raw.nbreads == 2
        assert f.read1(100) == b''
        assert raw.nbreads == 3
        f.close()

    def test_readinto(self):
        import _io
        a = bytearray(b'x' * 10)
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.readinto(a) == 5
        f.close()
        assert a == b'a\nb\ncxxxxx'

    def test_seek(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.read() == b"a\nb\nc"
        f.seek(0)
        assert f.read() == b"a\nb\nc"
        f.seek(-2, 2)
        assert f.read() == b"\nc"
        f.close()

    def test_readlines(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.readlines() == [b'a\n', b'b\n', b'c']

    def test_detach(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert f.fileno() == raw.fileno()
        assert f.detach() is raw
        raises(ValueError, f.fileno)
        raises(ValueError, f.close)
        raises(ValueError, f.detach)
        raises(ValueError, f.flush)
        assert not raw.closed
        raw.close()

    def test_tell(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw, buffer_size=2)
        assert f.tell() == 0
        d1 = f.read(1)
        assert f.tell() == 1
        d2 = f.read(2)
        assert f.tell() == 3
        assert f.seek(0) == 0
        assert f.tell() == 0
        d3 = f.read(3)
        assert f.tell() == 3
        assert d1 + d2 == d3
        f.close()

    def test_repr(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        f = _io.BufferedReader(raw)
        assert repr(f) == '<_io.BufferedReader name=%r>' % (self.tmpfile,)

    def test_read_interrupted(self):
        import _io, errno
        class MockRawIO(_io._RawIOBase):
            def __init__(self):
                self.count = 0
            def readable(self):
                return True
            def readinto(self, buf):
                self.count += 1
                if self.count < 3:
                    raise IOError(errno.EINTR, "interrupted")
                else:
                    buf[:3] = b"abc"
                    return 3
        rawio = MockRawIO()
        bufio = _io.BufferedReader(rawio)
        r = bufio.read(4)
        assert r == b"abca"
        assert rawio.count == 4

    def test_unseekable(self):
        import _io
        class Unseekable(_io.BytesIO):
            def seekable(self):
                return False
            def seek(self, *args):
                raise _io.UnsupportedOperation("not seekable")
            def tell(self, *args):
                raise _io.UnsupportedOperation("not seekable")
        bufio = _io.BufferedReader(Unseekable(b"A" * 10))
        raises(_io.UnsupportedOperation, bufio.tell)
        raises(_io.UnsupportedOperation, bufio.seek, 0)
        bufio.read(1)
        raises(_io.UnsupportedOperation, bufio.seek, 0)
        raises(_io.UnsupportedOperation, bufio.tell)

class AppTestBufferedReaderWithThreads(AppTestBufferedReader):
    spaceconfig = dict(usemodules=['_io', 'thread'])


class AppTestBufferedWriter:
    spaceconfig = dict(usemodules=['_io', 'thread'])

    def setup_class(cls):
        tmpfile = udir.join('tmpfile')
        cls.w_tmpfile = cls.space.wrap(str(tmpfile))

    def w_readfile(self):
        with open(self.tmpfile, 'rb') as f:
            return f.read()

    def test_write(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'w')
        f = _io.BufferedWriter(raw)
        f.write(b"abcd")
        f.close()
        assert self.readfile() == b"abcd"

    def test_largewrite(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'w')
        f = _io.BufferedWriter(raw)
        f.write(b"abcd" * 5000)
        f.close()
        assert self.readfile() == b"abcd" * 5000

    def test_incomplete(self):
        import _io
        raw = _io.FileIO(self.tmpfile)
        b = _io.BufferedWriter.__new__(_io.BufferedWriter)
        raises(IOError, b.__init__, raw) # because file is not writable
        raises(ValueError, getattr, b, 'closed')
        raises(ValueError, b.flush)
        raises(ValueError, b.close)

    def test_check_several_writes(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'w')
        b = _io.BufferedWriter(raw, 13)

        for i in range(4):
            assert b.write(b'x' * 10) == 10
        b.flush()
        assert self.readfile() == b'x' * 40

    def test_destructor(self):
        import _io

        record = []
        class MyIO(_io.BufferedWriter):
            def __del__(self):
                record.append(1)
                super(MyIO, self).__del__()
            def close(self):
                record.append(2)
                super(MyIO, self).close()
            def flush(self):
                record.append(3)
                super(MyIO, self).flush()
        raw = _io.FileIO(self.tmpfile, 'w')
        MyIO(raw)
        import gc; gc.collect()
        assert record == [1, 2, 3]

    def test_truncate(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'w+')
        raw.write(b'x' * 20)
        b = _io.BufferedReader(raw)
        assert b.seek(8) == 8
        assert b.truncate() == 8
        assert b.tell() == 8

    def test_write_non_blocking(self):
        import _io, io
        class MockNonBlockWriterIO(io.RawIOBase):
            def __init__(self):
                self._write_stack = []
                self._blocker_char = None

            def writable(self):
                return True
            closed = False

            def pop_written(self):
                s = b''.join(self._write_stack)
                self._write_stack[:] = []
                return s

            def block_on(self, char):
                """Block when a given char is encountered."""
                self._blocker_char = char

            def write(self, b):
                try:
                    b = b.tobytes()
                except AttributeError:
                    pass
                n = -1
                if self._blocker_char:
                    try:
                        n = b.index(self._blocker_char)
                    except ValueError:
                        pass
                    else:
                        if n > 0:
                            # write data up to the first blocker
                            self._write_stack.append(b[:n])
                            return n
                        else:
                            # cancel blocker and indicate would block
                            self._blocker_char = None
                            return None
                self._write_stack.append(b)
                return len(b)

        raw = MockNonBlockWriterIO()
        bufio = _io.BufferedWriter(raw, 8)

        assert bufio.write(b"abcd") == 4
        assert bufio.write(b"efghi") == 5
        # 1 byte will be written, the rest will be buffered
        raw.block_on(b"k")
        assert bufio.write(b"jklmn") == 5

        # 8 bytes will be written, 8 will be buffered and the rest will be lost
        raw.block_on(b"0")
        try:
            bufio.write(b"opqrwxyz0123456789")
        except _io.BlockingIOError as e:
            written = e.characters_written
        else:
            self.fail("BlockingIOError should have been raised")
        assert written == 16
        assert raw.pop_written() == b"abcdefghijklmnopqrwxyz"

        assert bufio.write(b"ABCDEFGHI") == 9
        s = raw.pop_written()
        # Previously buffered bytes were flushed
        assert s.startswith(b"01234567A")

    def test_nonblock_pipe_write_bigbuf(self):
        self.test_nonblock_pipe_write(16*1024)

    def test_nonblock_pipe_write_smallbuf(self):
        self.test_nonblock_pipe_write(1024)

    def w_test_nonblock_pipe_write(self, bufsize):
        import _io as io
        class NonBlockingPipe(io._BufferedIOBase):
            "write() returns None when buffer is full"
            def __init__(self, buffersize=4096):
                self.buffersize = buffersize
                self.buffer = b''
            def readable(self): return True
            def writable(self): return True

            def write(self, data):
                available = self.buffersize - len(self.buffer)
                if available <= 0:
                    return None
                self.buffer += data[:available]
                return min(len(data), available)
            def read(self, size=-1):
                if not self.buffer:
                    return None
                if size == -1:
                    size = len(self.buffer)
                data = self.buffer[:size]
                self.buffer = self.buffer[size:]
                return data

        sent = []
        received = []
        pipe = NonBlockingPipe()
        rf = io.BufferedReader(pipe, bufsize)
        wf = io.BufferedWriter(pipe, bufsize)

        for N in 9999, 7574:
            try:
                i = 0
                while True:
                    msg = bytes([i % 26 + 97] * N)
                    sent.append(msg)
                    wf.write(msg)
                    i += 1
            except io.BlockingIOError as e:
                sent[-1] = sent[-1][:e.characters_written]
                received.append(rf.read())
                msg = b'BLOCKED'
                wf.write(msg)
                sent.append(msg)
        while True:
            try:
                wf.flush()
                break
            except io.BlockingIOError as e:
                received.append(rf.read())
        received += iter(rf.read, None)
        rf.close()
        wf.close()
        sent, received = b''.join(sent), b''.join(received)
        assert sent == received

    def test_read_non_blocking(self):
        import _io
        class MockRawIO(_io._RawIOBase):
            def __init__(self, read_stack=()):
                self._read_stack = list(read_stack)
            def readable(self):
                return True
            def readinto(self, buf):
                max_len = len(buf)
                try:
                    data = self._read_stack[0]
                except IndexError:
                    self._extraneous_reads += 1
                    return 0
                if data is None:
                    del self._read_stack[0]
                    return None
                n = len(data)
                if len(data) <= max_len:
                    del self._read_stack[0]
                    buf[:n] = data
                    return n
                else:
                    buf[:] = data[:max_len]
                    self._read_stack[0] = data[max_len:]
                    return max_len
            def read(self, n=None):
                try:
                    return self._read_stack.pop(0)
                except IndexError:
                    return b""
        # Inject some None's in there to simulate EWOULDBLOCK
        rawio = MockRawIO((b"abc", b"d", None, b"efg", None, None, None))
        bufio = _io.BufferedReader(rawio)

        assert bufio.read(6) == b"abcd"
        assert bufio.read(1) == b"e"
        assert bufio.read() == b"fg"
        assert bufio.peek(1) == b""
        assert bufio.read() is None
        assert bufio.read() == b""

    def test_write_interrupted(self):
        import _io, errno
        class MockRawIO(_io._RawIOBase):
            def __init__(self):
                self.count = 0
            def writable(self):
                return True
            def write(self, data):
                self.count += 1
                if self.count < 3:
                    raise IOError(errno.EINTR, "interrupted")
                else:
                    return len(data)
        rawio = MockRawIO()
        bufio = _io.BufferedWriter(rawio)
        assert bufio.write(b"test") == 4
        bufio.flush()
        assert rawio.count == 3

    def test_reentrant_write(self):
        import _thread  # Reentrant-safe is only enabled with threads
        import _io, errno
        class MockRawIO(_io._RawIOBase):
            def writable(self):
                return True
            def write(self, data):
                bufio.write(b"something else")
                return len(data)

        rawio = MockRawIO()
        bufio = _io.BufferedWriter(rawio)
        bufio.write(b"test")
        exc = raises(RuntimeError, bufio.flush)
        assert "reentrant" in str(exc.value)  # And not e.g. recursion limit.

class AppTestBufferedRWPair:
    def test_pair(self):
        import _io
        pair = _io.BufferedRWPair(_io.BytesIO(b"abc"), _io.BytesIO())
        assert not pair.closed
        assert pair.readable()
        assert pair.writable()
        assert not pair.isatty()
        assert pair.read() == b"abc"
        assert pair.write(b"abc") == 3

    def test_constructor_with_not_readable(self):
        import _io
        class NotReadable:
            def readable(self):
                return False

        raises(IOError, _io.BufferedRWPair, NotReadable(), _io.BytesIO())

    def test_constructor_with_not_writable(self):
        import _io
        class NotWritable:
            def writable(self):
                return False

        raises(IOError, _io.BufferedRWPair, _io.BytesIO(), NotWritable())

class AppTestBufferedRandom:
    spaceconfig = dict(usemodules=['_io'])

    def setup_class(cls):
        tmpfile = udir.join('tmpfile')
        tmpfile.write(b"a\nb\nc", mode='wb')
        cls.w_tmpfile = cls.space.wrap(str(tmpfile))

    def test_simple_read(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'rb+')
        f = _io.BufferedRandom(raw)
        assert f.read(3) == b'a\nb'
        f.write(b'xxxx')
        f.seek(0)
        assert f.read() == b'a\nbxxxx'

    def test_simple_read_after_write(self):
        import _io
        raw = _io.FileIO(self.tmpfile, 'wb+')
        f = _io.BufferedRandom(raw)
        f.write(b'abc')
        f.seek(0)
        assert f.read() == b'abc'

    def test_write_rewind_write(self):
        # Various combinations of reading / writing / seeking
        # backwards / writing again
        import _io, errno
        def mutate(bufio, pos1, pos2):
            assert pos2 >= pos1
            # Fill the buffer
            bufio.seek(pos1)
            bufio.read(pos2 - pos1)
            bufio.write(b'\x02')
            # This writes earlier than the previous write, but still inside
            # the buffer.
            bufio.seek(pos1)
            bufio.write(b'\x01')

        b = b"\x80\x81\x82\x83\x84"
        for i in range(0, len(b)):
            for j in range(i, len(b)):
                raw = _io.BytesIO(b)
                bufio = _io.BufferedRandom(raw, 100)
                mutate(bufio, i, j)
                bufio.flush()
                expected = bytearray(b)
                expected[j] = 2
                expected[i] = 1
                assert raw.getvalue() == expected
        
    def test_interleaved_read_write(self):
        import _io as io
        # Test for issue #12213
        with io.BytesIO(b'abcdefgh') as raw:
            with io.BufferedRandom(raw, 100) as f:
                f.write(b"1")
                assert f.read(1) == b'b'
                f.write(b'2')
                assert f.read1(1) == b'd'
                f.write(b'3')
                buf = bytearray(1)
                f.readinto(buf)
                assert buf ==  b'f'
                f.write(b'4')
                assert f.peek(1) == b'h'
                f.flush()
                assert raw.getvalue() == b'1b2d3f4h'

        with io.BytesIO(b'abc') as raw:
            with io.BufferedRandom(raw, 100) as f:
                assert f.read(1) == b'a'
                f.write(b"2")
                assert f.read(1) == b'c'
                f.flush()
                assert raw.getvalue() == b'a2c'

    def test_interleaved_readline_write(self):
        import _io as io
        with io.BytesIO(b'ab\ncdef\ng\n') as raw:
            with io.BufferedRandom(raw) as f:
                f.write(b'1')
                assert f.readline() == b'b\n'
                f.write(b'2')
                assert f.readline() == b'def\n'
                f.write(b'3')
                assert f.readline() == b'\n'
                f.flush()
                assert raw.getvalue() == b'1b\n2def\n3\n'

    def test_readline(self):
        import _io as io
        with io.BytesIO(b"abc\ndef\nxyzzy\nfoo\x00bar\nanother line") as raw:
            with io.BufferedRandom(raw, buffer_size=10) as f:
                assert f.readline() == b"abc\n"
                assert f.readline(10) == b"def\n"
                assert f.readline(2) == b"xy"
                assert f.readline(4) == b"zzy\n"
                assert f.readline() == b"foo\x00bar\n"
                assert f.readline(None) == b"another line"
                raises(TypeError, f.readline, 5.3)


class AppTestDeprecation:

    def w_check_max_buffer_size_deprecation(self, test):
        import _io
        import _warnings
        def simplefilter(action, category):
            _warnings.filters.insert(0, (action, None, category, None, 0))
        simplefilter('error', DeprecationWarning)
        try:
            test(_io.BytesIO(), 8, 12)
        except DeprecationWarning as e:
            assert 'max_buffer_size is deprecated' in str(e)
        else:
            assert False, 'Expected DeprecationWarning'
        finally:
            simplefilter('default', DeprecationWarning)

    def test_max_buffer_size_deprecation(self):
        import _io
        self.check_max_buffer_size_deprecation(_io.BufferedWriter)
        self.check_max_buffer_size_deprecation(_io.BufferedRandom)
        self.check_max_buffer_size_deprecation(
            lambda raw, *args: _io.BufferedRWPair(raw, raw, *args))


class TestNonReentrantLock:
    spaceconfig = dict(usemodules=['thread'])

    def test_trylock(self, space):
        lock = interp_bufferedio.TryLock(space)
        with lock:
            pass
        with lock:
            exc = py.test.raises(OperationError, "with lock: pass")
        assert exc.value.match(space, space.w_RuntimeError)
