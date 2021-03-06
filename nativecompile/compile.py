
__all__ = ['compile','compile_asm']


import os
import tempfile
import atexit
import sys

from . import pyinternals
from .compile_raw import compile_raw

if pyinternals.ARCHITECTURE == "X86":
    from .x86_abi import CdeclAbi as Abi
elif pyinternals.ARCHITECTURE == "X86_64":
    from . import x86_64_abi
    if sys.platform in ('win32','cygwin'):
        Abi = x86_64_abi.MicrosoftX64Abi
    else:
        Abi = x86_64_abi.SystemVAbi
else:
    raise Exception("native compilation is not supported on this CPU")



def compile(code):
    f = tempfile.NamedTemporaryFile(mode='wb',delete=False)
    
    def delete_f():
        f.close()
        os.remove(f.name)
    
    atexit.register(delete_f)
    
    parts,entry_points = compile_raw(code,Abi)
    for p in parts:
        f.write(p)

    f.close()
    
    return pyinternals.CompiledCode(f.name,entry_points)


def compile_asm(code):
    """Compile code and return the assembly representation"""
    return compile_raw(code,Abi,binary=False)[0].dump()

