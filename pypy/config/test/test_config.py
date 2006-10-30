from pypy.config.config import *
import py

def make_description():
    gcoption = ChoiceOption('name', 'GC name', ['ref', 'framework'], 'ref')
    gcdummy = BoolOption('dummy', 'dummy', default=False)
    objspaceoption = ChoiceOption('objspace', 'Object space',
                                ['std', 'logic'], 'std')
    booloption = BoolOption('bool', 'Test boolean option', default=True)
    intoption = IntOption('int', 'Test int option', default=0)
    floatoption = FloatOption('float', 'Test float option', default=2.3)
    stroption = StrOption('str', 'Test string option', default="abc")

    wantref_option = BoolOption('wantref', 'Test requires', default=False,
                                    requires=[('gc.name', 'ref')])
    wantframework_option = BoolOption('wantframework', 'Test requires',
                                      default=False,
                                      requires=[('gc.name', 'framework')])
    
    gcgroup = OptionDescription('gc', '', [gcoption, gcdummy, floatoption])
    descr = OptionDescription('pypy', '', [gcgroup, booloption, objspaceoption,
                                           wantref_option, stroption,
                                           wantframework_option,
                                           intoption])
    return descr

def test_base_config():
    descr = make_description()
    config = Config(descr, bool=False)
    
    assert config.gc.name == 'ref'
    config.gc.name = 'framework'
    assert config.gc.name == 'framework'
    assert getattr(config, "gc.name") == 'framework'

    assert config.objspace == 'std'
    config.objspace = 'logic'
    assert config.objspace == 'logic'
    
    assert config.gc.float == 2.3
    assert config.int == 0
    config.gc.float = 3.4
    config.int = 123
    assert config.gc.float == 3.4
    assert config.int == 123

    assert not config.wantref

    assert config.str == "abc"
    config.str = "def"
    assert config.str == "def"

    py.test.raises(ValueError, 'config.objspace = "foo"')
    py.test.raises(ValueError, 'config.gc.name = "foo"')
    py.test.raises(AttributeError, 'config.gc.foo = "bar"')
    py.test.raises(ValueError, 'config.bool = 123')
    py.test.raises(ValueError, 'config.int = "hello"')
    py.test.raises(ValueError, 'config.gc.float = None')

    config = Config(descr, bool=False)
    assert config.gc.name == 'ref'
    config.wantframework = True
    py.test.raises(ValueError, 'config.gc.name = "ref"')
    config.gc.name = "framework"

def test_arbitrary_option():
    descr = OptionDescription("top", "", [
        ArbitraryOption("a", "no help", default=None)
    ])
    config = Config(descr)
    config.a = []
    config.a.append(1)
    assert config.a == [1]

    descr = OptionDescription("top", "", [
        ArbitraryOption("a", "no help", defaultfactory=list)
    ])
    c1 = Config(descr)
    c2 = Config(descr)
    c1.a.append(1)
    assert c2.a == []
    assert c1.a == [1]

def test_annotator_folding():
    from pypy.translator.interactive import Translation

    gcoption = ChoiceOption('name', 'GC name', ['ref', 'framework'], 'ref')
    gcgroup = OptionDescription('gc', '', [gcoption])
    descr = OptionDescription('pypy', '', [gcgroup])
    config = Config(descr)
    
    def f(x):
        if config.gc.name == 'ref':
            return x + 1
        else:
            return 'foo'

    t = Translation(f)
    t.rtype([int])
    
    block = t.context.graphs[0].startblock
    assert len(block.exits[0].target.operations) == 0
    assert len(block.operations) == 1
    assert len(block.exits) == 1
    assert block.operations[0].opname == 'int_add'

    assert config._freeze_()
    # does not raise, since it does not change the attribute
    config.gc.name = "ref"
    py.test.raises(TypeError, 'config.gc.name = "framework"')

