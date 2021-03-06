
# To avoid repeating code, most of the functions and classes defined in this
# module handle both 32 and 64 bit machine code, but this module should only be
# used for writing 32 bit code. Use x86_64_ops for correct 64 bit code.


import binascii
from functools import partial

from .multimethod import multimethod


SIZE_B = 0
# SIZE_W not used
SIZE_D = 1
SIZE_Q = 2

def int_to_32(x):
    return x.to_bytes(4,byteorder='little',signed=True)

def int_to_16(x):
    return x.to_bytes(2,byteorder='little',signed=True)

def int_to_8(x):
    return x.to_bytes(1,byteorder='little',signed=True)

def immediate_data(w,data):
    return data.to_bytes(4 if w else 1,byteorder='little',signed=data<0)



class Register:
    def __init__(self,size,code):
        self.size = size
        self.code = code

    @property
    def ext(self):
        return bool(self.code & 0b1000)

    @property
    def reg(self):
        return self.code & 0b111

    @property
    def w(self):
        return bool(self.size)
    
    def __eq__(self,b):
        if isinstance(b,Register):
            return self.size == b.size and self.code == b.code
        
        return NotImplemented
    
    def __ne__(self,b):
        if isinstance(b,Register):
            return self.size != b.size or self.code != b.code
        
        return NotImplemented

    def __str__(self):
        assert (not self.ext) and self.size < SIZE_Q
        return '%' + [
            ['al','cl','dl','bl','ah','ch','dh','bh'],
            ['eax','ecx','edx','ebx','esp','ebp','esi','edi']
        ][self.size][self.reg]

Register.__mmtype__ = Register


class Address:
    def __init__(self,offset=0,base=None,index=None,scale=1):
        assert scale in (1,2,4,8)
        assert (base is None or base.w) and (index is None or index.w)
        assert base is None or index is None or base.size == index.size
        
        # %esp cannot be used as the index
        assert index is None or index.reg != 0b100
        
        self.offset = offset
        self.base = base
        self.index = index
        self.scale = scale

    @property
    def size(self):
        if self.base: return self.base.size
        if self.index: return self.index.size
        return None
    
    def _sib(self):
        return bytes([
            ({1:0,2:1,4:2,8:3}[self.scale] << 6) | 
            ((self.index.reg if self.index else 0b100) << 3) | 
            (self.base.reg if self.base else 0b101)])
    
    def _mod_rm_sib_disp(self):
        if self.index or (self.base and self.base.reg == 0b100):
            # The opcode format is a little different when the base is 0b101 and 
            # mod is 0b00 so to use %ebp, we'll have to go with the next mod value.
            if self.offset == 0 and not (self.base and self.base.reg == 0b101):
                return 0b00, 0b100, self._sib()
                
            if fits_in_sbyte(self.offset):
                return 0b01, 0b100, self._sib() + int_to_8(self.offset)
                
            return 0b10, 0b100, self._sib() + int_to_32(self.offset)


        if self.base is None:
            return 0b00, 0b101, int_to_32(self.offset)
        
        # The opcode format is a little different when the base is 0b101 and 
        # mod is 0b00 so to use %ebp, we'll have to go with the next mod value.
        if self.offset == 0 and not (self.base and self.base.reg == 0b101):
            return 0b00, self.base.reg, b''
        
        if fits_in_sbyte(self.offset):
            return 0b01, self.base.reg, int_to_8(self.offset)
        
        return 0b10, self.base.reg, int_to_32(self.offset)
    
    def mod_rm_sib_disp(self,mid):
        """Get the mod field, the r/m field and the SIB and displacement bytes"""
    
        mod,rm,extra = self._mod_rm_sib_disp()
        return bytes([(mod << 6) | (mid << 3) | rm]) + extra
    
    def __str__(self):
        if self.scale != 1:
            return '{}({},{},{})'.format(hex(self.offset) if self.offset else '',self.base or '',self.index,self.scale)
        if self.index is not None:
            return '{}({},{})'.format(hex(self.offset) if self.offset else '',self.base or '',self.index)
        if self.base is not None:
            return '{}({})'.format(hex(self.offset) if self.offset else '',self.base)
        return hex(self.offset)

    def offset_only(self):
        return self.base is None and self.index is None and self.scale == 1

Address.__mmtype__ = Address


class Displacement:
    """A displacement relative to the next instruction.
    
    This class exists to make it clear that a given op code treats a value as a
    displacement and not an absolute address.
    
    """
    def __init__(self,value,force_full_size=False):
        self.val = value
        self.force_full_size = force_full_size


