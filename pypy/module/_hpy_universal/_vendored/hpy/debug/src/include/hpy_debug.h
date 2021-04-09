#ifndef HPY_DEBUG_H
#define HPY_DEBUG_H

#include "hpy.h"

/*
  This is the main public API for the debug mode, and it's meant to be used
  by hpy.universal implementations (including but not limited to the
  CPython's version of hpy.universal which is included in this repo).

  The idea is that for every uctx there is a corresponding unique dctx which
  wraps it.

  If you call hpy_debug_get_ctx twice on the same uctx, you get the same
  result.

  IMPLEMENTATION NOTE: at the moment of writing, the only known user of the
  debug mode is CPython's hpy.universal: in that module, the uctx is a
  statically allocated singleton, so for simplicity of implementation
  currently we do the same inside debug_ctx.c, with a sanity check to ensure
  that we don't call hpy_debug_get_ctx with different uctxs. But this is a
  limitation of the current implementation and users should not rely on it. It
  is likely that we will need to change it in the future, e.g. if we want to
  have per-subinterpreter uctxs.
*/

HPyContext hpy_debug_get_ctx(HPyContext uctx);

// take a debug handle, unwrap it and return the correspnding universal
// handle. This is basically the same as DHPy_unwrap, but has a different name
// because this is the public-facing API and DHPy/UHPy are only internal
// implementation details.
HPy hpy_debug_unwrap_handle(HPy uh);


// this is the HPy init function created by HPy_MODINIT. In CPython's version
// of hpy.universal the code is embedded inside the extension, so we can call
// this function directly instead of dlopen it. This is similar to what
// CPython does for its own built-in modules
HPy HPyInit__debug(HPyContext uctx);

#endif /* HPY_DEBUG_H */
