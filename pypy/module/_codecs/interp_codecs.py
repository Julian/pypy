import sys
from rpython.rlib import jit
from rpython.rlib.objectmodel import we_are_translated, not_rpython
from rpython.rlib.rstring import StringBuilder, UnicodeBuilder
from rpython.rlib import runicode
from rpython.rlib.runicode import (
    code_to_unichr, MAXUNICODE,
    raw_unicode_escape_helper_unicode)
from rpython.rlib.runicode import code_to_unichr, MAXUNICODE

from pypy.interpreter.error import OperationError, oefmt
from pypy.interpreter.gateway import interp2app, unwrap_spec, WrappedDefault
from pypy.interpreter import unicodehelper
from pypy.module.unicodedata import unicodedb


class VersionTag(object):
    pass


class CodecState(object):
    _immutable_fields_ = ["version?"]

    def __init__(self, space):
        self.codec_search_path = []
        self.codec_search_cache = {}
        self.codec_error_registry = {}
        self.codec_need_encodings = True
        self.decode_error_handler = self.make_decode_errorhandler(space)
        self.encode_error_handler = self.make_encode_errorhandler(space)

        self.unicodedata_handler = None
        self.modified()

    def _make_errorhandler(self, space, decode):
        def call_errorhandler(errors, encoding, reason, input, startpos,
                              endpos):
            """Generic wrapper for calling into error handlers.

            Note that error handler receives and returns position into
            the unicode characters, not into the position of utf8 bytes,
            so it needs to be converted by the codec

            Returns (unicode_or_none, str_or_none, newpos) as error
            handlers may return unicode or on Python 3, bytes.
            """
            w_errorhandler = lookup_error(space, errors)
            if decode:
                w_cls = space.w_UnicodeDecodeError
                w_input = space.newbytes(input)
                length = len(input)
            else:
                w_cls = space.w_UnicodeEncodeError
                length = rutf8.check_utf8(input, allow_surrogates=True)
                w_input = space.newutf8(input, length)
            w_exc =  space.call_function(
                w_cls,
                space.newtext(encoding),
                w_input,
                space.newint(startpos),
                space.newint(endpos),
                space.newtext(reason))
            w_res = space.call_function(w_errorhandler, w_exc)
            if (not space.isinstance_w(w_res, space.w_tuple)
                or space.len_w(w_res) != 2
                or not (space.isinstance_w(
                            space.getitem(w_res, space.newint(0)),
                            space.w_unicode) or
                        (not decode and space.isinstance_w(
                            space.getitem(w_res, space.newint(0)),
                            space.w_bytes)))):
                if decode:
                    msg = ("decoding error handler must return "
                           "(str, int) tuple")
                else:
                    msg = ("encoding error handler must return "
                           "(str/bytes, int) tuple")
                raise OperationError(space.w_TypeError, space.newtext(msg))

            w_replace, w_newpos = space.fixedview(w_res, 2)
            try:
                newpos = space.int_w(w_newpos)
            except OperationError as e:
                if not e.match(space, space.w_OverflowError):
                    raise
                newpos = -1
            else:
                if newpos < 0:
                newpos = length + newpos
            if newpos < 0 or newpos > length:
                raise oefmt(space.w_IndexError,
                            "position %d from error handler out of bounds",
                            newpos)
            w_replace = space.convert_to_w_unicode(w_replace)
            return w_replace._utf8, newpos
        return call_errorhandler

    def make_decode_errorhandler(self, space):
        errorhandler = self._make_errorhandler(space, True)
        def decode_call_errorhandler(errors, encoding, reason, input,
                                     startpos, endpos):
            w_replace, newpos = errorhandler(errors, encoding, reason, input,
                                             startpos, endpos)
            return space.unicode_w(w_replace), newpos
        return decode_call_errorhandler

    def make_encode_errorhandler(self, space):
        errorhandler = self._make_errorhandler(space, False)
        def encode_call_errorhandler(errors, encoding, reason, input,
                                     startpos, endpos):
            w_replace, newpos = errorhandler(errors, encoding, reason, input,
                                             startpos, endpos)
            if space.isinstance_w(w_replace, space.w_unicode):
                return space.unicode_w(w_replace), None, newpos
            return None, space.bytes_w(w_replace), newpos
        return encode_call_errorhandler

    def get_unicodedata_handler(self, space):
        if self.unicodedata_handler:
            return self.unicodedata_handler
        try:
            w_unicodedata = space.getbuiltinmodule("unicodedata")
            w_getcode = space.getattr(w_unicodedata, space.newtext("_get_code"))
        except OperationError:
            return None
        else:
            self.unicodedata_handler = UnicodeData_Handler(space, w_getcode)
            return self.unicodedata_handler

    def modified(self):
        self.version = VersionTag()

    def get_codec_from_cache(self, key):
        return self._get_codec_with_version(key, self.version)

    @jit.elidable
    def _get_codec_with_version(self, key, version):
        return self.codec_search_cache.get(key, None)

    def _cleanup_(self):
        assert not self.codec_search_path


