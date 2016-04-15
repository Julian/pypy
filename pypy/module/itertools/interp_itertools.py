from pypy.interpreter.baseobjspace import W_Root
from pypy.interpreter.error import OperationError, oefmt
from pypy.interpreter.typedef import TypeDef, make_weakref_descr
from pypy.interpreter.gateway import interp2app, unwrap_spec, WrappedDefault
from rpython.rlib import jit

from pypy.module.__builtin__.functional import W_Filter, W_Map

class W_Count(W_Root):
    def __init__(self, space, w_firstval, w_step):
        self.space = space
        self.w_c = w_firstval
        self.w_step = w_step

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        w_c = self.w_c
        self.w_c = self.space.add(w_c, self.w_step)
        return w_c

    def single_argument(self):
        space = self.space
        return (space.isinstance_w(self.w_step, space.w_int) and
                space.eq_w(self.w_step, space.wrap(1)))

    def repr_w(self):
        space = self.space
        c = space.str_w(space.repr(self.w_c))
        if self.single_argument():
            s = 'count(%s)' % (c,)
        else:
            step = space.str_w(space.repr(self.w_step))
            s = 'count(%s, %s)' % (c, step)
        return self.space.wrap(s)

    def reduce_w(self):
        space = self.space
        if self.single_argument():
            args_w = [self.w_c]
        else:
            args_w = [self.w_c, self.w_step]
        return space.newtuple([space.gettypefor(W_Count),
                               space.newtuple(args_w)])

def check_number(space, w_obj):
    if (space.lookup(w_obj, '__int__') is None and
        space.lookup(w_obj, '__float__') is None):
        raise OperationError(space.w_TypeError,
                             space.wrap("expected a number"))

@unwrap_spec(w_start=WrappedDefault(0), w_step=WrappedDefault(1))
def W_Count___new__(space, w_subtype, w_start, w_step):
    check_number(space, w_start)
    check_number(space, w_step)
    r = space.allocate_instance(W_Count, w_subtype)
    r.__init__(space, w_start, w_step)
    return space.wrap(r)

W_Count.typedef = TypeDef(
        'itertools.count',
        __new__ = interp2app(W_Count___new__),
        __iter__ = interp2app(W_Count.iter_w),
        __next__ = interp2app(W_Count.next_w),
        __reduce__ = interp2app(W_Count.reduce_w),
        __repr__ = interp2app(W_Count.repr_w),
        __doc__ = """Make an iterator that returns evenly spaced values starting
    with n.  If not specified n defaults to zero.  Often used as an
    argument to imap() to generate consecutive data points.  Also,
    used with izip() to add sequence numbers.

    Equivalent to:

    def count(start=0, step=1):
        n = start
        while True:
            yield n
            n += step
    """)


class W_Repeat(W_Root):
    def __init__(self, space, w_obj, w_times):
        self.space = space
        self.w_obj = w_obj

        if w_times is None:
            self.counting = False
            self.count = 0
        else:
            self.counting = True
            self.count = max(self.space.int_w(w_times), 0)

    def next_w(self):
        if self.counting:
            if self.count <= 0:
                raise OperationError(self.space.w_StopIteration, self.space.w_None)
            self.count -= 1
        return self.w_obj

    def iter_w(self):
        return self.space.wrap(self)

    def length_w(self):
        if not self.counting:
            return self.space.w_NotImplemented
        return self.space.wrap(self.count)

    def repr_w(self):
        objrepr = self.space.str_w(self.space.repr(self.w_obj))
        if self.counting:
            s = 'repeat(%s, %d)' % (objrepr, self.count)
        else:
            s = 'repeat(%s)' % (objrepr,)
        return self.space.wrap(s)

    def descr_reduce(self):
        space = self.space
        if self.counting:
            args_w = [self.w_obj, space.wrap(self.count)]
        else:
            args_w = [self.w_obj]
        return space.newtuple([space.gettypefor(W_Repeat),
                               space.newtuple(args_w)])

def W_Repeat___new__(space, w_subtype, w_object, w_times=None):
    r = space.allocate_instance(W_Repeat, w_subtype)
    r.__init__(space, w_object, w_times)
    return space.wrap(r)

W_Repeat.typedef = TypeDef(
        'itertools.repeat',
        __new__          = interp2app(W_Repeat___new__),
        __iter__         = interp2app(W_Repeat.iter_w),
        __length_hint__  = interp2app(W_Repeat.length_w),
        __next__         = interp2app(W_Repeat.next_w),
        __repr__         = interp2app(W_Repeat.repr_w),
        __reduce__       = interp2app(W_Repeat.descr_reduce),
        __doc__  = """Make an iterator that returns object over and over again.
    Runs indefinitely unless the times argument is specified.  Used
    as argument to imap() for invariant parameters to the called
    function.

    Equivalent to :

    def repeat(object, times=None):
        if times is None:
            while True:
                yield object
        else:
            for i in xrange(times):
                yield object
    """)


class W_TakeWhile(W_Root):
    def __init__(self, space, w_predicate, w_iterable):
        self.space = space
        self.w_predicate = w_predicate
        self.w_iterable = space.iter(w_iterable)
        self.stopped = False

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.stopped:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

        w_obj = self.space.next(self.w_iterable)  # may raise a w_StopIteration
        w_bool = self.space.call_function(self.w_predicate, w_obj)
        if not self.space.is_true(w_bool):
            self.stopped = True
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

        return w_obj

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([self.w_predicate, self.w_iterable]),
            space.wrap(self.stopped)
        ])

    def descr_setstate(self, space, w_state):
        self.stopped = space.bool_w(w_state)

def W_TakeWhile___new__(space, w_subtype, w_predicate, w_iterable):
    r = space.allocate_instance(W_TakeWhile, w_subtype)
    r.__init__(space, w_predicate, w_iterable)
    return space.wrap(r)


W_TakeWhile.typedef = TypeDef(
        'itertools.takewhile',
        __new__  = interp2app(W_TakeWhile___new__),
        __iter__ = interp2app(W_TakeWhile.iter_w),
        __next__ = interp2app(W_TakeWhile.next_w),
        __reduce__ = interp2app(W_TakeWhile.descr_reduce),
        __setstate__ = interp2app(W_TakeWhile.descr_setstate),
        __doc__  = """Make an iterator that returns elements from the iterable as
    long as the predicate is true.

    Equivalent to :

    def takewhile(predicate, iterable):
        for x in iterable:
            if predicate(x):
                yield x
            else:
                break
    """)


