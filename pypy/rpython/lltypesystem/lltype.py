import py
from pypy.rpython.rarithmetic import r_int, r_uint, intmask
from pypy.rpython.rarithmetic import r_ulonglong, r_longlong, base_int
from pypy.rpython.rarithmetic import normalizedinttype
from pypy.rpython.objectmodel import Symbolic
from pypy.tool.uid import Hashable
from pypy.tool.tls import tlsobject
from pypy.tool.picklesupport import getstate_with_slots, setstate_with_slots, pickleable_weakref
from types import NoneType
from sys import maxint
import weakref

log = py.log.Producer('lltype')

TLS = tlsobject()

def saferecursive(func, defl):
    def safe(*args):
        try:
            seeing = TLS.seeing
        except AttributeError:
            seeing = TLS.seeing = {}
        seeingkey = tuple([func] + [id(arg) for arg in args])
        if seeingkey in seeing:
            return defl
        seeing[seeingkey] = True
        try:
            return func(*args)
        finally:
            del seeing[seeingkey]
    return safe

#safe_equal = saferecursive(operator.eq, True)
def safe_equal(x, y):
    # a specialized version for performance
    try:
        seeing = TLS.seeing_eq
    except AttributeError:
        seeing = TLS.seeing_eq = {}
    seeingkey = (id(x), id(y))
    if seeingkey in seeing:
        return True
    seeing[seeingkey] = True
    try:
        return x == y
    finally:
        del seeing[seeingkey]


class frozendict(dict):

    def __hash__(self):
        items = self.items()
        items.sort()
        return hash(tuple(items))


class LowLevelType(object):
    # the following line prevents '__cached_hash' to be in the __dict__ of
    # the instance, which is needed for __eq__() and __hash__() to work.
    __slots__ = ['__dict__', '__cached_hash']

    def __eq__(self, other):
        return self.__class__ is other.__class__ and (
            self is other or safe_equal(self.__dict__, other.__dict__))

    def __ne__(self, other):
        return not (self == other)

    _is_compatible = __eq__

    def _enforce(self, value):
        if typeOf(value) != self:
            raise TypeError
        return value

    def __hash__(self):
        # cannot use saferecursive() -- see test_lltype.test_hash().
        # NB. the __cached_hash should neither be used nor updated
        # if we enter with hash_level > 0, because the computed
        # __hash__ can be different in this situation.
        hash_level = 0
        try:
            hash_level = TLS.nested_hash_level
            if hash_level == 0:
                return self.__cached_hash
        except AttributeError:
            pass
        if hash_level >= 3:
            return 0
        items = self.__dict__.items()
        items.sort()
        TLS.nested_hash_level = hash_level + 1
        try:
            result = hash((self.__class__,) + tuple(items))
        finally:
            TLS.nested_hash_level = hash_level
        if hash_level == 0:
            self.__cached_hash = result
        return result

    # due to this dynamic hash value, we should forbid
    # pickling, until we have an algorithm for that.
    # but we just provide a tag for external help.
    __hash_is_not_constant__ = True

    def __repr__(self):
        return '<%s>' % (self,)

    def __str__(self):
        return self.__class__.__name__

    def _short_name(self):
        return str(self)

    def _defl(self, parent=None, parentindex=None):
        raise NotImplementedError

    def _freeze_(self):
        return True

    def _inline_is_varsize(self, last):
        return False

    def _is_atomic(self):
        return False

    def _is_varsize(self):
        return False

    __getstate__ = getstate_with_slots
    __setstate__ = setstate_with_slots

NFOUND = object()

class ContainerType(LowLevelType):
    _adtmeths = {}

    def _gcstatus(self):
        return isinstance(self, GC_CONTAINER)

    def _inline_is_varsize(self, last):
        raise TypeError, "%r cannot be inlined in structure" % self

    def _install_extras(self, adtmeths={}, hints={}):
        self._adtmeths = frozendict(adtmeths)
        self._hints = frozendict(hints)

    def __getattr__(self, name):
        adtmeth = self._adtmeths.get(name, NFOUND)
        if adtmeth is not NFOUND:
            if getattr(adtmeth, '_type_method', False):
                return adtmeth.__get__(self)
            else:
                return adtmeth
        self._nofield(name)

    def _nofield(self, name):
        raise AttributeError("no field %r" % name)
        

class Struct(ContainerType):
    def __init__(self, name, *fields, **kwds):
        self._name = self.__name__ = name
        flds = {}
        names = []
        self._arrayfld = None
        for name, typ in fields:
            if name.startswith('_'):
                raise NameError, ("%s: field name %r should not start with "
                                  "an underscore" % (self._name, name,))
            names.append(name)
            if name in flds:
                raise TypeError("%s: repeated field name" % self._name)
            flds[name] = typ
            if isinstance(typ, GC_CONTAINER):
                if name == fields[0][0] and isinstance(self, GC_CONTAINER):
                    pass  # can inline a GC_CONTAINER as 1st field of GcStruct
                else:
                    raise TypeError("%s: cannot inline GC container %r" % (
                        self._name, typ))

        # look if we have an inlined variable-sized array as the last field
        if fields:
            for name, typ in fields[:-1]:
                typ._inline_is_varsize(False)
                first = False
            name, typ = fields[-1]
            if typ._inline_is_varsize(True):
                self._arrayfld = name
        self._flds = frozendict(flds)
        self._names = tuple(names)

        self._install_extras(**kwds)

    def _first_struct(self):
        if self._names:
            first = self._names[0]
            FIRSTTYPE = self._flds[first]
            if isinstance(FIRSTTYPE, Struct) and self._gcstatus() == FIRSTTYPE._gcstatus():
                return first, FIRSTTYPE
        return None, None

    def _inline_is_varsize(self, last):
        if self._arrayfld:
            raise TypeError("cannot inline a var-sized struct "
                            "inside another container")
        return False

    def _is_atomic(self):
        for typ in self._flds.values():
            if not typ._is_atomic():
                return False
        return True

    def _is_varsize(self):
        return self._arrayfld is not None

    def __getattr__(self, name):
        try:
            return self._flds[name]
        except KeyError:
            return ContainerType.__getattr__(self, name)


    def _nofield(self, name):
        raise AttributeError, 'struct %s has no field %r' % (self._name,
                                                             name)

    def _names_without_voids(self):
        names_without_voids = [name for name in self._names if self._flds[name] is not Void]
        return names_without_voids
    
    def _str_fields_without_voids(self):
        return ', '.join(['%s: %s' % (name, self._flds[name])
                          for name in self._names_without_voids(False)])
    _str_fields_without_voids = saferecursive(_str_fields_without_voids, '...')

    def _str_without_voids(self):
        return "%s %s { %s }" % (self.__class__.__name__,
                                 self._name, self._str_fields_without_voids())

    def _str_fields(self):
        return ', '.join(['%s: %s' % (name, self._flds[name])
                          for name in self._names])
    _str_fields = saferecursive(_str_fields, '...')

    def __str__(self):
        return "%s %s { %s }" % (self.__class__.__name__,
                                 self._name, self._str_fields())

    def _short_name(self):
        return "%s %s" % (self.__class__.__name__, self._name)

    def _defl(self, parent=None, parentindex=None):
        return _struct(self, parent=parent, parentindex=parentindex)

    def _container_example(self):
        if self._arrayfld is None:
            n = None
        else:
            n = 1
        return _struct(self, n)