def register_codec(space, w_search_function):
    """register(search_function)

    Register a codec search function. Search functions are expected to take
    one argument, the encoding name in all lower case letters, and return
    a tuple of functions (encoder, decoder, stream_reader, stream_writer).
    """
    state = space.fromcache(CodecState)
    if space.is_true(space.callable(w_search_function)):
        state.codec_search_path.append(w_search_function)
    else:
        raise oefmt(space.w_TypeError, "argument must be callable")


@unwrap_spec(encoding='text')
def lookup_codec(space, encoding):
    """lookup(encoding) -> (encoder, decoder, stream_reader, stream_writer)
    Looks up a codec tuple in the Python codec registry and returns
    a tuple of functions.
    """
    assert not (space.config.translating and not we_are_translated()), \
        "lookup_codec() should not be called during translation"
    state = space.fromcache(CodecState)
    normalized_encoding = encoding.replace(" ", "-").lower()
    w_result = state.get_codec_from_cache(normalized_encoding)
    if w_result is not None:
        return w_result
    return _lookup_codec_loop(space, encoding, normalized_encoding)


def _lookup_codec_loop(space, encoding, normalized_encoding):
    state = space.fromcache(CodecState)
    if state.codec_need_encodings:
        # registers new codecs.
        # This import uses the "builtin" import method, and is needed
        # to bootstrap the full importlib module.
        w_import = space.getattr(space.builtin, space.newtext("__import__"))
        space.call_function(w_import, space.newtext("encodings"))
        from pypy.module.sys.interp_encoding import base_encoding
        # May be 'utf-8'
        normalized_base = base_encoding.replace("-", "_").lower()
        space.call_function(w_import, space.newtext("encodings." +
                                                    normalized_base))
        state.codec_need_encodings = False
        if len(state.codec_search_path) == 0:
            raise oefmt(space.w_LookupError,
                        "no codec search functions registered: can't find "
                        "encoding")
    for w_search in state.codec_search_path:
        w_result = space.call_function(w_search,
                                       space.newtext(normalized_encoding))
        if not space.is_w(w_result, space.w_None):
            if not (space.isinstance_w(w_result, space.w_tuple) and
                    space.len_w(w_result) == 4):
                raise oefmt(space.w_TypeError,
                            "codec search functions must return 4-tuples")
            else:
                state.codec_search_cache[normalized_encoding] = w_result
                state.modified()
                return w_result
    raise oefmt(space.w_LookupError, "unknown encoding: %s", encoding)

# ____________________________________________________________
# Register standard error handlers

def check_exception(space, w_exc):
    try:
        w_start = space.getattr(w_exc, space.newtext('start'))
        w_end = space.getattr(w_exc, space.newtext('end'))
        w_obj = space.getattr(w_exc, space.newtext('object'))
    except OperationError as e:
        if not e.match(space, space.w_AttributeError):
            raise
        raise oefmt(space.w_TypeError, "wrong exception")

    delta = space.int_w(w_end) - space.int_w(w_start)
    if delta < 0 or not (space.isinstance_w(w_obj, space.w_bytes) or
                         space.isinstance_w(w_obj, space.w_unicode)):
        raise oefmt(space.w_TypeError, "wrong exception")

def strict_errors(space, w_exc):
    check_exception(space, w_exc)
    if space.isinstance_w(w_exc, space.w_BaseException):
        raise OperationError(space.type(w_exc), w_exc)
    else:
        raise oefmt(space.w_TypeError, "codec must pass exception instance")

def ignore_errors(space, w_exc):
    check_exception(space, w_exc)
    w_end = space.getattr(w_exc, space.newtext('end'))
    return space.newtuple([space.newutf8('', 0), w_end])

REPLACEMENT = u'\ufffd'.encode('utf8')

def replace_errors(space, w_exc):
    check_exception(space, w_exc)
    w_start = space.getattr(w_exc, space.newtext('start'))
    w_end = space.getattr(w_exc, space.newtext('end'))
    size = space.int_w(w_end) - space.int_w(w_start)
    if space.isinstance_w(w_exc, space.w_UnicodeEncodeError):
        text = '?' * size
        return space.newtuple([space.newutf8(text, size), w_end])
    elif space.isinstance_w(w_exc, space.w_UnicodeDecodeError):
        text = REPLACEMENT
        return space.newtuple([space.newutf8(text, 1), w_end])
    elif space.isinstance_w(w_exc, space.w_UnicodeTranslateError):
        text = REPLACEMENT * size
        return space.newtuple([space.newutf8(text, size), w_end])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)

def xmlcharrefreplace_errors(space, w_exc):
    from pypy.interpreter import unicodehelper

    check_exception(space, w_exc)
    if space.isinstance_w(w_exc, space.w_UnicodeEncodeError):
        w_obj = space.getattr(w_exc, space.newtext('object'))
        space.realutf8_w(w_obj) # weeoes
        w_obj = unicodehelper.convert_arg_to_w_unicode(space, w_obj)
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        end = space.int_w(w_end)
        start = w_obj._index_to_byte(start)
        end = w_obj._index_to_byte(end)
        builder = StringBuilder()
        pos = start
        obj = w_obj._utf8
        while pos < end:
            code = rutf8.codepoint_at_pos(obj, pos)
            builder.append("&#")
            builder.append(str(code))
            builder.append(";")
            pos = rutf8.next_codepoint_pos(obj, pos)
        r = builder.build()
        lgt = rutf8.check_utf8(r, True)
        return space.newtuple([space.newutf8(r, lgt), w_end])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)