def fits_in_sbyte(x):
    if isinstance(x,Displacement):
        if x.force_full_size: return False
        x = x.val
    return -128 <= x <= 127


def rex(reg,rm,need_w=True):
    """Return the REX prefix byte if needed.

    This is only needed for 64-bit code. If given only 32-bit compatible
    registers and addresses, this function will always return an empty byte
    string.

    """
    assert reg is None or isinstance(reg,Register)
    assert rm is None or isinstance(rm,(Register,Address))

    rxb = 0
    w = None

    if reg:
        w = reg.size == SIZE_Q
        rxb |= reg.ext << 2

    if rm:
        if isinstance(rm,Address):
            assert not (w is None and rm.size is None)
            if rm.size:
                assert w is None or w == (rm.size == SIZE_Q)
                w = rm.size == SIZE_Q

            if rm.index and rm.index.ext: rxb |= 0b10
            if rm.base and rm.base.ext: rxb |= 1
        else:
            assert rm.size is not None
            assert w is None or w == (rm.size == SIZE_Q)
            w = rm.size == SIZE_Q
            rxb |= rm.ext

    assert w is not None

    return bytes([0b01000000 | (w << 3) | rxb]) if ((w and need_w) or rxb) else b''



class Test:
    def __init__(self,val):
        self.val = val
    
    def __invert__(self):
        return Test(self.val ^ 1)
    
    def __int__(self):
        return self.val
    
    @property
    def mnemonic(self):
        return ['o','no','b','nb','e','ne','be','a','s','ns','p','np','l','ge','le','g'][self.val]




def asm_str(indirect,nextaddr,x):
    if isinstance(x,int):
        return '${:#x}'.format(x)
    if isinstance(x,Displacement):
        return '{:x}'.format(x.val + nextaddr)
    if indirect and isinstance(x,(Address,Register)):
        return '*'+str(x)
    return str(x)


class AsmSequence:
    def __init__(self,ops=None):
        self.ops = ops or []
    
    def __len__(self):
        return sum(len(op[0]) for op in self.ops)
    
    def __add__(self,b):
        if isinstance(b,AsmSequence):
            return AsmSequence(self.ops+b.ops)
            
        return NotImplemented
    
    def __iadd__(self,b):
        if isinstance(b,AsmSequence):
            self.ops += b.ops
            return self
        
        return NotImplemented

    def __mul__(self,b):
        if isinstance(b,int):
            return AsmSequence(self.ops*b)

        return NotImplemented

    def __imul__(self,b):
        if isinstance(b,int):
            self.ops *= b
            return self

        return NotImplemented
    
    def dump(self,base=0):
        lines = []
        addr = base
        for bin,name,args in self.ops:
            if name == '$COMMENT$':
                lines.append('; {}\n'.format(args))
            else:
                indirect = name == 'call' or name == 'jmp'
                nextaddr = addr + len(bin)
            
                lines.append('{:8x}: {:20}{:8} {}\n'.format(
                    addr,
                    binascii.hexlify(bin).decode(),
                    name,
                    ', '.join(asm_str(indirect,nextaddr,arg) for arg in args) ))
                
                addr = nextaddr
        
        return ''.join(lines)


class Assembly:
    def op(self,name,*args):
        return AsmSequence([(self.binary(name)(*args),name,args)])

    def binary(self,name):
        return globals()[name]
    
    def __getattr__(self,name):
        return partial(self.op,name)
    
    def jcc(self,test,x):
        return self.op('j'+test.mnemonic,x)

    def comment(self,comm):
        return AsmSequence([(b'','$COMMENT$',comm)])



al = Register(SIZE_B,0b000)
cl = Register(SIZE_B,0b001)
dl = Register(SIZE_B,0b010)
bl = Register(SIZE_B,0b011)
ah = Register(SIZE_B,0b100)
ch = Register(SIZE_B,0b101)
dh = Register(SIZE_B,0b110)
bh = Register(SIZE_B,0b111)
eax = Register(SIZE_D,0b000)
ecx = Register(SIZE_D,0b001)
edx = Register(SIZE_D,0b010)
ebx = Register(SIZE_D,0b011)
esp = Register(SIZE_D,0b100)
ebp = Register(SIZE_D,0b101)
esi = Register(SIZE_D,0b110)
edi = Register(SIZE_D,0b111)



