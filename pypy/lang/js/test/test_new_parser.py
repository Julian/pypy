
import py
from pypy.rlib.parsing.ebnfparse import parse_ebnf, make_parse_function
from pypy.rlib.parsing.parsing import ParseError, Rule
from pypy.rlib.parsing.tree import RPythonVisitor
from pypy import conftest
import sys

GFILE = py.magic.autopath().dirpath().dirpath().join("jsgrammar.txt")

try:
    t = GFILE.read()
    regexs, rules, ToAST = parse_ebnf(t)
except ParseError,e:
    print e.nice_error_message(filename=str(GFILE),source=t)
    raise

parse = make_parse_function(regexs, rules, eof=True)

def setstartrule(rules, start):
    "takes the rule start and put it on the beginning of the rules"
    oldpos = 0
    newrules = [Rule("hacked_first_symbol", [[start, "EOF"]])] + rules
    return newrules

def get_defaultparse():
    global parse
    if parse is None:
        parse = make_parse_function(regexs, rules, eof=True)
    return parse

def parse_func(start=None):
    if start is not None:
        parse = make_parse_function(regexs, setstartrule(rules, start),
                                    eof=True)
    else:
        parse = get_defaultparse()

    def methodparse(self, text):
        tree = parse(text)
        if start is not None:
            assert tree.symbol == "hacked_first_symbol"
            tree = tree.children[0]
        tree = tree.visit(ToAST())[0]
        if conftest.option.view:
            tree.view()
        return tree
    return methodparse

class CountingVisitor(RPythonVisitor):
    def __init__(self):
        self.counts = {}
    
    def general_nonterminal_visit(self, node):
        print node
        self.counts[node.symbol] = self.counts.get(node.symbol, 0) + 1
        for child in node.children:
            self.dispatch(child)

    def general_symbol_visit(self, node):
        self.counts[node.symbol] = self.counts.get(node.symbol, 0) + 1

class BaseGrammarTest(object):
    def setup_class(cls):
        cls.parse = parse_func()
            
class TestLiterals(BaseGrammarTest):
    def setup_class(cls):
        cls.parse = parse_func('literal')

    def test_numbers(self):
        for i in range(10):
            dc = CountingVisitor()
            self.parse(str(i)).visit(dc)
            assert dc.counts["DECIMALLITERAL"] == 1

class IntEvaluationVisitor(RPythonVisitor):
    def general_symbol_visit(self, node):
        return node.additional_info

    def visit_DECIMALLITERAL(self, node):
        return int(node.additional_info)

    def general_nonterminal_visit(self, node):
        if len(node.children) == 1:
            return self.dispatch(node.children[0])
        if len(node.children) >= 3:
            code = [str(self.dispatch(child)) for child in node.children]
            while len(code) >= 3:
                r = self.evalop(int(code.pop(0)), code.pop(0), int(code.pop(0)))
                code.insert(0, r)
            return code[0]
    
    def visit_unaryexpression(self, node):
        if len(node.children) == 1:
            return self.dispatch(node.children[0])
        else:
            arg1 = self.dispatch(node.children[1])
            op = self.dispatch(node.children[0])
            return self.evalop(arg1,op)
        
    opmap = {'+': lambda x,y: x+y,
            '-': lambda x,y: x-y,
            '*': lambda x,y: x*y,
            '++': lambda x,y: x+1,
            '--': lambda x,y: x-1,
            '!': lambda x,y: not x,
            '~': lambda x,y: ~ x,
            '/': lambda x,y: x / y,
            '>>': lambda x,y: x>>y,
            '<<': lambda x,y: x<<y,
            '&': lambda x,y: x&y,
            '|': lambda x,y: x|y,
            '^': lambda x,y: x^y,
            '%': lambda x,y: x%y,
    }
    
    def evalop(self, arg1, op, arg2=None):
        return self.opmap[op](arg1, arg2)
        


class TestExpressions(BaseGrammarTest):
    def setup_class(cls):
        cls.parse = parse_func('expression')
        cls.evaluator = IntEvaluationVisitor()

    def parse_and_evaluate(self, s):
        tree = self.parse(s)
        result1 = self.evaluator.dispatch(tree)
        result2 = eval(s)
        assert result1 == result2
        return tree
    
    def parse_and_compare(self, s, n):
        tree = self.parse(s)
        result1 = self.evaluator.dispatch(tree)
        assert result1 == n
        return tree
        

    def parse_and_eval_all(self, l):
        for i in l:
            self.parse_and_evaluate(i)

    def test_simple(self):
        self.parse_and_eval_all(["1",
                        "1 + 2",
                        "1 - 2",
                        "1 * 2",
                        "1 / 2",
                        "1 >> 2",
                        "4 % 2",
                        "4 | 1",
                        "4 ^ 2",
        ])
        self.parse_and_compare("++5", 6)
        self.parse_and_compare("~3", -4)
        
    def test_chained(self):
        self.parse_and_eval_all(["1 + 2 * 3",
                        "1 * 2 + 3",
                        "1 - 3 - 3",
                        "4 / 2 / 2",
                        "2 << 4 << 4",
                        "30 | 3 & 5",
        ])
        
