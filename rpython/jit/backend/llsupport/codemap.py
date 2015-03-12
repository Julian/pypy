
""" Bytecode for storage in asmmemmgr.jit_codemap. Format is as follows:

 list of tuples of shape (addr, machine code size, bytecode info)
 where bytecode info is a string made up of:
    8 bytes unique_id, 4 bytes start_addr (relative), 4 bytes size (relative),
    2 bytes how many items to skip to go to the next on similar level
    [so far represented by a list of integers for simplicity]

"""

import os
from rpython.rlib import rgc
from rpython.rlib.objectmodel import specialize, we_are_translated
from rpython.rlib.entrypoint import jit_entrypoint
from rpython.rlib.rbisect import bisect_right, bisect_right_addr
from rpython.rlib.rbisect import bisect_left, bisect_left_addr
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.translator import cdir


INT_LIST_PTR = rffi.CArrayPtr(lltype.Signed)


srcdir = os.path.join(os.path.dirname(__file__), 'src')

eci = ExternalCompilationInfo(post_include_bits=["""
#include <stdint.h>
RPY_EXTERN long pypy_jit_codemap_add(uintptr_t addr,
                                     unsigned int machine_code_size,
                                     long *bytecode_info,
                                     unsigned int bytecode_info_size);
RPY_EXTERN long *pypy_jit_codemap_del(uintptr_t addr);
RPY_EXTERN uintptr_t pypy_jit_codemap_firstkey(void);
RPY_EXTERN void *pypy_find_codemap_at_addr(long addr);
RPY_EXTERN long pypy_yield_codemap_at_addr(void *codemap_raw, long addr,
                                           long *current_pos_addr);

RPY_EXTERN long pypy_jit_depthmap_add(uintptr_t addr, unsigned int size,
                                      unsigned int stackdepth);
RPY_EXTERN void pypy_jit_depthmap_clear(uintptr_t addr, unsigned int size);

"""], separate_module_sources=[
    open(os.path.join(srcdir, 'skiplist.c'), 'r').read() +
    open(os.path.join(srcdir, 'codemap.c'), 'r').read()
], include_dirs=[cdir])

def llexternal(name, args, res):
    return rffi.llexternal(name, args, res, compilation_info=eci,
                           releasegil=False)

pypy_jit_codemap_add = llexternal('pypy_jit_codemap_add',
                                  [lltype.Signed, lltype.Signed,
                                   INT_LIST_PTR, lltype.Signed],
                                  lltype.Signed)
pypy_jit_codemap_del = llexternal('pypy_jit_codemap_del',
                                  [lltype.Signed], INT_LIST_PTR)
pypy_jit_codemap_firstkey = llexternal('pypy_jit_codemap_firstkey',
                                       [], lltype.Signed)

pypy_jit_depthmap_add = llexternal('pypy_jit_depthmap_add',
                                   [lltype.Signed, lltype.Signed,
                                    lltype.Signed], lltype.Signed)
pypy_jit_depthmap_clear = llexternal('pypy_jit_depthmap_clear',
                                     [lltype.Signed, lltype.Signed],
                                     lltype.Void)

stack_depth_at_loc = llexternal('pypy_jit_stack_depth_at_loc',
                                [lltype.Signed], lltype.Signed)
find_codemap_at_addr = llexternal('pypy_find_codemap_at_addr',
                                  [lltype.Signed], lltype.Signed)
yield_bytecode_at_addr = llexternal('pypy_yield_codemap_at_addr',
                                    [lltype.Signed, lltype.Signed,
                                     rffi.CArrayPtr(lltype.Signed)],
                                     lltype.Signed)


class CodemapStorage(object):
    """ An immortal wrapper around underlaying jit codemap data
    """
    def setup(self):
        if not we_are_translated():
             # in case someone failed to call free(), in tests only anyway
             self.free()

    def free(self):
        while True:
            key = pypy_jit_codemap_firstkey()
            if not key:
                break
            items = pypy_jit_codemap_del(key)
            lltype.free(items, flavor='raw', track_allocation=False)

    def free_asm_block(self, start, stop):
        items = pypy_jit_codemap_del(start)
        if items:
            lltype.free(items, flavor='raw', track_allocation=False)
        pypy_jit_depthmap_clear(start, stop - start)

    def register_frame_depth_map(self, rawstart, rawstop, frame_positions,
                                 frame_assignments):
        if not frame_positions:
            return
        assert len(frame_positions) == len(frame_assignments)
        for i in range(len(frame_positions)-1, -1, -1):
            pos = rawstart + frame_positions[i]
            length = rawstop - pos
            if length > 0:
                #print "ADD:", pos, length, frame_assignments[i]
                pypy_jit_depthmap_add(pos, length, frame_assignments[i])
            rawstop = pos

    def register_codemap(self, (start, size, l)):
        items = lltype.malloc(INT_LIST_PTR.TO, len(l), flavor='raw',
                              track_allocation=False)
        for i in range(len(l)):
            items[i] = l[i]
        if pypy_jit_codemap_add(start, size, items, len(l)) < 0:
            lltype.free(items, flavor='raw', track_allocation=False)

    def finish_once(self):
        self.free()

def unpack_traceback(addr):
    codemap_raw = find_codemap_at_addr(addr)
    if not codemap_raw:
        return [] # no codemap for that position
    storage = lltype.malloc(rffi.CArray(lltype.Signed), 1, flavor='raw')
    storage[0] = 0
    res = []
    while True:
        item = yield_bytecode_at_addr(codemap_raw, addr, storage)
        if item == 0:
            break
        res.append(item)
    lltype.free(storage, flavor='raw')
    return res


class CodemapBuilder(object):
    def __init__(self):
        self.l = []
        self.patch_position = []
        self.last_call_depth = -1

    def debug_merge_point(self, call_depth, unique_id, pos):
        if call_depth != self.last_call_depth:
            if unique_id == 0: # uninteresting case
                return
            assert unique_id & 1 == 0
            if call_depth > self.last_call_depth:
                self.l.append(unique_id)
                self.l.append(pos) # <- this is a relative pos
                self.patch_position.append(len(self.l))
                self.l.append(0) # marker
                self.l.append(0) # second marker
            else:
                for i in range(self.last_call_depth - call_depth):
                    to_patch = self.patch_position.pop()
                    self.l[to_patch] = pos
                    self.l[to_patch + 1] = len(self.l)
            self.last_call_depth = call_depth

    def inherit_code_from_position(self, pos):
        lst = unpack_traceback(pos)
        self.last_call_depth = len(lst) - 1
        for item in lst:
            self.l.append(item)
            self.l.append(0)
            self.patch_position.append(len(self.l))
            self.l.append(0) # marker
            self.l.append(0) # second marker

    def get_final_bytecode(self, addr, size):
        while self.patch_position:
            pos = self.patch_position.pop()
            self.l[pos] = size
            self.l[pos + 1] = len(self.l)
        # at the end there should be no zeros
        for i in range(len(self.l) / 4):
            item = self.l[i * 4] # unique_id
            assert item > 0 # no zeros here
            item = self.l[i * 4 + 2] # end in asm
            assert item > 0
            item = self.l[i * 4 + 3] # end in l
            assert item > 0
        return (addr, size, self.l) # XXX compact self.l