class W_DropWhile(W_Root):
    def __init__(self, space, w_predicate, w_iterable):
        self.space = space
        self.w_predicate = w_predicate
        self.w_iterable = space.iter(w_iterable)
        self.started = False

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.started:
            w_obj = self.space.next(self.w_iterable)  # may raise w_StopIter
        else:
            while True:
                w_obj = self.space.next(self.w_iterable)  # may raise w_StopIter
                w_bool = self.space.call_function(self.w_predicate, w_obj)
                if not self.space.is_true(w_bool):
                    self.started = True
                    break

        return w_obj

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([self.w_predicate, self.w_iterable]),
            space.wrap(self.started)
        ])

    def descr_setstate(self, space, w_state):
        self.started = space.bool_w(w_state)

def W_DropWhile___new__(space, w_subtype, w_predicate, w_iterable):
    r = space.allocate_instance(W_DropWhile, w_subtype)
    r.__init__(space, w_predicate, w_iterable)
    return space.wrap(r)


W_DropWhile.typedef = TypeDef(
        'itertools.dropwhile',
        __new__  = interp2app(W_DropWhile___new__),
        __iter__ = interp2app(W_DropWhile.iter_w),
        __next__ = interp2app(W_DropWhile.next_w),
        __reduce__ = interp2app(W_DropWhile.descr_reduce),
        __setstate__ = interp2app(W_DropWhile.descr_setstate),
        __doc__  = """Make an iterator that drops elements from the iterable as long
    as the predicate is true; afterwards, returns every
    element. Note, the iterator does not produce any output until the
    predicate is true, so it may have a lengthy start-up time.

    Equivalent to :

    def dropwhile(predicate, iterable):
        iterable = iter(iterable)
        for x in iterable:
            if not predicate(x):
                yield x
                break
        for x in iterable:
            yield x
    """)

class W_FilterFalse(W_Filter):
    reverse = True
    def descr_reduce(self, space):
        args_w = [space.w_None if self.no_predicate else self.w_predicate,
                  self.iterable]
        return space.newtuple([space.type(self), space.newtuple(args_w)])

def W_FilterFalse___new__(space, w_subtype, w_predicate, w_iterable):
    r = space.allocate_instance(W_FilterFalse, w_subtype)
    r.__init__(space, w_predicate, w_iterable)
    return space.wrap(r)

W_FilterFalse.typedef = TypeDef(
        'itertools.filterfalse',
        __new__  = interp2app(W_FilterFalse___new__),
        __iter__ = interp2app(W_FilterFalse.iter_w),
        __next__ = interp2app(W_FilterFalse.next_w),
        __reduce__ = interp2app(W_FilterFalse.descr_reduce),
        __doc__  = """Make an iterator that filters elements from iterable returning
    only those for which the predicate is False.  If predicate is
    None, return the items that are false.

    Equivalent to :

    def filterfalse(predicate, iterable):
        if predicate is None:
            predicate = bool
        for x in iterable:
            if not predicate(x):
                yield x
    """)


islice_ignore_items_driver = jit.JitDriver(name='islice_ignore_items',
                                           greens=['tp'],
                                           reds=['num', 'w_islice',
                                                 'w_iterator'])

class W_ISlice(W_Root):
    def __init__(self, space, w_iterable, w_startstop, args_w):
        self.iterable = space.iter(w_iterable)
        self.space = space

        num_args = len(args_w)

        if num_args == 0:
            start = 0
            w_stop = w_startstop
        elif num_args <= 2:
            if space.is_w(w_startstop, space.w_None):
                start = 0
            else:
                start = self.arg_int_w(w_startstop, 0,
                 "Indicies for islice() must be None or non-negative integers")
            w_stop = args_w[0]
        else:
            raise OperationError(space.w_TypeError, space.wrap("islice() takes at most 4 arguments (" + str(num_args) + " given)"))

        if space.is_w(w_stop, space.w_None):
            stop = -1
        else:
            stop = self.arg_int_w(w_stop, 0,
                "Stop argument must be a non-negative integer or None.")
            stop = max(start, stop)    # for obscure CPython compatibility

        if num_args == 2:
            w_step = args_w[1]
            if space.is_w(w_step, space.w_None):
                step = 1
            else:
                step = self.arg_int_w(w_step, 1,
                    "Step for islice() must be a positive integer or None")
        else:
            step = 1

        self.ignore = step - 1
        self.start = start
        self.stop = stop

    def arg_int_w(self, w_obj, minimum, errormsg):
        space = self.space
        try:
            result = space.int_w(space.int(w_obj))    # CPython allows floats as parameters
        except OperationError, e:
            if e.async(space):
                raise
            result = -1
        if result < minimum:
            raise OperationError(space.w_ValueError, space.wrap(errormsg))
        return result

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.start >= 0:               # first call only
            ignore = self.start
            self.start = -1
        else:                             # all following calls
            ignore = self.ignore
        stop = self.stop
        if stop >= 0:
            if stop <= ignore:
                self.stop = 0   # reset the state so that a following next_w()
                                # has no effect any more
                if stop > 0:
                    self._ignore_items(stop)
                self.iterable = None
                raise OperationError(self.space.w_StopIteration,
                                     self.space.w_None)
            self.stop = stop - (ignore + 1)
        if ignore > 0:
            self._ignore_items(ignore)
        if self.iterable is None:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)
        try:
            return self.space.next(self.iterable)
        except OperationError as e:
            if e.match(self.space, self.space.w_StopIteration):
                self.iterable = None
            raise

    def _ignore_items(self, num):
        w_iterator = self.iterable
        if w_iterator is None:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

        tp = self.space.type(w_iterator)
        while True:
            islice_ignore_items_driver.jit_merge_point(tp=tp,
                                                       num=num,
                                                       w_islice=self,
                                                       w_iterator=w_iterator)
            try:
                self.space.next(w_iterator)
            except OperationError as e:
                if e.match(self.space, self.space.w_StopIteration):
                    self.iterable = None
                raise
            num -= 1
            if num <= 0:
                break

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([self.iterable,
                            space.wrap(self.start),
                            space.wrap(self.stop),
                            space.wrap(self.ignore + 1)]),
        ])