test_O = Test(0b0000)
test_NO = Test(0b0001)
test_B = Test(0b0010)
test_NB = Test(0b0011)
test_E = Test(0b0100)

test_Z = test_E

test_NE = Test(0b0101)

test_NZ = test_NE

test_BE = Test(0b0110)
test_A = Test(0b0111)
test_S = Test(0b1000)
test_NS = Test(0b1001)
test_P = Test(0b1010)
test_NP = Test(0b1011)
test_L = Test(0b1100)
test_GE = Test(0b1101)
test_LE = Test(0b1110)
test_G = Test(0b1111)






def _op_reg_reg(byte1,a,b):
    assert a.size == b.size
    return rex(a,b) + bytes([
        byte1 | a.w,
        0b11000000 | (a.reg << 3) | b.reg])

def _op_addr_reg(byte1,a,b,reverse):
    return rex(b,a) + bytes([byte1 | (reverse << 1) | b.w]) + a.mod_rm_sib_disp(b.reg)

def _op_imm_reg(byte1,mid,byte_alt,a,b):
    if b.code == 0:
        r = bytes([byte_alt | b.w]) + immediate_data(b.w,a)
        if b.size == SIZE_Q: r = bytes([0b01001000]) + r
        return r

    fits = fits_in_sbyte(a)
    return rex(None,b) + bytes([
        byte1 | (fits << 1) | b.w,
        0b11000000 | (mid << 3) | b.reg]) + immediate_data(not fits,a)

def _op_imm_addr(byte1,mid,a,b,w):
    fits = fits_in_sbyte(a)
    return rex(None,b) + bytes([byte1 | (fits << 1) | w]) + b.mod_rm_sib_disp(mid) + immediate_data(not fits,a)



# Some functions are decorated with @multimethod even though they only have one
# version. This is to have consistent type-checking.


@multimethod
def add(a : Register,b : Register):
    return _op_reg_reg(0,a,b)

@multimethod
def add(a : Address,b : Register):
    return _op_addr_reg(0,a,b,True)

@multimethod
def add(a : Register,b : Address):
    return _op_addr_reg(0,b,a,False)

@multimethod
def add(a : int,b : Register):
    return _op_imm_reg(0b10000000,0,0b00000100,a,b)

def add_imm_addr(a,b,w):
    return _op_imm_addr(0b10000000,0b010 if b.size == SIZE_Q else 0,a,b,w)

@multimethod
def addb(a : int,b : Address):
    return add_imm_addr(a,b,False)

@multimethod
def addl(a : int,b : Address):
    return add_imm_addr(a,b,True)



@multimethod
def call(proc : Displacement):
    return bytes([0b11101000]) + int_to_32(proc.val)

CALL_DISP_LEN = 5

@multimethod
def call(proc : Register):
    assert proc.w
    return rex(None,proc,False) + bytes([
        0b11111111,
        0b11010000 | proc.reg])

@multimethod
def call(proc : Address):
    return rex(None,proc,False) + bytes([0b11111111]) + proc.mod_rm_sib_disp(0b010)


@multimethod
def cmp(a : Register,b : Register):
    return _op_reg_reg(0b00111000,a,b)

@multimethod
def cmp(a : Address,b : Register):
    return _op_addr_reg(0b00111000,a,b,True)

@multimethod
def cmp(a : Register,b : Address):
    return _op_addr_reg(0b00111000,b,a,False)

@multimethod
def cmp(a : int,b : Register):
    return _op_imm_reg(0b10000000,0b111,0b00111100,a,b)

@multimethod
def cmpb(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b111,a,b,False)

@multimethod
def cmpl(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b111,a,b,True)



@multimethod
def dec(x : Register):
    # REX omitted; dec is redefined in x86_64_ops
    if x.w:
        return bytes([0b01001000 | x.reg])
    else:
        return bytes([
            0b11111110,
            0b11001000 | x.reg])

def dec_addr(x,w):
    return rex(None,x) + bytes([0b11111110 | w]) + x.mod_rm_sib_disp(0b001)

@multimethod
def decb(x : Address):
    return dec_addr(x,False)

@multimethod
def decl(x : Address):
    return dec_addr(x,True)



@multimethod
def inc(x : Register):
    # REX omitted; inc is redefined in x86_64_ops
    if x.w:
        return bytes([0b01000000 | x.reg])
    else:
        return bytes([
            0b11111110,
            0b11000000 | x.reg])

def inc_addr(x,w):
    return rex(None,x) + bytes([0b11111110 | w]) + x.mod_rm_sib_disp(0)