def backslashreplace_errors(space, w_exc):
    from pypy.interpreter import unicodehelper

    check_exception(space, w_exc)
    if (space.isinstance_w(w_exc, space.w_UnicodeEncodeError) or
        space.isinstance_w(w_exc, space.w_UnicodeTranslateError)):
        obj = space.realunicode_w(space.getattr(w_exc, space.newtext('object')))
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        end = space.int_w(w_end)
        builder = UnicodeBuilder()
        pos = start
        while pos < end:
            oc = ord(obj[pos])
            raw_unicode_escape_helper_unicode(builder, oc)
            pos += 1
        return space.newtuple([space.newunicode(builder.build()), w_end])
    elif space.isinstance_w(w_exc, space.w_UnicodeDecodeError):
        obj = space.bytes_w(space.getattr(w_exc, space.newtext('object')))
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        end = space.int_w(w_end)
        builder = UnicodeBuilder()
        pos = start
        while pos < end:
            oc = ord(obj[pos])
            raw_unicode_escape_helper_unicode(builder, oc)
            pos += 1
        return space.newtuple([space.newunicode(builder.build()), w_end])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)

def namereplace_errors(space, w_exc):
    check_exception(space, w_exc)
    if space.isinstance_w(w_exc, space.w_UnicodeEncodeError):
        w_obj = space.getattr(w_exc, space.newtext('object'))
        space.realutf8_w(w_obj) # for errors
        w_obj = unicodehelper.convert_arg_to_w_unicode(space, w_obj)
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        end = space.int_w(w_end)
        start = w_obj._index_to_byte(start)
        end = w_obj._index_to_byte(end)
        builder = StringBuilder()
        obj = w_obj._utf8
        pos = start
        while pos < end:
            oc = rutf8.codepoint_at_pos(obj, pos)
            try:
                name = unicodedb.name(oc)
            except KeyError:
                raw_unicode_escape_helper_unicode(builder, oc)
            else:
                builder.append(u'\\N{')
                builder.append(unicode(name))
                builder.append(u'}')
            pos = rutf8.next_codepoint_pos(obj, pos)
        r = builder.build()
        lgt = rutf8.check_utf8(r, True)
        return space.newtuple([space.newutf8(r, lgt), w_end])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)


(ENC_UNKNOWN, ENC_UTF8,
 ENC_UTF16BE, ENC_UTF16LE,
 ENC_UTF32BE, ENC_UTF32LE) = range(-1, 5)
BIG_ENDIAN = sys.byteorder == 'big'

STANDARD_ENCODINGS = {
    'utf8':      (3, ENC_UTF8),
    'utf_8':     (3, ENC_UTF8),
    'cp_utf8':   (3, ENC_UTF8),
    'utf16':     (2, ENC_UTF16BE) if BIG_ENDIAN else (2, ENC_UTF16LE),
    'utf_16':    (2, ENC_UTF16BE) if BIG_ENDIAN else (2, ENC_UTF16LE),
    'utf16be':   (2, ENC_UTF16BE),
    'utf_16be':  (2, ENC_UTF16BE),
    'utf16_be':  (2, ENC_UTF16BE),
    'utf_16_be': (2, ENC_UTF16BE),
    'utf16le':   (2, ENC_UTF16LE),
    'utf_16le':  (2, ENC_UTF16LE),
    'utf16_le':  (2, ENC_UTF16LE),
    'utf_16_le': (2, ENC_UTF16LE),
    'utf32':     (4, ENC_UTF32BE) if BIG_ENDIAN else (4, ENC_UTF32LE),
    'utf_32':    (4, ENC_UTF32BE) if BIG_ENDIAN else (4, ENC_UTF32LE),
    'utf32be':   (4, ENC_UTF32BE),
    'utf_32be':  (4, ENC_UTF32BE),
    'utf32_be':  (4, ENC_UTF32BE),
    'utf_32_be': (4, ENC_UTF32BE),
    'utf32le':   (4, ENC_UTF32LE),
    'utf_32le':  (4, ENC_UTF32LE),
    'utf32_le':  (4, ENC_UTF32LE),
    'utf_32_le': (4, ENC_UTF32LE),
}

def get_standard_encoding(encoding):
    encoding = encoding.lower().replace('-', '_')
    return STANDARD_ENCODINGS.get(encoding, (0, ENC_UNKNOWN))

