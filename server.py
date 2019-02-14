import tornado.ioloop
import tornado.log
import tornado.web
import tornado.httpclient
import tornado.websocket
import tornado.options
import logging
from asyncio import Event
import json
import collections

log = logging.getLogger('server')
ADDRESS = ''
PORT = 7651


class NoSuchMethod(Exception):
    pass

class Queue(collections.deque):
    def __init__(self):
        super().__init__()
        self.sender = None

    async def push(self, item):
        self.append(item)
        if self.sender:
            await self.sender.sendout()

queue = Queue()


class NotificationSocket(tornado.websocket.WebSocketHandler):
    async def open(self, *args, **kwargs):
        q = self.request.query
        log.debug("Opened socket. %s", json.dumps(q))
        # self.application.db_changed.connect(self.send_event)

        queue.sender = self
        await self.sendout()

    async def sendout(self):
        while len(queue):
            await self.send_event(queue.popleft())

    def check_origin(self, origin):
        return self.application.check_origin(origin)

    async def send_event(self, obj):
        try:
            await self.write_message(json.dumps(obj))
        except tornado.websocket.WebSocketClosedError as e:
            log.error('Closed socket, unexpected... %s', e)
            self.on_close()

    async def on_message(self, message):
        log.info("Message received: %s", message)
        await self.send_event(message)

    def on_close(self):
        log.debug("Closed socket: code: {s.close_code}, reason: {s.close_reason}".format(s=self))
        queue.sender = None


class SendHandler(tornado.web.RequestHandler):
    async def options(self, *args, **kwargs):
        self.send_cors_headers()
        log.info("Options")
        await self.finish('')

    def send_cors_headers(self):
        self.application.cors_headers(self)

    async def post(self, *args, **kwargs):
        return await self.get(*args, **kwargs)

    async def get(self, *args, **kwargs):
        q = self.request.query
        log.info("Query")
        self.send_cors_headers()
        self.add_header('Content-Type', 'application/json; charset=utf-8')
        self.set_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')

        await self.finish(json.dumps(q))
        await queue.push(q)


class Server(tornado.web.Application):
    io_loop = None
    address = None
    port = None

    def __init__(self, address=ADDRESS, port=PORT, loglevel=logging.WARNING):
        tornado.log.enable_pretty_logging(logger=logging.getLogger('tornado'))
        log.setLevel(loglevel)
        log.debug('Initializing server')
        tornado.web.Application.__init__(self, [
            (r'/pub/send', SendHandler),
            (r'/pub/listen', NotificationSocket),
        ], websocket_ping_interval=10)
        self.stopped = Event()
        self.address = address
        self.port = port

    def cors_headers(self, handler):
        origin = None
        try:
            origin = handler.request.headers['origin']
        except KeyError:
            pass
        handler.add_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        handler.add_header('Access-Control-Allow-Headers', 'DNT,'
                                                           'X-CustomHeader,'
                                                           'Keep-Alive,'
                                                           'User-Agent,'
                                                           'X-Requested-With,'
                                                           'If-Modified-Since,'
                                                           'Cache-Control,'
                                                           'Content-Type,'
                                                           'Content-Range,'
                                                           'Range,'
                                                           'Origin,'
                                                           'Accept')

        if origin is not None and self.check_origin(origin):
            handler.add_header('Access-Control-Allow-Origin', origin)

    def check_origin(self, origin):
        return True

    def start(self):
        log.debug('Starting server at %s:%d' % (self.address, self.port))
        self.listen(self.port, self.address)
        self.io_loop = tornado.ioloop.IOLoop.current()
        callback = self.io_loop.add_callback
        self.io_loop.start()
        self.stopped.set()

    def stop(self):
        log.info('Stopping...')
        if not self.stopped.is_set():
            self.io_loop.stop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    srv = Server(loglevel=logging.DEBUG)
    srv.start()