def test_compare_configs():
    descr = make_description()
    conf1 = Config(descr)
    conf2 = Config(descr, wantref=True)
    assert conf1 != conf2
    assert hash(conf1) != hash(conf2)
    assert conf1.getkey() != conf2.getkey()
    conf1.wantref = True
    assert conf1 == conf2
    assert hash(conf1) == hash(conf2)
    assert conf1.getkey() == conf2.getkey()

def test_loop():
    descr = make_description()
    conf = Config(descr)
    for (name, value), (gname, gvalue) in \
        zip(conf.gc, [("name", "ref"), ("dummy", False)]):
        assert name == gname
        assert value == gvalue
        
def test_to_optparse():
    gcoption = ChoiceOption('name', 'GC name', ['ref', 'framework'], 'ref',
                                cmdline='--gc -g')
    gcgroup = OptionDescription('gc', '', [gcoption])
    descr = OptionDescription('pypy', '', [gcgroup])
    config = Config(descr)
    
    parser = to_optparse(config, ['gc.name'])
    (options, args) = parser.parse_args(args=['--gc=framework'])
    
    assert config.gc.name == 'framework'
    

    config = Config(descr)
    parser = to_optparse(config, ['gc.name'])
    (options, args) = parser.parse_args(args=['-g ref'])
    assert config.gc.name == 'ref'

    # XXX strange exception
    py.test.raises(SystemExit,
                    "(options, args) = parser.parse_args(args=['-g foobar'])")

def test_to_optparse_number():
    intoption = IntOption('int', 'Int option test', cmdline='--int -i')
    floatoption = FloatOption('float', 'Float option test', 
                                cmdline='--float -f')
    descr = OptionDescription('test', '', [intoption, floatoption])
    config = Config(descr)

    parser = to_optparse(config, ['int', 'float'])
    (options, args) = parser.parse_args(args=['-i 2', '--float=0.1'])

    assert config.int == 2
    assert config.float == 0.1
    
    py.test.raises(SystemExit,
        "(options, args) = parser.parse_args(args=['--int=foo', '-f bar'])")
    
def test_to_optparse_bool():
    booloption1 = BoolOption('bool1', 'Boolean option test', default=False,
                             cmdline='--bool1 -b')
    booloption2 = BoolOption('bool2', 'Boolean option test', default=True,
                             cmdline='--with-bool2 -c')
    booloption3 = BoolOption('bool3', 'Boolean option test', default=True,
                             cmdline='--bool3')
    booloption4 = BoolOption('bool4', 'Boolean option test', default=True,
                             cmdline='--bool4', negation=False)
    descr = OptionDescription('test', '', [booloption1, booloption2,
                                           booloption3, booloption4])
    config = Config(descr)

    parser = to_optparse(config, ['bool1', 'bool2'])
    (options, args) = parser.parse_args(args=['-b'])

    assert config.bool1
    assert config.bool2

    config = Config(descr)
    parser = to_optparse(config, ['bool1', 'bool2', 'bool3', 'bool4'])
    (options, args) = parser.parse_args(args=['--without-bool2', '--no-bool3',
                                              '--bool4'])
    assert not config.bool1
    assert not config.bool2
    assert not config.bool3

    py.test.raises(SystemExit,
            "(options, args) = parser.parse_args(args=['-bfoo'])")
    py.test.raises(SystemExit,
            "(options, args) = parser.parse_args(args=['--no-bool4'])")

def test_config_start():
    descr = make_description()
    config = Config(descr)
    parser = to_optparse(config, ["gc.*"])

    options, args = parser.parse_args(args=["--gc-name=framework", "--gc-dummy"])
    assert config.gc.name == "framework"
    assert config.gc.dummy