def W_ISlice___new__(space, w_subtype, w_iterable, w_startstop, args_w):
    r = space.allocate_instance(W_ISlice, w_subtype)
    r.__init__(space, w_iterable, w_startstop, args_w)
    return space.wrap(r)

W_ISlice.typedef = TypeDef(
        'itertools.islice',
        __new__  = interp2app(W_ISlice___new__),
        __iter__ = interp2app(W_ISlice.iter_w),
        __next__ = interp2app(W_ISlice.next_w),
        __reduce__ = interp2app(W_ISlice.descr_reduce),
        __doc__  = """Make an iterator that returns selected elements from the
    iterable.  If start is non-zero, then elements from the iterable
    are skipped until start is reached. Afterward, elements are
    returned consecutively unless step is set higher than one which
    results in items being skipped. If stop is None, then iteration
    continues until the iterator is exhausted, if at all; otherwise,
    it stops at the specified position. Unlike regular slicing,
    islice() does not support negative values for start, stop, or
    step. Can be used to extract related fields from data where the
    internal structure has been flattened (for example, a multi-line
    report may list a name field on every third line).
    """)


class W_Chain(W_Root):
    def __init__(self, space, w_iterables):
        self.space = space
        self.w_iterables = w_iterables
        self.w_it = None

    def iter_w(self):
        return self.space.wrap(self)

    def _advance(self):
        if self.w_iterables is None:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)
        self.w_it = self.space.iter(self.space.next(self.w_iterables))

    def next_w(self):
        if not self.w_it:
            try:
                self._advance()
            except OperationError, e:
                raise e
        try:
            return self.space.next(self.w_it)
        except OperationError, e:
            return self._handle_error(e)

    def _handle_error(self, e):
        while True:
            if not e.match(self.space, self.space.w_StopIteration):
                raise e
            try:
                self._advance() # may raise StopIteration itself
            except OperationError, e:
                self.w_iterables = None
                raise e
            try:
                return self.space.next(self.w_it)
            except OperationError, e:
                pass # loop back to the start of _handle_error(e)

    def descr_reduce(self, space):
        if self.w_iterables is not None:
            if self.w_it is not None:
                inner_contents = [self.w_iterables, self.w_it]
            else:
                inner_contents = [self.w_iterables]
            result_w = [space.type(self),
                        space.newtuple([]),
                        space.newtuple(inner_contents)]
        else:
            result_w = [space.type(self),
                        space.newtuple([])]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        state = space.unpackiterable(w_state)
        num_args = len(state)
        if num_args < 1:
            raise oefmt(space.w_TypeError,
                        "function takes at least 1 argument (%d given)",
                        num_args)
        elif num_args == 1:
            self.w_iterables = state[0]
        elif num_args == 2:
            self.w_iterables, self.w_it = state
        else:
            raise oefmt(space.w_TypeError,
                        "function takes at most 2 arguments (%d given)",
                        num_args)

def W_Chain___new__(space, w_subtype, args_w):
    r = space.allocate_instance(W_Chain, w_subtype)
    w_args = space.newtuple(args_w)
    r.__init__(space, space.iter(w_args))
    return space.wrap(r)

def chain_from_iterable(space, w_cls, w_arg):
    """chain.from_iterable(iterable) --> chain object

    Alternate chain() contructor taking a single iterable argument
    that evaluates lazily."""
    r = space.allocate_instance(W_Chain, w_cls)
    r.__init__(space, space.iter(w_arg))
    return space.wrap(r)

W_Chain.typedef = TypeDef(
        'itertools.chain',
        __new__  = interp2app(W_Chain___new__),
        __iter__ = interp2app(W_Chain.iter_w),
        __next__ = interp2app(W_Chain.next_w),
        __reduce__ = interp2app(W_Chain.descr_reduce),
        __setstate__ = interp2app(W_Chain.descr_setstate),
        from_iterable = interp2app(chain_from_iterable, as_classmethod=True),
        __doc__  = """Make an iterator that returns elements from the first iterable
    until it is exhausted, then proceeds to the next iterable, until
    all of the iterables are exhausted. Used for treating consecutive
    sequences as a single sequence.

    Equivalent to :

    def chain(*iterables):
        for it in iterables:
            for element in it:
                yield element
    """)


class W_ZipLongest(W_Map):
    _error_name = "zip_longest"
    _immutable_fields_ = ["w_fillvalue"]

    def _fetch(self, index):
        w_iter = self.iterators_w[index]
        if w_iter is not None:
            space = self.space
            try:
                return space.next(w_iter)
            except OperationError, e:
                if not e.match(space, space.w_StopIteration):
                    raise
                self.active -= 1
                if self.active <= 0:
                    # It was the last active iterator
                    raise
                self.iterators_w[index] = None
        return self.w_fillvalue

    def next_w(self):
        # common case: 2 arguments
        if len(self.iterators_w) == 2:
            objects = [self._fetch(0), self._fetch(1)]
        else:
            objects = self._get_objects()
        return self.space.newtuple(objects)

    def _get_objects(self):
        # the loop is out of the way of the JIT
        nb = len(self.iterators_w)
        if nb == 0:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)
        return [self._fetch(index) for index in range(nb)]

    def descr_reduce(self, space):
        result_w = [space.type(self)]

        if self.iterators_w is not None:
            iterators = [iterator if iterator is not None else space.newtuple([])
                             for iterator in self.iterators_w]
            iterators = space.newtuple(iterators)
        else:
            iterators = space.newtuple([])
        result_w = [space.type(self),
                    iterators,
                    self.w_fillvalue]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        self.w_fillvalue = w_state

def W_ZipLongest___new__(space, w_subtype, __args__):
    arguments_w, kwds_w = __args__.unpack()
    w_fillvalue = space.w_None
    if kwds_w:
        if "fillvalue" in kwds_w:
            w_fillvalue = kwds_w["fillvalue"]
            del kwds_w["fillvalue"]
        if kwds_w:
            raise OperationError(space.w_TypeError, space.wrap(
                "zip_longest() got unexpected keyword argument(s)"))

    self = space.allocate_instance(W_ZipLongest, w_subtype)
    self.__init__(space, space.w_None, arguments_w)
    self.w_fillvalue = w_fillvalue
    self.active = len(self.iterators_w)

    return space.wrap(self)

