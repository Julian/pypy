#!/usr/bin/env python
"""This module loads the python Grammar (2.3 or 2.4) and builds
the parser for this grammar in the global PYTHON_PARSER

helper functions are provided that use the grammar to parse
using file_input, single_input and eval_input targets
"""
from pypy.interpreter.error import OperationError, debug_print
from pypy.interpreter import gateway
from pypy.interpreter.pyparser.error import ParseError
from pypy.tool.option import Options
from pythonlexer import Source, match_encoding_declaration
import pysymbol
import ebnfparse
import sys
import os
import grammar

from codeop import PyCF_DONT_IMPLY_DEDENT

class PythonParser(object):
    """Wrapper class for python grammar"""
    def __init__(self, grammar_builder):
        self.items = grammar_builder.items
        self.rules = grammar_builder.rules
        # Build first sets for each rule (including anonymous ones)
        grammar.build_first_sets(self.items)

    def parse_source(self, textsrc, goal, builder, flags=0):
        """Parse a python source according to goal"""
        # Detect source encoding.
        if textsrc[:3] == '\xEF\xBB\xBF':
            textsrc = textsrc[3:]
            enc = 'utf-8'
        else:
            enc = _normalize_encoding(_check_for_encoding(textsrc))
            if enc is not None and enc not in ('utf-8', 'iso-8859-1'):
                textsrc = _recode_to_utf8(builder.space, textsrc, enc)

        lines = [line + '\n' for line in textsrc.split('\n')]
        builder.source_encoding = enc
        if textsrc[-1] =='\n':
            lines.pop()
            flags &= ~PyCF_DONT_IMPLY_DEDENT
        return self.parse_lines(lines, goal, builder, flags)

    def parse_lines(self, lines, goal, builder, flags=0):
        goalnumber = pysymbol.sym_values[goal]
        target = self.rules[goalnumber]
        src = Source(lines, flags)
    
        result = target.match(src, builder)
        if not result:
            line, lineno = src.debug()
            # XXX needs better error messages
            raise ParseError("invalid syntax", lineno, -1, line)
            # return None
        return builder
    
app_recode_to_utf8 = gateway.applevel(r'''
    def app_recode_to_utf8(text, encoding):
        return unicode(text, encoding).encode("utf-8")
''').interphook('app_recode_to_utf8')

def _recode_to_utf8(space, text, encoding):
    return space.str_w(app_recode_to_utf8(space, space.wrap(text),
                                          space.wrap(encoding)))
def _normalize_encoding(encoding):
    """returns normalized name for <encoding>

    see dist/src/Parser/tokenizer.c 'get_normal_name()'
    for implementation details / reference

    NOTE: for now, parser.suite() raises a MemoryError when
          a bad encoding is used. (SF bug #979739)
    """
    if encoding is None:
        return None
    # lower() + '_' / '-' conversion
    encoding = encoding.replace('_', '-').lower()
    if encoding.startswith('utf-8'):
        return 'utf-8'
    for variant in ['latin-1', 'iso-latin-1', 'iso-8859-1']:
        if encoding.startswith(variant):
            return 'iso-8859-1'
    return encoding

def _check_for_encoding(s):
    eol = s.find('\n')
    if eol == -1:
        return _check_line_for_encoding(s)
    enc = _check_line_for_encoding(s[:eol])
    eol2 = s.find('\n', eol + 1)
    if eol2 == -1:
        return _check_line_for_encoding(s[eol + 1:])
    return _check_line_for_encoding(s[eol + 1:eol2])
    
def _check_line_for_encoding(line):
    """returns the declared encoding or None"""
    i = 0
    for i in range(len(line)):
        if line[i] == '#':
            break
        if line[i] not in ' \t\014':
            return None
    return match_encoding_declaration(line[i:])

PYTHON_VERSION = ".".join([str(i) for i in sys.version_info[:2]])
def get_grammar_file( version ):
    """returns the python grammar corresponding to our CPython version"""
    if version == "native":
        _ver = PYTHON_VERSION
    elif version in ("2.3","2.4"):
        _ver = version
    return os.path.join( os.path.dirname(__file__), "data", "Grammar" + _ver ), _ver

# unfortunately the command line options are not parsed yet
PYTHON_GRAMMAR, PYPY_VERSION = get_grammar_file( Options.version )

def python_grammar(fname):
    """returns a PythonParser build from the specified grammar file"""
    level = grammar.DEBUG
    grammar.DEBUG = 0
    gram = ebnfparse.parse_grammar( file(fname) )
    grammar.DEBUG = level
    parser = PythonParser( gram )
    return parser

debug_print( "Loading grammar %s" % PYTHON_GRAMMAR )
PYTHON_PARSER = python_grammar( PYTHON_GRAMMAR )

def reload_grammar(version):
    """helper function to test with pypy different grammars"""
    global PYTHON_GRAMMAR, PYTHON_PARSER, PYPY_VERSION
    PYTHON_GRAMMAR, PYPY_VERSION = get_grammar_file( version )
    debug_print( "Reloading grammar %s" % PYTHON_GRAMMAR )
    PYTHON_PARSER = python_grammar( PYTHON_GRAMMAR )

def parse_file_input(pyf, gram, builder ):
    """Parse a python file"""
    return gram.parse_source( pyf.read(), "file_input", builder )
    
def parse_single_input(textsrc, gram, builder ):
    """Parse a python single statement"""
    return gram.parse_source( textsrc, "single_input", builder )

def parse_eval_input(textsrc, gram, builder):
    """Parse a python expression"""
    return gram.parse_source( textsrc, "eval_input", builder )