class GcStruct(Struct):
    _runtime_type_info = None

    def _attach_runtime_type_info_funcptr(self, funcptr, destrptr):
        if self._runtime_type_info is None:
            self._runtime_type_info = opaqueptr(RuntimeTypeInfo, name=self._name, about=self)._obj
        if funcptr is not None:
            T = typeOf(funcptr)
            if (not isinstance(T, Ptr) or
                not isinstance(T.TO, FuncType) or
                len(T.TO.ARGS) != 1 or
                T.TO.RESULT != Ptr(RuntimeTypeInfo) or
                castable(T.TO.ARGS[0], Ptr(self)) < 0):
                raise TypeError("expected a runtime type info function "
                                "implementation, got: %s" % funcptr)
            self._runtime_type_info.query_funcptr = funcptr
        if destrptr is not None :
            T = typeOf(destrptr)
            if (not isinstance(T, Ptr) or
                not isinstance(T.TO, FuncType) or
                len(T.TO.ARGS) != 1 or
                T.TO.RESULT != Void or
                castable(T.TO.ARGS[0], Ptr(self)) < 0):
                raise TypeError("expected a destructor function "
                                "implementation, got: %s" % destrptr)
            self._runtime_type_info.destructor_funcptr = destrptr
           

class Array(ContainerType):
    __name__ = 'array'
    _anonym_struct = False
    
    def __init__(self, *fields, **kwds):
        if len(fields) == 1 and isinstance(fields[0], LowLevelType):
            self.OF = fields[0]
        else:
            self.OF = Struct("<arrayitem>", *fields)
            self._anonym_struct = True
        if isinstance(self.OF, GC_CONTAINER):
            raise TypeError("cannot have a GC container as array item type")
        self.OF._inline_is_varsize(False)

        self._install_extras(**kwds)

    def _inline_is_varsize(self, last):
        if not last:
            raise TypeError("cannot inline an array in another container"
                            " unless as the last field of a structure")
        return True

    def _is_atomic(self):
        return self.OF._is_atomic()

    def _is_varsize(self):
        return True

    def _str_fields(self):
        if isinstance(self.OF, Struct):
            of = self.OF
            if self._anonym_struct:
                return "{ %s }" % of._str_fields()
            else:
                return "%s { %s }" % (of._name, of._str_fields())
        else:
            return str(self.OF)
    _str_fields = saferecursive(_str_fields, '...')

    def __str__(self):
        return "%s of %s " % (self.__class__.__name__,
                               self._str_fields(),)

    def _short_name(self):
        return "%s %s" % (self.__class__.__name__,
                          self.OF._short_name(),)
    _short_name = saferecursive(_short_name, '...')

    def _container_example(self):
        return _array(self, 1)

class GcArray(Array):
    def _inline_is_varsize(self, last):
        raise TypeError("cannot inline a GC array inside a structure")


class FixedSizeArray(Struct):
    # behaves more or less like a Struct with fields item0, item1, ...
    # but also supports __getitem__(), __setitem__(), __len__().

    def __init__(self, OF, length, **kwds):
        fields = [('item%d' % i, OF) for i in range(length)]
        super(FixedSizeArray, self).__init__('array%d' % length, *fields,
                                             **kwds)
        self.OF = OF
        self.length = length
        if isinstance(self.OF, GC_CONTAINER):
            raise TypeError("cannot have a GC container as array item type")
        self.OF._inline_is_varsize(False)

    def _str_fields(self):
        return str(self.OF)
    _str_fields = saferecursive(_str_fields, '...')

    def __str__(self):
        return "%s of %d %s " % (self.__class__.__name__,
                                 self.length,
                                 self._str_fields(),)

    def _short_name(self):
        return "%s %d %s" % (self.__class__.__name__,
                             self.length,
                             self.OF._short_name(),)
    _short_name = saferecursive(_short_name, '...')


class FuncType(ContainerType):
    __name__ = 'func'
    def __init__(self, args, result):
        for arg in args:
            assert isinstance(arg, LowLevelType)
            # -- disabled the following check for the benefits of rctypes --
            #if isinstance(arg, ContainerType):
            #    raise TypeError, "function arguments can only be primitives or pointers"
        self.ARGS = tuple(args)
        assert isinstance(result, LowLevelType)
        if isinstance(result, ContainerType):
            raise TypeError, "function result can only be primitive or pointer"
        self.RESULT = result

    def __str__(self):
        args = ', '.join(map(str, self.ARGS))
        return "Func ( %s ) -> %s" % (args, self.RESULT)
    __str__ = saferecursive(__str__, '...')

    def _short_name(self):        
        args = ', '.join([ARG._short_name() for ARG in self.ARGS])
        return "Func(%s)->%s" % (args, self.RESULT._short_name())        
    _short_name = saferecursive(_short_name, '...')

    def _container_example(self):
        def ex(*args):
            return self.RESULT._defl()
        return _func(self, _callable=ex)

    def _trueargs(self):
        return [arg for arg in self.ARGS if arg is not Void]