W_ZipLongest.typedef = TypeDef(
        'itertools.zip_longest',
        __new__  = interp2app(W_ZipLongest___new__),
        __iter__ = interp2app(W_ZipLongest.iter_w),
        __next__ = interp2app(W_ZipLongest.next_w),
        __reduce__ = interp2app(W_ZipLongest.descr_reduce),
        __setstate__ = interp2app(W_ZipLongest.descr_setstate),
        __doc__  = """Return a zip_longest object whose .next() method returns a tuple where
    the i-th element comes from the i-th iterable argument.  The .next()
    method continues until the longest iterable in the argument sequence
    is exhausted and then it raises StopIteration.  When the shorter iterables
    are exhausted, the fillvalue is substituted in their place.  The fillvalue
    defaults to None or can be specified by a keyword argument.
    """)


class W_Cycle(W_Root):
    def __init__(self, space, w_iterable):
        self.space = space
        self.saved_w = []
        self.w_iterable = space.iter(w_iterable)
        self.index = 0
        self.exhausted = False

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.exhausted:
            if not self.saved_w:
                raise OperationError(self.space.w_StopIteration, self.space.w_None)
            try:
                w_obj = self.saved_w[self.index]
            except IndexError:
                self.index = 1
                w_obj = self.saved_w[0]
            else:
                self.index += 1
        else:
            try:
                w_obj = self.space.next(self.w_iterable)
            except OperationError, e:
                if e.match(self.space, self.space.w_StopIteration):
                    self.exhausted = True
                    if not self.saved_w:
                        raise
                    self.index = 1
                    w_obj = self.saved_w[0]
                else:
                    raise
            else:
                self.index += 1
                self.saved_w.append(w_obj)
        return w_obj

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([self.w_iterable]),
            space.newtuple([
                space.newlist(self.saved_w),
                space.wrap(self.index),
                space.wrap(self.exhausted),
            ]),
        ])

    def descr_setstate(self, space, w_state):
        w_saved, w_index, w_exhausted = space.unpackiterable(w_state, 3)
        self.saved_w = space.unpackiterable(w_saved)
        self.index = space.int_w(w_index)
        self.exhausted = space.bool_w(w_exhausted)


def W_Cycle___new__(space, w_subtype, w_iterable):
    r = space.allocate_instance(W_Cycle, w_subtype)
    r.__init__(space, w_iterable)
    return space.wrap(r)

W_Cycle.typedef = TypeDef(
        'itertools.cycle',
        __new__  = interp2app(W_Cycle___new__),
        __iter__ = interp2app(W_Cycle.iter_w),
        __next__ = interp2app(W_Cycle.next_w),
        __reduce__ = interp2app(W_Cycle.descr_reduce),
        __setstate__ = interp2app(W_Cycle.descr_setstate),
        __doc__  = """Make an iterator returning elements from the iterable and
    saving a copy of each. When the iterable is exhausted, return
    elements from the saved copy. Repeats indefinitely.

    Equivalent to :

    def cycle(iterable):
        saved = []
        for element in iterable:
            yield element
            saved.append(element)
        while saved:
            for element in saved:
                yield element
    """)


class W_StarMap(W_Root):
    def __init__(self, space, w_fun, w_iterable):
        self.space = space
        self.w_fun = w_fun
        self.w_iterable = self.space.iter(w_iterable)

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        w_obj = self.space.next(self.w_iterable)
        return self.space.call(self.w_fun, w_obj)

    def descr_reduce(self):
        return self.space.newtuple([self.space.gettypefor(W_StarMap),
                                    self.space.newtuple([
                                        self.w_fun,
                                        self.w_iterable])
                                    ])

def W_StarMap___new__(space, w_subtype, w_fun, w_iterable):
    r = space.allocate_instance(W_StarMap, w_subtype)
    r.__init__(space, w_fun, w_iterable)
    return space.wrap(r)

W_StarMap.typedef = TypeDef(
        'itertools.starmap',
        __new__  = interp2app(W_StarMap___new__),
        __iter__ = interp2app(W_StarMap.iter_w),
        __next__ = interp2app(W_StarMap.next_w),
        __reduce__ = interp2app(W_StarMap.descr_reduce),
        __doc__  = """Make an iterator that computes the function using arguments
    tuples obtained from the iterable. Used instead of imap() when
    argument parameters are already grouped in tuples from a single
    iterable (the data has been ``pre-zipped''). The difference
    between imap() and starmap() parallels the distinction between
    function(a,b) and function(*c).

    Equivalent to :

    def starmap(function, iterable):
        iterable = iter(iterable)
        while True:
            yield function(*iterable.next())
    """)


@unwrap_spec(n=int)
def tee(space, w_iterable, n=2):
    """Return n independent iterators from a single iterable.
    Note : once tee() has made a split, the original iterable
    should not be used anywhere else; otherwise, the iterable could get
    advanced without the tee objects being informed.

    Note : this member of the toolkit may require significant auxiliary
    storage (depending on how much temporary data needs to be stored).
    In general, if one iterator is going to use most or all of the
    data before the other iterator, it is faster to use list() instead
    of tee()

    Equivalent to :

    def tee(iterable, n=2):
        def gen(next, data={}, cnt=[0]):
            for i in count():
                if i == cnt[0]:
                    item = data[i] = next()
                    cnt[0] += 1
                else:
                    item = data[i]   # data.pop(i) if it's the last one
                yield item
        it = iter(iterable)
        return tuple([gen(it.next) for i in range(n)])
    """
    if n < 0:
        raise OperationError(space.w_ValueError, space.wrap("n must be >= 0"))

    if isinstance(w_iterable, W_TeeIterable):     # optimization only
        chained_list = w_iterable.chained_list
        w_iterator = w_iterable.w_iterator
        iterators_w = [w_iterable] * n
        for i in range(1, n):
            iterators_w[i] = space.wrap(W_TeeIterable(space, w_iterator,
                                                      chained_list))
    else:
        w_iterator = space.iter(w_iterable)
        chained_list = TeeChainedListNode()
        iterators_w = [space.wrap(
                           W_TeeIterable(space, w_iterator, chained_list))
                       for x in range(n)]
    return space.newtuple(iterators_w)

class TeeChainedListNode(object):
    w_obj = None


