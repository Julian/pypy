#include <hpy_debug.h>
#include "dctx.h"

// the default symbol visibility is hidden: the easiest way to export
// these two functions is to write a small wrapper.
HPyContext pypy_hpy_debug_get_ctx(HPyContext uctx) {
    return hpy_debug_get_ctx(uctx);
}
int pypy_hpy_debug_ctx_init(HPyContext dctx, HPyContext uctx) {
    return hpy_debug_ctx_init(dctx, uctx);
}
HPy pypy_hpy_debug_wrap_handle(HPyContext dctx, HPy uh) {
    return hpy_debug_wrap_handle(dctx, uh);
}
HPy pypy_hpy_debug_unwrap_handle(HPy dh) {
    return hpy_debug_unwrap_handle(dh);
}
HPy pypy_HPyInit__debug(HPyContext uctx) {
    return HPyInit__debug(uctx);
}

void pypy_hpy_debug_set_ctx(HPyContext dctx) {
    hpy_debug_set_ctx(dctx);
}


// NOTE: this is currently unused: it is needed because it is
// referenced by hpy_magic_dump. But we could try to use this variable to
// store the actual ctx instead of malloc()ing it in setup_ctx.
struct _HPyContext_s g_universal_ctx;