class OpaqueType(ContainerType):
    
    def __init__(self, tag):
        self.tag = tag
        self.__name__ = tag

    def __str__(self):
        return "%s (opaque)" % self.tag

    def _inline_is_varsize(self, last):
        return False    # OpaqueType can be inlined

    def _container_example(self):
        return _opaque(self)

    def _defl(self, parent=None, parentindex=None):
        return _opaque(self, parent=parent, parentindex=parentindex)

RuntimeTypeInfo = OpaqueType("RuntimeTypeInfo")

class GcOpaqueType(OpaqueType):

    def __str__(self):
        return "%s (gcopaque)" % self.tag

    def _inline_is_varsize(self, last):
        raise TypeError, "%r cannot be inlined in structure" % self

class PyObjectType(ContainerType):
    __name__ = 'PyObject'
    def __str__(self):
        return "PyObject"
PyObject = PyObjectType()

class ForwardReference(ContainerType):
    def become(self, realcontainertype):
        if not isinstance(realcontainertype, ContainerType):
            raise TypeError("ForwardReference can only be to a container, "
                            "not %r" % (realcontainertype,))
        self.__class__ = realcontainertype.__class__
        self.__dict__ = realcontainertype.__dict__

    def __hash__(self):
        raise TypeError("%r object is not hashable" % self.__class__.__name__)

class GcForwardReference(ForwardReference):
    def become(self, realcontainertype):
        if not isinstance(realcontainertype, GC_CONTAINER):
            raise TypeError("GcForwardReference can only be to GcStruct or "
                            "GcArray, not %r" % (realcontainertype,))
        self.__class__ = realcontainertype.__class__
        self.__dict__ = realcontainertype.__dict__

GC_CONTAINER = (GcStruct, GcArray, PyObjectType, GcForwardReference,
                GcOpaqueType)


class Primitive(LowLevelType):
    def __init__(self, name, default):
        self._name = self.__name__ = name
        self._default = default

    def __str__(self):
        return self._name

    def _defl(self, parent=None, parentindex=None):
        return self._default

    def _is_atomic(self):
        return True

    _example = _defl

class Number(Primitive):

    def __init__(self, name, type, cast=None):
        Primitive.__init__(self, name, type())
        self._type = type
        if cast is None:
            self._cast = type
        else:
            self._cast = cast

    def normalized(self):
        return build_number(None, normalizedinttype(self._type))
        

_numbertypes = {int: Number("Signed", int, intmask)}
_numbertypes[r_int] = _numbertypes[int]

def build_number(name, type):
    try:
        return _numbertypes[type]
    except KeyError:
        pass
    if name is None:
        raise ValueError('No matching lowlevel type for %r'%type)
    number = _numbertypes[type] = Number(name, type)
    return number

Signed   = build_number("Signed", int)
Unsigned = build_number("Unsigned", r_uint)
SignedLongLong = build_number("SignedLongLong", r_longlong)
UnsignedLongLong = build_number("UnsignedLongLong", r_ulonglong)

Float    = Primitive("Float", 0.0)
Char     = Primitive("Char", '\x00')
Bool     = Primitive("Bool", False)
Void     = Primitive("Void", None)
UniChar  = Primitive("UniChar", u'\x00')


class Ptr(LowLevelType):
    __name__ = property(lambda self: '%sPtr' % self.TO.__name__)

    def __init__(self, TO):
        if not isinstance(TO, ContainerType):
            raise TypeError, ("can only point to a Container type, "
                              "not to %s" % (TO,))
        self.TO = TO

    def _needsgc(self):
        return self.TO._gcstatus()

    def __str__(self):
        return '* %s' % (self.TO, )
    
    def _short_name(self):
        return 'Ptr %s' % (self.TO._short_name(), )
    
    def _is_atomic(self):
        return not self.TO._gcstatus()

    def _defl(self, parent=None, parentindex=None):
        return _ptr(self, None)

    def _example(self):
        o = self.TO._container_example()
        return _ptr(self, o, solid=True)


# ____________________________________________________________


def typeOf(val):
    try:
        return val._TYPE
    except AttributeError:
        tp = type(val)
        if tp is NoneType:
            return Void   # maybe
        if tp is int:
            return Signed
        if tp is bool:
            return Bool
        if issubclass(tp, base_int):
            return build_number(None, tp)
        if tp is float:
            return Float
        if tp is str:
            assert len(val) == 1
            return Char
        if tp is unicode:
            assert len(val) == 1
            return UniChar
        if issubclass(tp, Symbolic):
            return val.lltype()
        raise TypeError("typeOf(%r object)" % (tp.__name__,))

_to_primitive = {
    Char: chr,
    UniChar: unichr,
    Float: float,
    Bool: bool,
}

def cast_primitive(TGT, value):
    ORIG = typeOf(value)
    if not isinstance(TGT, Primitive) or not isinstance(ORIG, Primitive):
        raise TypeError, "can only primitive to primitive"
    if ORIG == TGT:
        return value
    if ORIG == Char or ORIG == UniChar:
        value = ord(value)
    elif ORIG == Float:
        value = long(value)
    cast = _to_primitive.get(TGT)
    if cast is not None:
        return cast(value)
    if isinstance(TGT, Number):
        return TGT._cast(value)
    raise TypeError, "unsupported cast"

def _cast_whatever(TGT, value):
    from pypy.rpython.lltypesystem import llmemory
    ORIG = typeOf(value)
    if ORIG == TGT:
        return value
    if (isinstance(TGT, Primitive) and
        isinstance(ORIG, Primitive)):
        return cast_primitive(TGT, value)
    elif isinstance(TGT, Ptr):
        if isinstance(ORIG, Ptr):
            if (isinstance(TGT.TO, OpaqueType) or
                isinstance(ORIG.TO, OpaqueType)):
                return cast_opaque_ptr(TGT, value)
            else:
                return cast_pointer(TGT, value)
        elif ORIG == llmemory.Address:
            return llmemory.cast_adr_to_ptr(value, TGT)
    elif TGT == llmemory.Address and isinstance(ORIG, Ptr):
        return llmemory.cast_ptr_to_adr(value)
    raise TypeError("don't know how to cast from %r to %r" % (ORIG, TGT))


class InvalidCast(TypeError):
    pass

