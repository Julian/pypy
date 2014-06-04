"""Incremental version of the MiniMark GC.

Environment variables can be used to fine-tune the following parameters:

 PYPY_GC_NURSERY         The nursery size.  Defaults to 1/2 of your cache or
                         '4M'.  Small values
                         (like 1 or 1KB) are useful for debugging.

 PYPY_GC_NURSERY_CLEANUP The interval at which nursery is cleaned up. Must
                         be smaller than the nursery size and bigger than the
                         biggest object we can allotate in the nursery.

 PYPY_GC_INCREMENT_STEP  The size of memory marked during the marking step.
                         Default is size of nursery * 2. If you mark it too high
                         your GC is not incremental at all. The minimum is set
                         to size that survives minor collection * 1.5 so we
                         reclaim anything all the time.

 PYPY_GC_MAJOR_COLLECT   Major collection memory factor.  Default is '1.82',
                         which means trigger a major collection when the
                         memory consumed equals 1.82 times the memory
                         really used at the end of the previous major
                         collection.

 PYPY_GC_GROWTH          Major collection threshold's max growth rate.
                         Default is '1.4'.  Useful to collect more often
                         than normally on sudden memory growth, e.g. when
                         there is a temporary peak in memory usage.

 PYPY_GC_MAX             The max heap size.  If coming near this limit, it
                         will first collect more often, then raise an
                         RPython MemoryError, and if that is not enough,
                         crash the program with a fatal error.  Try values
                         like '1.6GB'.

 PYPY_GC_MAX_DELTA       The major collection threshold will never be set
                         to more than PYPY_GC_MAX_DELTA the amount really
                         used after a collection.  Defaults to 1/8th of the
                         total RAM size (which is constrained to be at most
                         2/3/4GB on 32-bit systems).  Try values like '200MB'.

 PYPY_GC_MIN             Don't collect while the memory size is below this
                         limit.  Useful to avoid spending all the time in
                         the GC in very small programs.  Defaults to 8
                         times the nursery.

 PYPY_GC_DEBUG           Enable extra checks around collections that are
                         too slow for normal use.  Values are 0 (off),
                         1 (on major collections) or 2 (also on minor
                         collections).
"""
# XXX Should find a way to bound the major collection threshold by the
# XXX total addressable size.  Maybe by keeping some minimarkpage arenas
# XXX pre-reserved, enough for a few nursery collections?  What about
# XXX raw-malloced memory?
import sys
from rpython.rtyper.lltypesystem import lltype, llmemory, llarena, llgroup
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.lltypesystem.llmemory import raw_malloc_usage
from rpython.memory.gc.base import GCBase, MovingGCBase
from rpython.memory.gc import env
from rpython.memory.support import mangle_hash
from rpython.rlib.rarithmetic import ovfcheck, LONG_BIT, intmask, r_uint
from rpython.rlib.rarithmetic import LONG_BIT_SHIFT
from rpython.rlib.debug import ll_assert, debug_print, debug_start, debug_stop
from rpython.rlib.objectmodel import specialize

#
# Handles the objects in 2 generations:
#
#  * young objects: allocated in the nursery if they are not too large, or
#    raw-malloced otherwise.  The nursery is a fixed-size memory buffer of
#    4MB by default.  When full, we do a minor collection;
#    the surviving objects from the nursery are moved outside, and the
#    non-surviving raw-malloced objects are freed.  All surviving objects
#    become old.
#
#  * old objects: never move again.  These objects are either allocated by
#    minimarkpage.py (if they are small), or raw-malloced (if they are not
#    small).  Collected by regular mark-n-sweep during major collections.
#
# XXX update doc string to contain object pinning (groggi)

WORD = LONG_BIT // 8
NULL = llmemory.NULL

