# The model produced by the flowobjspace
# this is to be used by the translator mainly.
# 
# the below object/attribute model evolved from
# a discussion in Berlin, 4th of october 2003
from __future__ import generators
from pypy.tool.uid import uid, Hashable
from pypy.tool.sourcetools import PY_IDENTIFIER, nice_repr_for_func

"""
    memory size before and after introduction of __slots__
    using targetpypymain with -no-c

    slottified          annotation  ann+genc
    -------------------------------------------
    nothing             321 MB      442 MB
    Var/Const/SpaceOp   205 MB      325 MB
    + Link              189 MB      311 MB
    + Block             185 MB      304 MB
    
    Dropping Variable.instances and using
    just an instancenames dict brought
    annotation down to 160 MB.
    Computing the Variable.renamed attribute
    and dropping Variable.instancenames
    got annotation down to 109 MB.
    Probably an effect of less fragmentation.
"""

__metaclass__ = type

class roproperty(object):
    def __init__(self, getter):
        self.getter = getter
    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        else:
            return self.getter(obj)


class FunctionGraph(object):
    
    def __init__(self, name, startblock, return_var=None):
        self.name        = name    # function name (possibly mangled already)
        self.startblock  = startblock
        self.startblock.isstartblock = True
        # build default returnblock
        self.returnblock = Block([return_var or Variable()])
        self.returnblock.operations = ()
        self.returnblock.exits      = ()
        # block corresponding to exception results
        self.exceptblock = Block([Variable('etype'),   # exception class
                                  Variable('evalue')])  # exception value
        self.exceptblock.operations = ()
        self.exceptblock.exits      = ()

    def getargs(self):
        return self.startblock.inputargs

    def getreturnvar(self):
        return self.returnblock.inputargs[0]

    def getsource(self):
        from pypy.tool.sourcetools import getsource
        return getsource(self.func)
    source = roproperty(getsource)

    def __repr__(self):
        if hasattr(self, 'func'):
            fnrepr = nice_repr_for_func(self.func, self.name)
        else:
            fnrepr = self.name
        return '<FunctionGraph of %s at 0x%x>' % (fnrepr, uid(self))

    def iterblocks(self):
        block = self.startblock
        yield block
        seen = {id(block): True}
        stack = list(block.exits[::-1])
        while stack:
            block = stack.pop().target
            if id(block) not in seen:
                yield block
                seen[id(block)] = True
                stack += block.exits[::-1]

    def iterlinks(self):
        block = self.startblock
        seen = {id(block): True}
        stack = list(block.exits[::-1])
        while stack:
            link = stack.pop()
            yield link
            block = link.target
            if id(block) not in seen:
                seen[id(block)] = True
                stack += block.exits[::-1]

    def show(self):
        from pypy.translator.tool.graphpage import SingleGraphPage
        SingleGraphPage(self).display()


class Link(object):

    __slots__ = """args target exitcase llexitcase prevblock
                last_exception last_exc_value""".split()

    def __init__(self, args, target, exitcase=None):
        if target is not None:
            assert len(args) == len(target.inputargs), "output args mismatch"
        self.args = list(args)     # mixed list of var/const
        self.target = target       # block
        self.exitcase = exitcase   # this is a concrete value
        self.prevblock = None      # the block this Link is an exit of

        # exception passing vars
        self.last_exception = None
        self.last_exc_value = None

    # right now only exception handling needs to introduce new variables on the links
    def extravars(self, last_exception=None, last_exc_value=None):
        self.last_exception = last_exception
        self.last_exc_value = last_exc_value

    def getextravars(self):
        "Return the extra vars created by this Link."
        result = []
        if isinstance(self.last_exception, Variable):
            result.append(self.last_exception)
        if isinstance(self.last_exc_value, Variable):
            result.append(self.last_exc_value)
        return result

    def copy(self, rename=lambda x: x):
        newargs = [rename(a) for a in self.args]
        newlink = Link(newargs, self.target, self.exitcase)
        newlink.prevblock = self.prevblock
        newlink.last_exception = rename(self.last_exception)
        newlink.last_exc_value = rename(self.last_exc_value)
        if hasattr(self, 'llexitcase'):
            newlink.llexitcase = self.llexitcase
        return newlink

    def settarget(self, targetblock):
        assert len(self.args) == len(targetblock.inputargs), (
            "output args mismatch")
        self.target = targetblock

    def __repr__(self):
        return "link from %s to %s" % (str(self.prevblock), str(self.target))