def _castdepth(OUTSIDE, INSIDE):
    if OUTSIDE == INSIDE:
        return 0
    dwn = 0
    while True:
        first, FIRSTTYPE = OUTSIDE._first_struct()
        if first is None:
            return -1
        dwn += 1
        if FIRSTTYPE == INSIDE:
            return dwn
        OUTSIDE = getattr(OUTSIDE, first)
 
def castable(PTRTYPE, CURTYPE):
    if CURTYPE._needsgc() != PTRTYPE._needsgc():
        raise TypeError("cast_pointer() cannot change the gc status: %s to %s"
                        % (CURTYPE, PTRTYPE))
    if CURTYPE == PTRTYPE:
        return 0
    if (not isinstance(CURTYPE.TO, Struct) or
        not isinstance(PTRTYPE.TO, Struct)):
        raise InvalidCast(CURTYPE, PTRTYPE)
    CURSTRUC = CURTYPE.TO
    PTRSTRUC = PTRTYPE.TO
    d = _castdepth(CURSTRUC, PTRSTRUC)
    if d >= 0:
        return d
    u = _castdepth(PTRSTRUC, CURSTRUC)
    if u == -1:
        raise InvalidCast(CURTYPE, PTRTYPE)
    return -u

def cast_pointer(PTRTYPE, ptr):
    CURTYPE = typeOf(ptr)
    if not isinstance(CURTYPE, Ptr) or not isinstance(PTRTYPE, Ptr):
        raise TypeError, "can only cast pointers to other pointers"
    return ptr._cast_to(PTRTYPE)

def cast_opaque_ptr(PTRTYPE, ptr):
    CURTYPE = typeOf(ptr)
    if not isinstance(CURTYPE, Ptr) or not isinstance(PTRTYPE, Ptr):
        raise TypeError, "can only cast pointers to other pointers"
    if CURTYPE._needsgc() != PTRTYPE._needsgc():
        raise TypeError("cast_opaque_ptr() cannot change the gc status: "
                        "%s to %s" % (CURTYPE, PTRTYPE))
    if (isinstance(CURTYPE.TO, OpaqueType)
        and not isinstance(PTRTYPE.TO, OpaqueType)):
        if not ptr:
            return nullptr(PTRTYPE.TO)
        try:
            container = ptr._obj.container
        except AttributeError:
            raise RuntimeError("%r does not come from a container" % (ptr,))
        if typeOf(container) != PTRTYPE.TO:
            raise RuntimeError("%r contains a container of the wrong type:\n"
                               "%r instead of %r" % (ptr, typeOf(container),
                                                     PTRTYPE.TO))
        solid = getattr(ptr._obj, 'solid', False)
        return _ptr(PTRTYPE, container, solid)
    elif (not isinstance(CURTYPE.TO, OpaqueType)
          and isinstance(PTRTYPE.TO, OpaqueType)):
        if not ptr:
            return nullptr(PTRTYPE.TO)
        return opaqueptr(PTRTYPE.TO, 'hidden', container = ptr._obj,
                                               solid     = ptr._solid)
    elif (isinstance(CURTYPE.TO, OpaqueType)
          and isinstance(PTRTYPE.TO, OpaqueType)):
        if not ptr:
            return nullptr(PTRTYPE.TO)
        try:
            container = ptr._obj.container
        except AttributeError:
            raise RuntimeError("%r does not come from a container" % (ptr,))
        return opaqueptr(PTRTYPE.TO, 'hidden',
                         container = container,
                         solid     = ptr._obj.solid)
    else:
        raise TypeError("invalid cast_opaque_ptr(): %r -> %r" %
                        (CURTYPE, PTRTYPE))

def direct_fieldptr(structptr, fieldname):
    """Get a pointer to a field in the struct.  The resulting
    pointer is actually of type Ptr(FixedSizeArray(FIELD, 1)).
    It can be used in a regular getarrayitem(0) or setarrayitem(0)
    to read or write to the field.
    """
    CURTYPE = typeOf(structptr).TO
    if not isinstance(CURTYPE, Struct):
        raise TypeError, "direct_fieldptr: not a struct"
    if fieldname not in CURTYPE._flds:
        raise TypeError, "%s has no field %r" % (CURTYPE, fieldname)
    if not structptr:
        raise RuntimeError("direct_fieldptr: NULL argument")
    return _subarray._makeptr(structptr._obj, fieldname)

def direct_arrayitems(arrayptr):
    """Get a pointer to the first item of the array.  The resulting
    pointer is actually of type Ptr(FixedSizeArray(ITEM, 1)) but can
    be used in a regular getarrayitem(n) or direct_ptradd(n) to access
    further elements.
    """
    CURTYPE = typeOf(arrayptr).TO
    if not isinstance(CURTYPE, (Array, FixedSizeArray)):
        raise TypeError, "direct_arrayitems: not an array"
    if not arrayptr:
        raise RuntimeError("direct_arrayitems: NULL argument")
    return _subarray._makeptr(arrayptr._obj, 0)

def direct_ptradd(ptr, n):
    """Shift a pointer forward or backward by n items.  The pointer must
    have been built by direct_arrayitems().
    """
    if not ptr:
        raise RuntimeError("direct_ptradd: NULL argument")
    if not isinstance(ptr._obj, _subarray):
        raise TypeError("direct_ptradd: only for direct_arrayitems() ptrs")
    parent, base = parentlink(ptr._obj)
    return _subarray._makeptr(parent, base + n)

def _expose(val, solid=False):
    """XXX A nice docstring here"""
    T = typeOf(val)
    if isinstance(T, ContainerType):
        val = _ptr(Ptr(T), val, solid=solid)
    return val

def parentlink(container):
    parent = container._parentstructure()
    if parent is not None:
        return parent, container._parent_index
##        if isinstance(parent, _struct):
##            for name in parent._TYPE._names:
##                if getattr(parent, name) is container:
##                    return parent, name
##            raise RuntimeError("lost ourselves")
##        if isinstance(parent, _array):
##            raise TypeError("cannot fish a pointer to an array item or an "
##                            "inlined substructure of it")
##        raise AssertionError("don't know about %r" % (parent,))
    else:
        return None, None

def top_container(container):
    top_parent = container
    while True:
        parent = top_parent._parentstructure()
        if parent is None:
            break
        top_parent = parent
    return top_parent