def surrogatepass_errors(space, w_exc):
    check_exception(space, w_exc)
    if space.isinstance_w(w_exc, space.w_UnicodeEncodeError):
        obj = space.realunicode_w(space.getattr(w_exc, space.newtext('object')))
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        encoding = space.text_w(space.getattr(w_exc, space.newtext('encoding')))
        bytelength, code = get_standard_encoding(encoding)
        if code == ENC_UNKNOWN:
            # Not supported, fail with original exception
            raise OperationError(space.type(w_exc), w_exc)
        end = space.int_w(w_end)
        builder = StringBuilder()
        pos = start
        while pos < end:
            ch = ord(obj[pos])
            pos += 1
            if ch < 0xd800 or ch > 0xdfff:
                # Not a surrogate, fail with original exception
                raise OperationError(space.type(w_exc), w_exc)
            if code == ENC_UTF8:
                builder.append(chr(0xe0 | (ch >> 12)))
                builder.append(chr(0x80 | ((ch >> 6) & 0x3f)))
                builder.append(chr(0x80 | (ch & 0x3f)))
            elif code == ENC_UTF16LE:
                builder.append(chr(ch & 0xff))
                builder.append(chr(ch >> 8))
            elif code == ENC_UTF16BE:
                builder.append(chr(ch >> 8))
                builder.append(chr(ch & 0xff))
            elif code == ENC_UTF32LE:
                builder.append(chr(ch & 0xff))
                builder.append(chr(ch >> 8))
                builder.append(chr(0))
                builder.append(chr(0))
            elif code == ENC_UTF32BE:
                builder.append(chr(0))
                builder.append(chr(0))
                builder.append(chr(ch >> 8))
                builder.append(chr(ch & 0xff))
        return space.newtuple([space.newbytes(builder.build()), w_end])
    elif space.isinstance_w(w_exc, space.w_UnicodeDecodeError):
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        obj = space.bytes_w(space.getattr(w_exc, space.newtext('object')))
        encoding = space.text_w(space.getattr(w_exc, space.newtext('encoding')))
        bytelength, code = get_standard_encoding(encoding)
        ch = 0
        # Try decoding a single surrogate character. If there are more,
        # let the codec call us again
        ch0 = ord(obj[start + 0]) if len(obj) > start + 0 else -1
        ch1 = ord(obj[start + 1]) if len(obj) > start + 1 else -1
        ch2 = ord(obj[start + 2]) if len(obj) > start + 2 else -1
        ch3 = ord(obj[start + 3]) if len(obj) > start + 3 else -1
        if code == ENC_UTF8:
            if (ch1 != -1 and ch2 != -1 and
                ch0 & 0xf0 == 0xe0 and
                ch1 & 0xc0 == 0x80 and
                ch2 & 0xc0 == 0x80):
                # it's a three-byte code
                ch = ((ch0 & 0x0f) << 12) + ((ch1 & 0x3f) << 6) + (ch2 & 0x3f)
        elif code == ENC_UTF16LE:
            ch = (ch1 << 8) | ch0
        elif code == ENC_UTF16BE:
            ch = (ch0 << 8) | ch1
        elif code == ENC_UTF32LE:
            ch = (ch3 << 24) | (ch2 << 16) | (ch1 << 8) | ch0
        elif code == ENC_UTF32BE:
            ch = (ch0 << 24) | (ch1 << 16) | (ch2 << 8) | ch3
        if ch < 0xd800 or ch > 0xdfff:
            # it's not a surrogate - fail
            ch = 0
        if ch == 0:
            raise OperationError(space.type(w_exc), w_exc)
        return space.newtuple([space.newunicode(unichr(ch)),
                               space.newint(start + bytelength)])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)

def surrogateescape_errors(space, w_exc):
    check_exception(space, w_exc)
    if space.isinstance_w(w_exc, space.w_UnicodeEncodeError):
        obj = space.realunicode_w(space.getattr(w_exc, space.newtext('object')))
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        w_end = space.getattr(w_exc, space.newtext('end'))
        end = space.int_w(w_end)
        res = ''
        pos = start
        while pos < end:
            ch = ord(obj[pos])
            pos += 1
            if ch < 0xdc80 or ch > 0xdcff:
                # Not a UTF-8b surrogate, fail with original exception
                raise OperationError(space.type(w_exc), w_exc)
            res += chr(ch - 0xdc00)
        return space.newtuple([space.newbytes(res), w_end])
    elif space.isinstance_w(w_exc, space.w_UnicodeDecodeError):
        consumed = 0
        start = space.int_w(space.getattr(w_exc, space.newtext('start')))
        end = space.int_w(space.getattr(w_exc, space.newtext('end')))
        obj = space.bytes_w(space.getattr(w_exc, space.newtext('object')))
        replace = u''
        while consumed < 4 and consumed < end - start:
            c = ord(obj[start+consumed])
            if c < 128:
                # Refuse to escape ASCII bytes.
                break
            replace += unichr(0xdc00 + c)
            consumed += 1
        if not consumed:
            # codec complained about ASCII byte.
            raise OperationError(space.type(w_exc), w_exc)
        return space.newtuple([space.newunicode(replace),
                               space.newint(start + consumed)])
    else:
        raise oefmt(space.w_TypeError,
                    "don't know how to handle %T in error callback", w_exc)

@not_rpython
def register_builtin_error_handlers(space):
    state = space.fromcache(CodecState)
    for error in ("strict", "ignore", "replace", "xmlcharrefreplace",
                  "backslashreplace", "surrogateescape", "surrogatepass",
                  "namereplace"):
        name = error + "_errors"
        state.codec_error_registry[error] = interp2app(
                globals()[name]).spacebind(space)


