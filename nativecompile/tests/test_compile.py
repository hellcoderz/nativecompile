
import sys
import subprocess
import io
import unittest


def exec_isolated(code):
    """Compile and run code in seperate process.

    A mistake in the compiler will cause a segfault most of the time. Running it
    in a seperate process will prevent it from terminating this test program.

    """
    s = subprocess
    with s.Popen(sys.executable,stdin=s.PIPE,stdout=s.PIPE,stderr=s.PIPE) as p:
        out,err = p.communicate(
            'import nativecompile;nativecompile.compile(compile({},"<string>","exec"))()'
            .format(repr(code)).encode('ascii'))
        rcode = p.returncode

    if rcode:
        raise Exception(
            'An error occured in the child process. Its output was:\n\n'+err.decode())

    return out.decode()


class TestCompile(unittest.TestCase):
    def compare_exec(self,code):
        """Run the code normally, then compile and run it and check to see that
        the results are the same."""
        
        res = io.StringIO()
        old = sys.stdout
        sys.stdout = res
        try:
            exec(code)
        finally:
            sys.stdout = old

        self.assertEqual(exec_isolated(code),res.getvalue())

    def test_compile(self):
        self.compare_exec('print("Hello World!")')

    def test_math(self):
        self.compare_exec('print((4 * 5 - 3) << 2)')

    def test_vars(self):
        self.compare_exec('''
x=23
y='goo'
print((x,y))
''')

    def test_for_loop(self):
        self.compare_exec('''
x = 0
for i in range(10):
    x += i
print(x)
''')

    def test_funcs(self):
        self.compare_exec('''
def a(x):
    return x * x

def b(x):
    return x + x

def c(x,y,z=None):
    return x + y + 9

print(a(b(9)))
print(c(1,2))
print(c(1,2,z=3))
''')

    def test_list_literal(self):
        self.compare_exec('print([3,2,1])')

    def test_if(self):
        self.compare_exec('''
x = 23
if x & 1:
    print('odd')
else:
    print('even')
''')

    def test_attr(self):
        self.compare_exec('''
x = " hello "
print(x.strip())
print(x.__class__.__name__)
''')

    def test_compare(self):
        self.compare_exec('''
a = 12
b = 14
c = [14,13,11]
d = a
print(a>b)
print(b>a)
print(a is b)
print(a is not b)
print(a is d)
print(a is not d)
print(b in c)
print(a in c)
print(b not in c)
print(a not in c)
''')

    def test_unpack(self):
        self.compare_exec('''
x = ['a','b','c']
a,b,c = x
print(a,b,c)
''')

    def test_class(self):
        self.compare_exec('''
class Thingy:
    a = 9
    def __init__(self,hi='go away'):
        self.hi = hi
    
    def __str__(self):
        return str(self.hi)

thing = Thingy()
print(thing)
''')


if __name__ == '__main__':
    unittest.main()