def normalizeptr(p):
    # If p is a pointer, returns the same pointer casted to the largest
    # containing structure (for the cast where p points to the header part).
    # Also un-hides pointers to opaque.  Null pointers become None.
    assert not isinstance(p, _container)  # pointer or primitive
    T = typeOf(p)
    if not isinstance(T, Ptr):
        return p      # primitive
    if not p:
        return None   # null pointer
    # - if p is an opaque pointer containing a normal Struct/GcStruct,
    #   unwrap it now
    if isinstance(T.TO, OpaqueType) and hasattr(p._obj, 'container'):
        T = Ptr(typeOf(p._obj.container))
        p = cast_opaque_ptr(T, p)
    # - if p points to the first inlined substructure of a structure,
    #   make it point to the whole (larger) structure instead
    container = p._obj
    while True:
        parent, index = parentlink(container)
        if parent is None:
            break
        T = typeOf(parent)
        if not isinstance(T, Struct) or T._first_struct()[0] != index:
            break
        container = parent
    if container is not p._obj:
        p = _ptr(Ptr(T), container, p._solid)
    return p


class _ptr(object):
    __slots__ = ('_TYPE', '_T', 
                 '_weak', '_solid',
                 '_obj0', '__weakref__')

    def _set_TYPE(self, TYPE):
        _ptr._TYPE.__set__(self, TYPE)

    def _set_T(self, T):
        _ptr._T.__set__(self, T)

    def _set_weak(self, weak):
        _ptr._weak.__set__(self, weak)

    def _set_solid(self, solid):
        _ptr._solid.__set__(self, solid)

    def _set_obj0(self, obj):
        _ptr._obj0.__set__(self, obj)

    def _needsgc(self):
        return self._TYPE._needsgc() # xxx other rules?

    def __init__(self, TYPE, pointing_to, solid=False):
        self._set_TYPE(TYPE)
        self._set_T(TYPE.TO)
        self._set_weak(False)
        self._setobj(pointing_to, solid)

    def _become(self, other):
        assert self._TYPE == other._TYPE
        assert not self._weak
        self._setobj(other._obj, other._solid)

    def __eq__(self, other):
        if not isinstance(other, _ptr):
            raise TypeError("comparing pointer with %r object" % (
                type(other).__name__,))
        if self._TYPE != other._TYPE:
            raise TypeError("comparing %r and %r" % (self._TYPE, other._TYPE))
        return self._obj == other._obj

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        raise TypeError("pointer objects are not hashable")

    def __nonzero__(self):
        return self._obj is not None

    # _setobj, _getobj and _obj0 are really _internal_ implementations details of _ptr,
    # use _obj if necessary instead !
    def _setobj(self, pointing_to, solid=False):        
        if pointing_to is None:
            obj0 = None
        elif solid or isinstance(self._T, (GC_CONTAINER, FuncType)):
            obj0 = pointing_to
        else:
            self._set_weak(True)
            obj0 = pickleable_weakref(pointing_to)
        self._set_solid(solid)
        self._set_obj0(obj0)
        
    def _getobj(self):
        obj = self._obj0
        if obj is not None:
            if self._weak:
                obj = obj()
                if obj is None:
                    raise RuntimeError("accessing already garbage collected %r"
                                   % (self._T,))
            if not isinstance(obj, int):
                obj._check()
        return obj
    _obj = property(_getobj)

    def __getattr__(self, field_name): # ! can only return basic or ptr !
        if isinstance(self._T, Struct):
            if field_name in self._T._flds:
                o = getattr(self._obj, field_name)
                return _expose(o, self._solid)
        if isinstance(self._T, ContainerType):
            try:
                adtmeth = self._T._adtmeths[field_name]
            except KeyError:
                pass
            else:
                try:
                    getter = adtmeth.__get__
                except AttributeError:
                    return adtmeth
                else:
                    return getter(self)
        raise AttributeError("%r instance has no field %r" % (self._T,
                                                              field_name))

    #def _setfirst(self, p):
    #    if isinstance(self._T, Struct) and self._T._names:
    #        if not isinstance(p, _ptr) or not isinstance(p._obj, _struct):
    #            raise InvalidCast(typeOf(p), typeOf(self))
    #        field_name = self._T._names[0]
    #        T1 = self._T._flds[field_name]
    #        T2 = typeOf(p._obj)
    #        if T1 != T2:
    #            raise InvalidCast(typeOf(p), typeOf(self))
    #        setattr(self._obj, field_name, p._obj)
    #        p._obj._setparentstructure(self._obj, 0)
    #        return
    #    raise TypeError("%r instance has no first field" % (self._T,))

    def __setattr__(self, field_name, val):
        if isinstance(self._T, Struct):
            if field_name in self._T._flds:
                T1 = self._T._flds[field_name]
                T2 = typeOf(val)
                if T1 == T2:
                    setattr(self._obj, field_name, val)
                else:
                    raise TypeError("%r instance field %r:\n"
                                    "expects %r\n"
                                    "    got %r" % (self._T, field_name, T1, T2))
                return
        raise AttributeError("%r instance has no field %r" % (self._T,
                                                              field_name))

    def __getitem__(self, i): # ! can only return basic or ptr !
        if isinstance(self._T, (Array, FixedSizeArray)):
            start, stop = self._obj.getbounds()
            if not (start <= i < stop):
                raise IndexError("array index out of bounds")
            o = self._obj.getitem(i)
            return _expose(o, self._solid)
        raise TypeError("%r instance is not an array" % (self._T,))

    def __setitem__(self, i, val):
        if isinstance(self._T, (Array, FixedSizeArray)):
            T1 = self._T.OF
            if isinstance(T1, ContainerType):
                raise TypeError("cannot directly assign to container array items")
            T2 = typeOf(val)
            if T2 != T1:
                    raise TypeError("%r items:\n"
                                    "expect %r\n"
                                    "   got %r" % (self._T, T1, T2))
            start, stop = self._obj.getbounds()
            if not (start <= i < stop):
                raise IndexError("array index out of bounds")
            self._obj.setitem(i, val)
            return
        raise TypeError("%r instance is not an array" % (self._T,))

    def __len__(self):
        if isinstance(self._T, (Array, FixedSizeArray)):
            if self._T._hints.get('nolength', False):
                raise TypeError("%r instance has no length attribute" %
                                    (self._T,))
            return self._obj.getlength()
        raise TypeError("%r instance is not an array" % (self._T,))

    def __iter__(self):
        # this is a work-around for the 'isrpystring' hack in __getitem__,
        # which otherwise causes list(p) to include the extra \x00 character.
        for i in range(len(self)):
            yield self[i]

    def __repr__(self):
        return '<%s>' % (self,)

    def __str__(self):
        try:
            return '* %s' % (self._obj, )
        except RuntimeError:
            return '* DEAD %s' % self._T

    def __call__(self, *args):
        if isinstance(self._T, FuncType):
            if len(args) != len(self._T.ARGS):
                raise TypeError,"calling %r with wrong argument number: %r" % (self._T, args)
            for a, ARG in zip(args, self._T.ARGS):
                if typeOf(a) != ARG:
                    raise TypeError,"calling %r with wrong argument types: %r" % (self._T, args)
            callb = self._obj._callable
            if callb is None:
                raise RuntimeError,"calling undefined function"
            return callb(*args)
        raise TypeError("%r instance is not a function" % (self._T,))

    __getstate__ = getstate_with_slots
    __setstate__ = setstate_with_slots

    def _cast_to(self, PTRTYPE):
        CURTYPE = self._TYPE
        down_or_up = castable(PTRTYPE, CURTYPE)
        if down_or_up == 0:
            return self
        if not self: # null pointer cast
            return PTRTYPE._defl()
        if isinstance(self._obj, int):
            return _ptr(PTRTYPE, self._obj, solid=True)
        if down_or_up > 0:
            p = self
            while down_or_up:
                p = getattr(p, typeOf(p).TO._names[0])
                down_or_up -= 1
            return _ptr(PTRTYPE, p._obj, solid=self._solid)
        u = -down_or_up
        struc = self._obj
        while u:
            parent = struc._parentstructure()
            if parent is None:
                raise RuntimeError("widening to trash: %r" % self)
            PARENTTYPE = struc._parent_type
            if getattr(parent, PARENTTYPE._names[0]) is not struc:
                raise InvalidCast(CURTYPE, PTRTYPE) # xxx different exception perhaps?
            struc = parent
            u -= 1
        if PARENTTYPE != PTRTYPE.TO:
            raise TypeError("widening %r inside %r instead of %r" % (CURTYPE, PARENTTYPE, PTRTYPE.TO))
        return _ptr(PTRTYPE, struc, solid=self._solid)

    def _cast_to_int(self):
        obj = self._obj
        if isinstance(obj, int):
            return obj     # special case for cast_int_to_ptr() results
        obj = top_container(obj)
        result = intmask(id(obj))
        # assume that id() returns an addressish value which is
        # not zero and aligned to at least a multiple of 4
        assert result != 0 and (result & 3) == 0
        return result

    def _cast_to_adr(self):
        from pypy.rpython.lltypesystem import llmemory
        if isinstance(self._obj, _subarray):
            # return an address built as an offset in the whole array
            parent, parentindex = parentlink(self._obj)
            T = typeOf(parent)
            addr = llmemory.fakeaddress(normalizeptr(_ptr(Ptr(T), parent)))
            addr += llmemory.itemoffsetof(T, parentindex)
            return addr
        else:
            # normal case
            return llmemory.fakeaddress(normalizeptr(self))

    def _as_ptr(self):
        return self
    def _as_obj(self):
        return self._obj