def _wrap_codec_error(space, operr, action, encoding):
    # Note that UnicodeErrors are not wrapped and returned as is,
    # "thanks to" a limitation of try_set_from_cause.
    message = "%s with '%s' codec failed" % (action, encoding)
    return operr.try_set_from_cause(space, message)

def _call_codec(space, w_decoder, w_obj, action, encoding, errors):
    try:
        w_res = space.call_function(w_decoder, w_obj, space.newtext(errors))
    except OperationError as operr:
        raise _wrap_codec_error(space, operr, action, encoding)
    if (not space.isinstance_w(w_res, space.w_tuple) or space.len_w(w_res) != 2):
        raise oefmt(space.w_TypeError,
                    "encoder must return a tuple (object, integer)")
    return space.getitem(w_res, space.newint(0))

@unwrap_spec(errors='text')
def lookup_error(space, errors):
    """lookup_error(errors) -> handler

    Return the error handler for the specified error handling name
    or raise a LookupError, if no handler exists under this name.
    """

    state = space.fromcache(CodecState)
    try:
        w_err_handler = state.codec_error_registry[errors]
    except KeyError:
        raise oefmt(space.w_LookupError,
                    "unknown error handler name %s", errors)
    return w_err_handler


@unwrap_spec(errors='text')
def encode(space, w_obj, w_encoding=None, errors='strict'):
    """encode(obj, [encoding[,errors]]) -> object

    Encodes obj using the codec registered for encoding. encoding defaults
    to the default encoding. errors may be given to set a different error
    handling scheme. Default is 'strict' meaning that encoding errors raise
    a ValueError. Other possible values are 'ignore', 'replace' and
    'xmlcharrefreplace' as well as any other name registered with
    codecs.register_error that can handle ValueErrors.
    """
    if w_encoding is None:
        encoding = space.sys.defaultencoding
    else:
        encoding = space.text_w(w_encoding)
    w_encoder = space.getitem(lookup_codec(space, encoding), space.newint(0))
    return _call_codec(space, w_encoder, w_obj, "encoding", encoding, errors)

@unwrap_spec(errors='text_or_none')
def readbuffer_encode(space, w_data, errors='strict'):
    s = space.getarg_w('s#', w_data)
    return space.newtuple([space.newbytes(s), space.newint(len(s))])

@unwrap_spec(errors='text')
def decode(space, w_obj, w_encoding=None, errors='strict'):
    """decode(obj, [encoding[,errors]]) -> object

    Decodes obj using the codec registered for encoding. encoding defaults
    to the default encoding. errors may be given to set a different error
    handling scheme. Default is 'strict' meaning that encoding errors raise
    a ValueError. Other possible values are 'ignore' and 'replace'
    as well as any other name registered with codecs.register_error that is
    able to handle ValueErrors.
    """
    if w_encoding is None:
        encoding = space.sys.defaultencoding
    else:
        encoding = space.text_w(w_encoding)
    w_decoder = space.getitem(lookup_codec(space, encoding), space.newint(1))
    return _call_codec(space, w_decoder, w_obj, "decoding", encoding, errors)

@unwrap_spec(errors='text')
def register_error(space, errors, w_handler):
    """register_error(errors, handler)

    Register the specified error handler under the name
    errors. handler must be a callable object, that
    will be called with an exception instance containing
    information about the location of the encoding/decoding
    error and must return a (replacement, new position) tuple.
    """
    state = space.fromcache(CodecState)
    if space.is_true(space.callable(w_handler)):
        state.codec_error_registry[errors] = w_handler
    else:
        raise oefmt(space.w_TypeError, "handler must be callable")

# ____________________________________________________________
# Helpers for unicode.encode() and bytes.decode()
def lookup_text_codec(space, action, encoding):
    codec_info = lookup_codec(space, encoding)
    try:
        is_text_encoding = space.is_true(
                space.getattr(codec_info, space.newtext('_is_text_encoding')))
    except OperationError as e:
        if e.match(space, space.w_AttributeError):
            is_text_encoding = True
        else:
            raise
    if not is_text_encoding:
        raise oefmt(space.w_LookupError,
                    "'%s' is not a text encoding; "
                    "use %s to handle arbitrary codecs", encoding, action)
    return codec_info

def encode_text(space, w_obj, encoding, errors):
    if errors is None:
        errors = 'strict'
    w_encoder = space.getitem(
        lookup_text_codec(space, "codecs.encode()", encoding), space.newint(0))
    return _call_codec(space, w_encoder, w_obj, "encoding", encoding, errors)

def decode_text(space, w_obj, encoding, errors):
    if errors is None:
        errors = 'strict'
    w_decoder = space.getitem(
        lookup_text_codec(space, "codecs.decode()", encoding), space.newint(1))
    return _call_codec(space, w_decoder, w_obj, "decoding", encoding, errors)

# ____________________________________________________________
# delegation to runicode/unicodehelper

def _find_implementation(impl_name):
    func = getattr(unicodehelper, impl_name)
    return func