@multimethod
def incb(x : Address):
    return inc_addr(x,False)

@multimethod
def incl(x : Address):
    return inc_addr(x,True)



def jcc(test : Test,x : Displacement):
    if fits_in_sbyte(x):
        return bytes([0b01110000 | test.val]) + int_to_8(x.val)
    
    return bytes([
        0b00001111,
        0b10000000 | test.val]) + int_to_32(x.val)

JCC_MIN_LEN = 2
JCC_MAX_LEN = 6

def jo(x): return jcc(test_O,x)
def jno(x): return jcc(test_NO,x)
def jb(x): return jcc(test_B,x)
def jnb(x): return jcc(test_NB,x)
def je(x): return jcc(test_E,x)
jz = je
def jne(x): return jcc(test_NE,x)
jnz = jne
def jbe(x): return jcc(test_BE,x)
def ja(x): return jcc(test_A,x)
def js(x): return jcc(test_S,x)
def jns(x): return jcc(test_NS,x)
def jp(x): return jcc(test_P,x)
def jnp(x): return jcc(test_NP,x)
def jl(x): return jcc(test_L,x)
def jge(x): return jcc(test_GE,x)
def jle(x): return jcc(test_LE,x)
def jg(x): return jcc(test_G,x)



@multimethod
def jmp(x : Displacement):
    fits = fits_in_sbyte(x)
    return bytes([0b11101001 | (fits << 1)]) + ([int_to_32,int_to_8][fits])(x.val)

JMP_DISP_MIN_LEN = 2
JMP_DISP_MAX_LEN = 5

@multimethod
def jmp(x : Register):
    assert x.w
    return rex(None,x) + bytes([
        0b11111111,
        0b11100000 | x.reg])

@multimethod
def jmp(x : Address):
    return rex(None,x) + bytes([0b11111111]) + x.mod_rm_sib_disp(0b100)



@multimethod
def lea(a : Address,b : Register):
    assert b.w
    return rex(b,a) + bytes([0b10001101]) + a.mod_rm_sib_disp(b.reg)



def leave():
    return bytes([0b11001001])



@multimethod
def loop(x : Displacement):
    return bytes([0b11100010]) + int_to_8(x.val)

@multimethod
def loopz(x : Displacement):
    return bytes([0b11100001]) + int_to_8(x.val)

loope = loopz
    
@multimethod
def loopnz(x : Displacement):
    return bytes([0b11100000]) + int_to_8(x.val)

loopne = loopnz

LOOP_LEN = 2



@multimethod
def mov(a : Register,b : Register):
    return _op_reg_reg(0b10001000,a,b)

def mov_addr_reg(a,b,forward):
    if b.reg == 0b000 and a.offset_only():
        r = bytes([0b10100000 | (forward << 1) | b.w]) + int_to_32(a.offset)
        if b.size == SIZE_Q: r = bytes([0b01001000]) + r
        return r

    return _op_addr_reg(0b10001000,a,b,forward)

@multimethod
def mov(a : Address,b : Register):
    return mov_addr_reg(a,b,True)

@multimethod
def mov(a : Register,b : Address):
    return mov_addr_reg(b,a,False)

@multimethod
def mov(a : int,b : Register):
    return rex(None,b) + bytes([0b10110000 | (b.w << 3) | b.reg]) + immediate_data(b.w,a)

def mov_imm_addr(a,b,w):
    return rex(None,b) + bytes([0b11000110 | w]) + b.mod_rm_sib_disp(0) + immediate_data(w,a)

@multimethod
def movb(a : int,b : Address):
    return mov_imm_addr(a,b,False)

@multimethod
def movl(a : int,b : Address):
    return mov_imm_addr(a,b,True)



def nop():
    return bytes([0b10010000])



@multimethod
def pop(x : Register):
    return rex(None,x,False) + bytes([0b01011000 | x.reg])

@multimethod
def pop(x : Address):
    return rex(None,x,False) + bytes([0b10001111]) + x.mod_rm_sib_disp(0)



@multimethod
def push(x : Register):
    return rex(None,x,False) + bytes([0b01010000 | x.reg])

@multimethod
def push(x : Address):
    return rex(None,x,False) + bytes([0b11111111]) + x.mod_rm_sib_disp(0b110)

@multimethod
def push(x : int):
    byte = fits_in_sbyte(x)
    return bytes([0b01101000 | (byte << 1)]) + immediate_data(not byte,x)