assert not '__dict__' in dir(_ptr)

class _container(object):
    __slots__ = ()
    def _parentstructure(self):
        return None
    def _check(self):
        pass
    def _as_ptr(self):
        return _ptr(Ptr(self._TYPE), self, True)
    def _as_obj(self):
        return self

class _parentable(_container):
    _kind = "?"

    __slots__ = ('_TYPE',
                 '_parent_type', '_parent_index', '_keepparent',
                 '_wrparent',
                 '__weakref__',
                 '_dead')

    def __init__(self, TYPE):
        self._wrparent = None
        self._TYPE = TYPE
        self._dead = False

    def _free(self):
        self._dead = True

    def _setparentstructure(self, parent, parentindex):
        self._wrparent = pickleable_weakref(parent)
        self._parent_type = typeOf(parent)
        self._parent_index = parentindex
        if (isinstance(self._parent_type, Struct)
            and parentindex == self._parent_type._names[0]
            and self._TYPE._gcstatus() == typeOf(parent)._gcstatus()):
            # keep strong reference to parent, we share the same allocation
            self._keepparent = parent 

    def _parentstructure(self):
        if self._wrparent is not None:
            parent = self._wrparent()
            if parent is None:
                raise RuntimeError("accessing sub%s %r,\n"
                                   "but already garbage collected parent %r"
                                   % (self._kind, self, self._parent_type))
            parent._check()
            return parent
        return None

    def _check(self):
        if self._dead:
            raise RuntimeError("accessing freed %r" % self._TYPE)
        self._parentstructure()

    __getstate__ = getstate_with_slots
    __setstate__ = setstate_with_slots

def _struct_variety(flds, cache={}):
    flds = list(flds)
    flds.sort()
    tag = tuple(flds)
    try:
        return cache[tag]
    except KeyError:
        class _struct1(_struct):
            __slots__ = flds
        cache[tag] = _struct1
        return _struct1
 
#for pickling support:
def _get_empty_instance_of_struct_variety(flds):
    cls = _struct_variety(flds)
    return object.__new__(cls)