class W_TeeIterable(W_Root):
    def __init__(self, space, w_iterator, chained_list):
        self.space = space
        self.w_iterator = w_iterator
        assert chained_list is not None
        self.chained_list = chained_list

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        chained_list = self.chained_list
        w_obj = chained_list.w_obj
        if w_obj is None:
            w_obj = self.space.next(self.w_iterator)
            chained_list.next = TeeChainedListNode()
            chained_list.w_obj = w_obj
        self.chained_list = chained_list.next
        return w_obj

def W_TeeIterable___new__(space, w_subtype, w_iterable):
    # Obscure and undocumented function.  PyPy only supports w_iterable
    # being a W_TeeIterable, because the case where it is a general
    # iterable is useless and confusing as far as I can tell (as the
    # semantics are then slightly different; see the XXX in lib-python's
    # test_itertools).
    myiter = space.interp_w(W_TeeIterable, w_iterable)
    return space.wrap(W_TeeIterable(space, myiter.w_iterator,
                                           myiter.chained_list))

W_TeeIterable.typedef = TypeDef(
        'itertools._tee',
        __new__ = interp2app(W_TeeIterable___new__),
        __iter__ = interp2app(W_TeeIterable.iter_w),
        __next__ = interp2app(W_TeeIterable.next_w),
        __weakref__ = make_weakref_descr(W_TeeIterable),
        )
W_TeeIterable.typedef.acceptable_as_base_class = False


class W_GroupBy(W_Root):
    def __init__(self, space, w_iterable, w_fun):
        self.space = space
        self.w_iterable = self.space.iter(w_iterable)
        if space.is_none(w_fun):
            self.w_fun = None
        else:
            self.w_fun = w_fun
        self.index = 0
        self.lookahead = False
        self.exhausted = False
        self.started = False
        # new_group - new group not started yet, next should not skip any items
        self.new_group = True
        self.w_lookahead = self.space.w_None
        self.w_key = self.space.w_None

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.exhausted:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

        if not self.new_group:
            self._consume_unwanted_input()

        if not self.started:
            self.started = True
            try:
                w_obj = self.space.next(self.w_iterable)
            except OperationError, e:
                if e.match(self.space, self.space.w_StopIteration):
                    self.exhausted = True
                raise
            else:
                self.w_lookahead = w_obj
                if self.w_fun is None:
                    self.w_key = w_obj
                else:
                    self.w_key = self.space.call_function(self.w_fun, w_obj)
                self.lookahead = True

        self.new_group = False
        w_iterator = self.space.wrap(W_GroupByIterator(self.space, self.index, self))
        return self.space.newtuple([self.w_key, w_iterator])

    def _consume_unwanted_input(self):
        # Consume unwanted input until we reach the next group
        try:
            while True:
                self.group_next(self.index)
        except StopIteration:
            pass
        if self.exhausted:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

    def group_next(self, group_index):
        if group_index < self.index:
            raise StopIteration
        else:
            if self.lookahead:
                self.lookahead = False
                return self.w_lookahead

            try:
                w_obj = self.space.next(self.w_iterable)
            except OperationError, e:
                if e.match(self.space, self.space.w_StopIteration):
                    self.exhausted = True
                    raise StopIteration
                else:
                    raise
            else:
                if self.w_fun is None:
                    w_new_key = w_obj
                else:
                    w_new_key = self.space.call_function(self.w_fun, w_obj)
                if self.space.eq_w(self.w_key, w_new_key):
                    return w_obj
                else:
                    self.index += 1
                    self.w_lookahead = w_obj
                    self.w_key = w_new_key
                    self.lookahead = True
                    self.new_group = True #new group
                    raise StopIteration

    def descr_reduce(self, space):
        if self.started:
            return space.newtuple([
                space.type(self),
                space.newtuple([
                    self.w_iterable,
                    self.w_fun]),
                space.newtuple([
                    self.w_key,
                    self.w_lookahead,
                    self.w_key])
                ])
        else:
            return space.newtuple([
                space.type(self),
                space.newtuple([
                    self.w_iterable,
                    self.w_fun])])

    def descr_setstate(self, space, w_state):
        state = space.unpackiterable(w_state)
        num_args = len(state)
        if num_args != 3:
            raise oefmt(space.w_TypeError,
                        "function takes exactly 3 arguments (%d given)",
                        num_args)
        w_key, w_lookahead, _ = state
        self.w_key = w_key
        self.w_lookahead = w_lookahead
        if self.w_lookahead:
            self.started = True
            self.lookahead = True

def W_GroupBy___new__(space, w_subtype, w_iterable, w_key=None):
    r = space.allocate_instance(W_GroupBy, w_subtype)
    r.__init__(space, w_iterable, w_key)
    return space.wrap(r)

W_GroupBy.typedef = TypeDef(
        'itertools.groupby',
        __new__  = interp2app(W_GroupBy___new__),
        __iter__ = interp2app(W_GroupBy.iter_w),
        __next__ = interp2app(W_GroupBy.next_w),
        __reduce__ = interp2app(W_GroupBy.descr_reduce),
        __setstate__ = interp2app(W_GroupBy.descr_setstate),
        __doc__  = """Make an iterator that returns consecutive keys and groups from the
    iterable. The key is a function computing a key value for each
    element. If not specified or is None, key defaults to an identity
    function and returns the element unchanged. Generally, the
    iterable needs to already be sorted on the same key function.

    The returned group is itself an iterator that shares the
    underlying iterable with groupby(). Because the source is shared,
    when the groupby object is advanced, the previous group is no
    longer visible. So, if that data is needed later, it should be
    stored as a list:

       groups = []
       uniquekeys = []
       for k, g in groupby(data, keyfunc):
           groups.append(list(g))      # Store group iterator as a list
           uniquekeys.append(k)
    """)


class W_GroupByIterator(W_Root):
    def __init__(self, space, index, groupby):
        self.space = space
        self.index = index
        self.groupby = groupby
        self.exhausted = False

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        if self.exhausted:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)

        try:
            w_obj = self.groupby.group_next(self.index)
        except StopIteration:
            self.exhausted = True
            raise OperationError(self.space.w_StopIteration, self.space.w_None)
        else:
            return w_obj

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([
                space.wrap(self.index),
                space.wrap(self.groupby)]),
            ])

