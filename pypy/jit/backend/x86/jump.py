import sys
from pypy.tool.pairtype import extendabletype
from pypy.jit.backend.x86.regloc import ImmedLoc, StackLoc

def remap_frame_layout(assembler, src_locations, dst_locations, tmpreg):
    pending_dests = len(dst_locations)
    srccount = {}    # maps dst_locations to how many times the same
                     # location appears in src_locations
    for dst in dst_locations:
        key = dst._getregkey()
        assert key not in srccount, "duplicate value in dst_locations!"
        srccount[key] = 0
    for i in range(len(dst_locations)):
        src = src_locations[i]
        if isinstance(src, ImmedLoc):
            continue
        key = src._getregkey()
        if key in srccount:
            if key == dst_locations[i]._getregkey():
                srccount[key] = -sys.maxint     # ignore a move "x = x"
                pending_dests -= 1
            else:
                srccount[key] += 1

    while pending_dests > 0:
        progress = False
        for i in range(len(dst_locations)):
            dst = dst_locations[i]
            key = dst._getregkey()
            if srccount[key] == 0:
                srccount[key] = -1       # means "it's done"
                pending_dests -= 1
                src = src_locations[i]
                if not isinstance(src, ImmedLoc):
                    key = src._getregkey()
                    if key in srccount:
                        srccount[key] -= 1
                _move(assembler, src, dst, tmpreg)
                progress = True
        if not progress:
            # we are left with only pure disjoint cycles
            sources = {}     # maps dst_locations to src_locations
            for i in range(len(dst_locations)):
                src = src_locations[i]
                dst = dst_locations[i]
                sources[dst._getregkey()] = src
            #
            for i in range(len(dst_locations)):
                dst = dst_locations[i]
                originalkey = dst._getregkey()
                if srccount[originalkey] >= 0:
                    assembler.regalloc_push(dst)
                    while True:
                        key = dst._getregkey()
                        assert srccount[key] == 1
                        # ^^^ because we are in a simple cycle
                        srccount[key] = -1
                        pending_dests -= 1
                        src = sources[key]
                        if src._getregkey() == originalkey:
                            break
                        _move(assembler, src, dst, tmpreg)
                        dst = src
                    assembler.regalloc_pop(dst)
            assert pending_dests == 0

def _move(assembler, src, dst, tmpreg):
    if dst.is_memory_reference() and src.is_memory_reference():
        assembler.regalloc_mov(src, tmpreg)
        src = tmpreg
    assembler.regalloc_mov(src, dst)
