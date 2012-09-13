from pypy.tool.autopath import pypydir
from pypy.tool.udir import udir
import py
import sys
# tests here are run as snippets through a pexpected python subprocess


def setup_module(mod):
    try:
        import curses
        curses.setupterm()
    except:
        py.test.skip("Cannot test this here")

class TestCurses(object):
    """ We need to fork here, to prevent
    the setup to be done
    """
    def _spawn(self, *args, **kwds):
        import pexpect
        kwds.setdefault('timeout', 600)
        print 'SPAWN:', args, kwds
        child = pexpect.spawn(*args, **kwds)
        child.logfile = sys.stdout
        return child

    def spawn(self, argv):
        py_py = py.path.local(pypydir).join('bin', 'py.py')
        return self._spawn(sys.executable, [str(py_py)] + argv)

    def setup_class(self):
        try:
            import pexpect
        except ImportError:
            py.test.skip('pexpect not found')

    def test_setupterm(self):
        source = py.code.Source("""
        import _minimal_curses
        try:
            _minimal_curses.tigetstr('cup')
        except _minimal_curses.error:
            print 'ok!'
        """)
        f = udir.join("test_setupterm.py")
        f.write(source)
        child = self.spawn(['--withmod-_minimal_curses', str(f)])
        child.expect('ok!')

    def test_tigetstr(self):
        source = py.code.Source("""
        import _minimal_curses
        _minimal_curses.setupterm()
        assert _minimal_curses.tigetstr('cup') == '\x1b[%i%p1%d;%p2%dH'
        print 'ok!'
        """)
        f = udir.join("test_tigetstr.py")
        f.write(source)
        child = self.spawn(['--withmod-_minimal_curses', str(f)])
        child.expect('ok!')

    def test_tparm(self):
        source = py.code.Source("""
        import _minimal_curses
        _minimal_curses.setupterm()
        assert _minimal_curses.tparm(_minimal_curses.tigetstr('cup'), 5, 3) == '\033[6;4H'
        print 'ok!'
        """)
        f = udir.join("test_tparm.py")
        f.write(source)
        child = self.spawn(['--withmod-_minimal_curses', str(f)])
        child.expect('ok!')
        
class ExpectTestCCurses(object):
    """ Test compiled version
    """
    def test_csetupterm(self):
        from pypy.translator.c.test.test_genc import compile
        from pypy.module._minimal_curses import interp_curses
        def runs_setupterm():
            interp_curses._curses_setupterm_null(1)

        fn = compile(runs_setupterm, [])
        fn()

    def test_ctgetstr(self):
        from pypy.translator.c.test.test_genc import compile
        from pypy.module._minimal_curses import interp_curses
        def runs_ctgetstr():
            interp_curses._curses_setupterm("xterm", 1)
            return interp_curses._curses_tigetstr('cup')

        fn = compile(runs_ctgetstr, [])
        res = fn()
        assert res == '\x1b[%i%p1%d;%p2%dH'

    def test_ctparm(self):
        from pypy.translator.c.test.test_genc import compile
        from pypy.module._minimal_curses import interp_curses
        def runs_tparm():
            interp_curses._curses_setupterm("xterm", 1)
            cup = interp_curses._curses_tigetstr('cup')
            return interp_curses._curses_tparm(cup, [5, 3])

        fn = compile(runs_tparm, [])
        res = fn()
        assert res == '\033[6;4H'

