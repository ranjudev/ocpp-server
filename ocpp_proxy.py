import logging
import asyncio
import websockets

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


class ChargingStationProxy:
    def __init__(self, uri, id, password, websocket):
        self.charger_ws = websocket
        self.csms_ws = None
        self.id = id
        self.password = password
        self.uri = uri
        self.useTLS = uri.startswith('wss://')
        i = uri.find('://')
        if self.password:
            self.url = uri[:i + 3] + id + ':' + password + '@' + uri[i + 3:] + '/' + id
        else:
            self.url = uri + '/' + id

    async def start(self):
        if self.useTLS:
            sslargs = {'ssl': True}
        else:
            sslargs = {}
        async with websockets.connect(self.url, subprotocols=[self.charger_ws.subprotocol], **sslargs) as self.csms_ws:
            loop = asyncio.get_event_loop()
            await asyncio.gather(
                loop.create_task(self.listen_charger()),
                loop.create_task(self.listen_csms())
            )
        self.csms_ws = None

    async def listen_charger(self):
        while True:
            message = await self.charger_ws.recv()
            logging.info('%s: message from charger: %s', self.id, message)
            await self.send_to_csms(message)

    async def listen_csms(self):
        while True:
            message = await self.csms_ws.recv()
            logging.info('%s: message from CSMS: %s', self.id, message)
            await self.send_to_charger(message)

    async def send_to_csms(self, message):
        logging.info('sending to csms: %s', message)
        await self.csms_ws.send(message)

    async def send_to_charger(self, message):
        logging.info('sending to charger: %s', message)
        await self.charger_ws.send(message)