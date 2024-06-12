import asyncio
import logging
import os
import websockets
from http import HTTPStatus
from ocpp_proxy import ChargingStationProxy
from base64 import b32decode

from admin import AdminSession, Managers
from ocpp201 import ChargingStation
from ocpp16 import ChargePoint

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s',level=logging.INFO,datefmt='%Y-%m-%d %H:%M:%S')

chargers = []
admins = {}
proxy_chargers = []

class AdminLogger(logging.StreamHandler):
    def __init__(self):
        super().__init__()
        self.background_tasks = set()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            loop = asyncio.get_running_loop()
            task = asyncio.current_task(loop)
            if task is not None:
                record.__setattr__("thread", f"{record.thread}[{task.get_name()}]")
                for _, session in admins.items():
                    logtask = loop.create_task(session.send(super().format(record)))
                    self.background_tasks.add(logtask)
                    logtask.add_done_callback(self.background_tasks.discard)
        except RuntimeError:
            pass
        super().emit(record)

console = AdminLogger()
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(levelname)s:%(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)


async def on_charger_connect(websocket, path):
    """ For every new charge point that connects, create a ChargePoint/ChargingStation
    instance and start listening for messages.
    """
    logging.info("Connection from %s on local address %s", websocket.remote_address, websocket.local_address)
    try:
        requested_protocols = websocket.request_headers[
            'Sec-WebSocket-Protocol']
    except KeyError:
        logging.error(
            "Client hasn't requested any Subprotocol. Closing Connection"
        )
        return await websocket.close()
    if websocket.subprotocol:
        logging.info("Protocols Matched: %s", websocket.subprotocol)
    else:
        # In the websockets lib if no subprotocols are supported by the
        # client and the server, it proceeds without a subprotocol,
        # so we have to manually close the connection.
        logging.warning('Protocols Mismatched | Expected Subprotocols: %s,'
                        ' but client supports  %s | Closing connection',
                        websocket.available_subprotocols,
                        requested_protocols)
        return await websocket.close()

    cs_id = path.strip('/')

    if "ocpp2.0.1" in websocket.subprotocol:
       cp = ChargingStation(cs_id, websocket, Managers())
    else: 
       cp = ChargePoint(cs_id, websocket, Managers())

    chargers.append(cp)

    await update_admins()

    try:
        await cp.start()
    except websockets.exceptions.ConnectionClosedError:
        logging.info('Connection closed %s', websocket.remote_address)
    finally:
        chargers.remove(cp)
        await update_admins()

async def update_admins():
    for _, session in admins.items():
        try:
            await session.list_chargers()
        except:
            logging.warn('Expired admin session - ignoring')

async def on_admin_connect(websocket, path):
    logging.info("Admin connection from %s", websocket.remote_address)
    try:
        admin_session = AdminSession(websocket, chargers, proxy_chargers)
        admins[websocket.remote_address] = admin_session
        logging.info('admins: %s', admins)
        await admin_session.start()
    except websockets.exceptions.ConnectionClosedError:
        logging.info('Admin connection closed %s', websocket.remote_address)
    finally:
        del admins[websocket.remote_address]
        logging.info('admins: %s', admins)

async def process_admin_request(path, request_headers):
    """Serves a file when doing a GET request with a valid path."""

    if "Upgrade" in request_headers:
        return  # Probably a WebSocket connection

    response_headers = [
        ('Server', 'asyncio websocket server'),
        ('Connection', 'close'),
    ]

    # Yes, this is security through obscurity.  Don't run this on an Internet-facing
    # server without taking appropriate extra precautions, like trusting specific IP's at minimum. 
    admin_path = '/admin/0ea1d463-53c6-486e-9b1b-3e8901e9411b'
    admin_file = 'index.html'

    # Validate the path
    if not (path == admin_path and os.path.exists(admin_file) and os.path.isfile(admin_file)):
        print("HTTP GET {} 404 NOT FOUND".format(path))
        return HTTPStatus.NOT_FOUND, [], b'404 NOT FOUND'

    response_headers.append(('Content-Type', "text/html"))
    response_headers.append(('Cache-Control', 'no-cache'))
    response_headers.append(('no-store', 'must-revalidate'))
    response_headers.append(('Pragma', 'no-cache'))
    response_headers.append(('Expires', '0'))

    # Read the whole file into memory and send it out
    body = open(admin_file, 'rb').read()
    response_headers.append(('Content-Length', str(len(body))))
    print("HTTP GET {} 200 OK".format(path))
    return HTTPStatus.OK, response_headers, body


