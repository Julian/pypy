
/************************************************************/
/***  C header file for code produced by genc.py          ***/

#include <stdlib.h>
#include <assert.h>
#include <math.h>

#include "src/mem.h"
#include "src/exception.h"
#include "src/support.h"
#ifndef PY_LONG_LONG
#define PY_LONG_LONG long long
#endif

#include "src/int.h"
#include "src/char.h"
#include "src/float.h"
#include "src/address.h"
#include "src/unichar.h"
#include "src/llgroup.h"

#include "src/instrument.h"
#include "src/asm.h"

#include "src/profiling.h"

#include "src/debug_print.h"

/*** modules ***/
#ifdef HAVE_RTYPER      /* only if we have an RTyper */
#  include "src/rtyper.h"
#  include "src/debug_traceback.h"
#  include "src/debug_alloc.h"
#  include "src/ll_os.h"
#  include "src/ll_strtod.h"
#endif

#  include "src/allocator.h"
#ifdef PYPY_STANDALONE
#  include "src/main.h"
#endif

/* suppress a few warnings in the generated code */
#ifdef MS_WINDOWS
#  ifdef _MSC_VER
#    pragma warning(disable: 4033 4102 4101 4716)
#  endif
#endif

/* work around waitpid expecting different pointer type */
#ifdef __CYGWIN__
#include "src/cygwin_wait.h"
#endif
