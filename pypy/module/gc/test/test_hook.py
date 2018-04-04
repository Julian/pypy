from rpython.rlib.rarithmetic import r_uint
from pypy.module.gc.hook import LowLevelGcHooks
from pypy.interpreter.baseobjspace import ObjSpace
from pypy.interpreter.gateway import interp2app, unwrap_spec

class AppTestGcHooks(object):

    def setup_class(cls):
        space = cls.space
        gchooks = space.fromcache(LowLevelGcHooks)

        @unwrap_spec(ObjSpace, r_uint, int)
        def fire_gc_minor(space, total_memory_used, pinned_objects):
            gchooks.fire_gc_minor(total_memory_used, pinned_objects)

        @unwrap_spec(ObjSpace, int, int)
        def fire_gc_collect_step(space, oldstate, newstate):
            gchooks.fire_gc_collect_step(oldstate, newstate)

        cls.w_fire_gc_minor = space.wrap(interp2app(fire_gc_minor))
        cls.w_fire_gc_collect_step = space.wrap(interp2app(fire_gc_collect_step))

    def test_on_gc_minor(self):
        import gc
        lst = []
        def on_gc_minor(stats):
            lst.append((stats.total_memory_used, stats.pinned_objects))
        gc.set_hooks(on_gc_minor=on_gc_minor)
        self.fire_gc_minor(10, 20)
        self.fire_gc_minor(30, 40)
        assert lst == [
            (10, 20),
            (30, 40),
            ]
        #
        gc.set_hooks(on_gc_minor=None)
        self.fire_gc_minor(50, 60)  # won't fire because the hooks is disabled
        assert lst == [
            (10, 20),
            (30, 40),
            ]

    def test_on_gc_collect_step(self):
        import gc
        lst = []
        def on_gc_collect_step(stats):
            lst.append((stats.oldstate, stats.newstate))
        gc.set_hooks(on_gc_collect_step=on_gc_collect_step)
        self.fire_gc_collect_step(10, 20)
        self.fire_gc_collect_step(30, 40)
        assert lst == [
            (10, 20),
            (30, 40),
            ]
        #
        gc.set_hooks(on_gc_collect_step=None)
        self.fire_gc_collect_step(50, 60)  # won't fire
        assert lst == [
            (10, 20),
            (30, 40),
            ]
