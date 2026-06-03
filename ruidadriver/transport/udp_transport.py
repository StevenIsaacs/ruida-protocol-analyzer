import socket
from typing import Optional

from .base import Transport


class UdpTransport(Transport):
    """Transport implementation for UDP network communication."""

    def __init__(self) -> None:
        self._socket: Optional[socket.socket] = None
        self._host: Optional[str] = None
        self._port: Optional[int] = None

    def open(self, host: str, port: int = 50200, **kwargs) -> bool:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._host = host
        self._port = port
        return True

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def write(self, packet: bytearray) -> None:
        self._socket.sendto(bytes(packet), (self._host, self._port))

    def read(self, length: int) -> Optional[bytes]:
        try:
            return self._socket.recv(length, socket.MSG_DONTWAIT)
        except BlockingIOError:
            return None

    def drain(self) -> None:
        while self.read(65536) is not None:
            pass

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    @property
    def is_usb(self) -> bool:
        return False

    @property
    def is_udp(self) -> bool:
        return True