def W_GroupByIterator__new__(space, w_subtype, w_index, w_groupby):
    r = space.allocate_instance(W_GroupByIterator, w_subtype)
    index = space.int_w(w_index)
    groupby = space.interp_w(W_GroupBy, w_groupby)
    r.__init__(space, index, groupby)
    return space.wrap(r)

W_GroupByIterator.typedef = TypeDef(
        'itertools._groupby',
        __new__ = interp2app(W_GroupByIterator__new__),
        __iter__ = interp2app(W_GroupByIterator.iter_w),
        __next__ = interp2app(W_GroupByIterator.next_w),
        __reduce__ = interp2app(W_GroupByIterator.descr_reduce))
W_GroupByIterator.typedef.acceptable_as_base_class = False


class W_Compress(W_Root):
    def __init__(self, space, w_data, w_selectors):
        self.space = space
        self.w_data = space.iter(w_data)
        self.w_selectors = space.iter(w_selectors)

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        # No need to check for StopIteration since either w_data
        # or w_selectors will raise this. The shortest one stops first.
        while True:
            w_next_item = self.space.next(self.w_data)
            w_next_selector = self.space.next(self.w_selectors)
            if self.space.is_true(w_next_selector):
                return w_next_item

    def descr_reduce(self, space):
        return space.newtuple([
            space.type(self),
            space.newtuple([self.w_data, self.w_selectors])
        ])


def W_Compress__new__(space, w_subtype, w_data, w_selectors):
    r = space.allocate_instance(W_Compress, w_subtype)
    r.__init__(space, w_data, w_selectors)
    return space.wrap(r)

W_Compress.typedef = TypeDef(
    'itertools.compress',
    __new__ = interp2app(W_Compress__new__),
    __iter__ = interp2app(W_Compress.iter_w),
    __next__ = interp2app(W_Compress.next_w),
    __reduce__ = interp2app(W_Compress.descr_reduce),
    __doc__ = """Make an iterator that filters elements from *data* returning
   only those that have a corresponding element in *selectors* that evaluates to
   ``True``.  Stops when either the *data* or *selectors* iterables has been
   exhausted.
   Equivalent to::

       def compress(data, selectors):
           # compress('ABCDEF', [1,0,1,0,1,1]) --> A C E F
           return (d for d, s in zip(data, selectors) if s)
""")


class W_Product(W_Root):
    def __init__(self, space, args_w, w_repeat):
        self.gears = [
            space.unpackiterable(arg_w)[:] for arg_w in args_w
        ] * space.int_w(w_repeat)
        #
        for gear in self.gears:
            if len(gear) == 0:
                self.lst = None
                self.stopped = True
                break
        else:
            self.indices = [0] * len(self.gears)
            self.lst = None
            self.stopped = False

    def _rotate_previous_gears(self):
        lst = self.lst
        x = len(self.gears) - 1
        lst[x] = self.gears[x][0]
        self.indices[x] = 0
        x -= 1
        # the outer loop runs as long as a we have a carry
        while x >= 0:
            gear = self.gears[x]
            index = self.indices[x] + 1
            if index < len(gear):
                # no carry: done
                lst[x] = gear[index]
                self.indices[x] = index
                return
            lst[x] = gear[0]
            self.indices[x] = 0
            x -= 1
        else:
            self.lst = None
            self.stopped = True

    def fill_next_result(self):
        # the last gear is done here, in a function with no loop,
        # to allow the JIT to look inside
        if self.lst is None:
            self.lst = [None for gear in self.gears]
            for index, gear in enumerate(self.gears):
                self.lst[index] = gear[0]
            return
        lst = self.lst
        x = len(self.gears) - 1
        if x >= 0:
            gear = self.gears[x]
            index = self.indices[x] + 1
            if index < len(gear):
                # no carry: done
                lst[x] = gear[index]
                self.indices[x] = index
            else:
                self._rotate_previous_gears()
        else:
            self.stopped = True

    def iter_w(self, space):
        return space.wrap(self)

    def next_w(self, space):
        if not self.stopped:
            self.fill_next_result()
        if self.stopped:
            raise OperationError(space.w_StopIteration, space.w_None)
        w_result = space.newtuple(self.lst[:])
        return w_result

    def descr_reduce(self, space):
        if not self.stopped:
            gears = [space.newtuple(gear) for gear in self.gears]
            result_w = [
                space.type(self),
                space.newtuple(gears)
            ]
            if self.lst is not None:
                result_w = result_w + [
			    space.newtuple([
                        	space.wrap(index) for index in self.indices])]
        else:
            result_w = [
                space.type(self),
                space.newtuple([space.newtuple([])])
            ]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        gear_count = len(self.gears)
        indices_w = space.unpackiterable(w_state)
        lst = []
        for i, gear in enumerate(self.gears):
            w_index = indices_w[i]
            index = space.int_w(w_index)
            if index < 0:
                index = 0
            if index > gear_count - 1:
                index = gear_count - 1
            self.indices[i] = index
            lst.append(gear[self.indices[i]])
        self.lst = lst

def W_Product__new__(space, w_subtype, __args__):
    arguments_w, kwds_w = __args__.unpack()
    w_repeat = space.wrap(1)
    if kwds_w:
        if 'repeat' in kwds_w:
            w_repeat = kwds_w['repeat']
            del kwds_w['repeat']
        if kwds_w:
            raise OperationError(space.w_TypeError, space.wrap(
                "product() got unexpected keyword argument(s)"))

    r = space.allocate_instance(W_Product, w_subtype)
    r.__init__(space, arguments_w, w_repeat)
    return space.wrap(r)

W_Product.typedef = TypeDef(
    'itertools.product',
    __new__ = interp2app(W_Product__new__),
    __iter__ = interp2app(W_Product.iter_w),
    __next__ = interp2app(W_Product.next_w),
    __reduce__ = interp2app(W_Product.descr_reduce),
    __setstate__ = interp2app(W_Product.descr_setstate),
    __doc__ = """
   Cartesian product of input iterables.

   Equivalent to nested for-loops in a generator expression. For example,
   ``product(A, B)`` returns the same as ``((x,y) for x in A for y in B)``.

   The nested loops cycle like an odometer with the rightmost element advancing
   on every iteration.  This pattern creates a lexicographic ordering so that if
   the input's iterables are sorted, the product tuples are emitted in sorted
   order.

   To compute the product of an iterable with itself, specify the number of
   repetitions with the optional *repeat* keyword argument.  For example,
   ``product(A, repeat=4)`` means the same as ``product(A, A, A, A)``.

   This function is equivalent to the following code, except that the
   actual implementation does not build up intermediate results in memory::

       def product(*args, **kwds):
           # product('ABCD', 'xy') --> Ax Ay Bx By Cx Cy Dx Dy
           # product(range(2), repeat=3) --> 000 001 010 011 100 101 110 111
           pools = map(tuple, args) * kwds.get('repeat', 1)
           result = [[]]
           for pool in pools:
               result = [x+[y] for x in result for y in pool]
           for prod in result:
               yield tuple(prod)
""")