class _struct(_parentable):
    _kind = "structure"

    __slots__ = ()

    def __new__(self, TYPE, n=None, parent=None, parentindex=None):
        my_variety = _struct_variety(TYPE._names)
        return object.__new__(my_variety)

    def __init__(self, TYPE, n=None, parent=None, parentindex=None):
        _parentable.__init__(self, TYPE)
        if n is not None and TYPE._arrayfld is None:
            raise TypeError("%r is not variable-sized" % (TYPE,))
        if n is None and TYPE._arrayfld is not None:
            raise TypeError("%r is variable-sized" % (TYPE,))
        for fld, typ in TYPE._flds.items():
            if fld == TYPE._arrayfld:
                value = _array(typ, n, parent=self, parentindex=fld)
            else:
                value = typ._defl(parent=self, parentindex=fld)
            setattr(self, fld, value)
        if parent is not None:
            self._setparentstructure(parent, parentindex)

    def __repr__(self):
        return '<%s>' % (self,)

    def _str_fields(self):
        fields = []
        names = self._TYPE._names
        if len(names) > 10:
            names = names[:5] + names[-1:]
            skipped_after = 5
        else:
            skipped_after = None
        for name in names:
            T = self._TYPE._flds[name]
            if isinstance(T, Primitive):
                reprvalue = repr(getattr(self, name))
            else:
                reprvalue = '...'
            fields.append('%s=%s' % (name, reprvalue))
        if skipped_after:
            fields.insert(skipped_after, '(...)')
        return ', '.join(fields)

    def __str__(self):
        return 'struct %s { %s }' % (self._TYPE._name, self._str_fields())

    def __reduce__(self):
        return _get_empty_instance_of_struct_variety, (self.__slots__, ), getstate_with_slots(self) 

    __setstate__ = setstate_with_slots

    def getlength(self):              # for FixedSizeArray kind of structs
        assert isinstance(self._TYPE, FixedSizeArray)
        return self._TYPE.length

    def getbounds(self):
        return 0, self.getlength()

    def getitem(self, index):         # for FixedSizeArray kind of structs
        assert isinstance(self._TYPE, FixedSizeArray)
        return getattr(self, 'item%d' % index)

    def setitem(self, index, value):  # for FixedSizeArray kind of structs
        assert isinstance(self._TYPE, FixedSizeArray)
        setattr(self, 'item%d' % index, value)

class _array(_parentable):
    _kind = "array"

    __slots__ = ('items',)

    def __init__(self, TYPE, n, parent=None, parentindex=None):
        if not isinstance(n, int):
            raise TypeError, "array length must be an int"
        if n < 0:
            raise ValueError, "negative array length"
        _parentable.__init__(self, TYPE)
        self.items = [TYPE.OF._defl(parent=self, parentindex=j)
                      for j in range(n)]
        if parent is not None:
            self._setparentstructure(parent, parentindex)

    def __repr__(self):
        return '<%s>' % (self,)

    def _str_item(self, item):
        if isinstance(self._TYPE.OF, Struct):
            of = self._TYPE.OF
            if self._TYPE._anonym_struct:
                return "{%s}" % item._str_fields()
            else:
                return "%s {%s}" % (of._name, item._str_fields())
        else:
            return repr(item)

    def __str__(self):
        items = self.items
        if len(items) > 20:
            items = items[:12] + items[-5:]
            skipped_at = 12
        else:
            skipped_at = None
        items = [self._str_item(item) for item in self.items]
        if skipped_at:
            items.insert(skipped_at, '(...)')
        return 'array [ %s ]' % (', '.join(items),)

    def getlength(self):
        return len(self.items)

    def getbounds(self):
        stop = len(self.items)
        if self._TYPE._hints.get('isrpystring', False):
            # special hack for the null terminator
            assert self._TYPE.OF == Char
            stop += 1
        return 0, stop

    def getitem(self, index):
        try:
            return self.items[index]
        except IndexError:
            if (self._TYPE._hints.get('isrpystring', False) and
                index == len(self.items)):
                # special hack for the null terminator
                assert self._TYPE.OF == Char
                return '\x00'
            raise

    def setitem(self, index, value):
        self.items[index] = value

assert not '__dict__' in dir(_array)
assert not '__dict__' in dir(_struct)


class _subarray(_parentable):     # only for cast_subarray_pointer()
                                  # and cast_structfield_pointer()
    _kind = "subarray"
    _cache = weakref.WeakKeyDictionary()  # parentarray -> {subarrays}

    def __init__(self, TYPE, parent, baseoffset_or_fieldname):
        _parentable.__init__(self, TYPE)
        self._setparentstructure(parent, baseoffset_or_fieldname)

    def getlength(self):
        assert isinstance(self._TYPE, FixedSizeArray)
        return self._TYPE.length

    def getbounds(self):
        baseoffset = self._parent_index
        if isinstance(baseoffset, str):
            return 0, 1     # structfield case
        start, stop = self._parentstructure().getbounds()
        return start - baseoffset, stop - baseoffset

    def getitem(self, index):
        baseoffset = self._parent_index
        if isinstance(baseoffset, str):
            assert index == 0
            fieldname = baseoffset    # structfield case
            return getattr(self._parentstructure(), fieldname)
        else:
            return self._parentstructure().getitem(baseoffset + index)

    def setitem(self, index, value):
        baseoffset = self._parent_index
        if isinstance(baseoffset, str):
            assert index == 0
            fieldname = baseoffset    # structfield case
            setattr(self._parentstructure(), fieldname, value)
        else:
            self._parentstructure().setitem(baseoffset + index, value)

    def _makeptr(parent, baseoffset_or_fieldname):
        cache = _subarray._cache.setdefault(parent, {})
        try:
            subarray = cache[baseoffset_or_fieldname]
        except KeyError:
            PARENTTYPE = typeOf(parent)
            if isinstance(baseoffset_or_fieldname, str):
                # for direct_fieldptr
                ITEMTYPE = getattr(PARENTTYPE, baseoffset_or_fieldname)
            else:
                # for direct_arrayitems
                ITEMTYPE = PARENTTYPE.OF
            ARRAYTYPE = FixedSizeArray(ITEMTYPE, 1)
            subarray = _subarray(ARRAYTYPE, parent, baseoffset_or_fieldname)
            cache[baseoffset_or_fieldname] = subarray
        return _ptr(Ptr(subarray._TYPE), subarray)
    _makeptr = staticmethod(_makeptr)


