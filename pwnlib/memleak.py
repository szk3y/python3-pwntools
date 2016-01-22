from .log import getLogger
from .util.packing import pack
from .util.packing import unpack

log = getLogger(__name__)


class MemLeak:
    """MemLeak is a caching and heuristic tool for exploiting memory leaks.

    It can be used as a decorator, around functions of the form:

        def some_leaker(addr):
            ...
            return data_as_string_or_None

    It will cache leaked memory (which requires either non-randomized static
    data or a continouous session). If required, dynamic or known data can be
    set with the set-functions, but this is usually not required. If a byte
    cannot be recovered, it will try to leak nearby bytes in the hope that the
    byte is recovered as a side-effect.

    Arguments:
        f (function): The leaker function.
        search_range (int): How many bytes to search backwards in case an address does not work.
        reraise (bool): Whether to reraise call :func:`pwnlib.log.warning` in case the leaker function throws an exception.

    Example:

        .. doctest:: leaker

            >>> import pwnlib
            >>> binsh = pwnlib.util.misc.read('/bin/sh', mode='rb')
            >>> @pwnlib.memleak.MemLeak
            ... def leaker(addr):
            ...     print("leaking 0x%x" % addr)
            ...     return binsh[addr:addr+4]
            >>> leaker.s(0)[:4]
            leaking 0x0
            leaking 0x4
            b'\\x7fELF'
            >>> hex(leaker.d(0))
            '0x464c457f'
            >>> hex(leaker.clearb(1))
            '0x45'
            >>> hex(leaker.d(0))
            leaking 0x1
            '0x464c457f'
    """

    def __init__(self, f, search_range=20, reraise=True):
        self.leak = f
        self.search_range = search_range
        self.reraise = reraise

        # Map of address: byte for all bytes received
        self.cache = {}

    def struct(self, address, struct):
        """struct(address, struct) => structure object
        Leak an entire structure.
        Arguments:
            address(int):  Address of structure in memory
            struct(class): A ctypes structure to be instantiated with leaked data
        Return Value:
            An instance of the provided struct class, with the leaked data decoded
        """
        size = sizeof(struct)
        data = self.n(address, size)
        obj = struct.from_buffer_copy(data)
        return obj

    def field(self, address, obj):
        """field(address, field) => a structure field.

        Leak a field from a structure.

        Arguments:
            address(int): Base address to calculate offsets from
            field(obj):   Instance of a ctypes field

        Return Value:
            The type of the return value will be dictated by
            the type of ``field``.
        """
        size = obj.size
        offset = obj.offset
        data = self.n(address + offset, size)
        return unpack(data, size * 8)

    def _leak(self, addr, n, recurse=True):
        """_leak(addr, n) => bytes

        Leak ``n`` consecutive bytes starting at ``addr``.

        Returns:
            A bytes of length ``n``, or ``None``.
        """
        addresses = [addr + i for i in range(n)]

        for address in addresses:
            # Cache hit
            if address in self.cache:
                continue

            # Cache miss, get the data from the leaker
            data = None
            try:
                data = self.leak(address)
            except Exception as e:
                if self.reraise:
                    raise

            # We could not leak this particular byte, search backwardd
            # to see if another request will satisfy it
            if not data and recurse:
                for i in range(1, self.search_range):
                    data = self._leak(address - i, i, False)
                    if address in self.cache:
                        break
                else:
                    return None

            # Could not receive any data, even overlapped with previous
            # requests.
            if not data:
                return None

            # Fill cache for as many bytes as we received
            for i, byte in enumerate(data):
                self.cache[address + i] = byte

        # Ensure everything is in the cache
        if not all(a in self.cache for a in addresses):
            return None

        # Cache is filled, satisfy the request
        return bytes(self.cache[addr + i] for i in range(n))

    def raw(self, addr, numb):
        """raw(addr, numb) -> bytes

        Leak `numb` bytes at `addr`"""
        return b''.join(self._leak(a, 1) for a in range(addr, addr + numb))

    def _b(self, addr, ndx, size):
        addr += ndx * size
        data = self._leak(addr, size)

        if not data:
            return None

        return unpack(data, 8 * size)

    def b(self, addr, ndx=0):
        """b(addr, ndx=0) -> int

        Leak byte at ``((uint8_t*) addr)[ndx]``

        Examples:

            >>> import string
            >>> data = string.ascii_lowercase.encode('utf8')
            >>> l = MemLeak(lambda a: data[a:a+2], reraise=False)
            >>> l.b(0) == ord('a')
            True
            >>> l.b(25) == ord('z')
            True
            >>> l.b(26) is None
            True
        """
        return self._b(addr, ndx, 1)

    def w(self, addr, ndx=0):
        """w(addr, ndx=0) -> int

        Leak word at ``((uint16_t*) addr)[ndx]``

        Examples:

            >>> import string
            >>> data = string.ascii_lowercase.encode('utf8')
            >>> l = MemLeak(lambda a: data[a:a+4], reraise=False)
            >>> l.w(0) == unpack(b'ab', 16)
            True
            >>> l.w(24) == unpack(b'yz', 16)
            True
            >>> l.w(25) is None
            True
        """
        return self._b(addr, ndx, 2)

    def d(self, addr, ndx=0):
        """d(addr, ndx=0) -> int

        Leak dword at ``((uint32_t*) addr)[ndx]``

        Examples:

            >>> import string
            >>> data = string.ascii_lowercase.encode('utf8')
            >>> l = MemLeak(lambda a: data[a:a+8], reraise=False)
            >>> l.d(0) == unpack(b'abcd', 32)
            True
            >>> l.d(22) == unpack(b'wxyz', 32)
            True
            >>> l.d(23) is None
            True
        """
        return self._b(addr, ndx, 4)

    def q(self, addr, ndx=0):
        """q(addr, ndx=0) -> int

        Leak qword at ``((uint64_t*) addr)[ndx]``

        Examples:

            >>> import string
            >>> data = string.ascii_lowercase.encode('utf8')
            >>> l = MemLeak(lambda a: data[a:a+16], reraise=False)
            >>> l.q(0) == unpack(b'abcdefgh', 64)
            True
            >>> l.q(18) == unpack(b'stuvwxyz', 64)
            True
            >>> l.q(19) is None
            True
        """
        return self._b(addr, ndx, 8)

    def s(self, addr):
        r"""s(addr) -> bytes

        Leak bytes at `addr` until failure or a nullbyte is found

        Return:
            A bytes, without a NULL terminator.
            The returned bytes will be empty if the first byte is
            a NULL terminator, or if the first byte could not be
            retrieved.

        Examples:

            >>> data = b"Hello\x00World"
            >>> l = MemLeak(lambda a: data[a:a+4], reraise=False)
            >>> l.s(0) == b"Hello"
            True
            >>> l.s(5) == b""
            True
            >>> l.s(6) == b"World"
            True
            >>> l.s(999) == b""
            True
        """

        # This relies on the behavior of _leak to fill the cache
        orig = addr
        while self.b(addr):
            addr += 1
        return self._leak(orig, addr - orig)

    def n(self, addr, numb):
        """n(addr, ndx=0) -> bytes

        Leak `numb` bytes at `addr`.

        Returns:
            A bytes with the leaked bytes, will return `None` if any are missing

        Examples:

            >>> import string
            >>> data = string.ascii_lowercase.encode('utf8')
            >>> l = MemLeak(lambda a: data[a:a+4], reraise=False)
            >>> l.n(0,1) == b'a'
            True
            >>> l.n(0,26) == data
            True
            >>> len(l.n(0,26)) == 26
            True
            >>> l.n(0,27) is None
            True
        """
        return self._leak(addr, numb) or None

    def _clear(self, addr, ndx, size):
        addr += ndx * size
        data = list(map(lambda x: self.cache.pop(x, None), range(addr, addr + size)))

        if not all(data):
            return None

        return unpack(bytes(data), size * 8)

    def clearb(self, addr, ndx=0):
        """clearb(addr, ndx=0) -> int

        Clears byte at ``((uint8_t*)addr)[ndx]`` from the cache and
        returns the removed value or `None` if the address was not completely set.

        Examples:

            >>> l = MemLeak(lambda a: None)
            >>> l.cache = {0: 97}
            >>> l.n(0,1) == b'a'
            True
            >>> l.clearb(0) == unpack(b'a', 8)
            True
            >>> l.cache
            {}
            >>> l.clearb(0) is None
            True
        """
        return self._clear(addr, ndx, 1)

    def clearw(self, addr, ndx=0):
        """clearw(addr, ndx=0) -> int

        Clears word at ``((uint16_t*)addr)[ndx]`` from the cache and
        returns the removed value or `None` if the address was not completely set.

        Examples:

            >>> l = MemLeak(lambda a: None)
            >>> l.cache = {0: 97, 1: 98}
            >>> l.n(0, 2) == b'ab'
            True
            >>> l.clearw(0) == unpack(b'ab', 16)
            True
            >>> l.cache
            {}
        """
        return self._clear(addr, ndx, 2)

    def cleard(self, addr, ndx=0):
        """cleard(addr, ndx=0) -> int

        Clears dword at ``((uint32_t*)addr)[ndx]`` from the cache and
        returns the removed value or `None` if the address was not completely set.

        Examples:

            >>> l = MemLeak(lambda a: None)
            >>> l.cache = {0: 97, 1: 98, 2: 99, 3: 100}
            >>> l.n(0, 4) == b'abcd'
            True
            >>> l.cleard(0) == unpack(b'abcd', 32)
            True
            >>> l.cache
            {}
        """
        return self._clear(addr, ndx, 4)

    def clearq(self, addr, ndx=0):
        """clearq(addr, ndx=0) -> int

        Clears qword at ``((uint64_t*)addr)[ndx]`` from the cache and
        returns the removed value or `None` if the address was not completely set.

        Examples:

            >>> c = MemLeak(lambda addr: b'')
            >>> c.cache = {x: 120 for x in range(0x100, 0x108)}
            >>> c.clearq(0x100) == unpack(b'xxxxxxxx', 64)
            True
            >>> c.cache == {}
            True
        """
        return self._clear(addr, ndx, 8)

    def _set(self, addr, val, ndx, size):
        addr += ndx * size
        for i, b in enumerate(pack(val, size * 8)):
            self.cache[addr + i] = b

    def setb(self, addr, val, ndx=0):
        """Sets byte at ``((uint8_t*)addr)[ndx]`` to `val` in the cache.

        Examples:

            >>> l = MemLeak(lambda x: '')
            >>> l.cache == {}
            True
            >>> l.setb(33, 0x41)
            >>> l.cache == {33: 65}
            True
        """
        return self._set(addr, val, ndx, 1)

    def setw(self, addr, val, ndx=0):
        r"""Sets word at ``((uint16_t*)addr)[ndx]`` to `val` in the cache.

        Examples:

            >>> l = MemLeak(lambda x: b'')
            >>> l.cache == {}
            True
            >>> l.setw(33, 0x41)
            >>> l.cache == {33: 65, 34: 0}
            True
        """
        return self._set(addr, val, ndx, 2)

    def setd(self, addr, val, ndx=0):
        """Sets dword at ``((uint32_t*)addr)[ndx]`` to `val` in the cache.

        Examples:
            See :meth:`setw`.
        """
        return self._set(addr, val, ndx, 4)

    def setq(self, addr, val, ndx=0):
        """Sets qword at ``((uint64_t*)addr)[ndx]`` to `val` in the cache.

        Examples:
            See :meth:`setw`.
        """
        return self._set(addr, val, ndx, 8)

    def sets(self, addr, val, null_terminate=True):
        r"""Set known string at `addr`, which will be optionally be null-terminated

        Note that this method is a bit dumb about how it handles the data.
        It will null-terminate the data, but it will not stop at the first null.

        Examples:

            >>> l = MemLeak(lambda x: '')
            >>> l.cache == {}
            True
            >>> l.sets(0, b'H\x00ello')
            >>> l.cache == {0: 72, 1: 0, 2: 101, 3: 108, 4: 108, 5: 111, 6: 0}
            True
        """
        if null_terminate:
            val += b'\x00'

        for i, b in enumerate(val):
            self.cache[addr + i] = b