class W_Combinations(W_Root):
    def __init__(self, space, pool_w, indices, r):
        self.pool_w = pool_w
        self.indices = indices
        self.r = r
        self.last_result_w = None
        self.stopped = r > len(pool_w)

    def get_maximum(self, i):
        return i + len(self.pool_w) - self.r

    def max_index(self, j):
        return self.indices[j - 1] + 1

    def descr__iter__(self, space):
        return self

    def descr_next(self, space):
        if self.stopped:
            raise OperationError(space.w_StopIteration, space.w_None)
        if self.last_result_w is None:
            # On the first pass, initialize result tuple using the indices
            result_w = [None] * self.r
            for i in xrange(self.r):
                index = self.indices[i]
                result_w[i] = self.pool_w[index]
        else:
            # Copy the previous result
            result_w = self.last_result_w[:]
            # Scan indices right-to-left until finding one that is not at its
            # maximum
            i = self.r - 1
            while i >= 0 and self.indices[i] == self.get_maximum(i):
                i -= 1

            # If i is negative, then the indices are all at their maximum value
            # and we're done
            if i < 0:
                self.stopped = True
                raise OperationError(space.w_StopIteration, space.w_None)

            # Increment the current index which we know is not at its maximum.
            # Then move back to the right setting each index to its lowest
            # possible value
            self.indices[i] += 1
            for j in xrange(i + 1, self.r):
                self.indices[j] = self.max_index(j)

            # Update the result for the new indices starting with i, the
            # leftmost index that changed
            for i in xrange(i, self.r):
                index = self.indices[i]
                w_elem = self.pool_w[index]
                result_w[i] = w_elem
        self.last_result_w = result_w
        return space.newtuple(result_w)

    def descr_reduce(self, space):
        if self.stopped:
            pool_w = []
        else:
            pool_w = self.pool_w
        result_w = [
            space.type(self),
            space.newtuple([
                space.newtuple(pool_w), space.wrap(self.r)
            ])]
        if self.last_result_w is not None and not self.stopped:
            # we must pickle the indices and use them for setstate
            result_w = result_w + [
                space.newtuple([
                    space.wrap(index) for index in self.indices])]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        indices_w = space.fixedview(w_state)
        if len(indices_w) != self.r:
            raise OperationError(space.w_ValueError, space.wrap(
                "invalid arguments"))
        for i in range(self.r):
            index = space.int_w(indices_w[i])
            max = self.get_maximum(i)
            # clamp the index (beware of negative max)
            if index > max:
                index = max
            if index < 0:
                index = 0
            self.indices[i] = index
        self.last_result_w = [
            self.pool_w[self.indices[i]]
            for i in range(self.r)]

@unwrap_spec(r=int)
def W_Combinations__new__(space, w_subtype, w_iterable, r):
    pool_w = space.fixedview(w_iterable)
    if r < 0:
        raise OperationError(space.w_ValueError,
            space.wrap("r must be non-negative")
        )
    indices = range(r)
    res = space.allocate_instance(W_Combinations, w_subtype)
    res.__init__(space, pool_w, indices, r)
    return space.wrap(res)

W_Combinations.typedef = TypeDef("itertools.combinations",
    __new__ = interp2app(W_Combinations__new__),
    __iter__ = interp2app(W_Combinations.descr__iter__),
    __next__ = interp2app(W_Combinations.descr_next),
    __reduce__ = interp2app(W_Combinations.descr_reduce),
    __setstate__ = interp2app(W_Combinations.descr_setstate),
    __doc__ = """\
combinations(iterable, r) --> combinations object

Return successive r-length combinations of elements in the iterable.

combinations(range(4), 3) --> (0,1,2), (0,1,3), (0,2,3), (1,2,3)""",
)

class W_CombinationsWithReplacement(W_Combinations):
    def __init__(self, space, pool_w, indices, r):
        W_Combinations.__init__(self, space, pool_w, indices, r)
        self.stopped = len(pool_w) == 0 and r > 0

    def get_maximum(self, i):
        return len(self.pool_w) - 1

    def max_index(self, j):
        return self.indices[j - 1]

    def descr_reduce(self, space):
        if self.stopped:
            pool_w = []
        else:
            pool_w = self.pool_w
        result_w = [
            space.type(self),
            space.newtuple([
                space.newtuple(pool_w), space.wrap(self.r)
            ])]
        if self.last_result_w is not None and not self.stopped:
            # we must pickle the indices and use them for setstate
            result_w = result_w + [
                space.newtuple([
                    space.wrap(index) for index in self.indices])]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        indices_w = space.fixedview(w_state)
        if len(indices_w) != self.r:
            raise oefmt(space.w_ValueError, "invalid arguments")
        for i in range(self.r):
            index = space.int_w(indices_w[i])
            max = self.get_maximum(i)
            # clamp the index (beware of negative max)
            if index > max:
                index = max
            if index < 0:
                index = 0
            self.indices[i] = index
        self.last_result_w = [
            self.pool_w[self.indices[i]]
            for i in range(self.r)]

@unwrap_spec(r=int)
def W_CombinationsWithReplacement__new__(space, w_subtype, w_iterable, r):
    pool_w = space.fixedview(w_iterable)
    if r < 0:
        raise OperationError(space.w_ValueError,
                             space.wrap("r must be non-negative"))
    indices = [0] * r
    res = space.allocate_instance(W_CombinationsWithReplacement, w_subtype)
    res.__init__(space, pool_w, indices, r)
    return space.wrap(res)