def test_star_works_recursively():
    descr = OptionDescription("top", "", [
        OptionDescription("a", "", [
            BoolOption("b1", "", default=False, cmdline="--b1"),
            OptionDescription("sub", "", [
                BoolOption("b2", "", default=False, cmdline="--b2")
            ])
        ]),
        BoolOption("b3", "", default=False, cmdline="--b3"),
    ])
    config = Config(descr)
    assert not config.a.b1
    assert not config.a.sub.b2
    parser = to_optparse(config, ['a.*'])
    options, args = parser.parse_args(args=["--b1", "--b2"])
    assert config.a.b1
    assert config.a.sub.b2
    py.test.raises(SystemExit,
            "(options, args) = parser.parse_args(args=['--b3'])")

    config = Config(descr)
    assert not config.a.b1
    assert not config.a.sub.b2
    # does not lead to an option conflict
    parser = to_optparse(config, ['a.*', 'a.sub.*']) 
    options, args = parser.parse_args(args=["--b1", "--b2"])
    assert config.a.b1
    assert config.a.sub.b2
    
def test_optparse_path_options():
    gcoption = ChoiceOption('name', 'GC name', ['ref', 'framework'], 'ref')
    gcgroup = OptionDescription('gc', '', [gcoption])
    descr = OptionDescription('pypy', '', [gcgroup])
    config = Config(descr)
    
    parser = to_optparse(config, ['gc.name'])
    (options, args) = parser.parse_args(args=['--gc-name=framework'])

    assert config.gc.name == 'framework'

def test_getpaths():
    descr = make_description()
    config = Config(descr)
    
    assert config.getpaths() == ['gc.name', 'gc.dummy', 'gc.float', 'bool',
                                 'objspace', 'wantref', 'str', 'wantframework',
                                 'int']
    assert config.gc.getpaths() == ['name', 'dummy', 'float']
    assert config.getpaths(include_groups=True) == [
        'gc', 'gc.name', 'gc.dummy', 'gc.float',
        'bool', 'objspace', 'wantref', 'str', 'wantframework', 'int']

def test_underscore_in_option_name():
    descr = OptionDescription("opt", "", [
        BoolOption("_stackless", "", default=False),
    ])
    config = Config(descr)
    parser = to_optparse(config)
    assert parser.has_option("--_stackless")

def test_none():
    dummy1 = BoolOption('dummy1', 'doc dummy', default=False, cmdline=None)
    dummy2 = BoolOption('dummy2', 'doc dummy', default=False, cmdline='--dummy')
    group = OptionDescription('group', '', [dummy1, dummy2])
    config = Config(group)

    parser = to_optparse(config)
    py.test.raises(SystemExit,
        "(options, args) = parser.parse_args(args=['--dummy1'])")
 
def test_requirements_from_top():
    descr = OptionDescription("test", '', [
        BoolOption("toplevel", "", default=False),
        OptionDescription("sub", '', [
            BoolOption("opt", "", default=False,
                       requires=[("toplevel", True)])
        ])
    ])
    config = Config(descr)
    config.sub.opt = True
    assert config.toplevel

def test_requirements_for_choice():
    descr = OptionDescription("test", '', [
        BoolOption("toplevel", "", default=False),
        OptionDescription("s", '', [
            ChoiceOption("type_system", "", ["ll", "oo"], "ll"),
            ChoiceOption("backend", "",
                         ["c", "llvm", "cli"], "llvm",
                         requires={
                             "c": [("s.type_system", "ll"),
                                   ("toplevel", True)],
                             "llvm": [("s.type_system", "ll")],
                             "cli": [("s.type_system", "oo")],
                         })
        ])
    ])
    config = Config(descr)
    config.s.backend = "cli"
    assert config.s.type_system == "oo"

def test_choice_with_no_default():
    descr = OptionDescription("test", "", [
        ChoiceOption("backend", "", ["c", "llvm"])])
    config = Config(descr)
    assert config.backend is None
    config.backend = "c"