class Block(object):
    __slots__ = """isstartblock inputargs operations exitswitch
                exits exc_handler""".split()
    
    def __init__(self, inputargs):
        self.isstartblock = False
        self.inputargs = list(inputargs)  # mixed list of variable/const XXX 
        self.operations = []              # list of SpaceOperation(s)
        self.exitswitch = None            # a variable or
                                          #  Constant(last_exception), see below
        self.exits      = []              # list of Link(s)

        self.exc_handler = False          # block at the start of exception handling code

    def at(self):
        if self.operations:
            return "@%d" % self.operations[0].offset
        else:
            return ""

    def __str__(self):
        if self.operations:
            txt = "block@%d" % self.operations[0].offset
        else:
            txt = "codeless block"
        if self.exc_handler:
            txt = txt +" EH"
        return txt
    
    def __repr__(self):
        txt = "%s with %d exits" % (str(self), len(self.exits))
        if self.exitswitch:
            txt = "%s(%s)" % (txt, self.exitswitch)
        return txt

    def getvariables(self):
        "Return all variables mentioned in this Block."
        result = self.inputargs[:]
        for op in self.operations:
            result += op.args
            result.append(op.result)
        return uniqueitems([w for w in result if isinstance(w, Variable)])

    def getconstants(self):
        "Return all constants mentioned in this Block."
        result = self.inputargs[:]
        for op in self.operations:
            result += op.args
        return uniqueitems([w for w in result if isinstance(w, Constant)])

    def renamevariables(self, mapping):
        for a in mapping:
            assert isinstance(a, Variable), a
        self.inputargs = [mapping.get(a, a) for a in self.inputargs]
        for op in self.operations:
            op.args = [mapping.get(a, a) for a in op.args]
            op.result = mapping.get(op.result, op.result)
        self.exitswitch = mapping.get(self.exitswitch, self.exitswitch)
        for link in self.exits:
            link.args = [mapping.get(a, a) for a in link.args]

    def closeblock(self, *exits):
        assert self.exits == [], "block already closed"
        self.recloseblock(*exits)
        
    def recloseblock(self, *exits):
        for exit in exits:
            exit.prevblock = self
        self.exits = exits


class Variable(object):
    __slots__ = ["_name", "_nr", "concretetype"]

    dummyname = 'v'
    namesdict = {dummyname : (dummyname, 0)}

    def name(self):
        _name = self._name
        _nr = self._nr
        if _nr == -1:
            # consume numbers lazily
            nd = self.namesdict
            _nr = self._nr = nd[_name][1]
            nd[_name] = (_name, _nr + 1)
        return "%s%d" % (_name, _nr)
    name = property(name)

    def renamed(self):
        return self._name is not self.dummyname
    renamed = property(renamed)
    
    def __init__(self, name=None):
        self._name = self.dummyname
        self._nr = -1
        # numbers are bound lazily, when the name is requested
        if name is not None:
            self.rename(name)

    def __repr__(self):
        return self.name

    def rename(self, name):
        if self._name is not self.dummyname:   # don't rename several times
            return
        if type(name) is not str:
            #assert isinstance(name, Variable) -- disabled for speed reasons
            name = name._name
            if name is self.dummyname:    # the other Variable wasn't renamed either
                return
        else:
            # remove strange characters in the name
            name = name.translate(PY_IDENTIFIER) + '_'
            if name[0] <= '9':   # skipped the   '0' <=   which is always true
                name = '_' + name
            name = self.namesdict.setdefault(name, (name, 0))[0]
        self._name = name
        self._nr = -1

    def set_name_from(self, v):
        # this is for SSI_to_SSA only which should not know about internals
        v.name  # make sure v's name is finalized
        self._name = v._name
        self._nr = v._nr

    def set_name(self, name, nr):
        # this is for wrapper.py which wants to assign a name explicitly
        self._name = intern(name)
        self._nr = nr

    def __reduce_ex__(self, *args):
        if hasattr(self, 'concretetype'):
            return _bv, (self._name, self._nr, self.concretetype)
        else:
            return _bv, (self._name, self._nr)
    __reduce__ = __reduce_ex__