def make_encoder_wrapper(name):
    rname = "utf8_encode_%s" % (name.replace("_encode", ""), )
    func = _find_implementation(rname)
    @unwrap_spec(errors='text_or_none')
    def wrap_encoder(space, w_arg, errors="strict"):
        from pypy.interpreter import unicodehelper

        w_arg = unicodehelper.convert_arg_to_w_unicode(space, w_arg, rname)
        if errors is None:
            errors = 'strict'
        state = space.fromcache(CodecState)
        utf8len = w_arg._length
        # XXX deal with func() returning length or not
        result = func(w_arg._utf8, errors, state.encode_error_handler)
        return space.newtuple([space.newbytes(result), space.newint(utf8len)])
    wrap_encoder.__name__ = func.__name__
    globals()[name] = wrap_encoder

def make_utf_encoder_wrapper(name):
    rname = "unicode_encode_%s" % (name.replace("_encode", ""), )
    func = _find_implementation(rname)
    @unwrap_spec(uni=unicode, errors='text_or_none')
    def wrap_encoder(space, uni, errors="strict"):
        if errors is None:
            errors = 'strict'
        state = space.fromcache(CodecState)
        result = func(uni, len(uni), errors, state.encode_error_handler,
                      allow_surrogates=False)
        return space.newtuple([space.newbytes(result), space.newint(len(uni))])
    wrap_encoder.__name__ = func.__name__
    globals()[name] = wrap_encoder

def make_decoder_wrapper(name):
    rname = "str_decode_%s" % (name.replace("_decode", ""), )
    func = _find_implementation(rname)
    @unwrap_spec(string='bufferstr', errors='text_or_none',
                 w_final=WrappedDefault(False))
    def wrap_decoder(space, string, errors="strict", w_final=None):
        from pypy.interpreter import unicodehelper

        if errors is None:
            errors = 'strict'
        final = space.is_true(w_final)
        state = space.fromcache(CodecState)
        result, consumed, length = func(string, errors,
                                              final, state.decode_error_handler)
        return space.newtuple([space.newutf8(result, length),
                               space.newint(consumed)])
    wrap_decoder.__name__ = func.__name__
    globals()[name] = wrap_decoder

for encoder in [
         "ascii_encode",
         "latin_1_encode",
         "utf_7_encode",
         "utf_16_encode",
         "utf_16_be_encode",
         "utf_16_le_encode",
         "utf_32_encode",
         "utf_32_be_encode",
         "utf_32_le_encode",
         "unicode_escape_encode",
         "raw_unicode_escape_encode",
         "unicode_internal_encode",
        ]:
    make_encoder_wrapper(encoder)

for decoder in [
         "ascii_decode",
         "latin_1_decode",
         "utf_7_decode",
         "utf_16_decode",
         "utf_16_be_decode",
         "utf_16_le_decode",
         "utf_32_decode",
         "utf_32_be_decode",
         "utf_32_le_decode",
         "raw_unicode_escape_decode",
         ]:
    make_decoder_wrapper(decoder)

if hasattr(runicode, 'str_decode_mbcs'):
    # mbcs functions are not regular, because we have to pass
    # "force_ignore/replace=False"
    @unwrap_spec(uni=unicode, errors='text_or_none')
    def mbcs_encode(space, uni, errors="strict"):
        if errors is None:
            errors = 'strict'
        state = space.fromcache(CodecState)
        result = runicode.unicode_encode_mbcs(
            uni, len(uni), errors, state.encode_error_handler,
            force_replace=False)
        return space.newtuple([space.newbytes(result), space.newint(len(uni))])

    @unwrap_spec(string='bufferstr', errors='text_or_none',
                 w_final=WrappedDefault(False))
    def mbcs_decode(space, string, errors="strict", w_final=None):
        if errors is None:
            errors = 'strict'
        final = space.is_true(w_final)
        state = space.fromcache(CodecState)
        result, consumed = runicode.str_decode_mbcs(
            string, len(string), errors,
            final, state.decode_error_handler,
            force_ignore=False)
        return space.newtuple([space.newunicode(result), space.newint(consumed)])

# utf-8 functions are not regular, because we have to pass
# "allow_surrogates=False"
@unwrap_spec(uni=unicode, errors='text_or_none')
def utf_8_encode(space, uni, errors="strict"):
    if errors is None:
        errors = 'strict'
    state = space.fromcache(CodecState)
    # NB. can't call unicode_encode_utf_8() directly because that's
    # an @elidable function nowadays.  Instead, we need the _impl().
    # (The problem is the errorhandler, which calls arbitrary Python.)
    result = runicode.unicode_encode_utf_8_impl(
        uni, len(uni), errors, state.encode_error_handler,
        allow_surrogates=False)
    return space.newtuple([space.newbytes(result), space.newint(len(uni))])

@unwrap_spec(string='bufferstr', errors='text_or_none',
             w_final = WrappedDefault(False))
def utf_8_decode(space, string, errors="strict", w_final=None):
    from pypy.interpreter import unicodehelper

    if errors is None:
        errors = 'strict'
    final = space.is_true(w_final)
    state = space.fromcache(CodecState)
    # call the fast version for checking
    try:
        lgt = rutf8.check_utf8(string, allow_surrogates=True)
    except rutf8.CheckError:
        res, consumed, lgt = unicodehelper.str_decode_utf8(string,
            errors, final, state.decode_error_handler)
        return space.newtuple([space.newutf8(res, lgt),
                               space.newint(consumed)])
    else:
        return space.newtuple([space.newutf8(string, lgt),
                               space.newint(len(string))])