W_CombinationsWithReplacement.typedef = TypeDef(
    "itertools.combinations_with_replacement",
    __new__ = interp2app(W_CombinationsWithReplacement__new__),
    __iter__ = interp2app(W_CombinationsWithReplacement.descr__iter__),
    __next__ = interp2app(W_CombinationsWithReplacement.descr_next),
    __reduce__ = interp2app(W_CombinationsWithReplacement.descr_reduce),
    __setstate__ = interp2app(W_CombinationsWithReplacement.descr_setstate),
    __doc__ = """\
combinations_with_replacement(iterable, r) --> combinations_with_replacement object

Return successive r-length combinations of elements in the iterable
allowing individual elements to have successive repeats.
combinations_with_replacement('ABC', 2) --> AA AB AC BB BC CC""",
)


class W_Permutations(W_Root):
    def __init__(self, space, pool_w, r):
        self.pool_w = pool_w
        self.r = r
        n = len(pool_w)
        n_minus_r = n - r
        if n_minus_r < 0:
            self.stopped = self.raised_stop_iteration = True
        else:
            self.stopped = self.raised_stop_iteration = False
            self.indices = range(n)
            self.cycles = range(n, n_minus_r, -1)
            self.started = False

    def descr__iter__(self, space):
        return self

    def descr_next(self, space):
        if self.stopped:
            self.raised_stop_iteration = True
            raise OperationError(space.w_StopIteration, space.w_None)
        r = self.r
        indices = self.indices
        w_result = space.newtuple([self.pool_w[indices[i]]
                                   for i in range(r)])
        cycles = self.cycles
        i = r - 1
        while i >= 0:
            j = cycles[i] - 1
            if j > 0:
                cycles[i] = j
                indices[i], indices[-j] = indices[-j], indices[i]
                return w_result
            cycles[i] = len(indices) - i
            n1 = len(indices) - 1
            assert n1 >= 0
            num = indices[i]
            for k in range(i, n1):
                indices[k] = indices[k+1]
            indices[n1] = num
            i -= 1
        self.stopped = True
        if self.started:
            raise OperationError(space.w_StopIteration, space.w_None)
        else:
            self.started = True
        return w_result

    def descr_reduce(self, space):
        if self.raised_stop_iteration:
            pool_w = []
        else:
            pool_w = self.pool_w
        result_w = [
            space.type(self),
            space.newtuple([
                space.newtuple(pool_w), space.wrap(self.r)
            ])]
        if not self.raised_stop_iteration:
            # we must pickle the indices and use them for setstate
            result_w = result_w + [
                space.newtuple([
                    space.newtuple([
                        space.wrap(index) for index in self.indices]),
                    space.newtuple([
                        space.wrap(num) for num in self.cycles]),
                    space.wrap(self.started)
                ])]
        return space.newtuple(result_w)

    def descr_setstate(self, space, w_state):
        state = space.unpackiterable(w_state)
        if len(state) == 3:
            w_indices, w_cycles, w_started = state
            indices_w = space.unpackiterable(w_indices)
            cycles_w = space.unpackiterable(w_cycles)
            self.started = space.bool_w(w_started)
        else:
            raise oefmt(space.w_ValueError, "invalid arguments")

        if len(indices_w) != len(self.pool_w) or len(cycles_w) != self.r:
            raise oefmt(space.w_ValueError, "inavalid arguments")

        n = len(self.pool_w)
        for i in range(n):
            index = space.int_w(indices_w[i])
            if index < 0:
                index = 0
            elif index > n-1:
                index = n-1
            self.indices[i] = index

        for i in range(self.r):
            index = space.int_w(cycles_w[i])
            if index < 1:
                index = 1
            elif index > n-i:
                index = n-i
            self.cycles[i] = index

def W_Permutations__new__(space, w_subtype, w_iterable, w_r=None):
    pool_w = space.fixedview(w_iterable)
    if space.is_none(w_r):
        r = len(pool_w)
    else:
        r = space.gateway_nonnegint_w(w_r)
    res = space.allocate_instance(W_Permutations, w_subtype)
    res.__init__(space, pool_w, r)
    return space.wrap(res)

W_Permutations.typedef = TypeDef("itertools.permutations",
    __new__ = interp2app(W_Permutations__new__),
    __iter__ = interp2app(W_Permutations.descr__iter__),
    __next__ = interp2app(W_Permutations.descr_next),
    __reduce__ = interp2app(W_Permutations.descr_reduce),
    __setstate__ = interp2app(W_Permutations.descr_setstate),
    __doc__ = """\
permutations(iterable[, r]) --> permutations object

Return successive r-length permutations of elements in the iterable.

permutations(range(3), 2) --> (0,1), (0,2), (1,0), (1,2), (2,0), (2,1)""",
)


class W_Accumulate(W_Root):
    def __init__(self, space, w_iterable, w_func=None):
        self.space = space
        self.w_iterable = w_iterable
        self.w_func = w_func if not space.is_w(w_func, space.w_None) else None
        self.w_total = None

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        w_value = self.space.next(self.w_iterable)
        if self.w_total is None:
            self.w_total = w_value
            return w_value

        if self.w_func is None:
            self.w_total = self.space.add(self.w_total, w_value)
        else:
            self.w_total = self.space.call_function(self.w_func, self.w_total, w_value)
        return self.w_total

    def reduce_w(self):
        space = self.space
        w_total = space.w_None if self.w_total is None else self.w_total
        w_func = space.w_None if self.w_func is None else self.w_func
        return space.newtuple([space.gettypefor(W_Accumulate),
                               space.newtuple([self.w_iterable, w_func]), w_total])

    def setstate_w(self, space, w_state):
        self.w_total = w_state if not space.is_w(w_state, space.w_None) else None

def W_Accumulate__new__(space, w_subtype, w_iterable, w_func=None):
    r = space.allocate_instance(W_Accumulate, w_subtype)
    r.__init__(space, space.iter(w_iterable), w_func)
    return space.wrap(r)

W_Accumulate.typedef = TypeDef("itertools.accumulate",
    __new__  = interp2app(W_Accumulate__new__),
    __iter__ = interp2app(W_Accumulate.iter_w),
    __next__ = interp2app(W_Accumulate.next_w),
    __reduce__ = interp2app(W_Accumulate.reduce_w),
    __setstate__ = interp2app(W_Accumulate.setstate_w),
    __doc__  = """\
"accumulate(iterable) --> accumulate object

Return series of accumulated sums.""")