async def check_credentials(username, password):
    logging.info('Basic Auth credentials: username:%s password:%s', username, password)
    return True

async def on_charger_connect_proxy(websocket, path):
    logging.info("Proxy connection from %s on local address %s", websocket.remote_address, websocket.local_address)
    try:
        requested_protocols = websocket.request_headers['Sec-WebSocket-Protocol']
    except KeyError:
        logging.error("Client hasn't requested any Subprotocol. Closing Connection")
        return await websocket.close()
    if websocket.subprotocol:
        logging.info("Protocols Matched: %s", websocket.subprotocol)
    else:
        logging.warning('Protocols Mismatched | Expected Subprotocols: %s,'
                        ' but client supports  %s | Closing connection',
                        websocket.available_subprotocols,
                        requested_protocols)
        return await websocket.close()

    uri = 'ws://localhost:9000'
    password = None

    # This is a workaround to allow the client to pass a target uri and basic auth password in the
    # websocket connection request, because the WebSocket browser implementation is really limited
    # and only allows setting subprotocols as an option.
    # Subprotocol itself has a strict set of allowed characters, hence the complicated encoding. 
    for protocol in requested_protocols.split(', '):
        if protocol.startswith('uri.'):
            uri = b32decode(protocol[4:].replace('-', '=')).decode('utf-8')
        elif protocol.startswith('pw.'):
            password = b32decode(protocol[3:].replace('-', '=')).decode('utf-8')

    cs_id = path.strip('/')
    logging.info('Proxying CS=%s from %s to uri=%s', cs_id, websocket.remote_address, uri)
    cp = ChargingStationProxy(uri, cs_id, password, websocket)
    proxy_chargers.append(cp)
    await update_admins()

    try:
        await cp.start()
    except websockets.exceptions.ConnectionClosedError:
        logging.info('Proxy connection closed CS=%s from %s', cs_id, websocket.remote_address)
    finally:
        proxy_chargers.remove(cp)
        await update_admins()

async def main():
    server = await websockets.serve(
        on_charger_connect,
        '0.0.0.0',
        9000,
        subprotocols=['ocpp1.5', 'ocpp1.6', 'ocpp2.0.1']
    )

    admin_server = await websockets.serve(
        on_admin_connect,
        '0.0.0.0',
        9001,
        process_request=process_admin_request
    )

    server_auth = await websockets.serve(
        on_charger_connect,
        '0.0.0.0',
        9002,
        subprotocols=['ocpp1.5', 'ocpp1.6', 'ocpp2.0.1'],
        create_protocol=websockets.basic_auth_protocol_factory(
            realm="Elventronic CSMS",
            check_credentials=check_credentials
        )
    )

    # Uncomment this if you want to run a TLS server, and add it to the asyncio.gather().
    # You will need to have generated a suitable TLS certificate and key, and the charger
    # will need to have the relevant root CA certificate installed, otherwise it will refuse
    # to connect.
    # 
    # ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # ssl_context.load_cert_chain(
    #     pathlib.Path(__file__).with_name('server.pem'),
    #     keyfile=pathlib.Path(__file__).with_name('server.key')
    # )
    # server_tls = await websockets.serve(
    #     on_charger_connect,
    #     '0.0.0.0',
    #     9003,
    #     subprotocols=['ocpp1.5', 'ocpp1.6', 'ocpp2.0.1'],
    #     ssl=ssl_context
    # )

    proxy_server = await websockets.serve(
        on_charger_connect_proxy,
        '0.0.0.0',
        9100,
        subprotocols=['ocpp1.5', 'ocpp1.6', 'ocpp2.0.1']
    )

    logging.info("Server Started listening to new connections...")
    await asyncio.gather(server.wait_closed(),
                         admin_server.wait_closed(),
                         server_auth.wait_closed(),
                         proxy_server.wait_closed())

if __name__ == "__main__":
    # asyncio.run() is used when running this example with Python >= 3.7v
    asyncio.run(main())