@unwrap_spec(data='bufferstr', errors='text_or_none', byteorder=int,
             w_final=WrappedDefault(False))
def utf_16_ex_decode(space, data, errors='strict', byteorder=0, w_final=None):
    from pypy.interpreter.unicodehelper import str_decode_utf_16_helper

    if errors is None:
        errors = 'strict'
    final = space.is_true(w_final)
    state = space.fromcache(CodecState)
    if byteorder == 0:
        byteorder = 'native'
    elif byteorder == -1:
        byteorder = 'little'
    else:
        byteorder = 'big'
    consumed = len(data)
    if final:
        consumed = 0
    res, consumed, lgt, byteorder = str_decode_utf_16_helper(
        data, errors, final,
        state.decode_error_handler, byteorder)
    return space.newtuple([space.newutf8(res, lgt),
                           space.newint(consumed),
                           space.newint(byteorder)])

@unwrap_spec(data='bufferstr', errors='text_or_none', byteorder=int,
             w_final=WrappedDefault(False))
def utf_32_ex_decode(space, data, errors='strict', byteorder=0, w_final=None):
    from pypy.interpreter.unicodehelper import str_decode_utf_32_helper

    final = space.is_true(w_final)
    state = space.fromcache(CodecState)
    if byteorder == 0:
        byteorder = 'native'
    elif byteorder == -1:
        byteorder = 'little'
    else:
        byteorder = 'big'
    consumed = len(data)
    if final:
        consumed = 0
    res, consumed, lgt, byteorder = str_decode_utf_32_helper(
        data, errors, final,
        state.decode_error_handler, byteorder)
    return space.newtuple([space.newutf8(res, lgt),
                           space.newint(consumed),
                           space.newint(byteorder)])

# ____________________________________________________________
# Charmap

class Charmap_Decode:
    def __init__(self, space, w_mapping):
        self.space = space
        self.w_mapping = w_mapping

        # fast path for all the stuff in the encodings module
        if space.isinstance_w(w_mapping, space.w_tuple):
            self.mapping_w = space.fixedview(w_mapping)
        else:
            self.mapping_w = None

    def get(self, ch, errorchar):
        space = self.space

        # get the character from the mapping
        if self.mapping_w is not None:
            w_ch = self.mapping_w[ch]
        else:
            try:
                w_ch = space.getitem(self.w_mapping, space.newint(ch))
            except OperationError as e:
                if not e.match(space, space.w_LookupError):
                    raise
                return errorchar

        if space.isinstance_w(w_ch, space.w_unicode):
            # Charmap may return a unicode string
            return space.utf8_w(w_ch)
        elif space.isinstance_w(w_ch, space.w_int):
            # Charmap may return a number
            x = space.int_w(w_ch)
            if not 0 <= x <= 0x10FFFF:
                raise oefmt(space.w_TypeError,
                    "character mapping must be in range(0x110000)")
            return rutf8.unichr_as_utf8(x)
        elif space.is_w(w_ch, space.w_None):
            # Charmap may return None
            return errorchar

        raise oefmt(space.w_TypeError,
            "character mapping must return integer, None or str")

class Charmap_Encode:
    def __init__(self, space, w_mapping):
        self.space = space
        self.w_mapping = w_mapping

    def get(self, ch, errorchar):
        space = self.space

        # get the character from the mapping
        try:
            w_ch = space.getitem(self.w_mapping, space.newint(ch))
        except OperationError as e:
            if not e.match(space, space.w_LookupError):
                raise
            return errorchar

        if space.isinstance_w(w_ch, space.w_bytes):
            # Charmap may return a string
            return space.bytes_w(w_ch)
        elif space.isinstance_w(w_ch, space.w_int):
            # Charmap may return a number
            x = space.int_w(w_ch)
            if not 0 <= x < 256:
                raise oefmt(space.w_TypeError,
                    "character mapping must be in range(256)")
            return chr(x)
        elif space.is_w(w_ch, space.w_None):
            # Charmap may return None
            return errorchar

        raise oefmt(space.w_TypeError,
            "character mapping must return integer, bytes or None, not str")


@unwrap_spec(string='bufferstr', errors='text_or_none')
def charmap_decode(space, string, errors="strict", w_mapping=None):
    from pypy.interpreter import unicodehelper

    if errors is None:
        errors = 'strict'
    if len(string) == 0:
        return space.newtuple([space.newutf8('', 0),
                               space.newint(0)])

    if space.is_none(w_mapping):
        mapping = None
    else:
        mapping = Charmap_Decode(space, w_mapping)

    final = True
    state = space.fromcache(CodecState)
    result, consumed, lgt = unicodehelper.str_decode_charmap(
        string, errors, final, state.decode_error_handler, mapping)
    return space.newtuple([space.newutf8(result, lgt),
                           space.newint(consumed)])

