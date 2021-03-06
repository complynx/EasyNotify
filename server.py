#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#  EasyNotify
#  Copyright (c) 2019, Daniel Drizhuk <complynx@yandex.ru>
#  All rights reserved.
#
#  This code is distributed under the terms of the BSD licence
#
#  Redistribution and use of this software in source and binary forms, with or without modification,
#  are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice, this list of conditions
#      and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice, this list of
#      conditions and the following disclaimer in the documentation and/or other materials provided
#      with the distribution.
#    * The names of the contributors to this file may not be used to endorse or promote products
#      derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

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
        q = self.request.query_arguments
        if self.application.listener_key and ('key' not in q or q['key'][0].decode() != self.application.listener_key):
            return self.send_error(403)
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
        q = self.request.query_arguments
        if self.application.notifier_key and ('key' not in q or q['key'][0].decode() != self.application.notifier_key):
            return self.send_error(403)
        log.info("Query")
        self.send_cors_headers()
        self.add_header('Content-Type', 'application/json; charset=utf-8')
        self.set_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        m = self.request.body
        if m:
            m = json.loads(m)

        await self.finish(json.dumps(m))

        if 'overwrite' in m and m['overwrite'] and 'type' in m:
            i = 0
            while i < len(queue):
                if 'type' in queue[i] and m['type'] == queue[i]['type']:
                    del queue[i]
                else:
                    i += 1
        await queue.push(m)


class Server(tornado.web.Application):
    io_loop = None
    address = None
    port = None

    def __init__(self, address='', port=8080, loglevel=logging.WARNING, mounting_point='',
                 listener_key=None, notifier_key=None):
        self.listener_key = listener_key
        self.notifier_key = notifier_key
        # tornado.log.enable_pretty_logging(logger=logging.getLogger('tornado'))
        log.setLevel(loglevel)
        log.debug('Initializing server')
        tornado.web.Application.__init__(self, [
            (mounting_point + r'/send', SendHandler),
            (mounting_point + r'/listen', NotificationSocket),
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
        self.io_loop.start()
        self.stopped.set()

    def stop(self):
        log.info('Stopping...')
        if not self.stopped.is_set():
            self.io_loop.stop()


if __name__ == "__main__":
    import argparse
    import time
    import logging.handlers

    parser = argparse.ArgumentParser(description='Starts a server that helps with retransmitting of notifications'
                                                 ' to your IoT device. You can use it as is or mount it at some Nginx'
                                                 ' mountpoint as a proxy.')
    parser.add_argument('-a', '--address', default='', help="Address to listen on")
    parser.add_argument('-p', '--port', type=int, default=8080, help="Port to listen on")
    parser.add_argument('-P', '--prefix', default='', help="Prefix of the api. As '<prefix>/send'")
    parser.add_argument('-k', '--notifier_key', default=None, help="Key for notifier identification")
    parser.add_argument('-K', '--listener_key', default=None, help="Key for listener identification")
    parser.add_argument('-L', '--logfile', default='', help="Logger file, stdout if none")
    parser.add_argument(
        '-d', '--debug',
        help="Print lots of debugging statements",
        action="store_const", dest="loglevel", const=logging.DEBUG,
        default=logging.WARNING,
    )
    parser.add_argument(
        '-v', '--verbose',
        help="Be verbose",
        action="store_const", dest="loglevel", const=logging.INFO,
    )
    arg = parser.parse_args()

    formatter = logging.Formatter(
        '%(asctime)s (UTC) [ %(levelname)s ] %(name)s %(filename)s:%(lineno)d %(funcName)s(): %(message)s')
    formatter.converter = time.gmtime
    log_handler = logging.handlers.RotatingFileHandler(maxBytes=5*1024*1024, backupCount=5, filename=arg.logfile) if \
        len(arg.logfile) > 0 else logging.StreamHandler()
    log_handler.setFormatter(formatter)
    while len(logging.root.handlers):
        log.removeHandler(logging.root.handlers[0])
    logging.root.addHandler(log_handler)

    logging.basicConfig(level=arg.loglevel, handlers=[log_handler])

    srv = Server(
        address=arg.address,
        port=arg.port,
        mounting_point=arg.prefix,
        notifier_key=arg.notifier_key,
        listener_key=arg.listener_key,
        loglevel=arg.loglevel)
    srv.start()

