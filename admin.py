import logging
import json
from ocpp201 import ChargingStation
from ocpp16 import ChargePoint
from ocpp_proxy import ChargingStationProxy
import websockets


class Managers:
    def __init__(self):
        self.managers = []

    def add(self, manager):
        self.managers.append(manager)

    def remove(self, manager):
        self.managers.remove(manager)

    async def send(self, message):
        for manager in self.managers:
            try:
                await manager.send(message)
            except:
                logging.warn('Exception sending to manager - removing')
                self.remove(manager)


class AdminSession:
    def __init__(self, connection, chargers, proxy_chargers, response_timeout=30):
        self._connection = connection
        self._chargers = chargers
        self._proxy_chargers = proxy_chargers
        self._managing_charger_id = None

    async def send(self, message):
        await self._connection.send(message)

    async def start(self):
        while True:
            try:
                message = await self._connection.recv()
                logging.info('receive message %s', message)
                await self.handle_command(message)
            except websockets.exceptions.ConnectionClosedOK:
                logging.info('Admin connection closed')
                return
            except Exception as e:
                logging.warn(e)
                return

    async def list_chargers(self):
        await self.send(json.dumps({
            'messageType': 'Chargers',
            'payload': self._chargers}, cls=ChargerEncoder))
        await self.send(json.dumps({
            'messageType': 'ProxyChargers',
            'payload': self._proxy_chargers}, cls=ChargerEncoder))

    def managing_charger(self):
        for charger in self._chargers:
            if charger.id == self._managing_charger_id:
                return charger

    async def handle_command(self, message):
        try:
            command = json.loads(message)['msg']
            if command.startswith('list'):
                await self.list_chargers()
            elif command.startswith('manage'):
                (_, id) = command.split()
                for charger in self._chargers:
                    if charger.id == id:
                        self._managing_charger_id = id
                        logging.info('managing %s', charger.id)
                        charger.add_manager(self)
                        await self.send(json.dumps({
                            'messageType': 'Managing',
                            'payload': self._managing_charger_id}))
            # All other commands need to be targeted at a specific charger
            elif not self._managing_charger_id:
                await self.send('Need to "manage" a charger first')
                raise Exception()
            elif command.startswith('status'):
                await self.managing_charger().send_trigger_status_notification()

            elif command.startswith('downgrade'):
                await self.managing_charger().set_protocol_version('1.6')

            elif command.startswith('upgrade'):
                await self.managing_charger().set_protocol_version('2.0.1')

            elif command.startswith('resetevse'):
                (_, reset_type, evse) = command.split()
                await self.managing_charger().reset(reset_type=reset_type, evse_id=int(evse))

            elif command.startswith('reset'):
                try:
                    (_, reset_type) = command.split()
                    await self.managing_charger().reset(reset_type=reset_type)
                except ValueError:
                    await self.managing_charger().reset()

            elif command.startswith('fixnotificationmode'):
                if 'ocpp1.6' in self.managing_charger().websocket.subprotocol:
                    await self.managing_charger().send_change_configuration('StatusNotificationMode', 'Queued')
                else:
                    await self.send('Unsupported in this OCPP version')

            elif command.startswith('metervalues'):
                await self.managing_charger().send_trigger_meter_values()

            elif command.startswith('transactionstatus'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    await self.managing_charger().send_get_transaction_status()
                else:
                    await self.send('Unsupported in this OCPP version')

            elif command.startswith('seturl9000'):
                newuri = 'ws://ev.elventronic.com:9000'
                newpath = ''
                await self.set_url(self.managing_charger(), newuri, newpath)

            elif command.startswith('seturl9002'):
                newuri = 'ws://ev.elventronic.com:9002'
                newpath = ''
                await self.set_url(self.managing_charger(), newuri, newpath)
            elif command.startswith('seturlPR'):
                newuri = 'ws://ocpp.powerradar:8080/websocket/CentralSystemService'
                newpath = ''
                await self.set_url(self.managing_charger(), newuri, newpath)

            elif command.startswith('setnetworkprofile'):
                newurl = 'ws://ev.elventronic.com:9002'
                try:
                    (_, slot, version, url, security_profile, interface) = command.split()
                    await self.managing_charger().send_set_network_profile_request(slot, version, url, security_profile,
                                                                                   interface)
                except ValueError:
                    logging.info('Usage: setnetworkprofile slot, version, url, security_profile, interface')

            elif command.startswith('seturl9003'):
                newuri = 'wss://ev.elventronic.com:9003'
                newpath = ''
                await self.set_url(self.managing_charger(), newuri, newpath)

            elif command.startswith('seturldev4'):
                newuri = 'ws://hiveev-wss-dev.dev4.bgch.io'
                newpath = '/csms/2.0.1'
                await self.set_url(self.managing_charger(), newuri, newpath)

            elif command.startswith('setpassword'):
                # password='TESTTESTTESTTEST' # must be 16-20 characters for Alfen
                (_, password) = command.split()
                await self.set_credentials(self.managing_charger(), password)

            elif command.startswith('setsecurityprofile'):
                (_, profile) = command.split()
                await self.set_security_profile(self.managing_charger(), profile)

            elif command.startswith('getconfig'):
                try:
                    (_, reporttype) = command.split()
                except ValueError:
                    pass
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    if (reporttype):
                        await self.managing_charger().send_get_base_report(reporttype=reporttype)
                    else:
                        await self.managing_charger().send_get_base_report()
                else:
                    await self.managing_charger().send_get_configuration()

            elif command.startswith('chargingprofiles'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    await self.managing_charger().send_get_charging_profiles()
                else:
                    await self.send('Unsupported in this OCPP version')

            elif command.startswith('setchargingprofileorig'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    await self.managing_charger().send_set_charging_profile_orig()
                else:
                    await self.send('Unsupported in this OCPP version')

            elif command.startswith('setchargingprofile'):
                (_, evse, profile) = command.split(None, 2)
                logging.info(evse)
                logging.info(profile)
                await self.managing_charger().send_set_charging_profile(evse=evse, profile=profile)

            elif command.startswith('clearchargingprofile'):
                try:
                    (_, id, criteria) = command.split(None, 2)
                except ValueError:
                    criteria = None
                    try:
                        (_, id) = command.split()
                    except ValueError:
                        id = None
                await self.managing_charger().send_clear_charging_profile(id=id, criteria=criteria)

            elif command.startswith('schedule'):
                await self.managing_charger().send_get_composite_schedule()

            elif command.startswith('settxstartstop'):
                if 'ocpp2.0.1' not in charger.websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_set_variables(component='TxCtrlr', key='TxStartPoint',
                                                                 value="PowerPathClosed")
                await self.managing_charger().send_set_variables(component='TxCtrlr', key='TxStopPoint',
                                                                 value="PowerPathClosed")

            elif command.startswith('setauthenabledfalse'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_set_variables(component='AuthCtrlr', key='AuthEnabled',
                                                                 value="False")

            elif command.startswith('setremoteauth'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_set_variables(component='AlfenStation', key='AuthorizationMethod',
                                                                 value="RemoteAuth")

            elif command.startswith('setrfid'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_set_variables(component='AlfenStation', key='AuthorizationMethod',
                                                                 value="RFID")

            elif command.startswith('setplugandcharge'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_set_variables(component='AlfenStation', key='AuthorizationMethod',
                                                                 value="Plug&Charge")

            elif command.startswith('remotestart'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_request_start_transaction()

            elif command.startswith('remotestop'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_request_stop_transaction()

            elif command.startswith('triggertxnevent'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_trigger_transaction_event()

            elif command.startswith('triggerbootnotification'):
                await self.managing_charger().send_trigger_boot_notification()

            elif command.startswith('trigger '):
                (_, message_type) = command.split()
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_trigger_message(message_type)

            elif command.startswith('unlock'):
                if 'ocpp2.0.1' not in self.managing_charger().websocket.subprotocol:
                    return await self.send('Unsupported in this OCPP version')
                await self.managing_charger().send_remote_unlock()

            elif command.startswith('setpncid'):
                (_, id) = command.split()
                await self.managing_charger().send_set_variables(component='AlfenStation',
                                                                 key='PlugAndChargeIdentifier', value=id)

            elif command.startswith('changeavailability'):
                (_, available_str) = command.split()
                await self.managing_charger().send_change_availability(available=(available_str == 'operative'))

            elif command.startswith('setupmonitoring'):
                await self.managing_charger().send_set_variable_monitoring()

            elif command.startswith('startpolling'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    (_, component, key, interval) = command.split()
                    self.managing_charger().start_polling(component, key, interval)
                else:
                    (_, key, interval) = command.split()
                    self.managing_charger().start_polling(key, interval)

            elif command.startswith('stoppolling'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    (_, component, key, interval) = command.split()
                    self.managing_charger().stop_polling(component, key, interval)
                else:
                    (_, key, interval) = command.split()
                    self.managing_charger().stop_polling(key, interval)

            elif command.startswith('dropconnection'):
                logging.info('Admin requested drop websocket connection: %s' % self._managing_charger_id)
                await self.managing_charger().close()

            elif command.startswith('updatefirmware'):
                if 'ocpp2.0.1' in self.managing_charger().websocket.subprotocol:
                    logging.info('Only supported on 1.6 at the moment')
                    return
                (_, sourceVersion, targetVersion) = command.split()
                s3_url = 'https://ev-qa-ocpp-ota-firmwares.s3.eu-west-1.amazonaws.com/'
                firmware_URL = s3_url + 'updater__eo_' + sourceVersion + '__to__eo_' + targetVersion + '.zip'
                logging.info(firmware_URL)
                await self.managing_charger().send_update_firmware(filename=firmware_URL)

            elif command.startswith('set '):
                charger = self.managing_charger()
                if 'ocpp2.0.1' in charger.websocket.subprotocol:
                    (_, component, key, value) = command.split()
                    await charger.send_set_variables(component=component, key=key, value=value)
                else:
                    (_, key, value) = command.split()
                    await charger.send_change_configuration(key=key, value=value)

            elif command.startswith('get '):
                charger = self.managing_charger()
                if 'ocpp2.0.1' in charger.websocket.subprotocol:
                    (_, component, key) = command.split()
                    await charger.send_get_variables(component=component, key=key)
                else:
                    (_, key) = command.split()
                    await charger.send_get_configuration(key=key)

        except Exception as e:
            await self.send('Bad command')
            logging.warn('Exception handling command %s: %s', message, e)

    async def set_url(self, charger, newuri, newpath):
        if 'ocpp2.0.1' in charger.websocket.subprotocol:
            await charger.send_set_variables(component='AlfenStation', key='BackOffice-URL-wired', value=newuri)
            await charger.send_set_variables(component='AlfenStation', key='BackOffice-URL-APN', value=newuri)
            await charger.send_set_variables(component='AlfenStation', key='BackOffice-Path-wired', value=newpath)
            await charger.send_set_variables(component='AlfenStation', key='BackOffice-Path-APN', value=newpath)
        else:
            await charger.send_change_configuration('BackOffice-URL-wired', newuri)
            await charger.send_change_configuration('BackOffice-URL-APN', newuri)
            await charger.send_change_configuration('BackOffice-Path-wired', newpath)
            await charger.send_change_configuration('BackOffice-Path-APN', newpath)

    async def set_credentials(self, charger, password):
        if 'ocpp2.0.1' in charger.websocket.subprotocol:
            #  For pre-6.0 firmware
            #  await charger.send_set_variables(component='SecurityCtrlr', key='BasicAuthPassword', value=password.encode(encoding="UTF-8").hex()) # unknown variable
            await charger.send_set_variables(component='SecurityCtrlr', key='BasicAuthPassword',
                                             value=password)  # unknown variable
        else:
            await charger.send_change_configuration('AuthorizationKey', password.encode(encoding="UTF-8").hex())

    async def set_security_profile(self, charger, profile):
        if 'ocpp2.0.1' in charger.websocket.subprotocol:
            await charger.send_set_variables(component='SecurityCtrlr', key='SecurityProfile', value=profile)
        else:
            await charger.send_change_configuration('SecurityProfile', profile)


class ChargerEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ChargingStation) or isinstance(obj, ChargePoint):
            return obj.id
        elif isinstance(obj, ChargingStationProxy):
            return {'id': obj.id, 'uri': obj.uri}
        return json.JSONEncoder.default(self, obj)