def _bv(_name, _nr, concretetype=None):
    v = Variable.__new__(Variable, object)
    v._name = _name
    v._nr = _nr
    if concretetype is not None:
        v.concretetype = concretetype
    nd = v.namesdict
    if _nr >= nd.get(_name, 0):
        nd[_name] = _nr + 1
    return v


class Constant(Hashable):
    __slots__ = ["concretetype"]

    def __init__(self, value, concretetype = None):
        Hashable.__init__(self, value)
        if concretetype is not None:
            self.concretetype = concretetype
    def __reduce_ex__(self, *args):
        if hasattr(self, 'concretetype'):
            return Constant, (self.value, self.concretetype)
        else:
            return Constant, (self.value,)
    __reduce__ = __reduce_ex__


class SpaceOperation(object):
    __slots__ = "opname args result offset".split()

    def __init__(self, opname, args, result, offset=-1):
        self.opname = intern(opname)      # operation name
        self.args   = list(args)  # mixed list of var/const
        self.result = result      # either Variable or Constant instance
        self.offset = offset      # offset in code string

    def __eq__(self, other):
        return (self.__class__ is other.__class__ and 
                self.opname == other.opname and
                self.args == other.args and
                self.result == other.result)

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash((self.opname,tuple(self.args),self.result))

    def __repr__(self):
        return "%r = %s(%s)" % (self.result, self.opname, ", ".join(map(repr, self.args)))

    def __reduce_ex__(self, *args):
        # avoid lots of useless list entities
        return _sop, (self.opname, self.result, self.offset) + tuple(self.args)
    __reduce__ = __reduce_ex__

# a small and efficient restorer
def _sop(opname, result, offset, *args):
    return SpaceOperation(opname, args, result, offset)


class Atom:
    def __init__(self, name):
        self.__name__ = name # make save_global happy
    def __repr__(self):
        return self.__name__

last_exception = Atom('last_exception')
c_last_exception = Constant(last_exception)
# if Block().exitswitch == Constant(last_exception), it means that we are
# interested in catching the exception that the *last operation* of the
# block could raise.  The exitcases of the links are None for no exception
# or XxxError classes to catch the matching exceptions.

def uniqueitems(lst):
    "Returns a list with duplicate elements removed."
    result = []
    seen = {}
    for item in lst:
        if item not in seen:
            result.append(item)
            seen[item] = True
    return result


#_________________________________________________________
# a visitor for easy traversal of the above model

##import inspect   # for getmro

##class traverse:

##    def __init__(self, visitor, functiongraph):
##        """ send the visitor over all (reachable) nodes. 
##            the visitor needs to have either callable attributes 'visit_typename'
##            or otherwise is callable itself.  
##        """
##        self.visitor = visitor
##        self.visitor_cache = {}
##        self.seen = {}
##        self.visit(functiongraph)

##    def visit(self, node):
##        if id(node) in self.seen:
##            return

##        # do the visit
##        cls = node.__class__
##        try:
##            consume = self.visitor_cache[cls]
##        except KeyError:
##            for subclass in inspect.getmro(cls):
##                consume = getattr(self.visitor, "visit_" + subclass.__name__, None)
##                if consume:
##                    break
##            else:
##                consume = getattr(self.visitor, 'visit', self.visitor)

##                assert callable(consume), "visitor not found for %r on %r" % (cls, self.visitor)

##                self.visitor_cache[cls] = consume

##        self.seen[id(node)] = consume(node)

##        # recurse
##        if isinstance(node, Block):
##            for obj in node.exits:
##                self.visit(obj)
##        elif isinstance(node, Link):
##            self.visit(node.target)
##        elif isinstance(node, FunctionGraph):
##            self.visit(node.startblock)
##        else:
##            raise ValueError, "could not dispatch %r" % cls

