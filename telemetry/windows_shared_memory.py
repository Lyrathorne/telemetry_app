import ctypes
from ctypes import wintypes


FILE_MAP_READ = 0x0004

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.MapViewOfFile.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_size_t,
)
kernel32.MapViewOfFile.restype = wintypes.LPVOID
kernel32.UnmapViewOfFile.argtypes = (wintypes.LPCVOID,)
kernel32.UnmapViewOfFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


class NamedSharedMemory:
    def __init__(self, name: str, size: int) -> None:
        self.name = name
        self.size = size
        self._handle = None
        self._address = None

    def open(self) -> None:
        handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, self.name)

        if not handle:
            raise FileNotFoundError(f"Shared memory map not found: {self.name}")

        address = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self.size)

        if not address:
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), f"Could not map view: {self.name}")

        self._handle = handle
        self._address = address

    def read_bytes(self, offset: int, size: int) -> bytes:
        if self._address is None:
            raise OSError(f"Shared memory map is closed: {self.name}")

        if offset < 0 or offset + size > self.size:
            raise ValueError(f"Read outside shared memory map: {self.name}")

        return ctypes.string_at(self._address + offset, size)

    def read_structure(self, structure_type):
        data = self.read_bytes(0, ctypes.sizeof(structure_type))
        return structure_type.from_buffer_copy(data)

    def close(self) -> None:
        if self._address is not None:
            kernel32.UnmapViewOfFile(self._address)
            self._address = None

        if self._handle is not None:
            kernel32.CloseHandle(self._handle)
            self._handle = None