def test_overrides_are_defaults():
    descr = OptionDescription("test", "", [
        BoolOption("b1", "", default=False, requires=[("b2", False)]),
        BoolOption("b2", "", default=False),
        ])
    config = Config(descr, b2=True)
    assert config.b2
    config.b1 = True
    assert not config.b2
    print config._cfgimpl_value_owners

def test_overrides_require_as_default():
    descr = OptionDescription("test", "", [
        ChoiceOption("backend", "", ['c', 'cli'], 'c',
                     requires={'c': [('type_system', 'll')],
                               'cli': [('type_system', 'oo')]}),
        ChoiceOption("type_system", "", ['ll', 'oo'], 'll')
        ])
    config = Config(descr, backend='c')
    config.set(backend=None, type_system=None)
    config = Config(descr, backend='c')
    config.set(backend='cli')
    assert config.backend == 'cli'
    assert config.type_system == 'oo'
    
def test_overrides_dont_change_user_options():
    descr = OptionDescription("test", "", [
        BoolOption("b", "", default=False)])
    config = Config(descr)
    config.b = True
    config.override({'b': False})
    assert config.b
    
def test_str():
    descr = make_description()
    c = Config(descr)
    print c # does not crash

def test_dwim_set():
    descr = OptionDescription("opt", "", [
        OptionDescription("sub", "", [
            BoolOption("b1", ""),
            ChoiceOption("c1", "", ['a', 'b', 'c'], 'a'),
            BoolOption("d1", ""),
        ]),
        BoolOption("b2", ""),
        BoolOption("d1", ""),
    ])
    c = Config(descr)
    c.set(b1=False, c1='b')
    assert not c.sub.b1
    assert c.sub.c1 == 'b'
    # new config, because you cannot change values once they are set
    c = Config(descr)
    c.set(b2=False, **{'sub.c1': 'c'})
    assert not c.b2
    assert c.sub.c1 == 'c'
    py.test.raises(AmbigousOptionError, "c.set(d1=True)")
    py.test.raises(NoMatchingOptionFound, "c.set(unknown='foo')")

def test_more_set():
    descr = OptionDescription("opt", "", [
        OptionDescription("s1", "", [
            BoolOption("a", "", default=False)]),
        IntOption("int", "", default=42)])
    d = {'s1.a': True, 'int': 23}
    config = Config(descr)
    config.set(**d)
    assert config.s1.a
    assert config.int == 23

def test_optparse_help():
    import cStringIO
    descr = OptionDescription("opt", "", [
        BoolOption("bool1", 'do bool1', default=False, cmdline='--bool1'),
        BoolOption("bool2", 'do bool2', default=False, cmdline='--bool2', negation=False),
        BoolOption("bool3", 'do bool3', default=True, cmdline='--bool3'),
        ChoiceOption("choice", "choose!", ['a', 'b', 'c'], 'a', '--choice'),
        ChoiceOption("choice2", "choose2!", ['x', 'y', 'z'], None, '--choice2'),
        StrOption("str", 'specify xyz', default='hello', cmdline='--str'),
    ])
    conf = Config(descr)
    parser = to_optparse(conf)
    out = cStringIO.StringIO()
    parser.print_help(out)
    help = out.getvalue()
    #print help
    assert "do bool1\n" in help
    assert "unset option set by --bool1 [default]" in help
    assert "do bool2\n" in help
    assert "do bool3 [default]" in help
    assert "choose! [CHOICE=a|b|c, default: a]" in help
    assert "choose2! [CHOICE2=x|y|z]" in help
    assert "specify xyz [default: hello]" in help

def test_make_dict():
    descr = OptionDescription("opt", "", [
        OptionDescription("s1", "", [
            BoolOption("a", "", default=False)]),
        IntOption("int", "", default=42)])
    config = Config(descr)
    d = make_dict(config)
    assert d == {"s1.a": False, "int": 42}
    config.int = 43
    config.s1.a = True
    d = make_dict(config)
    assert d == {"s1.a": True, "int": 43}

