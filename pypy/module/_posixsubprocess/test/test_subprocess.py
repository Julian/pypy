from os.path import dirname
import py, sys

if sys.platform == 'win32':
    py.test.skip("not used on win32")


class AppTestSubprocess:
    spaceconfig = dict(usemodules=('_posixsubprocess', 'signal',
                                   'fcntl', 'select', 'time', 'struct'))
    # XXX write more tests

    def setup_class(cls):
        cls.w_dir = cls.space.wrap(dirname(__file__))

    def test_close_fds_true(self):
        import traceback  # Work around a recursion limit
        import subprocess
        import os.path
        import os

        fds = os.pipe()
        #self.addCleanup(os.close, fds[0])
        #self.addCleanup(os.close, fds[1])

        open_fds = set(fds)
        # add a bunch more fds
        for _ in range(9):
            fd = os.open("/dev/null", os.O_RDONLY)
            #self.addCleanup(os.close, fd)
            open_fds.add(fd)

        p = subprocess.Popen(['/usr/bin/env', 'python', os.path.join(self.dir, 'fd_status.py')], stdout=subprocess.PIPE, close_fds=True)
        output, ignored = p.communicate()
        remaining_fds = set(map(int, output.split(b',')))

        assert not (remaining_fds & open_fds), "Some fds were left open"
        assert 1 in remaining_fds, "Subprocess failed"

    def test_start_new_session(self):
        # For code coverage of calling setsid().  We don't care if we get an
        # EPERM error from it depending on the test execution environment, that
        # still indicates that it was called.
        import traceback  # Work around a recursion limit
        import subprocess
        import os
        try:
            output = subprocess.check_output(
                    ['/usr/bin/env', 'python', "-c",
                     "import os; print(os.getpgid(os.getpid()))"],
                    start_new_session=True)
        except OSError as e:
            if e.errno != errno.EPERM:
                raise
        else:
            parent_pgid = os.getpgid(os.getpid())
            child_pgid = int(output)
            assert parent_pgid != child_pgid

    def test_cpython_issue15736(self):
        import _posixsubprocess
        import sys
        n = 0
        class Z(object):
            def __len__(self):
                return sys.maxsize + n
            def __getitem__(self, i):
                return b'x'
        raises(MemoryError, _posixsubprocess.fork_exec,
               1,Z(),3,[1, 2],5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21)
        n = 1
        raises(OverflowError, _posixsubprocess.fork_exec,
               1,Z(),3,[1, 2],5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21)

    def test_pass_fds_make_inheritable(self):
        import subprocess, posix

        fd1, fd2 = posix.pipe()
        assert posix.get_inheritable(fd1) is False
        assert posix.get_inheritable(fd2) is False

        subprocess.check_call(['/usr/bin/env', 'python', '-c',
                               'import os;os.write(%d,b"K")' % fd2],
                              close_fds=True, pass_fds=[fd2])
        res = posix.read(fd1, 1)
        assert res == b"K"
        posix.close(fd1)
        posix.close(fd2)

    def test_user(self):
        import os
        import sys
        import subprocess
        import errno

        try:
            import pwd
            import grp
        except ImportError:
            pwd = grp = None

        uid = os.geteuid()
        test_users = [65534 if uid != 65534 else 65533, uid]

        for user in test_users:
            try:
                output = subprocess.check_output(
                        ["python", "-c",
                         "import os; print(os.getuid())"],
                        user=user)
            except OSError as e:
                if e.errno != errno.EPERM:
                    raise
            else:
                if isinstance(user, str):
                    user_uid = pwd.getpwnam(user).pw_uid
                else:
                    user_uid = user
                child_user = int(output)
                assert child_user == user_uid

        with raises(ValueError):
            subprocess.check_call(["python", "-c", "pass"], user=-1)

    def test_extra_groups(self):
        import os
        import sys
        import subprocess
        import errno

        try:
            import pwd
            import grp
        except ImportError:
            pwd = grp = None

        gid = os.getegid()
        group_list = [65534 if gid != 65534 else 65533]
        perm_error = False

        try:
            output = subprocess.check_output(
                    ["python", "-c",
                     "import os, sys, json; json.dump(os.getgroups(), sys.stdout)"],
                    extra_groups=group_list)
        except OSError as ex:
            if ex.errno != errno.EPERM:
                raise
            perm_error = True

        else:
            parent_groups = os.getgroups()
            child_groups = json.loads(output)

            if grp is not None:
                desired_gids = [grp.getgrnam(g).gr_gid if isinstance(g, str) else g
                                for g in group_list]
            else:
                desired_gids = group_list

            if perm_error:
                assert set(child_groups) == set(parent_groups)
            else:
                assert set(desired_gids) == set(parent_groups)

    def test_umask(self):
        """
        import tempfile, shutil, os, subprocess
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp()
            name = os.path.join(tmpdir, "beans")
            # We set an unusual umask in the child so as a unique mode
            # for us to test the child's touched file for.
            subprocess.check_call(
                    ["python", "-c", f"open({name!r}, 'w').close()"],
                    umask=0o053)
            # Ignore execute permissions entirely in our test,
            # filesystems could be mounted to ignore or force that.
            st_mode = os.stat(name).st_mode & 0o666
            expected_mode = 0o624
            assert expected_mode == st_mode
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir)
        """