@unwrap_spec(errors='text_or_none')
def charmap_encode(space, w_unicode, errors="strict", w_mapping=None):
    from pypy.interpreter import unicodehelper

    if errors is None:
        errors = 'strict'
    if space.is_none(w_mapping):
        mapping = None
    else:
        mapping = Charmap_Encode(space, w_mapping)

    state = space.fromcache(CodecState)
    w_uni = unicodehelper.convert_arg_to_w_unicode(space, w_unicode)
    result = unicodehelper.utf8_encode_charmap(
        space.utf8_w(w_uni), errors, state.encode_error_handler, mapping)
    return space.newtuple([space.newbytes(result), space.newint(w_uni._len())])


@unwrap_spec(chars='utf8')
def charmap_build(space, chars):
    # XXX CPython sometimes uses a three-level trie
    w_charmap = space.newdict()
    pos = 0
    num = 0
    while pos < len(chars):
        w_char = space.newint(rutf8.codepoint_at_pos(chars, pos))
        space.setitem(w_charmap, w_char, space.newint(num))
        pos = rutf8.next_codepoint_pos(chars, pos)
        num += 1
    return w_charmap

# ____________________________________________________________
# Unicode escape

class UnicodeData_Handler:
    def __init__(self, space, w_getcode):
        self.space = space
        self.w_getcode = w_getcode

    def call(self, name):
        space = self.space
        try:
            w_code = space.call_function(self.w_getcode, space.newtext(name))
        except OperationError as e:
            if not e.match(space, space.w_KeyError):
                raise
            return -1
        return space.int_w(w_code)

@unwrap_spec(errors='text_or_none', w_final=WrappedDefault(False))
def unicode_escape_decode(space, w_string, errors="strict", w_final=None):
    string = space.getarg_w('s*', w_string).as_str()
    from pypy.interpreter import unicodehelper

    if errors is None:
        errors = 'strict'
    final = space.is_true(w_final)
    state = space.fromcache(CodecState)

    unicode_name_handler = state.get_unicodedata_handler(space)

    result, consumed, lgt = unicodehelper.str_decode_unicode_escape(
        string, errors,
        final, state.decode_error_handler,
        unicode_name_handler)

    return space.newtuple([space.newutf8(result, lgt), space.newint(consumed)])

# ____________________________________________________________
# Raw Unicode escape (accepts bytes or str)

@unwrap_spec(errors='text_or_none', w_final=WrappedDefault(False))
def raw_unicode_escape_decode(space, w_string, errors="strict", w_final=None):
    string = space.getarg_w('s*', w_string).as_str()
    if errors is None:
        errors = 'strict'
    final = space.is_true(w_final)
    state = space.fromcache(CodecState)
    result, consumed = runicode.str_decode_raw_unicode_escape(
        string, len(string), errors,
        final, state.decode_error_handler)
    return space.newtuple([space.newunicode(result), space.newint(consumed)])

# ____________________________________________________________
# Unicode-internal

@unwrap_spec(errors='text_or_none')
def unicode_internal_decode(space, w_string, errors="strict"):
    from pypy.interpreter import unicodehelper

    if errors is None:
        errors = 'strict'
    # special case for this codec: unicodes are returned as is
    if space.isinstance_w(w_string, space.w_unicode):
        return space.newtuple([w_string, space.len(w_string)])

    string = space.charbuf_w(w_string)
    space.warn(space.newtext("unicode_internal codec has been deprecated"),
               space.w_DeprecationWarning)

    if len(string) == 0:
        return space.newtuple([space.newutf8('', 0),
                               space.newint(0)])

    final = True
    state = space.fromcache(CodecState)
    result, consumed, lgt = unicodehelper.str_decode_unicode_internal(
        string, errors,
        final, state.decode_error_handler)
    return space.newtuple([space.newutf8(result, lgt),
                           space.newint(consumed)])

@unwrap_spec(errors='text_or_none')
def unicode_internal_encode(space, w_uni, errors="strict"):
    space.warn(space.newtext("unicode_internal codec has been deprecated"),
               space.w_DeprecationWarning)
    if errors is None:
        errors = 'strict'
    if space.isinstance_w(w_uni, space.w_unicode):
        uni = space.unicode_w(w_uni)
        state = space.fromcache(CodecState)
        result = runicode.unicode_encode_unicode_internal(
            uni, len(uni), errors, state.encode_error_handler)
        return space.newtuple([space.newbytes(result), space.newint(len(uni))])
    else:
        # special case for this codec: bytes are returned as is
        string = space.charbuf_w(w_uni)
        return space.newtuple([space.newbytes(string), space.newint(len(string))])

# ____________________________________________________________
# support for the "string escape" translation
# This is a bytes-to bytes transformation

@unwrap_spec(data='bytes', errors='text_or_none')
def escape_encode(space, data, errors='strict'):
    from pypy.objspace.std.bytesobject import string_escape_encode
    result = string_escape_encode(data, False)
    return space.newtuple([space.newbytes(result), space.newint(len(data))])

@unwrap_spec(errors='text_or_none')
def escape_decode(space, w_data, errors='strict'):
    data = space.getarg_w('s#', w_data)
    from pypy.interpreter.pyparser.parsestring import PyString_DecodeEscape
    result = PyString_DecodeEscape(space, data, errors, None)
    return space.newtuple([space.newbytes(result), space.newint(len(data))])