first_gcflag = 1 << (LONG_BIT//2)

# The following flag is set on objects if we need to do something to
# track the young pointers that it might contain.  The flag is not set
# on young objects (unless they are large arrays, see below), and we
# simply assume that any young object can point to any other young object.
# For old and prebuilt objects, the flag is usually set, and is cleared
# when we write any pointer to it.  For large arrays with
# GCFLAG_HAS_CARDS, we rely on card marking to track where the
# young pointers are; the flag GCFLAG_TRACK_YOUNG_PTRS is set in this
# case too, to speed up the write barrier.
GCFLAG_TRACK_YOUNG_PTRS = first_gcflag << 0

# The following flag is set on some prebuilt objects.  The flag is set
# unless the object is already listed in 'prebuilt_root_objects'.
# When a pointer is written inside an object with GCFLAG_NO_HEAP_PTRS
# set, the write_barrier clears the flag and adds the object to
# 'prebuilt_root_objects'.
GCFLAG_NO_HEAP_PTRS = first_gcflag << 1

# The following flag is set on surviving objects during a major collection.
GCFLAG_VISITED      = first_gcflag << 2

# The following flag is set on nursery objects of which we asked the id
# or the identityhash.  It means that a space of the size of the object
# has already been allocated in the nonmovable part.  The same flag is
# abused to mark prebuilt objects whose hash has been taken during
# translation and is statically recorded.
GCFLAG_HAS_SHADOW   = first_gcflag << 3

# The following flag is set temporarily on some objects during a major
# collection.  See pypy/doc/discussion/finalizer-order.txt
GCFLAG_FINALIZATION_ORDERING = first_gcflag << 4

# This flag is reserved for RPython.
GCFLAG_EXTRA        = first_gcflag << 5

# The following flag is set on externally raw_malloc'ed arrays of pointers.
# They are allocated with some extra space in front of them for a bitfield,
# one bit per 'card_page_indices' indices.
GCFLAG_HAS_CARDS    = first_gcflag << 6
GCFLAG_CARDS_SET    = first_gcflag << 7     # <- at least one card bit is set
# note that GCFLAG_CARDS_SET is the most significant bit of a byte:
# this is required for the JIT (x86)

# The following flag is set on surviving raw-malloced young objects during
# a minor collection.
GCFLAG_VISITED_RMY   = first_gcflag << 8

# The following flag is set on nursery objects of which we expect not to
# move.  This means that a young object with this flag is not moved out
# of the nursery during a minor collection. See pin()/unpin() for further
# details.
GCFLAG_PINNED        = first_gcflag << 9

_GCFLAG_FIRST_UNUSED = first_gcflag << 10    # the first unused bit


# States for the incremental GC

# The scanning phase, next step call will scan the current roots
# This state must complete in a single step
STATE_SCANNING = 0

# The marking phase. We walk the list 'objects_to_trace' of all gray objects
# and mark all of the things they point to gray. This step lasts until there
# are no more gray objects.
STATE_MARKING = 1

# here we kill all the unvisited objects
STATE_SWEEPING = 2

# here we call all the finalizers
STATE_FINALIZING = 3

GC_STATES = ['SCANNING', 'MARKING', 'SWEEPING', 'FINALIZING']


FORWARDSTUB = lltype.GcStruct('forwarding_stub',
                              ('forw', llmemory.Address))
FORWARDSTUBPTR = lltype.Ptr(FORWARDSTUB)
NURSARRAY = lltype.Array(llmemory.Address)

# ____________________________________________________________

class IncrementalMiniMarkGC(MovingGCBase):
    _alloc_flavor_ = "raw"
    inline_simple_malloc = True
    inline_simple_malloc_varsize = True
    needs_write_barrier = True
    prebuilt_gc_objects_are_static_roots = False
    malloc_zero_filled = True    # xxx experiment with False
    can_usually_pin_objects = True
    gcflag_extra = GCFLAG_EXTRA

    # All objects start with a HDR, i.e. with a field 'tid' which contains
    # a word.  This word is divided in two halves: the lower half contains
    # the typeid, and the upper half contains various flags, as defined
    # by GCFLAG_xxx above.
    HDR = lltype.Struct('header', ('tid', lltype.Signed))
    typeid_is_in_field = 'tid'
    withhash_flag_is_in_field = 'tid', GCFLAG_HAS_SHADOW
    # ^^^ prebuilt objects may have the flag GCFLAG_HAS_SHADOW;
    #     then they are one word longer, the extra word storing the hash.


    # During a minor collection, the objects in the nursery that are
    # moved outside are changed in-place: their header is replaced with
    # the value -42, and the following word is set to the address of
    # where the object was moved.  This means that all objects in the
    # nursery need to be at least 2 words long, but objects outside the
    # nursery don't need to.
    minimal_size_in_nursery = (
        llmemory.sizeof(HDR) + llmemory.sizeof(llmemory.Address))


    TRANSLATION_PARAMS = {
        # Automatically adjust the size of the nursery and the
        # 'major_collection_threshold' from the environment.
        # See docstring at the start of the file.
        "read_from_env": True,

        # The size of the nursery.  Note that this is only used as a
        # fall-back number.
        "nursery_size": 896*1024,

        # The system page size.  Like obmalloc.c, we assume that it is 4K
        # for 32-bit systems; unlike obmalloc.c, we assume that it is 8K
        # for 64-bit systems, for consistent results.
        "page_size": 1024*WORD,

        # The size of an arena.  Arenas are groups of pages allocated
        # together.
        "arena_size": 65536*WORD,

        # The maximum size of an object allocated compactly.  All objects
        # that are larger are just allocated with raw_malloc().  Note that
        # the size limit for being first allocated in the nursery is much
        # larger; see below.
        "small_request_threshold": 35*WORD,

        # Full collection threshold: after a major collection, we record
        # the total size consumed; and after every minor collection, if the
        # total size is now more than 'major_collection_threshold' times,
        # we trigger the next major collection.
        "major_collection_threshold": 1.82,

        # Threshold to avoid that the total heap size grows by a factor of
        # major_collection_threshold at every collection: it can only
        # grow at most by the following factor from one collection to the
        # next.  Used e.g. when there is a sudden, temporary peak in memory
        # usage; this avoids that the upper bound grows too fast.
        "growth_rate_max": 1.4,

        # The number of array indices that are mapped to a single bit in
        # write_barrier_from_array().  Must be a power of two.  The default
        # value of 128 means that card pages are 512 bytes (1024 on 64-bits)
        # in regular arrays of pointers; more in arrays whose items are
        # larger.  A value of 0 disables card marking.
        "card_page_indices": 128,

        # Objects whose total size is at least 'large_object' bytes are
        # allocated out of the nursery immediately, as old objects.  The
        # minimal allocated size of the nursery is 2x the following
        # number (by default, at least 132KB on 32-bit and 264KB on 64-bit).
        "large_object": (16384+512)*WORD,

        # This is the chunk that we cleanup in the nursery. The point is
        # to avoid having to trash all the caches just to zero the nursery,
        # so we trade it by cleaning it bit-by-bit, as we progress through
        # nursery. Has to fit at least one large object
        "nursery_cleanup": 32768 * WORD,

        # Number of  objects that are allowed to be pinned in the nursery
        # at the same time.  Must be lesser than or equal to the chunk size
        # of an AddressStack.
        "max_number_of_pinned_objects": 100,
        }

    def __init__(self, config,
                 read_from_env=False,
                 nursery_size=32*WORD,
                 nursery_cleanup=9*WORD,
                 page_size=16*WORD,
                 arena_size=64*WORD,
                 small_request_threshold=5*WORD,
                 major_collection_threshold=2.5,
                 growth_rate_max=2.5,   # for tests
                 card_page_indices=0,
                 max_number_of_pinned_objects=100,
                 large_object=8*WORD,
                 ArenaCollectionClass=None,
                 **kwds):
        MovingGCBase.__init__(self, config, **kwds)
        assert small_request_threshold % WORD == 0
        self.read_from_env = read_from_env
        self.nursery_size = nursery_size
        self.nursery_cleanup = nursery_cleanup
        self.small_request_threshold = small_request_threshold
        self.major_collection_threshold = major_collection_threshold
        self.growth_rate_max = growth_rate_max
        self.num_major_collects = 0
        self.min_heap_size = 0.0
        self.max_heap_size = 0.0
        self.max_heap_size_already_raised = False
        self.max_delta = float(r_uint(-1))
        self.max_number_of_pinned_objects = max_number_of_pinned_objects
        #
        self.card_page_indices = card_page_indices
        if self.card_page_indices > 0:
            self.card_page_shift = 0
            while (1 << self.card_page_shift) < self.card_page_indices:
                self.card_page_shift += 1
        #
        # 'large_object' limit how big objects can be in the nursery, so
        # it gives a lower bound on the allowed size of the nursery.
        self.nonlarge_max = large_object - 1
        #
        self.nursery      = NULL
        self.nursery_free = NULL
        self.nursery_top  = NULL
        self.nursery_real_top = NULL
        self.debug_tiny_nursery = -1
        self.debug_rotating_nurseries = lltype.nullptr(NURSARRAY)
        self.extra_threshold = 0
        #
        # The ArenaCollection() handles the nonmovable objects allocation.
        if ArenaCollectionClass is None:
            from rpython.memory.gc import minimarkpage
            ArenaCollectionClass = minimarkpage.ArenaCollection
        self.ac = ArenaCollectionClass(arena_size, page_size,
                                       small_request_threshold)
        #
        # Used by minor collection: a list of (mostly non-young) objects that
        # (may) contain a pointer to a young object.  Populated by
        # the write barrier: when we clear GCFLAG_TRACK_YOUNG_PTRS, we
        # add it to this list.
        # Note that young array objects may (by temporary "mistake") be added
        # to this list, but will be removed again at the start of the next
        # minor collection.
        self.old_objects_pointing_to_young = self.AddressStack()
        #
        # Similar to 'old_objects_pointing_to_young', but lists objects
        # that have the GCFLAG_CARDS_SET bit.  For large arrays.  Note
        # that it is possible for an object to be listed both in here
        # and in 'old_objects_pointing_to_young', in which case we
        # should just clear the cards and trace it fully, as usual.
        # Note also that young array objects are never listed here.
        self.old_objects_with_cards_set = self.AddressStack()
        #
        # A list of all prebuilt GC objects that contain pointers to the heap
        self.prebuilt_root_objects = self.AddressStack()
        #
        self._init_writebarrier_logic()


    def setup(self):
        """Called at run-time to initialize the GC."""
        #
        # Hack: MovingGCBase.setup() sets up stuff related to id(), which
        # we implement differently anyway.  So directly call GCBase.setup().
        GCBase.setup(self)
        #
        # Two lists of all raw_malloced objects (the objects too large)
        self.young_rawmalloced_objects = self.null_address_dict()
        self.old_rawmalloced_objects = self.AddressStack()
        self.raw_malloc_might_sweep = self.AddressStack()
        self.rawmalloced_total_size = r_uint(0)

        self.gc_state = STATE_SCANNING
        #
        # A list of all objects with finalizers (these are never young).
        self.objects_with_finalizers = self.AddressDeque()
        self.young_objects_with_light_finalizers = self.AddressStack()
        self.old_objects_with_light_finalizers = self.AddressStack()
        #
        # Two lists of the objects with weakrefs.  No weakref can be an
        # old object weakly pointing to a young object: indeed, weakrefs
        # are immutable so they cannot point to an object that was
        # created after it.
        self.young_objects_with_weakrefs = self.AddressStack()
        self.old_objects_with_weakrefs = self.AddressStack()
        #
        # Support for id and identityhash: map nursery objects with
        # GCFLAG_HAS_SHADOW to their future location at the next
        # minor collection.
        self.nursery_objects_shadows = self.AddressDict()
        #
        # A sorted deque containing all pinned objects *before* the last
        # minor collection. This deque must be consulted when considering
        # next nursery ceiling.
        self.nursery_barriers = self.AddressDeque()
        #
        # Counter tracking how many pinned objects currently reside inside
        # the nursery.
        self.pinned_objects_in_nursery = 0
        #
        # Allocate a nursery.  In case of auto_nursery_size, start by
        # allocating a very small nursery, enough to do things like look
        # up the env var, which requires the GC; and then really
        # allocate the nursery of the final size.
        if not self.read_from_env:
            self.allocate_nursery()
            self.gc_increment_step = self.nursery_size * 4
        else:
            #
            defaultsize = self.nursery_size
            minsize = 2 * (self.nonlarge_max + 1)
            self.nursery_size = minsize
            self.allocate_nursery()
            #
            # From there on, the GC is fully initialized and the code
            # below can use it
            newsize = env.read_from_env('PYPY_GC_NURSERY')
            # PYPY_GC_NURSERY=smallvalue means that minor collects occur
            # very frequently; the extreme case is PYPY_GC_NURSERY=1, which
            # forces a minor collect for every malloc.  Useful to debug
            # external factors, like trackgcroot or the handling of the write
            # barrier.  Implemented by still using 'minsize' for the nursery
            # size (needed to handle mallocs just below 'large_objects') but
            # hacking at the current nursery position in collect_and_reserve().
            if newsize <= 0:
                newsize = env.estimate_best_nursery_size()
                if newsize <= 0:
                    newsize = defaultsize
            if newsize < minsize:
                self.debug_tiny_nursery = newsize & ~(WORD-1)
                newsize = minsize

            nurs_cleanup = env.read_from_env('PYPY_GC_NURSERY_CLEANUP')
            if nurs_cleanup > 0:
                self.nursery_cleanup = nurs_cleanup
            #
            major_coll = env.read_float_from_env('PYPY_GC_MAJOR_COLLECT')
            if major_coll > 1.0:
                self.major_collection_threshold = major_coll
            #
            growth = env.read_float_from_env('PYPY_GC_GROWTH')
            if growth > 1.0:
                self.growth_rate_max = growth
            #
            min_heap_size = env.read_uint_from_env('PYPY_GC_MIN')
            if min_heap_size > 0:
                self.min_heap_size = float(min_heap_size)
            else:
                # defaults to 8 times the nursery
                self.min_heap_size = newsize * 8
            #
            max_heap_size = env.read_uint_from_env('PYPY_GC_MAX')
            if max_heap_size > 0:
                self.max_heap_size = float(max_heap_size)
            #
            max_delta = env.read_uint_from_env('PYPY_GC_MAX_DELTA')
            if max_delta > 0:
                self.max_delta = float(max_delta)
            else:
                self.max_delta = 0.125 * env.get_total_memory()

            gc_increment_step = env.read_uint_from_env('PYPY_GC_INCREMENT_STEP')
            if gc_increment_step > 0:
                self.gc_increment_step = gc_increment_step
            else:
                self.gc_increment_step = newsize * 4
            #
            self.minor_collection()    # to empty the nursery
            llarena.arena_free(self.nursery)
            self.nursery_size = newsize
            self.allocate_nursery()
        #
        if self.nursery_cleanup < self.nonlarge_max + 1:
            self.nursery_cleanup = self.nonlarge_max + 1
        # We need exactly initial_cleanup + N*nursery_cleanup = nursery_size.
        # We choose the value of initial_cleanup to be between 1x and 2x the
        # value of nursery_cleanup.
        self.initial_cleanup = self.nursery_cleanup + (
                self.nursery_size % self.nursery_cleanup)
        if (r_uint(self.initial_cleanup) > r_uint(self.nursery_size) or
            self.debug_tiny_nursery >= 0):
            self.initial_cleanup = self.nursery_size

    def _nursery_memory_size(self):
        extra = self.nonlarge_max + 1
        return self.nursery_size + extra

    def _alloc_nursery(self):
        # the start of the nursery: we actually allocate a bit more for
        # the nursery than really needed, to simplify pointer arithmetic
        # in malloc_fixedsize_clear().  The few extra pages are never used
        # anyway so it doesn't even count.
        nursery = llarena.arena_malloc(self._nursery_memory_size(), 2)
        if not nursery:
            raise MemoryError("cannot allocate nursery")
        return nursery

    def allocate_nursery(self):
        debug_start("gc-set-nursery-size")
        debug_print("nursery size:", self.nursery_size)
        self.nursery = self._alloc_nursery()
        # the current position in the nursery:
        self.nursery_free = self.nursery
        # the end of the nursery:
        self.nursery_top = self.nursery + self.nursery_size
        self.nursery_real_top = self.nursery_top
        # initialize the threshold
        self.min_heap_size = max(self.min_heap_size, self.nursery_size *
                                              self.major_collection_threshold)
        # the following two values are usually equal, but during raw mallocs
        # of arrays, next_major_collection_threshold is decremented to make
        # the next major collection arrive earlier.
        # See translator/c/test/test_newgc, test_nongc_attached_to_gc
        self.next_major_collection_initial = self.min_heap_size
        self.next_major_collection_threshold = self.min_heap_size
        self.set_major_threshold_from(0.0)
        ll_assert(self.extra_threshold == 0, "extra_threshold set too early")
        self.initial_cleanup = self.nursery_size
        debug_stop("gc-set-nursery-size")


    def set_major_threshold_from(self, threshold, reserving_size=0):
        # Set the next_major_collection_threshold.
        threshold_max = (self.next_major_collection_initial *
                         self.growth_rate_max)
        if threshold > threshold_max:
            threshold = threshold_max
        #
        threshold += reserving_size
        if threshold < self.min_heap_size:
            threshold = self.min_heap_size
        #
        if self.max_heap_size > 0.0 and threshold > self.max_heap_size:
            threshold = self.max_heap_size
            bounded = True
        else:
            bounded = False
        #
        self.next_major_collection_initial = threshold
        self.next_major_collection_threshold = threshold
        return bounded


    def post_setup(self):
        # set up extra stuff for PYPY_GC_DEBUG.
        MovingGCBase.post_setup(self)
        if self.DEBUG and llarena.has_protect:
            # gc debug mode: allocate 23 nurseries instead of just 1,
            # and use them alternatively, while mprotect()ing the unused
            # ones to detect invalid access.
            debug_start("gc-debug")
            self.debug_rotating_nurseries = lltype.malloc(
                NURSARRAY, 22, flavor='raw', track_allocation=False)
            i = 0
            while i < 22:
                nurs = self._alloc_nursery()
                llarena.arena_protect(nurs, self._nursery_memory_size(), True)
                self.debug_rotating_nurseries[i] = nurs
                i += 1
            debug_print("allocated", len(self.debug_rotating_nurseries),
                        "extra nurseries")
            debug_stop("gc-debug")

    def debug_rotate_nursery(self):
        if self.debug_rotating_nurseries:
            debug_start("gc-debug")
            oldnurs = self.nursery
            llarena.arena_protect(oldnurs, self._nursery_memory_size(), True)
            #
            newnurs = self.debug_rotating_nurseries[0]
            i = 0
            while i < len(self.debug_rotating_nurseries) - 1:
                self.debug_rotating_nurseries[i] = (
                    self.debug_rotating_nurseries[i + 1])
                i += 1
            self.debug_rotating_nurseries[i] = oldnurs
            #
            llarena.arena_protect(newnurs, self._nursery_memory_size(), False)
            self.nursery = newnurs
            self.nursery_top = self.nursery + self.initial_cleanup
            self.nursery_real_top = self.nursery + self.nursery_size
            debug_print("switching from nursery", oldnurs,
                        "to nursery", self.nursery,
                        "size", self.nursery_size)
            debug_stop("gc-debug")


    def malloc_fixedsize_clear(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size
        rawtotalsize = raw_malloc_usage(totalsize)
        #
        # If the object needs a finalizer, ask for a rawmalloc.
        # The following check should be constant-folded.
        if needs_finalizer and not is_finalizer_light:
            ll_assert(not contains_weakptr,
                     "'needs_finalizer' and 'contains_weakptr' both specified")
            obj = self.external_malloc(typeid, 0, can_make_young=False)
            self.objects_with_finalizers.append(obj)
        #
        # If totalsize is greater than nonlarge_max (which should never be
        # the case in practice), ask for a rawmalloc.  The following check
        # should be constant-folded.
        elif rawtotalsize > self.nonlarge_max:
            ll_assert(not contains_weakptr,
                      "'contains_weakptr' specified for a large object")
            obj = self.external_malloc(typeid, 0)
            #
        else:
            # If totalsize is smaller than minimal_size_in_nursery, round it
            # up.  The following check should also be constant-folded.
            min_size = raw_malloc_usage(self.minimal_size_in_nursery)
            if rawtotalsize < min_size:
                totalsize = rawtotalsize = min_size
            #
            # Get the memory from the nursery.  If there is not enough space
            # there, do a collect first.
            result = self.nursery_free
            self.nursery_free = result + totalsize
            if self.nursery_free > self.nursery_top:
                result = self.collect_and_reserve(result, totalsize)
            #
            # Build the object.
            llarena.arena_reserve(result, totalsize)
            obj = result + size_gc_header
            if is_finalizer_light:
                self.young_objects_with_light_finalizers.append(obj)
            self.init_gc_object(result, typeid, flags=0)
            #
            # If it is a weakref, record it (check constant-folded).
            if contains_weakptr:
                self.young_objects_with_weakrefs.append(obj)
        #
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)


    def malloc_varsize_clear(self, typeid, length, size, itemsize,
                             offset_to_length):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        nonvarsize = size_gc_header + size
        #
        # Compute the maximal length that makes the object still
        # below 'nonlarge_max'.  All the following logic is usually
        # constant-folded because self.nonlarge_max, size and itemsize
        # are all constants (the arguments are constant due to
        # inlining).
        maxsize = self.nonlarge_max - raw_malloc_usage(nonvarsize)
        if maxsize < 0:
            toobig = r_uint(0)    # the nonvarsize alone is too big
        elif raw_malloc_usage(itemsize):
            toobig = r_uint(maxsize // raw_malloc_usage(itemsize)) + 1
        else:
            toobig = r_uint(sys.maxint) + 1

        if r_uint(length) >= r_uint(toobig):
            #
            # If the total size of the object would be larger than
            # 'nonlarge_max', then allocate it externally.  We also
            # go there if 'length' is actually negative.
            obj = self.external_malloc(typeid, length)
            #
        else:
            # With the above checks we know now that totalsize cannot be more
            # than 'nonlarge_max'; in particular, the + and * cannot overflow.
            totalsize = nonvarsize + itemsize * length
            totalsize = llarena.round_up_for_allocation(totalsize)
            #
            # 'totalsize' should contain at least the GC header and
            # the length word, so it should never be smaller than
            # 'minimal_size_in_nursery'
            ll_assert(raw_malloc_usage(totalsize) >=
                      raw_malloc_usage(self.minimal_size_in_nursery),
                      "malloc_varsize_clear(): totalsize < minimalsize")
            #
            # Get the memory from the nursery.  If there is not enough space
            # there, do a collect first.
            result = self.nursery_free
            self.nursery_free = result + totalsize
            if self.nursery_free > self.nursery_top:
                result = self.collect_and_reserve(result, totalsize)
            #
            # Build the object.
            llarena.arena_reserve(result, totalsize)
            self.init_gc_object(result, typeid, flags=0)
            #
            # Set the length and return the object.
            obj = result + size_gc_header
            (obj + offset_to_length).signed[0] = length
        #
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)


    def collect(self, gen=2):
        """Do a minor (gen=0), start a major (gen=1), or do a full
        major (gen>=2) collection."""
        if gen <= 1:
            self.minor_collection()
            if gen == 1 or self.gc_state != STATE_SCANNING:
                self.major_collection_step()
        else:
            self.minor_and_major_collection()

    def move_nursery_top(self, totalsize):
        size = self.nursery_cleanup
        ll_assert(self.nursery_real_top - self.nursery_top >= size,
            "nursery_cleanup not a divisor of nursery_size - initial_cleanup")
        ll_assert(llmemory.raw_malloc_usage(totalsize) <= size,
            "totalsize > nursery_cleanup")
        llarena.arena_reset(self.nursery_top, size, 2)
        self.nursery_top += size
    move_nursery_top._always_inline_ = True

    def collect_and_reserve(self, prev_result, totalsize):
        """To call when nursery_free overflows nursery_top.
        In case of pinned objects try to reserve 'totalsize' between
        two pinned objects. In this case we can just continue.
        Otherwise check first if the nursery_top is the real top,
        if not we can just move the top of one cleanup and continue.

        Do a minor collection, and possibly also a major collection,
        and finally reserve 'totalsize' bytes at the start of the
        now-empty nursery.
        """
        # be careful: 'nursery_free' may have been changed by the caller.
        # 'prev_result' contains the expected address for the new object.

        # keep track how many iteration we've gone trough
        minor_collection_count = 0
        while True:
            if self.nursery_barriers.non_empty():
                # we have multiple blocks of free memory in the nursery
                # which are divided by pinned object. First thing to do
                # is to move to the next free block.
                size_gc_header = self.gcheaderbuilder.size_gc_header
                pinned_obj_size = size_gc_header + self.get_size(
                    self.nursery_top + size_gc_header)
                #
                # move search area to the next free memory block in the
                # nursery.
                self.nursery_free = self.nursery_top + pinned_obj_size
                self.nursery_top = self.nursery_barriers.popleft()
            else:
                #
                # no barriers (i.e. pinned objects) after 'nursery_free'.
                # if possible just enlarge the used part of the nursery.
                # otherwise we are forced to clean up the nursery.
                if self.nursery_top < self.nursery_real_top:
                    self.move_nursery_top(totalsize)
                    return prev_nursery_free
                #
                self.minor_collection()
                minor_collection_count += 1
                if minor_collection_count == 1:
                    #
                    # If the gc_state is not STATE_SCANNING, we're in the middle of
                    # an incremental major collection.  In this case, always progress
                    # one step.  If the gc_state is STATE_SCANNING, wait until there
                    # is too much garbage before starting the next major collection.
                    if (self.gc_state != STATE_SCANNING or
                                self.get_total_memory_used() >
                                self.next_major_collection_threshold):
                        self.major_collection_step()
                        #
                        # The nursery might not be empty now, because of
                        # execute_finalizers() or pinned objects.  If it is almost
                        # full again, we need to fix it with another call to
                        # minor_collection().
                        if self.nursery_free + totalsize > self.nursery_top:
                            #
                            if self.nursery_free + totalsize > self.nursery_real_top:
                                # still not enough space, we need to collect.
                                # maybe nursery contains too many pinned objects (see
                                # assert below).
                                self.minor_collection()
                            else:
                                # execute loop one more time. This should find
                                # enough space to allocate the object
                                pass
                else:
                    ll_assert(minor_collection_count == 2,
                        "Seeing minor_collection() at least twice. "
                        "Too many pinned objects?")
            #
            # attempt to get 'totalzise' out of the nursery now.  This may
            # fail again, and then we loop.
            result = self.nursery_free
            self.nursery_free = result + totalsize
            if self.nursery_free <= self.nursery_top:
                break
        #
        if self.debug_tiny_nursery >= 0:   # for debugging
            # XXX solution for this assert? (groggi)
            ll_assert(not self.nursery_barriers.non_empty(),
                "no support for nursery debug and pinning")
            if self.nursery_top - self.nursery_free > self.debug_tiny_nursery:
                self.nursery_free = self.nursery_top - self.debug_tiny_nursery
        #
        return result
    collect_and_reserve._dont_inline_ = True


    def external_malloc(self, typeid, length, can_make_young=True):
        """Allocate a large object using the ArenaCollection or
        raw_malloc(), possibly as an object with card marking enabled,
        if it has gc pointers in its var-sized part.  'length' should be
        specified as 0 if the object is not varsized.  The returned
        object is fully initialized and zero-filled."""
        #
        # Here we really need a valid 'typeid', not 0 (as the JIT might
        # try to send us if there is still a bug).
        ll_assert(bool(self.combine(typeid, 0)),
                  "external_malloc: typeid == 0")
        #
        # Compute the total size, carefully checking for overflows.
        size_gc_header = self.gcheaderbuilder.size_gc_header
        nonvarsize = size_gc_header + self.fixed_size(typeid)
        if length == 0:
            # this includes the case of fixed-size objects, for which we
            # should not even ask for the varsize_item_sizes().
            totalsize = nonvarsize
        elif length > 0:
            # var-sized allocation with at least one item
            itemsize = self.varsize_item_sizes(typeid)
            try:
                varsize = ovfcheck(itemsize * length)
                totalsize = ovfcheck(nonvarsize + varsize)
            except OverflowError:
                raise MemoryError
        else:
            # negative length!  This likely comes from an overflow
            # earlier.  We will just raise MemoryError here.
            raise MemoryError
        #
        # If somebody calls this function a lot, we must eventually
        # force a full collection.  XXX make this more incremental!
        if (float(self.get_total_memory_used()) + raw_malloc_usage(totalsize) >
                self.next_major_collection_threshold):
            self.gc_step_until(STATE_SWEEPING)
            self.gc_step_until(STATE_FINALIZING, raw_malloc_usage(totalsize))
        #
        # Check if the object would fit in the ArenaCollection.
        if raw_malloc_usage(totalsize) <= self.small_request_threshold:
            #
            # Yes.  Round up 'totalsize' (it cannot overflow and it
            # must remain <= self.small_request_threshold.)
            totalsize = llarena.round_up_for_allocation(totalsize)
            ll_assert(raw_malloc_usage(totalsize) <=
                      self.small_request_threshold,
                      "rounding up made totalsize > small_request_threshold")
            #
            # Allocate from the ArenaCollection and clear the memory returned.
            result = self.ac.malloc(totalsize)
            llmemory.raw_memclear(result, totalsize)
            #
            # An object allocated from ArenaCollection is always old, even
            # if 'can_make_young'.  The interesting case of 'can_make_young'
            # is for large objects, bigger than the 'large_objects' threshold,
            # which are raw-malloced but still young.
            extra_flags = GCFLAG_TRACK_YOUNG_PTRS
            #
        else:
            # No, so proceed to allocate it externally with raw_malloc().
            # Check if we need to introduce the card marker bits area.
            if (self.card_page_indices <= 0  # <- this check is constant-folded
                or not self.has_gcptr_in_varsize(typeid) or
                raw_malloc_usage(totalsize) <= self.nonlarge_max):
                #
                # In these cases, we don't want a card marker bits area.
                # This case also includes all fixed-size objects.
                cardheadersize = 0
                extra_flags = 0
                #
            else:
                # Reserve N extra words containing card bits before the object.
                extra_words = self.card_marking_words_for_length(length)
                cardheadersize = WORD * extra_words
                extra_flags = GCFLAG_HAS_CARDS | GCFLAG_TRACK_YOUNG_PTRS
                # if 'can_make_young', then we also immediately set
                # GCFLAG_CARDS_SET, but without adding the object to
                # 'old_objects_with_cards_set'.  In this way it should
                # never be added to that list as long as it is young.
                if can_make_young:
                    extra_flags |= GCFLAG_CARDS_SET
            #
            # Detect very rare cases of overflows
            if raw_malloc_usage(totalsize) > (sys.maxint - (WORD-1)
                                              - cardheadersize):
                raise MemoryError("rare case of overflow")
            #
            # Now we know that the following computations cannot overflow.
            # Note that round_up_for_allocation() is also needed to get the
            # correct number added to 'rawmalloced_total_size'.
            allocsize = (cardheadersize + raw_malloc_usage(
                            llarena.round_up_for_allocation(totalsize)))
            #
            # Allocate the object using arena_malloc(), which we assume here
            # is just the same as raw_malloc(), but allows the extra
            # flexibility of saying that we have extra words in the header.
            # The memory returned is cleared by a raw_memclear().
            arena = llarena.arena_malloc(allocsize, 2)
            if not arena:
                raise MemoryError("cannot allocate large object")
            #
            # Reserve the card mark bits as a list of single bytes
            # (the loop is empty in C).
            i = 0
            while i < cardheadersize:
                llarena.arena_reserve(arena + i, llmemory.sizeof(lltype.Char))
                i += 1
            #
            # Reserve the actual object.  (This is also a no-op in C).
            result = arena + cardheadersize
            llarena.arena_reserve(result, totalsize)
            #
            # Record the newly allocated object and its full malloced size.
            # The object is young or old depending on the argument.
            self.rawmalloced_total_size += r_uint(allocsize)
            if can_make_young:
                if not self.young_rawmalloced_objects:
                    self.young_rawmalloced_objects = self.AddressDict()
                self.young_rawmalloced_objects.add(result + size_gc_header)
            else:
                self.old_rawmalloced_objects.append(result + size_gc_header)
                extra_flags |= GCFLAG_TRACK_YOUNG_PTRS
        #
        # Common code to fill the header and length of the object.
        self.init_gc_object(result, typeid, extra_flags)
        if self.is_varsize(typeid):
            offset_to_length = self.varsize_offset_to_length(typeid)
            (result + size_gc_header + offset_to_length).signed[0] = length
        return result + size_gc_header


    # ----------
    # Other functions in the GC API

    def set_max_heap_size(self, size):
        self.max_heap_size = float(size)
        if self.max_heap_size > 0.0:
            if self.max_heap_size < self.next_major_collection_initial:
                self.next_major_collection_initial = self.max_heap_size
            if self.max_heap_size < self.next_major_collection_threshold:
                self.next_major_collection_threshold = self.max_heap_size

    def raw_malloc_memory_pressure(self, sizehint):
        # Decrement by 'sizehint' plus a very little bit extra.  This
        # is needed e.g. for _rawffi, which may allocate a lot of tiny
        # arrays.
        self.next_major_collection_threshold -= (sizehint + 2 * WORD)
        if self.next_major_collection_threshold < 0:
            # cannot trigger a full collection now, but we can ensure
            # that one will occur very soon
            self.nursery_top = self.nursery_real_top
            self.nursery_free = self.nursery_real_top

    def can_malloc_nonmovable(self):
        return True

    def can_optimize_clean_setarrayitems(self):
        if self.card_page_indices > 0:
            return False
        return MovingGCBase.can_optimize_clean_setarrayitems(self)

    def can_move(self, obj):
        """Overrides the parent can_move()."""
        return self.is_in_nursery(obj)

    def pin(self, obj):
        if self.pinned_objects_in_nursery >= self.max_number_of_pinned_objects:
            return False
        if not self.is_in_nursery(obj):
            # Old objects are already non-moving, therefore pinning
            # makes no sense. If you run into this case, you may forgot
            # to check if can_move(obj) already returns True in which
            # case a call to pin() is unnecessary.
            return False
        if self.header(obj).tid & GCFLAG_PINNED:
            # Already pinned, we do not allow to pin it again.
            # Reason: It would be possible that the first caller unpins
            # while the second caller thinks it's still pinned.
            return False

        self.header(obj).tid |= GCFLAG_PINNED
        self.pinned_objects_in_nursery += 1
        return True


    def unpin(self, obj):
        ll_assert(self.header(obj).tid & GCFLAG_PINNED != 0,
            "unpin: object is already not pinned")
        #
        self.header(obj).tid &= ~GCFLAG_PINNED
        self.pinned_objects_in_nursery -= 1

    def shrink_array(self, obj, smallerlength):
        #
        # Only objects in the nursery can be "resized".  Resizing them
        # means recording that they have a smaller size, so that when
        # moved out of the nursery, they will consume less memory.
        # In particular, an array with GCFLAG_HAS_CARDS is never resized.
        # Also, a nursery object with GCFLAG_HAS_SHADOW is not resized
        # either, as this would potentially loose part of the memory in
        # the already-allocated shadow.
        if not self.is_in_nursery(obj):
            return False
        if self.header(obj).tid & GCFLAG_HAS_SHADOW:
            return False
        #
        size_gc_header = self.gcheaderbuilder.size_gc_header
        typeid = self.get_type_id(obj)
        totalsmallersize = (
            size_gc_header + self.fixed_size(typeid) +
            self.varsize_item_sizes(typeid) * smallerlength)
        llarena.arena_shrink_obj(obj - size_gc_header, totalsmallersize)
        #
        offset_to_length = self.varsize_offset_to_length(typeid)
        (obj + offset_to_length).signed[0] = smallerlength
        return True


    def malloc_fixedsize_nonmovable(self, typeid):
        obj = self.external_malloc(typeid, 0)
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)

    def malloc_varsize_nonmovable(self, typeid, length):
        obj = self.external_malloc(typeid, length)
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)

    def malloc_nonmovable(self, typeid, length, zero):
        # helper for testing, same as GCBase.malloc
        return self.external_malloc(typeid, length or 0)    # None -> 0


    # ----------
    # Simple helpers

    def get_type_id(self, obj):
        tid = self.header(obj).tid
        return llop.extract_ushort(llgroup.HALFWORD, tid)

    def combine(self, typeid16, flags):
        return llop.combine_ushort(lltype.Signed, typeid16, flags)

    def init_gc_object(self, addr, typeid16, flags=0):
        # The default 'flags' is zero.  The flags GCFLAG_NO_xxx_PTRS
        # have been chosen to allow 'flags' to be zero in the common
        # case (hence the 'NO' in their name).
        hdr = llmemory.cast_adr_to_ptr(addr, lltype.Ptr(self.HDR))
        hdr.tid = self.combine(typeid16, flags)

    def init_gc_object_immortal(self, addr, typeid16, flags=0):
        # For prebuilt GC objects, the flags must contain
        # GCFLAG_NO_xxx_PTRS, at least initially.
        flags |= GCFLAG_NO_HEAP_PTRS | GCFLAG_TRACK_YOUNG_PTRS
        self.init_gc_object(addr, typeid16, flags)

    def is_in_nursery(self, addr):
        ll_assert(llmemory.cast_adr_to_int(addr) & 1 == 0,
                  "odd-valued (i.e. tagged) pointer unexpected here")
        return self.nursery <= addr < self.nursery_real_top

    def appears_to_be_young(self, addr):
        # "is a valid addr to a young object?"
        # but it's ok to occasionally return True accidentally.
        # Maybe the best implementation would be a bloom filter
        # of some kind instead of the dictionary lookup that is
        # sometimes done below.  But the expected common answer
        # is "Yes" because addr points to the nursery, so it may
        # not be useful to optimize the other case too much.
        #
        # First, if 'addr' appears to be a pointer to some place within
        # the nursery, return True
        if not self.translated_to_c:
            # When non-translated, filter out tagged pointers explicitly.
            # When translated, it may occasionally give a wrong answer
            # of True if 'addr' is a tagged pointer with just the wrong value.
            if not self.is_valid_gc_object(addr):
                return False

        if self.nursery <= addr < self.nursery_real_top:
            return True      # addr is in the nursery
        #
        # Else, it may be in the set 'young_rawmalloced_objects'
        return (bool(self.young_rawmalloced_objects) and
                self.young_rawmalloced_objects.contains(addr))
    appears_to_be_young._always_inline_ = True

    def debug_is_old_object(self, addr):
        return (self.is_valid_gc_object(addr)
                and not self.appears_to_be_young(addr))

    def is_forwarded(self, obj):
        """Returns True if the nursery obj is marked as forwarded.
        Implemented a bit obscurely by checking an unrelated flag
        that can never be set on a young object -- except if tid == -42.
        """
        assert self.is_in_nursery(obj)
        tid = self.header(obj).tid
        result = (tid & GCFLAG_FINALIZATION_ORDERING != 0)
        if result:
            ll_assert(tid == -42, "bogus header for young obj")
        else:
            ll_assert(bool(tid), "bogus header (1)")
            ll_assert(tid & -_GCFLAG_FIRST_UNUSED == 0, "bogus header (2)")
        return result

    def get_forwarding_address(self, obj):
        return llmemory.cast_adr_to_ptr(obj, FORWARDSTUBPTR).forw

    def get_possibly_forwarded_type_id(self, obj):
        if self.is_in_nursery(obj) and self.is_forwarded(obj):
            obj = self.get_forwarding_address(obj)
        return self.get_type_id(obj)

    def get_total_memory_used(self):
        """Return the total memory used, not counting any object in the
        nursery: only objects in the ArenaCollection or raw-malloced.
        """
        return self.ac.total_memory_used + self.rawmalloced_total_size

    def card_marking_words_for_length(self, length):
        # --- Unoptimized version:
        #num_bits = ((length-1) >> self.card_page_shift) + 1
        #return (num_bits + (LONG_BIT - 1)) >> LONG_BIT_SHIFT
        # --- Optimized version:
        return intmask(
          ((r_uint(length) + r_uint((LONG_BIT << self.card_page_shift) - 1)) >>
           (self.card_page_shift + LONG_BIT_SHIFT)))

    def card_marking_bytes_for_length(self, length):
        # --- Unoptimized version:
        #num_bits = ((length-1) >> self.card_page_shift) + 1
        #return (num_bits + 7) >> 3
        # --- Optimized version:
        return intmask(
            ((r_uint(length) + r_uint((8 << self.card_page_shift) - 1)) >>
             (self.card_page_shift + 3)))

    def debug_check_consistency(self):
        if self.DEBUG:
            ll_assert(not self.young_rawmalloced_objects,
                      "young raw-malloced objects in a major collection")
            ll_assert(not self.young_objects_with_weakrefs.non_empty(),
                      "young objects with weakrefs in a major collection")

            if self.raw_malloc_might_sweep.non_empty():
                ll_assert(self.gc_state == STATE_SWEEPING,
                      "raw_malloc_might_sweep must be empty outside SWEEPING")

            if self.gc_state == STATE_MARKING:
                self._debug_objects_to_trace_dict1 = \
                                            self.objects_to_trace.stack2dict()
                self._debug_objects_to_trace_dict2 = \
                                       self.more_objects_to_trace.stack2dict()
                MovingGCBase.debug_check_consistency(self)
                self._debug_objects_to_trace_dict2.delete()
                self._debug_objects_to_trace_dict1.delete()
            else:
                MovingGCBase.debug_check_consistency(self)

    def debug_check_object(self, obj):
        # We are after a minor collection, and possibly after a major
        # collection step.  No object should be in the nursery (except
        # pinned ones)
        if self.header(obj).tid & GCFLAG_PINNED == 0:
            ll_assert(not self.is_in_nursery(obj),
                      "object in nursery after collection")
            ll_assert(self.header(obj).tid & GCFLAG_VISITED_RMY == 0,
                      "GCFLAG_VISITED_RMY after collection")
        else:
            ll_assert(self.is_in_nursery(obj),
                      "pinned object not in nursery")
            # XXX check if we can support that or if it makes no sense (groggi)
            ll_assert(not self.header(obj).tid & GCFLAG_TRACK_YOUNG_PTRS,
                      "pinned nursery object with GCFLAG_TRACK_YOUNG_PTRS")

        if self.gc_state == STATE_SCANNING:
            self._debug_check_object_scanning(obj)
        elif self.gc_state == STATE_MARKING:
            self._debug_check_object_marking(obj)
        elif self.gc_state == STATE_SWEEPING:
            self._debug_check_object_sweeping(obj)
        elif self.gc_state == STATE_FINALIZING:
            self._debug_check_object_finalizing(obj)
        else:
            ll_assert(False, "unknown gc_state value")

    def _debug_check_object_marking(self, obj):
        if self.header(obj).tid & GCFLAG_VISITED != 0:
            # A black object.  Should NEVER point to a white object.
            self.trace(obj, self._debug_check_not_white, None)
            # During marking, all visited (black) objects should always have
            # the GCFLAG_TRACK_YOUNG_PTRS flag set, for the write barrier to
            # trigger --- at least if they contain any gc ptr.  We are just
            # after a minor or major collection here, so we can't see the
            # object state VISITED & ~WRITE_BARRIER.
            typeid = self.get_type_id(obj)
            if self.has_gcptr(typeid):
                ll_assert(self.header(obj).tid & GCFLAG_TRACK_YOUNG_PTRS != 0,
                          "black object without GCFLAG_TRACK_YOUNG_PTRS")

    def _debug_check_not_white(self, root, ignored):
        obj = root.address[0]
        if self.header(obj).tid & GCFLAG_VISITED != 0:
            pass    # black -> black
        elif (self._debug_objects_to_trace_dict1.contains(obj) or
              self._debug_objects_to_trace_dict2.contains(obj)):
            pass    # black -> gray
        elif self.header(obj).tid & GCFLAG_NO_HEAP_PTRS != 0:
            pass    # black -> white-but-prebuilt-so-dont-care
        else:
            ll_assert(False, "black -> white pointer found")

    def _debug_check_object_sweeping(self, obj):
        # We see only reachable objects here.  They all start as VISITED
        # but this flag is progressively removed in the sweeping phase.

        # All objects should have this flag, except if they
        # don't have any GC pointer
        typeid = self.get_type_id(obj)
        if not self.header(obj).tid & GCFLAG_PINNED:
            # XXX do we need checks if the object is actually pinned? (groggi)
            if self.has_gcptr(typeid):
                ll_assert(self.header(obj).tid & GCFLAG_TRACK_YOUNG_PTRS != 0,
                          "missing GCFLAG_TRACK_YOUNG_PTRS")
        # the GCFLAG_FINALIZATION_ORDERING should not be set between coll.
        ll_assert(self.header(obj).tid & GCFLAG_FINALIZATION_ORDERING == 0,
                  "unexpected GCFLAG_FINALIZATION_ORDERING")
        # the GCFLAG_CARDS_SET should not be set between collections
        ll_assert(self.header(obj).tid & GCFLAG_CARDS_SET == 0,
                  "unexpected GCFLAG_CARDS_SET")
        # if the GCFLAG_HAS_CARDS is set, check that all bits are zero now
        if self.header(obj).tid & GCFLAG_HAS_CARDS:
            if self.card_page_indices <= 0:
                ll_assert(False, "GCFLAG_HAS_CARDS but not using card marking")
                return
            typeid = self.get_type_id(obj)
            ll_assert(self.has_gcptr_in_varsize(typeid),
                      "GCFLAG_HAS_CARDS but not has_gcptr_in_varsize")
            ll_assert(self.header(obj).tid & GCFLAG_NO_HEAP_PTRS == 0,
                      "GCFLAG_HAS_CARDS && GCFLAG_NO_HEAP_PTRS")
            offset_to_length = self.varsize_offset_to_length(typeid)
            length = (obj + offset_to_length).signed[0]
            extra_words = self.card_marking_words_for_length(length)
            #
            size_gc_header = self.gcheaderbuilder.size_gc_header
            p = llarena.getfakearenaaddress(obj - size_gc_header)
            i = extra_words * WORD
            while i > 0:
                p -= 1
                ll_assert(p.char[0] == '\x00',
                          "the card marker bits are not cleared")
                i -= 1

    def _debug_check_object_finalizing(self, obj):
        # Same invariants as STATE_SCANNING.
        self._debug_check_object_scanning(obj)

    def _debug_check_object_scanning(self, obj):
        # This check is called before scanning starts.
        # Scanning is done in a single step.
        # the GCFLAG_VISITED should not be set between collections
        ll_assert(self.header(obj).tid & GCFLAG_VISITED == 0,
                  "unexpected GCFLAG_VISITED")

        # All other invariants from the sweeping phase should still be
        # satisfied.
        self._debug_check_object_sweeping(obj)


    # ----------
    # Write barrier

    # for the JIT: a minimal description of the write_barrier() method
    # (the JIT assumes it is of the shape
    #  "if addr_struct.int0 & JIT_WB_IF_FLAG: remember_young_pointer()")
    JIT_WB_IF_FLAG = GCFLAG_TRACK_YOUNG_PTRS

    # for the JIT to generate custom code corresponding to the array
    # write barrier for the simplest case of cards.  If JIT_CARDS_SET
    # is already set on an object, it will execute code like this:
    #    MOV eax, index
    #    SHR eax, JIT_WB_CARD_PAGE_SHIFT
    #    XOR eax, -8
    #    BTS [object], eax
    if TRANSLATION_PARAMS['card_page_indices'] > 0:
        JIT_WB_CARDS_SET = GCFLAG_CARDS_SET
        JIT_WB_CARD_PAGE_SHIFT = 1
        while ((1 << JIT_WB_CARD_PAGE_SHIFT) !=
               TRANSLATION_PARAMS['card_page_indices']):
            JIT_WB_CARD_PAGE_SHIFT += 1

    @classmethod
    def JIT_max_size_of_young_obj(cls):
        return cls.TRANSLATION_PARAMS['large_object']

    @classmethod
    def JIT_minimal_size_in_nursery(cls):
        return cls.minimal_size_in_nursery

    def write_barrier(self, addr_struct):
        if self.header(addr_struct).tid & GCFLAG_TRACK_YOUNG_PTRS:
            self.remember_young_pointer(addr_struct)

    def write_barrier_from_array(self, addr_array, index):
        if self.header(addr_array).tid & GCFLAG_TRACK_YOUNG_PTRS:
            if self.card_page_indices > 0:
                self.remember_young_pointer_from_array2(addr_array, index)
            else:
                self.remember_young_pointer(addr_array)

    def _init_writebarrier_logic(self):
        DEBUG = self.DEBUG
        # The purpose of attaching remember_young_pointer to the instance
        # instead of keeping it as a regular method is to
        # make the code in write_barrier() marginally smaller
        # (which is important because it is inlined *everywhere*).
        def remember_young_pointer(addr_struct):
            # 'addr_struct' is the address of the object in which we write.
            # We know that 'addr_struct' has GCFLAG_TRACK_YOUNG_PTRS so far.
            #
            if DEBUG:   # note: PYPY_GC_DEBUG=1 does not enable this
                ll_assert(self.debug_is_old_object(addr_struct) or
                          self.header(addr_struct).tid & GCFLAG_HAS_CARDS != 0,
                      "young object with GCFLAG_TRACK_YOUNG_PTRS and no cards")
            #
            # We need to remove the flag GCFLAG_TRACK_YOUNG_PTRS and add
            # the object to the list 'old_objects_pointing_to_young'.
            # We know that 'addr_struct' cannot be in the nursery,
            # because nursery objects never have the flag
            # GCFLAG_TRACK_YOUNG_PTRS to start with.  Note that in
            # theory we don't need to do that if the pointer that we're
            # writing into the object isn't pointing to a young object.
            # However, it isn't really a win, because then sometimes
            # we're going to call this function a lot of times for the
            # same object; moreover we'd need to pass the 'newvalue' as
            # an argument here.  The JIT has always called a
            # 'newvalue'-less version, too.  Moreover, the incremental
            # GC nowadays relies on this fact.
            self.old_objects_pointing_to_young.append(addr_struct)
            objhdr = self.header(addr_struct)
            objhdr.tid &= ~GCFLAG_TRACK_YOUNG_PTRS
            #
            # Second part: if 'addr_struct' is actually a prebuilt GC
            # object and it's the first time we see a write to it, we
            # add it to the list 'prebuilt_root_objects'.
            if objhdr.tid & GCFLAG_NO_HEAP_PTRS:
                objhdr.tid &= ~GCFLAG_NO_HEAP_PTRS
                self.prebuilt_root_objects.append(addr_struct)

        remember_young_pointer._dont_inline_ = True
        self.remember_young_pointer = remember_young_pointer
        #
        if self.card_page_indices > 0:
            self._init_writebarrier_with_card_marker()


    def _init_writebarrier_with_card_marker(self):
        DEBUG = self.DEBUG
        def remember_young_pointer_from_array2(addr_array, index):
            # 'addr_array' is the address of the object in which we write,
            # which must have an array part;  'index' is the index of the
            # item that is (or contains) the pointer that we write.
            # We know that 'addr_array' has GCFLAG_TRACK_YOUNG_PTRS so far.
            #
            objhdr = self.header(addr_array)
            if objhdr.tid & GCFLAG_HAS_CARDS == 0:
                #
                if DEBUG:   # note: PYPY_GC_DEBUG=1 does not enable this
                    ll_assert(self.debug_is_old_object(addr_array),
                        "young array with no card but GCFLAG_TRACK_YOUNG_PTRS")
                #
                # no cards, use default logic.  Mostly copied from above.
                self.old_objects_pointing_to_young.append(addr_array)
                objhdr.tid &= ~GCFLAG_TRACK_YOUNG_PTRS
                if objhdr.tid & GCFLAG_NO_HEAP_PTRS:
                    objhdr.tid &= ~GCFLAG_NO_HEAP_PTRS
                    self.prebuilt_root_objects.append(addr_array)
                return
            #
            # 'addr_array' is a raw_malloc'ed array with card markers
            # in front.  Compute the index of the bit to set:
            bitindex = index >> self.card_page_shift
            byteindex = bitindex >> 3
            bitmask = 1 << (bitindex & 7)
            #
            # If the bit is already set, leave now.
            addr_byte = self.get_card(addr_array, byteindex)
            byte = ord(addr_byte.char[0])
            if byte & bitmask:
                return
            #
            # We set the flag (even if the newly written address does not
            # actually point to the nursery, which seems to be ok -- actually
            # it seems more important that remember_young_pointer_from_array2()
            # does not take 3 arguments).
            addr_byte.char[0] = chr(byte | bitmask)
            #
            if objhdr.tid & GCFLAG_CARDS_SET == 0:
                self.old_objects_with_cards_set.append(addr_array)
                objhdr.tid |= GCFLAG_CARDS_SET

        remember_young_pointer_from_array2._dont_inline_ = True
        assert self.card_page_indices > 0
        self.remember_young_pointer_from_array2 = (
            remember_young_pointer_from_array2)

        def jit_remember_young_pointer_from_array(addr_array):
            # minimal version of the above, with just one argument,
            # called by the JIT when GCFLAG_TRACK_YOUNG_PTRS is set
            # but GCFLAG_CARDS_SET is cleared.  This tries to set
            # GCFLAG_CARDS_SET if possible; otherwise, it falls back
            # to remember_young_pointer().
            objhdr = self.header(addr_array)
            if objhdr.tid & GCFLAG_HAS_CARDS:
                self.old_objects_with_cards_set.append(addr_array)
                objhdr.tid |= GCFLAG_CARDS_SET
            else:
                self.remember_young_pointer(addr_array)

        self.jit_remember_young_pointer_from_array = (
            jit_remember_young_pointer_from_array)

    def get_card(self, obj, byteindex):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        addr_byte = obj - size_gc_header
        return llarena.getfakearenaaddress(addr_byte) + (~byteindex)


    def writebarrier_before_copy(self, source_addr, dest_addr,
                                 source_start, dest_start, length):
        """ This has the same effect as calling writebarrier over
        each element in dest copied from source, except it might reset
        one of the following flags a bit too eagerly, which means we'll have
        a bit more objects to track, but being on the safe side.
        """
        source_hdr = self.header(source_addr)
        dest_hdr = self.header(dest_addr)
        if dest_hdr.tid & GCFLAG_TRACK_YOUNG_PTRS == 0:
            return True
        # ^^^ a fast path of write-barrier
        #
        if source_hdr.tid & GCFLAG_HAS_CARDS != 0:
            #
            if source_hdr.tid & GCFLAG_TRACK_YOUNG_PTRS == 0:
                # The source object may have random young pointers.
                # Return False to mean "do it manually in ll_arraycopy".
                return False
            #
            if source_hdr.tid & GCFLAG_CARDS_SET == 0:
                # The source object has no young pointers at all.  Done.
                return True
            #
            if dest_hdr.tid & GCFLAG_HAS_CARDS == 0:
                # The dest object doesn't have cards.  Do it manually.
                return False
            #
            if source_start != 0 or dest_start != 0:
                # Misaligned.  Do it manually.
                return False
            #
            self.manually_copy_card_bits(source_addr, dest_addr, length)
            return True
        #
        if source_hdr.tid & GCFLAG_TRACK_YOUNG_PTRS == 0:
            # there might be in source a pointer to a young object
            self.old_objects_pointing_to_young.append(dest_addr)
            dest_hdr.tid &= ~GCFLAG_TRACK_YOUNG_PTRS
        #
        if dest_hdr.tid & GCFLAG_NO_HEAP_PTRS:
            if source_hdr.tid & GCFLAG_NO_HEAP_PTRS == 0:
                dest_hdr.tid &= ~GCFLAG_NO_HEAP_PTRS
                self.prebuilt_root_objects.append(dest_addr)
        return True

    def manually_copy_card_bits(self, source_addr, dest_addr, length):
        # manually copy the individual card marks from source to dest
        assert self.card_page_indices > 0
        bytes = self.card_marking_bytes_for_length(length)
        #
        anybyte = 0
        i = 0
        while i < bytes:
            addr_srcbyte = self.get_card(source_addr, i)
            addr_dstbyte = self.get_card(dest_addr, i)
            byte = ord(addr_srcbyte.char[0])
            anybyte |= byte
            addr_dstbyte.char[0] = chr(ord(addr_dstbyte.char[0]) | byte)
            i += 1
        #
        if anybyte:
            dest_hdr = self.header(dest_addr)
            if dest_hdr.tid & GCFLAG_CARDS_SET == 0:
                self.old_objects_with_cards_set.append(dest_addr)
                dest_hdr.tid |= GCFLAG_CARDS_SET

    def record_pinned_object_with_shadow(self, obj, new_shadow_object_dict):
        # checks if the pinned object has a shadow and if so add it to the
        # dict of shadows.
        obj = obj + self.gcheaderbuilder.size_gc_header
        shadow = self.nursery_objects_shadows.get(obj)
        if shadow != NULL:
            new_shadow_object_dict.setitem(obj, shadow)

    # ----------
    # Nursery collection

    def minor_collection(self):
        """Perform a minor collection: find the objects from the nursery
        that remain alive and move them out."""
        #
        debug_start("gc-minor")
        #
        # All nursery barriers are invalid from this point on.  They
        # are evaluated anew as part of the minor collection.
        self.nursery_barriers.delete()
        #
        # Keeps track of surviving pinned objects. See also '_trace_drag_out()'
        # where this stack is filled.  Pinning an object only prevents it from
        # being move, not from being collected if it is not used anymore.
        self.surviving_pinned_objects = self.AddressStack()
        #
        # The following counter keeps track of the amount of alive and pinned
        # objects inside the nursery.  The counter is reset, as we have to
        # check which pinned objects are actually still alive. Pinning an
        # object does not prevent the removal of an object, if it's not used
        # anymore.
        # XXX is this true? does it make sense? (groggi)
        self.pinned_objects_in_nursery = 0
        #
        # Before everything else, remove from 'old_objects_pointing_to_young'
        # the young arrays.
        if self.young_rawmalloced_objects:
            self.remove_young_arrays_from_old_objects_pointing_to_young()
        #
        # A special step in the STATE_MARKING phase.
        if self.gc_state == STATE_MARKING:
            # Copy the 'old_objects_pointing_to_young' list so far to
            # 'more_objects_to_trace'.  Turn black objects back to gray.
            # This is because these are precisely the old objects that
            # have been modified and need rescanning.
            self.old_objects_pointing_to_young.foreach(
                self._add_to_more_objects_to_trace, None)
        #
        # First, find the roots that point to young objects.  All nursery
        # objects found are copied out of the nursery, and the occasional
        # young raw-malloced object is flagged with GCFLAG_VISITED_RMY.
        # Note that during this step, we ignore references to further
        # young objects; only objects directly referenced by roots
        # are copied out or flagged.  They are also added to the list
        # 'old_objects_pointing_to_young'.
        self.nursery_surviving_size = 0
        self.collect_roots_in_nursery()
        #
        while True:
            # If we are using card marking, do a partial trace of the arrays
            # that are flagged with GCFLAG_CARDS_SET.
            if self.card_page_indices > 0:
                self.collect_cardrefs_to_nursery()
            #
            # Now trace objects from 'old_objects_pointing_to_young'.
            # All nursery objects they reference are copied out of the
            # nursery, and again added to 'old_objects_pointing_to_young'.
            # All young raw-malloced object found are flagged
            # GCFLAG_VISITED_RMY.
            # We proceed until 'old_objects_pointing_to_young' is empty.
            self.collect_oldrefs_to_nursery()
            #
            # We have to loop back if collect_oldrefs_to_nursery caused
            # new objects to show up in old_objects_with_cards_set
            if self.card_page_indices > 0:
                if self.old_objects_with_cards_set.non_empty():
                    continue
            break
        #
        # Now all live nursery objects should be out.  Update the young
        # weakrefs' targets.
        if self.young_objects_with_weakrefs.non_empty():
            self.invalidate_young_weakrefs()
        if self.young_objects_with_light_finalizers.non_empty():
            self.deal_with_young_objects_with_finalizers()
        #
        # Clear this mapping.  Without pinned objects we just clear the dict
        # as all objects in the nursery are dragged out of the nursery and, if
        # needed, into their shadow.  However, if we have pinned objects we have
        # to check if those pinned object have a shadow and keep a dictionary
        # filled with shadow information for them as they stay in the nursery.
        if self.nursery_objects_shadows.length() > 0:
            if self.surviving_pinned_objects.non_empty():
                new_shadows = self.AddressDict()
                self.surviving_pinned_objects.foreach(
                    self.record_pinned_object_with_shadow, new_shadows)
                self.nursery_objects_shadows.delete()
                self.nursery_objects_shadows = new_shadows
            else:
                self.nursery_objects_shadows.clear()
        #
        # Walk the list of young raw-malloced objects, and either free
        # them or make them old.
        if self.young_rawmalloced_objects:
            self.free_young_rawmalloced_objects()
        #
        # All live nursery objects are out of the nursery or pinned inside
        # the nursery.  Create nursery barriers to protect the pinned object,
        # fill the rest of the nursery with zeros and reset the current nursery
        # pointer.
        size_gc_header = self.gcheaderbuilder.size_gc_header
        nursery_barriers = self.AddressDeque()
        prev = self.nursery
        self.surviving_pinned_objects.sort()
        assert self.pinned_objects_in_nursery == \
            self.surviving_pinned_objects.length()
        while self.surviving_pinned_objects.non_empty():
            #
            # prepare information about the surviving pinned object
            next = self.surviving_pinned_objects.pop()
            assert next >= prev
            #
            # clear the arena between the last pinned object or arena start
            # and the pinned object
            pinned_obj_size = llarena.getfakearenaaddress(next) - prev
            llarena.arena_reset(prev, pinned_obj_size, 2)
            #
            # clean up object's flags
            obj = next + size_gc_header
            self.header(obj).tid &= ~GCFLAG_VISITED
            #
            # create a new nursery barrier for the pinned object
            nursery_barriers.append(next)
            #
            # update 'prev' to the end of the 'next' object
            prev = prev + pinned_obj_size + \
                (size_gc_header + self.get_size(obj))
        #
        # clean up a bit more after the last pinned object
        llarena.arena_reset(prev, self.nursery_real_top - prev, 0)
        llarena.arena_reset(prev, self.initial_cleanup, 2)
        nursery_barriers.append(prev + self.initial_cleanup)
        #
        self.nursery_barriers = nursery_barriers
        self.surviving_pinned_objects.delete()
        #
        # XXX gc-minimark-pinning does a debug_rotate_nursery() here (groggi)
        self.nursery_free = self.nursery
        self.nursery_top = self.nursery_barriers.popleft()

        debug_print("minor collect, total memory used:",
                    self.get_total_memory_used())
        debug_print("number of pinned objects:",
                    self.pinned_objects_in_nursery)
        if self.DEBUG >= 2:
            self.debug_check_consistency()     # expensive!
        #
        self.root_walker.finished_minor_collection()
        #
        debug_stop("gc-minor")


    def collect_roots_in_nursery(self):
        # we don't need to trace prebuilt GcStructs during a minor collect:
        # if a prebuilt GcStruct contains a pointer to a young object,
        # then the write_barrier must have ensured that the prebuilt
        # GcStruct is in the list self.old_objects_pointing_to_young.
        debug_start("gc-minor-walkroots")
        if self.gc_state == STATE_MARKING:
            callback = IncrementalMiniMarkGC._trace_drag_out1_marking_phase
        else:
            callback = IncrementalMiniMarkGC._trace_drag_out1
        self.root_walker.walk_roots(
            callback,     # stack roots
            callback,     # static in prebuilt non-gc
            None)         # static in prebuilt gc
        debug_stop("gc-minor-walkroots")

    def collect_cardrefs_to_nursery(self):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        oldlist = self.old_objects_with_cards_set
        while oldlist.non_empty():
            obj = oldlist.pop()
            #
            # Remove the GCFLAG_CARDS_SET flag.
            ll_assert(self.header(obj).tid & GCFLAG_CARDS_SET != 0,
                "!GCFLAG_CARDS_SET but object in 'old_objects_with_cards_set'")
            self.header(obj).tid &= ~GCFLAG_CARDS_SET
            #
            # Get the number of card marker bytes in the header.
            typeid = self.get_type_id(obj)
            offset_to_length = self.varsize_offset_to_length(typeid)
            length = (obj + offset_to_length).signed[0]
            bytes = self.card_marking_bytes_for_length(length)
            p = llarena.getfakearenaaddress(obj - size_gc_header)
            #
            # If the object doesn't have GCFLAG_TRACK_YOUNG_PTRS, then it
            # means that it is in 'old_objects_pointing_to_young' and
            # will be fully traced by collect_oldrefs_to_nursery() just
            # afterwards.
            if self.header(obj).tid & GCFLAG_TRACK_YOUNG_PTRS == 0:
                #
                # In that case, we just have to reset all card bits.
                while bytes > 0:
                    p -= 1
                    p.char[0] = '\x00'
                    bytes -= 1
                #
            else:
                # Walk the bytes encoding the card marker bits, and for
                # each bit set, call trace_and_drag_out_of_nursery_partial().
                interval_start = 0
                while bytes > 0:
                    p -= 1
                    cardbyte = ord(p.char[0])
                    p.char[0] = '\x00'           # reset the bits
                    bytes -= 1
                    next_byte_start = interval_start + 8*self.card_page_indices
                    #
                    while cardbyte != 0:
                        interval_stop = interval_start + self.card_page_indices
                        #
                        if cardbyte & 1:
                            if interval_stop > length:
                                interval_stop = length
                                ll_assert(cardbyte <= 1 and bytes == 0,
                                          "premature end of object")
                            self.trace_and_drag_out_of_nursery_partial(
                                obj, interval_start, interval_stop)
                        #
                        interval_start = interval_stop
                        cardbyte >>= 1
                    interval_start = next_byte_start
                #
                # If we're incrementally marking right now, sorry, we also
                # need to add the object to 'more_objects_to_trace' and have
                # it fully traced once at the end of the current marking phase.
                if self.gc_state == STATE_MARKING:
                    self.header(obj).tid &= ~GCFLAG_VISITED
                    self.more_objects_to_trace.append(obj)


    def collect_oldrefs_to_nursery(self):
        # Follow the old_objects_pointing_to_young list and move the
        # young objects they point to out of the nursery.
        oldlist = self.old_objects_pointing_to_young
        while oldlist.non_empty():
            obj = oldlist.pop()
            #
            # Check that the flags are correct: we must not have
            # GCFLAG_TRACK_YOUNG_PTRS so far.
            ll_assert(self.header(obj).tid & GCFLAG_TRACK_YOUNG_PTRS == 0,
                      "old_objects_pointing_to_young contains obj with "
                      "GCFLAG_TRACK_YOUNG_PTRS")
            #
            # Add the flag GCFLAG_TRACK_YOUNG_PTRS.  All live objects should
            # have this flag set after a nursery collection.
            self.header(obj).tid |= GCFLAG_TRACK_YOUNG_PTRS
            #
            # Trace the 'obj' to replace pointers to nursery with pointers
            # outside the nursery, possibly forcing nursery objects out
            # and adding them to 'old_objects_pointing_to_young' as well.
            self.trace_and_drag_out_of_nursery(obj)

    def trace_and_drag_out_of_nursery(self, obj):
        """obj must not be in the nursery.  This copies all the
        young objects it references out of the nursery.
        """
        self.trace(obj, self._trace_drag_out, None)

    def trace_and_drag_out_of_nursery_partial(self, obj, start, stop):
        """Like trace_and_drag_out_of_nursery(), but limited to the array
        indices in range(start, stop).
        """
        ll_assert(start < stop, "empty or negative range "
                                "in trace_and_drag_out_of_nursery_partial()")
        #print 'trace_partial:', start, stop, '\t', obj
        self.trace_partial(obj, start, stop, self._trace_drag_out, None)


    def _trace_drag_out1(self, root):
        self._trace_drag_out(root, None)

    def _trace_drag_out1_marking_phase(self, root):
        self._trace_drag_out(root, None)
        #
        # We are in the MARKING state: we must also record this object
        # if it was young.  Don't bother with old objects in general,
        # as they are anyway added to 'more_objects_to_trace' if they
        # are modified (see _add_to_more_objects_to_trace).  But we do
        # need to record the not-visited-yet (white) old objects.  So
        # as a conservative approximation, we need to add the object to
        # the list if and only if it doesn't have GCFLAG_VISITED yet.
        obj = root.address[0]
        if not self.header(obj).tid & GCFLAG_VISITED:
            self.more_objects_to_trace.append(obj)

    def _trace_drag_out(self, root, ignored):
        obj = root.address[0]
        #print '_trace_drag_out(%x: %r)' % (hash(obj.ptr._obj), obj)
        #
        # If 'obj' is not in the nursery, nothing to change -- expect
        # that we must set GCFLAG_VISITED_RMY on young raw-malloced objects.
        if not self.is_in_nursery(obj):
            # cache usage trade-off: I think that it is a better idea to
            # check if 'obj' is in young_rawmalloced_objects with an access
            # to this (small) dictionary, rather than risk a lot of cache
            # misses by reading a flag in the header of all the 'objs' that
            # arrive here.
            if (bool(self.young_rawmalloced_objects)
                and self.young_rawmalloced_objects.contains(obj)):
                self._visit_young_rawmalloced_object(obj)
            return
        #
        size_gc_header = self.gcheaderbuilder.size_gc_header
        if self.header(obj).tid & (GCFLAG_HAS_SHADOW | GCFLAG_PINNED) == 0:
            #
            # Common case: 'obj' was not already forwarded (otherwise
            # tid == -42, containing all flags), and it doesn't have the
            # HAS_SHADOW flag either.  We must move it out of the nursery,
            # into a new nonmovable location.
            totalsize = size_gc_header + self.get_size(obj)
            self.nursery_surviving_size += raw_malloc_usage(totalsize)
            newhdr = self._malloc_out_of_nursery(totalsize)
            #
        elif self.is_forwarded(obj):
            #
            # 'obj' was already forwarded.  Change the original reference
            # to point to its forwarding address, and we're done.
            root.address[0] = self.get_forwarding_address(obj)
            return
            #
        elif self.header(obj).tid & GCFLAG_PINNED:
            hdr = self.header(obj)
            if hdr.tid & GCFLAG_VISITED:
                # already visited and keeping track of the object
                return
            hdr.tid |= GCFLAG_VISITED
            # XXX add additional checks for unsupported pinned objects (groggi)
            # XXX implement unsupported object types with pinning
            ll_assert(not self.header(obj).tid & GCFLAG_HAS_CARDS,
                "pinned object with GCFLAG_HAS_CARDS not supported")
            self.surviving_pinned_objects.append(
                llarena.getfakearenaaddress(obj - size_gc_header))
            self.pinned_objects_in_nursery += 1
            return
        else:
            # First visit to an object that has already a shadow.
            newobj = self.nursery_objects_shadows.get(obj)
            ll_assert(newobj != NULL, "GCFLAG_HAS_SHADOW but no shadow found")
            newhdr = newobj - size_gc_header
            #
            # Remove the flag GCFLAG_HAS_SHADOW, so that it doesn't get
            # copied to the shadow itself.
            self.header(obj).tid &= ~GCFLAG_HAS_SHADOW
            #
            totalsize = size_gc_header + self.get_size(obj)
        #
        # Copy it.  Note that references to other objects in the
        # nursery are kept unchanged in this step.
        llmemory.raw_memcopy(obj - size_gc_header, newhdr, totalsize)
        #
        # Set the old object's tid to -42 (containing all flags) and
        # replace the old object's content with the target address.
        # A bit of no-ops to convince llarena that we are changing
        # the layout, in non-translated versions.
        typeid = self.get_type_id(obj)
        obj = llarena.getfakearenaaddress(obj)
        llarena.arena_reset(obj - size_gc_header, totalsize, 0)
        llarena.arena_reserve(obj - size_gc_header,
                              size_gc_header + llmemory.sizeof(FORWARDSTUB))
        self.header(obj).tid = -42
        newobj = newhdr + size_gc_header
        llmemory.cast_adr_to_ptr(obj, FORWARDSTUBPTR).forw = newobj
        #
        # Change the original pointer to this object.
        root.address[0] = newobj
        #
        # Add the newobj to the list 'old_objects_pointing_to_young',
        # because it can contain further pointers to other young objects.
        # We will fix such references to point to the copy of the young
        # objects when we walk 'old_objects_pointing_to_young'.
        if self.has_gcptr(typeid):
            # we only have to do it if we have any gcptrs
            self.old_objects_pointing_to_young.append(newobj)

    _trace_drag_out._always_inline_ = True

    def _visit_young_rawmalloced_object(self, obj):
        # 'obj' points to a young, raw-malloced object.
        # Any young rawmalloced object never seen by the code here
        # will end up without GCFLAG_VISITED_RMY, and be freed at the
        # end of the current minor collection.  Note that there was
        # a bug in which dying young arrays with card marks would
        # still be scanned before being freed, keeping a lot of
        # objects unnecessarily alive.
        hdr = self.header(obj)
        if hdr.tid & GCFLAG_VISITED_RMY:
            return
        hdr.tid |= GCFLAG_VISITED_RMY
        #
        # we just made 'obj' old, so we need to add it to the correct lists
        added_somewhere = False
        #
        if hdr.tid & GCFLAG_TRACK_YOUNG_PTRS == 0:
            self.old_objects_pointing_to_young.append(obj)
            added_somewhere = True
        #
        if hdr.tid & GCFLAG_HAS_CARDS != 0:
            ll_assert(hdr.tid & GCFLAG_CARDS_SET != 0,
                      "young array: GCFLAG_HAS_CARDS without GCFLAG_CARDS_SET")
            self.old_objects_with_cards_set.append(obj)
            added_somewhere = True
        #
        ll_assert(added_somewhere, "wrong flag combination on young array")


    def _malloc_out_of_nursery(self, totalsize):
        """Allocate non-movable memory for an object of the given
        'totalsize' that lives so far in the nursery."""
        if raw_malloc_usage(totalsize) <= self.small_request_threshold:
            # most common path
            return self.ac.malloc(totalsize)
        else:
            # for nursery objects that are not small
            return self._malloc_out_of_nursery_nonsmall(totalsize)
    _malloc_out_of_nursery._always_inline_ = True

    def _malloc_out_of_nursery_nonsmall(self, totalsize):
        # 'totalsize' should be aligned.
        ll_assert(raw_malloc_usage(totalsize) & (WORD-1) == 0,
                  "misaligned totalsize in _malloc_out_of_nursery_nonsmall")
        #
        arena = llarena.arena_malloc(raw_malloc_usage(totalsize), False)
        if not arena:
            raise MemoryError("cannot allocate object")
        llarena.arena_reserve(arena, totalsize)
        #
        size_gc_header = self.gcheaderbuilder.size_gc_header
        self.rawmalloced_total_size += r_uint(raw_malloc_usage(totalsize))
        self.old_rawmalloced_objects.append(arena + size_gc_header)
        return arena

    def free_young_rawmalloced_objects(self):
        self.young_rawmalloced_objects.foreach(
            self._free_young_rawmalloced_obj, None)
        self.young_rawmalloced_objects.delete()
        self.young_rawmalloced_objects = self.null_address_dict()

    def _free_young_rawmalloced_obj(self, obj, ignored1, ignored2):
        # If 'obj' has GCFLAG_VISITED_RMY, it was seen by _trace_drag_out
        # and survives.  Otherwise, it dies.
        self.free_rawmalloced_object_if_unvisited(obj, GCFLAG_VISITED_RMY)

    def remove_young_arrays_from_old_objects_pointing_to_young(self):
        old = self.old_objects_pointing_to_young
        new = self.AddressStack()
        while old.non_empty():
            obj = old.pop()
            if not self.young_rawmalloced_objects.contains(obj):
                new.append(obj)
        # an extra copy, to avoid assignments to
        # 'self.old_objects_pointing_to_young'
        while new.non_empty():
            old.append(new.pop())
        new.delete()

    def _add_to_more_objects_to_trace(self, obj, ignored):
        self.header(obj).tid &= ~GCFLAG_VISITED
        self.more_objects_to_trace.append(obj)

    def minor_and_major_collection(self):
        # First, finish the current major gc, if there is one in progress.
        # This is a no-op if the gc_state is already STATE_SCANNING.
        self.gc_step_until(STATE_SCANNING)
        #
        # Then do a complete collection again.
        self.gc_step_until(STATE_MARKING)
        self.gc_step_until(STATE_SCANNING)

    def gc_step_until(self, state, reserving_size=0):
        while self.gc_state != state:
            self.minor_collection()
            self.major_collection_step(reserving_size)

    debug_gc_step_until = gc_step_until   # xxx

    def debug_gc_step(self, n=1):
        while n > 0:
            self.minor_collection()
            self.major_collection_step()
            n -= 1

    # Note - minor collections seem fast enough so that one
    # is done before every major collection step
    def major_collection_step(self, reserving_size=0):
        debug_start("gc-collect-step")
        debug_print("starting gc state: ", GC_STATES[self.gc_state])
        # Debugging checks
        ll_assert(self.nursery_free == self.nursery,
                  "nursery not empty in major_collection_step()")
        self.debug_check_consistency()


        # XXX currently very course increments, get this working then split
        # to smaller increments using stacks for resuming
        if self.gc_state == STATE_SCANNING:
            self.objects_to_trace = self.AddressStack()
            self.collect_roots()
            self.gc_state = STATE_MARKING
            self.more_objects_to_trace = self.AddressStack()
            #END SCANNING
        elif self.gc_state == STATE_MARKING:
            debug_print("number of objects to mark",
                        self.objects_to_trace.length(),
                        "plus",
                        self.more_objects_to_trace.length())
            estimate = self.gc_increment_step
            estimate_from_nursery = self.nursery_surviving_size * 2
            if estimate_from_nursery > estimate:
                estimate = estimate_from_nursery
            estimate = intmask(estimate)
            remaining = self.visit_all_objects_step(estimate)
            #
            if remaining >= estimate // 2:
                if self.more_objects_to_trace.non_empty():
                    # We consumed less than 1/2 of our step's time, and
                    # there are more objects added during the marking steps
                    # of this major collection.  Visit them all now.
                    # The idea is to ensure termination at the cost of some
                    # incrementality, in theory.
                    swap = self.objects_to_trace
                    self.objects_to_trace = self.more_objects_to_trace
                    self.more_objects_to_trace = swap
                    self.visit_all_objects()

            # XXX A simplifying assumption that should be checked,
            # finalizers/weak references are rare and short which means that
            # they do not need a seperate state and do not need to be
            # made incremental.
            if (not self.objects_to_trace.non_empty() and
                not self.more_objects_to_trace.non_empty()):
                #
                if self.objects_with_finalizers.non_empty():
                    self.deal_with_objects_with_finalizers()
                elif self.old_objects_with_weakrefs.non_empty():
                    # Weakref support: clear the weak pointers to dying objects
                    # (if we call deal_with_objects_with_finalizers(), it will
                    # invoke invalidate_old_weakrefs() itself directly)
                    self.invalidate_old_weakrefs()

                ll_assert(not self.objects_to_trace.non_empty(),
                          "objects_to_trace should be empty")
                ll_assert(not self.more_objects_to_trace.non_empty(),
                          "more_objects_to_trace should be empty")
                self.objects_to_trace.delete()
                self.more_objects_to_trace.delete()

                #
                # Light finalizers
                if self.old_objects_with_light_finalizers.non_empty():
                    self.deal_with_old_objects_with_finalizers()
                #objects_to_trace processed fully, can move on to sweeping
                self.ac.mass_free_prepare()
                self.start_free_rawmalloc_objects()
                self.gc_state = STATE_SWEEPING
            #END MARKING
        elif self.gc_state == STATE_SWEEPING:
            #
            if self.raw_malloc_might_sweep.non_empty():
                # Walk all rawmalloced objects and free the ones that don't
                # have the GCFLAG_VISITED flag.  Visit at most 'limit' objects.
                # This limit is conservatively high enough to guarantee that
                # a total object size of at least '3 * nursery_size' bytes
                # is processed.
                limit = 3 * self.nursery_size // self.small_request_threshold
                self.free_unvisited_rawmalloc_objects_step(limit)
                done = False    # the 2nd half below must still be done
            else:
                # Ask the ArenaCollection to visit a fraction of the objects.
                # Free the ones that have not been visited above, and reset
                # GCFLAG_VISITED on the others.  Visit at most '3 *
                # nursery_size' bytes.
                limit = 3 * self.nursery_size // self.ac.page_size
                done = self.ac.mass_free_incremental(self._free_if_unvisited,
                                                     limit)
            # XXX tweak the limits above
            #
            if done:
                self.num_major_collects += 1
                #
                # We also need to reset the GCFLAG_VISITED on prebuilt GC objects.
                self.prebuilt_root_objects.foreach(self._reset_gcflag_visited, None)
                #
                # Set the threshold for the next major collection to be when we
                # have allocated 'major_collection_threshold' times more than
                # we currently have -- but no more than 'max_delta' more than
                # we currently have.
                total_memory_used = float(self.get_total_memory_used())
                bounded = self.set_major_threshold_from(
                    min(total_memory_used * self.major_collection_threshold,
                        total_memory_used + self.max_delta),
                    reserving_size)
                #
                # Max heap size: gives an upper bound on the threshold.  If we
                # already have at least this much allocated, raise MemoryError.
                if bounded and (float(self.get_total_memory_used()) + reserving_size >=
                                self.next_major_collection_initial):
                    #
                    # First raise MemoryError, giving the program a chance to
                    # quit cleanly.  It might still allocate in the nursery,
                    # which might eventually be emptied, triggering another
                    # major collect and (possibly) reaching here again with an
                    # even higher memory consumption.  To prevent it, if it's
                    # the second time we are here, then abort the program.
                    if self.max_heap_size_already_raised:
                        llop.debug_fatalerror(lltype.Void,
                                              "Using too much memory, aborting")
                    self.max_heap_size_already_raised = True
                    raise MemoryError

                self.gc_state = STATE_FINALIZING
            # FINALIZING not yet incrementalised
            # but it seems safe to allow mutator to run after sweeping and
            # before finalizers are called. This is because run_finalizers
            # is a different list to objects_with_finalizers.
            # END SWEEPING
        elif self.gc_state == STATE_FINALIZING:
            # XXX This is considered rare,
            # so should we make the calling incremental? or leave as is

            # Must be ready to start another scan
            # just in case finalizer calls collect again.
            self.gc_state = STATE_SCANNING

            self.execute_finalizers()
            #END FINALIZING
        else:
            pass #XXX which exception to raise here. Should be unreachable.

        debug_print("stopping, now in gc state: ", GC_STATES[self.gc_state])
        debug_stop("gc-collect-step")

    def _free_if_unvisited(self, hdr):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        obj = hdr + size_gc_header
        if self.header(obj).tid & GCFLAG_VISITED:
            self.header(obj).tid &= ~GCFLAG_VISITED
            return False     # survives
        return True      # dies

    def _reset_gcflag_visited(self, obj, ignored):
        self.header(obj).tid &= ~GCFLAG_VISITED

    def free_rawmalloced_object_if_unvisited(self, obj, check_flag):
        if self.header(obj).tid & check_flag:
            self.header(obj).tid &= ~check_flag   # survives
            self.old_rawmalloced_objects.append(obj)
        else:
            size_gc_header = self.gcheaderbuilder.size_gc_header
            totalsize = size_gc_header + self.get_size(obj)
            allocsize = raw_malloc_usage(totalsize)
            arena = llarena.getfakearenaaddress(obj - size_gc_header)
            #
            # Must also include the card marker area, if any
            if (self.card_page_indices > 0    # <- this is constant-folded
                and self.header(obj).tid & GCFLAG_HAS_CARDS):
                #
                # Get the length and compute the number of extra bytes
                typeid = self.get_type_id(obj)
                ll_assert(self.has_gcptr_in_varsize(typeid),
                          "GCFLAG_HAS_CARDS but not has_gcptr_in_varsize")
                offset_to_length = self.varsize_offset_to_length(typeid)
                length = (obj + offset_to_length).signed[0]
                extra_words = self.card_marking_words_for_length(length)
                arena -= extra_words * WORD
                allocsize += extra_words * WORD
            #
            llarena.arena_free(arena)
            self.rawmalloced_total_size -= r_uint(allocsize)

    def start_free_rawmalloc_objects(self):
        ll_assert(not self.raw_malloc_might_sweep.non_empty(),
                  "raw_malloc_might_sweep must be empty")
        swap = self.raw_malloc_might_sweep
        self.raw_malloc_might_sweep = self.old_rawmalloced_objects
        self.old_rawmalloced_objects = swap

    # Returns true when finished processing objects
    def free_unvisited_rawmalloc_objects_step(self, nobjects):
        while self.raw_malloc_might_sweep.non_empty() and nobjects > 0:
            obj = self.raw_malloc_might_sweep.pop()
            self.free_rawmalloced_object_if_unvisited(obj, GCFLAG_VISITED)
            nobjects -= 1

        return nobjects


    def collect_roots(self):
        # Collect all roots.  Starts from all the objects
        # from 'prebuilt_root_objects'.
        self.prebuilt_root_objects.foreach(self._collect_obj,
                                           self.objects_to_trace)
        #
        # Add the roots from the other sources.
        self.root_walker.walk_roots(
            IncrementalMiniMarkGC._collect_ref_stk, # stack roots
            IncrementalMiniMarkGC._collect_ref_stk, # static in prebuilt non-gc structures
            None)   # we don't need the static in all prebuilt gc objects
        #
        # If we are in an inner collection caused by a call to a finalizer,
        # the 'run_finalizers' objects also need to be kept alive.
        self.run_finalizers.foreach(self._collect_obj,
                                    self.objects_to_trace)

    def enumerate_all_roots(self, callback, arg):
        self.prebuilt_root_objects.foreach(callback, arg)
        MovingGCBase.enumerate_all_roots(self, callback, arg)
    enumerate_all_roots._annspecialcase_ = 'specialize:arg(1)'

    @staticmethod
    def _collect_obj(obj, objects_to_trace):
        objects_to_trace.append(obj)

    def _collect_ref_stk(self, root):
        obj = root.address[0]
        llop.debug_nonnull_pointer(lltype.Void, obj)
        self.objects_to_trace.append(obj)

    def _collect_ref_rec(self, root, ignored):
        self.objects_to_trace.append(root.address[0])

    def visit_all_objects(self):
        while self.objects_to_trace.non_empty():
            self.visit_all_objects_step(sys.maxint)

    def visit_all_objects_step(self, size_to_track):
        # Objects can be added to pending by visit
        pending = self.objects_to_trace
        while pending.non_empty():
            obj = pending.pop()
            size_to_track -= self.visit(obj)
            if size_to_track < 0:
                return 0
        return size_to_track

    def visit(self, obj):
        #
        # 'obj' is a live object.  Check GCFLAG_VISITED to know if we
        # have already seen it before.
        #
        # Moreover, we can ignore prebuilt objects with GCFLAG_NO_HEAP_PTRS.
        # If they have this flag set, then they cannot point to heap
        # objects, so ignoring them is fine.  If they don't have this
        # flag set, then the object should be in 'prebuilt_root_objects',
        # and the GCFLAG_VISITED will be reset at the end of the
        # collection.
        hdr = self.header(obj)
        if hdr.tid & (GCFLAG_VISITED | GCFLAG_NO_HEAP_PTRS | GCFLAG_PINNED):
            # XXX ^^^ update doc in any way because of GCFLAG_PINNED addition? (groggi)
            return 0
        #
        # It's the first time.  We set the flag VISITED.  The trick is
        # to also set TRACK_YOUNG_PTRS here, for the write barrier.
        hdr.tid |= GCFLAG_VISITED | GCFLAG_TRACK_YOUNG_PTRS

        if self.has_gcptr(llop.extract_ushort(llgroup.HALFWORD, hdr.tid)):
            #
            # Trace the content of the object and put all objects it references
            # into the 'objects_to_trace' list.
            self.trace(obj, self._collect_ref_rec, None)

        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + self.get_size(obj)
        return raw_malloc_usage(totalsize)

    # ----------
    # id() and identityhash() support

    def _allocate_shadow(self, obj):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        size = self.get_size(obj)
        shadowhdr = self._malloc_out_of_nursery(size_gc_header +
                                                size)
        # Initialize the shadow enough to be considered a
        # valid gc object.  If the original object stays
        # alive at the next minor collection, it will anyway
        # be copied over the shadow and overwrite the
        # following fields.  But if the object dies, then
        # the shadow will stay around and only be freed at
        # the next major collection, at which point we want
        # it to look valid (but ready to be freed).
        shadow = shadowhdr + size_gc_header
        self.header(shadow).tid = self.header(obj).tid
        typeid = self.get_type_id(obj)
        if self.is_varsize(typeid):
            lenofs = self.varsize_offset_to_length(typeid)
            (shadow + lenofs).signed[0] = (obj + lenofs).signed[0]
        #
        self.header(obj).tid |= GCFLAG_HAS_SHADOW
        self.nursery_objects_shadows.setitem(obj, shadow)
        return shadow

    def _find_shadow(self, obj):
        #
        # The object is not a tagged pointer, and it is still in the
        # nursery.  Find or allocate a "shadow" object, which is
        # where the object will be moved by the next minor
        # collection
        if self.header(obj).tid & GCFLAG_HAS_SHADOW:
            shadow = self.nursery_objects_shadows.get(obj)
            ll_assert(shadow != NULL,
                      "GCFLAG_HAS_SHADOW but no shadow found")
        else:
            shadow = self._allocate_shadow(obj)
        #
        # The answer is the address of the shadow.
        return shadow
    _find_shadow._dont_inline_ = True

    @specialize.arg(2)
    def id_or_identityhash(self, gcobj, is_hash):
        """Implement the common logic of id() and identityhash()
        of an object, given as a GCREF.
        """
        obj = llmemory.cast_ptr_to_adr(gcobj)
        #
        if self.is_valid_gc_object(obj):
            if self.is_in_nursery(obj):
                obj = self._find_shadow(obj)
            elif is_hash:
                if self.header(obj).tid & GCFLAG_HAS_SHADOW:
                    #
                    # For identityhash(), we need a special case for some
                    # prebuilt objects: their hash must be the same before
                    # and after translation.  It is stored as an extra word
                    # after the object.  But we cannot use it for id()
                    # because the stored value might clash with a real one.
                    size = self.get_size(obj)
                    i = (obj + size).signed[0]
                    # Important: the returned value is not mangle_hash()ed!
                    return i
        #
        i = llmemory.cast_adr_to_int(obj)
        if is_hash:
            i = mangle_hash(i)
        return i
    id_or_identityhash._always_inline_ = True

    def id(self, gcobj):
        return self.id_or_identityhash(gcobj, False)

    def identityhash(self, gcobj):
        return self.id_or_identityhash(gcobj, True)

    # ----------
    # Finalizers

    def deal_with_young_objects_with_finalizers(self):
        """ This is a much simpler version of dealing with finalizers
        and an optimization - we can reasonably assume that those finalizers
        don't do anything fancy and *just* call them. Among other things
        they won't resurrect objects
        """
        while self.young_objects_with_light_finalizers.non_empty():
            obj = self.young_objects_with_light_finalizers.pop()
            if not self.is_forwarded(obj):
                finalizer = self.getlightfinalizer(self.get_type_id(obj))
                ll_assert(bool(finalizer), "no light finalizer found")
                finalizer(obj)
            else:
                obj = self.get_forwarding_address(obj)
                self.old_objects_with_light_finalizers.append(obj)

    def deal_with_old_objects_with_finalizers(self):
        """ This is a much simpler version of dealing with finalizers
        and an optimization - we can reasonably assume that those finalizers
        don't do anything fancy and *just* call them. Among other things
        they won't resurrect objects
        """
        new_objects = self.AddressStack()
        while self.old_objects_with_light_finalizers.non_empty():
            obj = self.old_objects_with_light_finalizers.pop()
            if self.header(obj).tid & GCFLAG_VISITED:
                # surviving
                new_objects.append(obj)
            else:
                # dying
                finalizer = self.getlightfinalizer(self.get_type_id(obj))
                ll_assert(bool(finalizer), "no light finalizer found")
                finalizer(obj)
        self.old_objects_with_light_finalizers.delete()
        self.old_objects_with_light_finalizers = new_objects

    def deal_with_objects_with_finalizers(self):
        # Walk over list of objects with finalizers.
        # If it is not surviving, add it to the list of to-be-called
        # finalizers and make it survive, to make the finalizer runnable.
        # We try to run the finalizers in a "reasonable" order, like
        # CPython does.  The details of this algorithm are in
        # pypy/doc/discussion/finalizer-order.txt.
        new_with_finalizer = self.AddressDeque()
        marked = self.AddressDeque()
        pending = self.AddressStack()
        self.tmpstack = self.AddressStack()
        while self.objects_with_finalizers.non_empty():
            x = self.objects_with_finalizers.popleft()
            ll_assert(self._finalization_state(x) != 1,
                      "bad finalization state 1")
            if self.header(x).tid & GCFLAG_VISITED:
                new_with_finalizer.append(x)
                continue
            marked.append(x)
            pending.append(x)
            while pending.non_empty():
                y = pending.pop()
                state = self._finalization_state(y)
                if state == 0:
                    self._bump_finalization_state_from_0_to_1(y)
                    self.trace(y, self._append_if_nonnull, pending)
                elif state == 2:
                    self._recursively_bump_finalization_state_from_2_to_3(y)
            self._recursively_bump_finalization_state_from_1_to_2(x)

        # Clear the weak pointers to dying objects.  Also clears them if
        # they point to objects which have the GCFLAG_FINALIZATION_ORDERING
        # bit set here.  These are objects which will be added to
        # run_finalizers().
        self.invalidate_old_weakrefs()

        while marked.non_empty():
            x = marked.popleft()
            state = self._finalization_state(x)
            ll_assert(state >= 2, "unexpected finalization state < 2")
            if state == 2:
                self.run_finalizers.append(x)
                # we must also fix the state from 2 to 3 here, otherwise
                # we leave the GCFLAG_FINALIZATION_ORDERING bit behind
                # which will confuse the next collection
                self._recursively_bump_finalization_state_from_2_to_3(x)
            else:
                new_with_finalizer.append(x)

        self.tmpstack.delete()
        pending.delete()
        marked.delete()
        self.objects_with_finalizers.delete()
        self.objects_with_finalizers = new_with_finalizer

    def _append_if_nonnull(pointer, stack):
        stack.append(pointer.address[0])
    _append_if_nonnull = staticmethod(_append_if_nonnull)

    def _finalization_state(self, obj):
        tid = self.header(obj).tid
        if tid & GCFLAG_VISITED:
            if tid & GCFLAG_FINALIZATION_ORDERING:
                return 2
            else:
                return 3
        else:
            if tid & GCFLAG_FINALIZATION_ORDERING:
                return 1
            else:
                return 0

    def _bump_finalization_state_from_0_to_1(self, obj):
        ll_assert(self._finalization_state(obj) == 0,
                  "unexpected finalization state != 0")
        hdr = self.header(obj)
        hdr.tid |= GCFLAG_FINALIZATION_ORDERING

    def _recursively_bump_finalization_state_from_2_to_3(self, obj):
        ll_assert(self._finalization_state(obj) == 2,
                  "unexpected finalization state != 2")
        pending = self.tmpstack
        ll_assert(not pending.non_empty(), "tmpstack not empty")
        pending.append(obj)
        while pending.non_empty():
            y = pending.pop()
            hdr = self.header(y)
            if hdr.tid & GCFLAG_FINALIZATION_ORDERING:     # state 2 ?
                hdr.tid &= ~GCFLAG_FINALIZATION_ORDERING   # change to state 3
                self.trace(y, self._append_if_nonnull, pending)

    def _recursively_bump_finalization_state_from_1_to_2(self, obj):
        # recursively convert objects from state 1 to state 2.
        # The call to visit_all_objects() will add the GCFLAG_VISITED
        # recursively.
        self.objects_to_trace.append(obj)
        self.visit_all_objects()


    # ----------
    # Weakrefs

    # The code relies on the fact that no weakref can be an old object
    # weakly pointing to a young object.  Indeed, weakrefs are immutable
    # so they cannot point to an object that was created after it.
    # Thanks to this, during a minor collection, we don't have to fix
    # or clear the address stored in old weakrefs.
    def invalidate_young_weakrefs(self):
        """Called during a nursery collection."""
        # walk over the list of objects that contain weakrefs and are in the
        # nursery.  if the object it references survives then update the
        # weakref; otherwise invalidate the weakref
        while self.young_objects_with_weakrefs.non_empty():
            obj = self.young_objects_with_weakrefs.pop()
            if not self.is_forwarded(obj):
                continue # weakref itself dies
            obj = self.get_forwarding_address(obj)
            offset = self.weakpointer_offset(self.get_type_id(obj))
            pointing_to = (obj + offset).address[0]
            if self.is_in_nursery(pointing_to):
                if self.is_forwarded(pointing_to):
                    (obj + offset).address[0] = self.get_forwarding_address(
                        pointing_to)
                else:
                    (obj + offset).address[0] = llmemory.NULL
                    continue    # no need to remember this weakref any longer
            #
            elif (bool(self.young_rawmalloced_objects) and
                  self.young_rawmalloced_objects.contains(pointing_to)):
                # young weakref to a young raw-malloced object
                if self.header(pointing_to).tid & GCFLAG_VISITED_RMY:
                    pass    # survives, but does not move
                else:
                    (obj + offset).address[0] = llmemory.NULL
                    continue    # no need to remember this weakref any longer
            #
            elif self.header(pointing_to).tid & GCFLAG_NO_HEAP_PTRS:
                # see test_weakref_to_prebuilt: it's not useful to put
                # weakrefs into 'old_objects_with_weakrefs' if they point
                # to a prebuilt object (they are immortal).  If moreover
                # the 'pointing_to' prebuilt object still has the
                # GCFLAG_NO_HEAP_PTRS flag, then it's even wrong, because
                # 'pointing_to' will not get the GCFLAG_VISITED during
                # the next major collection.  Solve this by not registering
                # the weakref into 'old_objects_with_weakrefs'.
                continue
            #
            self.old_objects_with_weakrefs.append(obj)

    def invalidate_old_weakrefs(self):
        """Called during a major collection."""
        # walk over list of objects that contain weakrefs
        # if the object it references does not survive, invalidate the weakref
        new_with_weakref = self.AddressStack()
        while self.old_objects_with_weakrefs.non_empty():
            obj = self.old_objects_with_weakrefs.pop()
            if self.header(obj).tid & GCFLAG_VISITED == 0:
                continue # weakref itself dies
            offset = self.weakpointer_offset(self.get_type_id(obj))
            pointing_to = (obj + offset).address[0]
            ll_assert((self.header(pointing_to).tid & GCFLAG_NO_HEAP_PTRS)
                      == 0, "registered old weakref should not "
                            "point to a NO_HEAP_PTRS obj")
            tid = self.header(pointing_to).tid
            if ((tid & (GCFLAG_VISITED | GCFLAG_FINALIZATION_ORDERING)) ==
                        GCFLAG_VISITED):
                new_with_weakref.append(obj)
            else:
                (obj + offset).address[0] = llmemory.NULL
        self.old_objects_with_weakrefs.delete()
        self.old_objects_with_weakrefs = new_with_weakref