class _func(_container):
    def __init__(self, TYPE, **attrs):
        self._TYPE = TYPE
        self._name = "?"
        self._callable = None
        self.__dict__.update(attrs)

    def __repr__(self):
        return '<%s>' % (self,)

    def __str__(self):
        return "fn %s" % self._name

    def __eq__(self, other):
        return (self.__class__ is other.__class__ and
                self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash(frozendict(self.__dict__))

    def __getstate__(self):
        import pickle, types
        __dict__ = self.__dict__.copy()
        try:
            pickle.dumps(self._callable)
        except pickle.PicklingError:
            __dict__['_callable'] = None
        return __dict__

    def __setstate__(self, __dict__):
        import new
        self.__dict__ = __dict__

class _opaque(_parentable):
    def __init__(self, TYPE, parent=None, parentindex=None, **attrs):
        _parentable.__init__(self, TYPE)
        self._name = "?"
        self.__dict__.update(attrs)
        if parent is not None:
            self._setparentstructure(parent, parentindex)

    def __repr__(self):
        return '<%s>' % (self,)

    def __str__(self):
        return "%s %s" % (self._TYPE.__name__, self._name)


class _pyobject(Hashable, _container):
    __slots__ = []   # or we get in trouble with pickling

    _TYPE = PyObject

    def __repr__(self):
        return '<%s>' % (self,)

    def __str__(self):
        return "pyobject %s" % (super(_pyobject, self).__str__(),)


def malloc(T, n=None, flavor='gc', immortal=False):
    if isinstance(T, Struct):
        o = _struct(T, n)
    elif isinstance(T, Array):
        o = _array(T, n)
    else:
        raise TypeError, "malloc for Structs and Arrays only"
    if not isinstance(T, GC_CONTAINER) and not immortal and flavor.startswith('gc'):
        raise TypeError, "gc flavor malloc of a non-GC non-immortal structure"
    solid = immortal or not flavor.startswith('gc') # immortal or non-gc case
    return _ptr(Ptr(T), o, solid)

def free(p, flavor):
    if flavor.startswith('gc'):
        raise TypeError, "gc flavor free"
    T = typeOf(p)
    if not isinstance(T, Ptr) or p._needsgc():
        raise TypeError, "free(): only for pointers to non-gc containers"

def functionptr(TYPE, name, **attrs):
    if not isinstance(TYPE, FuncType):
        raise TypeError, "functionptr() for FuncTypes only"
    try:
        hash(tuple(attrs.items()))
    except TypeError:
        raise TypeError("'%r' must be hashable"%attrs)
    o = _func(TYPE, _name=name, **attrs)
    return _ptr(Ptr(TYPE), o)

def nullptr(T):
    return Ptr(T)._defl()

def opaqueptr(TYPE, name, **attrs):
    if not isinstance(TYPE, OpaqueType):
        raise TypeError, "opaqueptr() for OpaqueTypes only"
    o = _opaque(TYPE, _name=name, **attrs)
    return _ptr(Ptr(TYPE), o, solid=True)

def pyobjectptr(obj):
    o = _pyobject(obj)
    return _ptr(Ptr(PyObject), o) 

def cast_ptr_to_int(ptr):
    return ptr._cast_to_int()

def cast_int_to_ptr(PTRTYPE, oddint):
    assert oddint & 1, "only odd integers can be cast back to ptr"
    return _ptr(PTRTYPE, oddint, solid=True)

def attachRuntimeTypeInfo(GCSTRUCT, funcptr=None, destrptr=None):
    if not isinstance(GCSTRUCT, GcStruct):
        raise TypeError, "expected a GcStruct: %s" % GCSTRUCT
    GCSTRUCT._attach_runtime_type_info_funcptr(funcptr, destrptr)
    return _ptr(Ptr(RuntimeTypeInfo), GCSTRUCT._runtime_type_info)

def getRuntimeTypeInfo(GCSTRUCT):
    if not isinstance(GCSTRUCT, GcStruct):
        raise TypeError, "expected a GcStruct: %s" % GCSTRUCT
    if GCSTRUCT._runtime_type_info is None:
        raise ValueError, "no attached runtime type info for %s" % GCSTRUCT
    return _ptr(Ptr(RuntimeTypeInfo), GCSTRUCT._runtime_type_info)

def runtime_type_info(p):
    T = typeOf(p)
    if not isinstance(T, Ptr) or not isinstance(T.TO, GcStruct):
        raise TypeError, "runtime_type_info on non-GcStruct pointer: %s" % p
    struct = p._obj
    top_parent = top_container(struct)
    result = getRuntimeTypeInfo(top_parent._TYPE)
    static_info = getRuntimeTypeInfo(T.TO)
    query_funcptr = getattr(static_info._obj, 'query_funcptr', None)
    if query_funcptr is not None:
        T = typeOf(query_funcptr).TO.ARGS[0]
        result2 = query_funcptr(cast_pointer(T, p))
        if result != result2:
            raise RuntimeError, ("runtime type-info function for %s:\n"
                                 "        returned: %s,\n"
                                 "should have been: %s" % (p, result2, result))
    return result

def isCompatibleType(TYPE1, TYPE2):
    return TYPE1._is_compatible(TYPE2)

def enforce(TYPE, value):
    return TYPE._enforce(value)

# mark type ADT methods

def typeMethod(func):
    func._type_method = True
    return func

class staticAdtMethod(object):
    # Like staticmethod(), but for ADT methods.  The difference is only
    # that this version compares and hashes correctly, unlike CPython's.
    def __init__(self, obj):
        self.obj = obj

    def __get__(self, inst, typ=None):
        return self.obj

    def __hash__(self):
        return hash(self.obj)

    def __eq__(self, other):
        if not isinstance(other, staticAdtMethod):
            return NotImplemented
        else:
            return self.obj == other.obj

    def __ne__(self, other):
        if not isinstance(other, staticAdtMethod):
            return NotImplemented
        else:
            return self.obj != other.obj


def dissect_ll_instance(v, t=None, memo=None):
    if memo is None:
        memo = {}
    if id(v) in memo:
        return
    memo[id(v)] = True
    if t is None:
        t = typeOf(v)
    yield t, v
    if isinstance(t, Ptr):
        if v._obj:
            for i in dissect_ll_instance(v._obj, t.TO, memo):
                yield i
    elif isinstance(t, Struct):
        parent = v._parentstructure()
        if parent:
            for i in dissect_ll_instance(parent, typeOf(parent), memo):
                yield i
        for n in t._flds:
            f = getattr(t, n)
            for i in dissect_ll_instance(getattr(v, n), t._flds[n], memo):
                yield i
    elif isinstance(t, Array):
        for item in v.items:
            for i in dissect_ll_instance(item, t.OF, memo):
                yield i