def traverse(visit, functiongraph):
    block = functiongraph.startblock
    visit(block)
    seen = {id(block): True}
    stack = list(block.exits[::-1])
    while stack:
        link = stack.pop()
        visit(link)
        block = link.target
        if id(block) not in seen:
            visit(block)
            seen[id(block)] = True
            stack += block.exits[::-1]


def flatten(funcgraph):
    l = []
    traverse(l.append, funcgraph)
    return l

def flattenobj(*args):
    for arg in args:
        try:
            for atom in flattenobj(*arg):
                yield atom
        except: yield arg

def mkentrymap(funcgraph):
    "Returns a dict mapping Blocks to lists of Links."
    startlink = Link(funcgraph.getargs(), funcgraph.startblock)
    result = {funcgraph.startblock: [startlink]}
    for link in funcgraph.iterlinks():
        lst = result.setdefault(link.target, [])
        lst.append(link)
    return result

def checkgraph(graph):
    "Check the consistency of a flow graph."
    if not __debug__:
        return
    try:

        vars_previous_blocks = {}

        exitblocks = {graph.returnblock: 1,   # retval
                      graph.exceptblock: 2}   # exc_cls, exc_value

        for block, nbargs in exitblocks.items():
            assert len(block.inputargs) == nbargs
            assert not block.operations
            assert not block.exits

        for block in graph.iterblocks():
            assert bool(block.isstartblock) == (block is graph.startblock)
            if not block.exits:
                assert block in exitblocks
            vars = {}

            def definevar(v, only_in_link=None):
                assert isinstance(v, Variable)
                assert v not in vars, "duplicate variable %r" % (v,)
                assert v not in vars_previous_blocks, (
                    "variable %r used in more than one block" % (v,))
                vars[v] = only_in_link

            def usevar(v, in_link=None):
                assert v in vars
                if in_link is not None:
                    assert vars[v] is None or vars[v] is in_link

            for v in block.inputargs:
                definevar(v)

            for op in block.operations:
                for v in op.args:
                    assert isinstance(v, (Constant, Variable))
                    if isinstance(v, Variable):
                        usevar(v)
                    else:
                        assert v.value is not last_exception
                        #assert v.value != last_exc_value
                definevar(op.result)

            exc_links = {}
            if block.exitswitch is None:
                assert len(block.exits) <= 1
                if block.exits:
                    assert block.exits[0].exitcase is None
            elif block.exitswitch == Constant(last_exception):
                assert len(block.operations) >= 1
                # check if an exception catch is done on a reasonable
                # operation
                assert block.operations[-1].opname not in ("keepalive",
                                                           "cast_pointer",
                                                           "same_as")
                assert len(block.exits) >= 2
                assert block.exits[0].exitcase is None
                for link in block.exits[1:]:
                    assert issubclass(link.exitcase, Exception)
                    exc_links[link] = True
            else:
                assert isinstance(block.exitswitch, Variable)
                assert block.exitswitch in vars
                assert len(block.exits) > 1

            allexitcases = {}
            for link in block.exits:
                assert len(link.args) == len(link.target.inputargs)
                assert link.prevblock is block
                exc_link = link in exc_links
                if exc_link:
                    for v in [link.last_exception, link.last_exc_value]:
                        assert isinstance(v, (Variable, Constant))
                        if isinstance(v, Variable):
                            definevar(v, only_in_link=link)
                else:
                    assert link.last_exception is None
                    assert link.last_exc_value is None
                for v in link.args:
                    assert isinstance(v, (Constant, Variable))
                    if isinstance(v, Variable):
                        usevar(v, in_link=link)
                        if exc_link:
                            assert v != block.operations[-1].result
                    #else:
                    #    if not exc_link:
                    #        assert v.value is not last_exception
                    #        #assert v.value != last_exc_value
                allexitcases[link.exitcase] = True
            assert len(allexitcases) == len(block.exits)
            vars_previous_blocks.update(vars)

    except AssertionError, e:
        # hack for debug tools only
        #graph.show()  # <== ENABLE THIS TO SEE THE BROKEN GRAPH
        if block and not hasattr(e, '__annotator_block'):
            setattr(e, '__annotator_block', block)
        raise