@multimethod
def ret():
    return bytes([0b11000011])

@multimethod
def ret(pop : int):
    return bytes([0b11000010]) + int_to_16(pop)



def shx_imm_reg(amount,x,shiftright):
    r = rex(None,x)

    if amount == 1:
        return r + bytes([
            0b11010000 | x.w,
            0b11100000 | (shiftright << 3) | x.reg])
    
    return r + bytes([
        0b11000000 | x.w,
        0b11100000 | (shiftright << 3) | x.reg]) + immediate_data(False,amount)

def shx_reg_reg(amount,x,shiftright):
    assert amount == cl
    
    return rex(None,x) + bytes([
        0b11010010 | x.w,
        0b11100000 | (shiftright << 3) | x.reg])

def shx_imm_addr(amount,x,w,shiftright):
    r = rex(None,x)
    rmsd = x.mod_rm_sib_disp(0b100 | shiftright)
    if amount == 1:
        return r + bytes([0b11010000 | w]) + rmsd
    
    return r + bytes([0b11000000 | w]) + rmsd + immediate_data(False,amount)

def shx_reg_addr(amount,x,w,shiftright):
    assert amount == cl
    
    return rex(None,x) + bytes([0b11010010 | w]) + x.mod_rm_sib_disp(0b100 | shiftright)


@multimethod
def shl(amount : int,x : Register):
    return shx_imm_reg(amount,x,False)

@multimethod
def shl(amount : Register,x : Register):
    return shx_reg_reg(amount,x,False)

@multimethod
def shlb(amount : int,x : Address):
    return shx_imm_addr(amount,x,False,False)

@multimethod
def shll(amount : int,x : Address):
    return shx_imm_addr(amount,x,True,False)

@multimethod
def shlb(amount : Register,x : Address):
    return shx_reg_addr(amount,x,False,False)

@multimethod
def shll(amount : Register,x : Address):
    return shx_reg_addr(amount,x,True,False)



@multimethod
def shr(amount : int,x : Register):
    return shx_imm_reg(amount,x,True)

@multimethod
def shr(amount : Register,x : Register):
    return shx_reg_reg(amount,x,True)

@multimethod
def shrb(amount : int,x : Address):
    return shx_imm_addr(amount,x,False,True)

@multimethod
def shrl(amount : int,x : Address):
    return shx_imm_addr(amount,x,True,True)

@multimethod
def shrb(amount : Register,x : Address):
    return shx_reg_addr(amount,x,False,True)

@multimethod
def shrl(amount : Register,x : Address):
    return shx_reg_addr(amount,x,True,True)



@multimethod
def sub(a : Register,b : Register):
    return _op_reg_reg(0b00101000,a,b)

@multimethod
def sub(a : Address,b : Register):
    return _op_addr_reg(0b00101000,a,b,True)

@multimethod
def sub(a : Register,b : Address):
    return _op_addr_reg(0b00101000,b,a,False)

@multimethod
def sub(a : int,b : Register):
    return _op_imm_reg(0b10000000,0b101,0b00101100,a,b)

@multimethod
def subb(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b101,a,b,False)

@multimethod
def subl(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b101,a,b,True)



@multimethod
def test(a : Register,b : Register):
    return _op_reg_reg(0b10000100,a,b)

@multimethod
def test(a : Address,b : Register):
    return _op_addr_reg(0b10000100,a,b,True)

@multimethod
def test(a : int,b : Register):
    return _op_imm_reg(0b11110110,0,0b10101000,a,b)

def test_imm_addr(a,b,w):
    return rex(None,b) + bytes([0b11110110 | w]) + b.mod_rm_sib_disp(0) + immedate_data(w,a)

@multimethod
def testb(a : int,b : Address):
    return test_imm_addr(a,b,False)

@multimethod
def testl(a : int,b : Address):
    return test_imm_addr(a,b,True)



@multimethod
def xor(a : Register,b : Register):
    return _op_reg_reg(0b00110000,a,b)

@multimethod
def xor(a : Register,b : Address):
    return _op_addr_reg(0b00110000,b,a,True)

@multimethod
def xor(a : Address,b : Register):
    return _op_addr_reg(0b00110000,a,b,False)

@multimethod
def xor(a : int,b : Register):
    return _op_imm_reg(0b10000000,0b110,0b00110100,a,b)

@multimethod
def xorb(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b110,a,b,False)

@multimethod
def xorl(a : int,b : Address):
    return _op_imm_addr(0b10000000,0b110,a,b,True)
