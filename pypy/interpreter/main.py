import sys
from pypy.interpreter import eval, module
from pypy.interpreter.error import OperationError


def ensure__main__(space):
    w_main = space.wrap('__main__')
    w_modules = space.sys.get('modules')
    try:
        return space.getitem(w_modules, w_main)
    except OperationError, e:
        if not e.match(space, space.w_KeyError):
            raise
    mainmodule = module.Module(space, w_main)
    space.setitem(w_modules, w_main, mainmodule)
    return mainmodule

def compilecode(space, source, filename, cmd='exec'):
    w = space.wrap
    w_code = space.builtin.call('compile', 
             w(source), w(filename), w(cmd), w(0), w(0))
    pycode = space.interp_w(eval.Code, w_code)
    return pycode


def _run_eval_string(source, filename, space, eval):
    if eval:
        cmd = 'eval'
    else:
        cmd = 'exec'
 
    try:
        if space is None:
            from pypy.objspace.std import StdObjSpace
            space = StdObjSpace()

        w = space.wrap

        pycode = compilecode(space, source, filename or '<string>', cmd)

        mainmodule = ensure__main__(space)
        w_globals = mainmodule.w_dict

        space.setitem(w_globals, w('__builtins__'), space.builtin)
        if filename is not None:
            space.setitem(w_globals, w('__file__'), w(filename))

        retval = pycode.exec_code(space, w_globals, w_globals)
        if eval:
            return retval
        else:
            return

    except OperationError, operationerr:
        operationerr.record_interpreter_traceback()
        raise

def run_string(source, filename=None, space=None):
    _run_eval_string(source, filename, space, False)

def eval_string(source, filename=None, space=None):
    return _run_eval_string(source, filename, space, True)

def run_file(filename, space=None):
    if __name__=='__main__':
        print "Running %r with %r" % (filename, space)
    istring = open(filename).read()
    run_string(istring, filename, space)

def run_module(module_name, args, space=None):
    """Implements PEP 338 'Executing modules as scripts', overwriting
    sys.argv[1:] using `args` and executing the module `module_name`.
    sys.argv[0] always is `module_name`.

    Delegates the real work to the runpy module provided as the reference
    implementation.
    """
    if space is None:
        from pypy.objspace.std import StdObjSpace
        space = StdObjSpace()
    w = space.wrap
    argv = [module_name]
    if args is not None:
        argv.extend(args)
    space.setitem(space.sys.w_dict, w('argv'), w(argv))
    w_import = space.builtin.get('__import__')
    runpy = space.call_function(w_import, w('runpy'))
    w_run_module = space.getitem(runpy.w_dict, w('run_module'))
    return space.call_function(w_run_module, w(module_name), space.w_None,
                               w('__main__'), space.w_True)

# ____________________________________________________________

def run_toplevel(space, f, verbose=False):
    """Calls f() and handle all OperationErrors.
    Intended use is to run the main program or one interactive statement.
    run_protected() handles details like forwarding exceptions to
    sys.excepthook(), catching SystemExit, printing a newline after
    sys.stdout if needed, etc.
    """
    try:
        # run it
        f()

        # we arrive here if no exception is raised.  stdout cosmetics...
        try:
            w_stdout = space.sys.get('stdout')
            w_softspace = space.getattr(w_stdout, space.wrap('softspace'))
        except OperationError, e:
            if not e.match(space, space.w_AttributeError):
                raise
            # Don't crash if user defined stdout doesn't have softspace
        else:
            if space.is_true(w_softspace):
                space.call_method(w_stdout, 'write', space.wrap('\n'))

    except OperationError, operationerr:
        operationerr.normalize_exception(space)
        w_type = operationerr.w_type
        w_value = operationerr.get_w_value(space)
        w_traceback = space.wrap(operationerr.get_traceback())

        # for debugging convenience we also insert the exception into
        # the interpreter-level sys.last_xxx
        operationerr.record_interpreter_traceback()
        sys.last_type, sys.last_value, sys.last_traceback = sys.exc_info()

        try:
            # exit if we catch a w_SystemExit
            if operationerr.match(space, space.w_SystemExit):
                w_exitcode = space.getattr(w_value,
                                           space.wrap('code'))
                if space.is_w(w_exitcode, space.w_None):
                    exitcode = 0
                else:
                    try:
                        exitcode = space.int_w(w_exitcode)
                    except OperationError:
                        # not an integer: print it to stderr
                        msg = space.str_w(space.str(w_exitcode))
                        print >> sys.stderr, msg
                        exitcode = 1
                raise SystemExit(exitcode)

            # set the sys.last_xxx attributes
            space.setitem(space.sys.w_dict, space.wrap('last_type'), w_type)
            space.setitem(space.sys.w_dict, space.wrap('last_value'), w_value)
            space.setitem(space.sys.w_dict, space.wrap('last_traceback'),
                          w_traceback)

            # call sys.excepthook if present
            w_hook = space.sys.getdictvalue(space, 'excepthook')
            if w_hook is not None:
                # hack: skip it if it wasn't modified by the user,
                #       to do instead the faster verbose/nonverbose thing below
                w_original = space.sys.getdictvalue(space, '__excepthook__')
                if w_original is None or not space.is_w(w_hook, w_original):
                    space.call_function(w_hook, w_type, w_value, w_traceback)
                    return False   # done

        except OperationError, err2:
            # XXX should we go through sys.get('stderr') ?
            print >> sys.stderr, 'Error calling sys.excepthook:'
            err2.print_application_traceback(space)
            print >> sys.stderr
            print >> sys.stderr, 'Original exception was:'

        # we only get here if sys.excepthook didn't do its job
        if verbose:
            operationerr.print_detailed_traceback(space)
        else:
            operationerr.print_application_traceback(space)
        return False

    return True   # success
