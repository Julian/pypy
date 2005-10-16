
/************************************************************/
 /***  C header subsection: operations on LowLevelTypes    ***/


/* a reasonably safe bound on the largest allowed argument value
   that we can pass to malloc.  This is used for var-sized mallocs
   to compute the largest allowed number of items in the array. */
#define MAXIMUM_MALLOCABLE_SIZE   (LONG_MAX-4096)

#define OP_MAX_VARSIZE(numitems, itemtype, err)  {			\
    if (((unsigned)(numitems)) > (MAXIMUM_MALLOCABLE_SIZE / sizeof(itemtype)))\
        FAIL_EXCEPTION(err, PyExc_MemoryError, "addr space overflow");	\
  } 


/* XXX hack to initialize the refcount of global structures: officially,
   we need a value equal to the number of references to this global from
   other globals, plus one.  This upper bound "approximation" will do... */
#define REFCOUNT_IMMORTAL  (INT_MAX/2)

#define OP_ZERO_MALLOC(size, r, err)  {                                 \
    r = (void*) PyObject_Malloc(size);                                  \
    if (r == NULL) FAIL_EXCEPTION(err, PyExc_MemoryError, "out of memory");\
    memset((void*) r, 0, size);                                         \
    COUNT_MALLOC;                                                       \
  }

#define OP_FREE(p)	{ PyObject_Free(p); COUNT_FREE; }

/* XXX uses officially bad fishing */
#define PUSH_ALIVE(obj) obj->refcount++

/*------------------------------------------------------------*/
#ifndef COUNT_OP_MALLOCS
/*------------------------------------------------------------*/

#define COUNT_MALLOC	/* nothing */
#define COUNT_FREE	/* nothing */

/*------------------------------------------------------------*/
#else /*COUNT_OP_MALLOCS*/
/*------------------------------------------------------------*/

static int count_mallocs=0, count_frees=0;

#define COUNT_MALLOC	count_mallocs++
#define COUNT_FREE	count_frees++

PyObject* malloc_counters(PyObject* self, PyObject* args)
{
  return Py_BuildValue("ii", count_mallocs, count_frees);
}

/*------------------------------------------------------------*/
#endif /*COUNT_OP_MALLOCS*/
/*------------------------------------------------------------*/

/* for Boehm GC */

#ifdef USING_BOEHM_GC

#define BOEHM_MALLOC_0_0   GC_MALLOC
#define BOEHM_MALLOC_1_0   GC_MALLOC_ATOMIC
#define BOEHM_MALLOC_0_1   GC_MALLOC
#define BOEHM_MALLOC_1_1   GC_MALLOC_ATOMIC
/* #define BOEHM_MALLOC_0_1   GC_MALLOC_IGNORE_OFF_PAGE */
/* #define BOEHM_MALLOC_1_1   GC_MALLOC_ATOMIC_IGNORE_OFF_PAGE */

#define OP_BOEHM_ZERO_MALLOC(size, r, is_atomic, is_varsize, err)   {        \
	r = (void*) BOEHM_MALLOC_ ## is_atomic ## _ ## is_varsize (size);    \
	if (r == NULL) FAIL_EXCEPTION(err, PyExc_MemoryError, "out of memory");	\
	if (is_atomic)  /* the non-atomic versions return cleared memory */  \
		memset((void*) r, 0, size);                                  \
  }

#undef PUSH_ALIVE
#define PUSH_ALIVE(obj)

#endif /* USING_BOEHM_GC */

/* for no GC */
#ifdef USING_NO_GC

#undef OP_ZERO_MALLOC

#define OP_ZERO_MALLOC(size, r, err)  {                                 \
    r = (void*) malloc(size);                                  \
    if (r == NULL) FAIL_EXCEPTION(err, PyExc_MemoryError, "out of memory");\
    memset((void*) r, 0, size);                                         \
    COUNT_MALLOC;                                                       \
  }

#undef PUSH_ALIVE
#define PUSH_ALIVE(obj)

#endif /* USING_NO_GC */
