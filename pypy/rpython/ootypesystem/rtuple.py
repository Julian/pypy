from pypy.rpython.rmodel import inputconst
from pypy.rpython.rtuple import AbstractTupleRepr, AbstractTupleIteratorRepr
from pypy.rpython.ootypesystem.riterable import iterator_type
from pypy.rpython.ootypesystem import ootype


class TupleRepr(AbstractTupleRepr):

    def __init__(self, rtyper, items_r):
        AbstractTupleRepr.__init__(self, rtyper, items_r)
        self.lowleveltype = tuple_type(self.fieldnames, self.lltypes)

    def newtuple(cls, llops, r_tuple, items_v):
        # items_v should have the lowleveltype of the internal reprs
        if len(r_tuple.items_r) == 0:
            return inputconst(r_tuple, ()) # always the same empty tuple
        c1 = inputconst(ootype.Void, r_tuple.lowleveltype)
        v_result = llops.genop("new", [c1], resulttype=r_tuple.lowleveltype)
        for i in range(len(r_tuple.items_r)):
            cname = inputconst(ootype.Void, r_tuple.fieldnames[i])
            llops.genop("oosetfield", [v_result, cname, items_v[i]])
        return v_result
    newtuple = classmethod(newtuple)

    def instantiate(self):
        return ootype.new(self.lowleveltype)

    def getitem(self, llops, v_tuple, index): # ! returns internal repr lowleveltype
        name = self.fieldnames[index]
        llresult = self.lltypes[index]
        cname = inputconst(ootype.Void, name)
        return  llops.genop("oogetfield", [v_tuple, cname], resulttype=llresult)

    def rtype_id(self, hop):
        vinst, = hop.inputargs(self)
        return hop.genop('ooidentityhash', [vinst], resulttype=ootype.Signed)


_tuple_types = {}

def tuple_type(fieldnames, fieldtypes):
    key = tuple(fieldtypes)
    if _tuple_types.has_key(key):
        return _tuple_types[key]
    else:
        fields = dict(zip(fieldnames, fieldtypes))
        INST = ootype.Instance("Tuple%s" % len(fieldnames), ootype.ROOT, fields)
        _tuple_types[key] = INST
        return INST


def rtype_newtuple(hop):
    return TupleRepr._rtype_newtuple(hop)

# ____________________________________________________________
#
#  Iteration.

class Length1TupleIteratorRepr(AbstractTupleIteratorRepr):

    def __init__(self, r_tuple):
        self.r_tuple = r_tuple
        self.lowleveltype = iterator_type(r_tuple)
        self.ll_tupleiter = ll_tupleiter
        self.ll_tuplenext = ll_tuplenext

TupleRepr.IteratorRepr = Length1TupleIteratorRepr

def ll_tupleiter(ITERINST, tuple):
    iter = ootype.new(ITERINST)
    iter.iterable = tuple
    return iter

def ll_tuplenext(iter):
    # for iterating over length 1 tuples only!
    t = iter.iterable
    if t:
        iter.iterable = ootype.null(ootype.typeOf(t))
        return t.item0
    else:
        raise StopIteration

