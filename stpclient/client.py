#!/usr/bin/python
# coding: utf-8
import functools
from stpclient.ioloop import IOLoop
from stpclient.iostream import IOStream
import socket
import time


# exceptions for Client
class STPError(Exception):
    pass


class STPTimeoutError(Exception):
    pass


class STPNetworkError(Exception):
    pass


class STPProtocolError(Exception):
    pass


class STPReqeust(object):
    def __init__(self, args, connect_timeout=None, request_timeout=None):
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._argv = list(args)

    def _encode(self, value):
        "Return a bytestring representation of the value"
        if isinstance(value, unicode):
            return value.encode('utf-8', 'strict')
        return str(value)

    def add(self, arg):
        self._argv.append(arg)

    def serialize(self):
        buf = ''
        for arg in self._argv:
            earg = self._encode(arg)
            buf += '%d\r\n%s\r\n' % (len(earg), earg)
        buf += '\r\n'
        return buf


class STPResponse(object):
    def __init__(self, request_time, error=None):
        self.request_time = request_time
        self.error = error
        self._argv = []

    @property
    def argv(self):
        return self._argv

    def rethrow(self):
        """If there was an error on the request, raise an `STPError`."""
        if self.error:
            raise self.error


class Connection(object):
    def __init__(self, io_loop, client, connect_timeout=None, max_buffer_size=104857600):
        self.start_time = time.time()
        self.io_loop = io_loop
        self.client = client
        self.stream = None
        self.connect_timeout = connect_timeout
        self._timeout = None
        self.callback = None
        self.connect()

    def _on_timeout(self):
        self._timeout = None
        self._run_callback(STPResponse(request_time=time.time() - self.start_time,
                                        error=STPTimeoutError('Timeout')))
        self.stream.close()

    def connect(self):
        af = socket.AF_INET if self.client.unix_socket is None else socket.AF_UNIX
        self.stream = IOStream(socket.socket(af, socket.SOCK_STREAM),
                                io_loop=self.io_loop,
                                max_buffer_size=self.client.max_buffer_size)
        self._timeout = self.io_loop.add_timeout(self.start_time + self.connect_timeout, self._on_timeout) if self.connect_timeout else None
        self.stream.set_close_callback(self._on_close)
        addr = self.client.unix_socket if self.client.unix_socket is not None else (self.client.host, self.client.port)
        self.stream.connect(addr, self._on_connect)

    def _on_close(self):
        self._run_callback(STPResponse(request_time=time.time() - self.start_time,
                                        error=STPNetworkError('Connection closed')))

    def _on_connect(self):
        if self._timeout is not None:
            self.io_loop.remove_timeout(self._timeout)
            self._timeout = None
        if self.request.request_timeout:
            self._timeout = self.io_loop.add_timeout(
                self.start_time + self.request.request_timeout,
                self._on_timeout)

    def send_request(self, request, callback):
        self.callback = callback
        self.stream.write(request.serialize())
        self.read_arg()

    def read_arg(self):
        self.stream.read_until(b'\r\n', self._on_arglen)

    def _run_callback(self, response):
        if self.callback is not None:
            callback = self.callback
            self.callback = None
            callback(response)

    def _on_arglen(self, data):
        if data == '\r\n':
            self._run_callback(STPResponse(request_time=time.time() - self.start_time,
                                            error=STPTimeoutError("Timeout")))
        else:
            try:
                arglen = int(data[:-2])
                self.stream.read_bytes(arglen, self._on_arg)
            except Exception as e:
                self._run_callback(STPResponse(request_time=time.time() - self.start_time,
                                                error=STPProtocolError(str(e))))

    def _on_arg(self, data):
        self._request.add_arg(data)
        self.stream.read_until(b'\r\n', self._on_strip_arg_endl)

    def _on_strip_arg_endl(self, data):
        self.read_arg()


class AsyncClient(object):
    def __init__(self, host, port, unix_socket=None, io_loop=None, max_buffer_size=104857600):
        self.host = host
        self.port = port
        self.unix_socket = unix_socket
        self.io_loop = io_loop or IOLoop.instance()
        self.max_buffer_size = max_buffer_size
        self.connection = Connection(self.io_loop, self, self.max_buffer_size)

    def close(self):
        self.connection.close()

    def async_call(self, request, callback):
        if not isinstance(request, STPReqeust):
            request = STPReqeust([request])
        callback = callback
        self.connection.send_request(request, callback)


class Client(object):
    def __init__(self, host, port, timeout=None, unix_socket=None, max_buffer_size=104857600):
        self._io_loop = IOLoop()
        self._async_client = AsyncClient(host, port, timeout, unix_socket, io_loop=self._io_loop, max_buffer_size=max_buffer_size)
        self._response = None
        self._closed = False

    def __del__(self):
        self.close()

    @property
    def connection(self):
        return self._async_client.connection

    def close(self):
        if not self._closed:
            self._async_client.close()
            self._io_loop.close()
            self._closed = True

    def call(self, request):
        def callback(response):
            self._response = response
            self._io_loop.stop()
        self._async_client.call(request, callback)
        self._io_loop.start()
        response = self._response
        self._response = None
        response.rethrow()
        return response
