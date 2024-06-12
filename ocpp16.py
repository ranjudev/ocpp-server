import asyncio
import logging
from datetime import datetime
from typing import Optional
import json

from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp
from ocpp.v16.enums import Action, RegistrationStatus, CertificateUse, ResetType, AvailabilityType, MessageTrigger
from ocpp.v16.datatypes import MeterValue
from ocpp.v16 import call, call_result

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


class ChargePoint(cp):
    def __init__(self, cp_id, websocket, managers):
        super().__init__(cp_id, websocket)
        self.cp_id = cp_id
        self.websocket = websocket
        self.managers = managers
        self.current_txn_id = None
        self.background_tasks = set()
        self.variables_to_poll = set()
        # Here you could queue some tasks to perform each time a charger connects, for example:
        # self.queue_task(self.send_get_configuration, delay=10)

    def add_manager(self, manager):
        self.managers.add(manager)

    def queue_task(self, fn, **kwargs):
        task = asyncio.create_task(fn(**kwargs))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def close(self):
        await self.websocket.close()

    @on(Action.BootNotification)
    def on_boot_notification(self, charge_point_vendor: str, charge_point_model: str, **kwargs):
        # on Alfen setting interval to a number < 30 stops heartbeat messages altogether
        # if self.cp_id == 'HIVE_006500':
        #     return call_result.BootNotificationPayload(
        #         current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]+'Z',
        #         interval=900,
        #         status=RegistrationStatus.pending
        #     )
        return call_result.BootNotificationPayload(
            current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            interval=900,
            status=RegistrationStatus.accepted
        )

    @on(Action.Heartbeat)
    def on_heartbeat(self):
        return call_result.HeartbeatPayload(
            current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        )

    @on(Action.SecurityEventNotification)
    def on_security_event_notification(self, type: str, timestamp: str, tech_info: Optional[str] = None):
        logging.info("type: %s, timestamp: %s, tech_info: %s", type, timestamp, tech_info)
        return call_result.SecurityEventNotificationPayload()

    @on(Action.StatusNotification)
    async def on_status_notification(self, connector_id: int, error_code: str, status: str, **kwargs):
        logging.info("connector_id: %d, status: %s, error_code: %s", connector_id, status, error_code)
        await self.managers.send("connector_id: %d, status: %s, error_code: %s" % (connector_id, status, error_code))
        return call_result.StatusNotificationPayload()

    @on(Action.StartTransaction)
    def on_start_transaction(self, connector_id: int, id_tag: str, meter_start: int, **kwargs):
        logging.info("start transaction connector_id: %d, id_tag: %s, meter_start: %d, other: %s", connector_id, id_tag,
                     meter_start, kwargs)
        return call_result.StartTransactionPayload(
            id_tag_info={'status': 'Accepted', 'expiryDate': '1970-01-01T00:00:00.000Z'},
            transaction_id=self.generate_txn_id(),
        )

    @on(Action.StopTransaction)
    def on_stop_transaction(self, transaction_id, timestamp, meter_stop, **kwargs):
        logging.info("stop transaction transaction id: %s, meter_stop: %d, timestamp: %s, other: %s", transaction_id,
                     meter_stop, timestamp, kwargs)
        return call_result.StopTransactionPayload()

    @on(Action.MeterValues)
    async def on_meter_values(self, connector_id: int, meter_value: MeterValue, **kwargs):
        await self.managers.send("meter values for connector id %d (%s): %s" % (connector_id, kwargs, meter_value))
        logging.info("meter values connector_id: %s, meter_value: %s, other: %s", connector_id, meter_value, kwargs)
        transaction_id = kwargs.get('transaction_id')
        if transaction_id is not None:
            logging.info("setting current transaction id from meter values: %d", transaction_id)
        self.current_txn_id = transaction_id
        return call_result.MeterValuesPayload()

    @on(Action.DiagnosticsStatusNotification)
    def on_diagnostics_status_notification(self, status):
        logging.info("diagnostics status: %s", status)
        return call_result.DiagnosticsStatusNotificationPayload()

    @on(Action.FirmwareStatusNotification)
    def on_firmware_status_notification(self, status):
        logging.info("firmware status: %s", status)
        return call_result.FirmwareStatusNotificationPayload()

    async def set_protocol_version(self, version='2.0.1'):
        return await self.send_change_configuration('ProtocolVersion', version)

    async def reset(self, reset_type='hard', evse_id=None):
        return await self.send_reset(ResetType.hard if reset_type == 'hard' else ResetType.soft)

    def generate_txn_id(self):
        if self.current_txn_id is None:
            self.current_txn_id = 0
        self.current_txn_id += 1
        return self.current_txn_id

    async def send_request(self, description, request):
        logging.info("sending %s request", description)
        response = await self.call(request)
        if response is not None:
            await self.managers.send("%s response: %s" % (description, response))
        else:
            await self.managers.send("%s error response." % description)
            logging.warning("%s error response", description)
        return response

    async def send_reset(self, reset_type=ResetType.hard, delay=0):
        await asyncio.sleep(delay)
        request = call.ResetPayload(type=reset_type)
        return await self.send_request("reset", request)

    async def send_get_configuration(self, key=None, delay=0):
        await asyncio.sleep(delay)
        print('key %s' % key)
        if key is None:
            request = call.GetConfigurationPayload()
        else:
            request = call.GetConfigurationPayload(key=[key])
        response = await self.send_request("get configuration", request)
        if response:
            await self.managers.send(json.dumps({
                'messageType': 'Config1.6',
                'payload': response.configuration_key}))
        return response

    async def send_change_configuration(self, key, value):
        request = call.ChangeConfigurationPayload(
            key=key,
            value=value
        )
        return await self.send_request("change configuration", request)

    async def send_install_certificate(self, delay=0):
        await asyncio.sleep(delay)
        with open('AmazonRootCA1.pem', 'r') as certfile:
            cert = certfile.read()  #  results in "Failed"

        request = call.InstallCertificatePayload(
            certificate_type=CertificateUse.central_system_root_certificate,
            certificate=cert
        )
        return await self.send_request("install certficate", request)

    async def send_clear_cache(self, delay=0):
        await asyncio.sleep(delay)
        request = call.ClearCachePayload()
        return await self.send_request("clear cache", request)

    async def send_change_availability(self, delay=0):
        await asyncio.sleep(delay)
        request = call.ChangeAvailabilityPayload(
            connector_id=1,
            type=AvailabilityType.inoperative
        )
        return await self.send_request("change availability", request)

    async def send_trigger_status_notification(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=MessageTrigger.status_notification
        )
        return await self.send_request("trigger status notification", request)

    async def send_trigger_meter_values(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=MessageTrigger.meter_values
        )
        return await self.send_request("trigger meter values", request)

    async def send_trigger_boot_notification(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message='BootNotification'
        )
        return await self.send_request("boot notification", request)

    async def send_remote_stop_transaction(self, delay=0):
        await asyncio.sleep(delay)
        if self.current_txn_id is None:
            logging.info("can't remotely stop transaction, no current transaction id recorded")
            return

        request = call.RemoteStopTransactionPayload(
            transaction_id=self.current_txn_id
        )
        return await self.send_request("remote stop transaction", request)

    # This works, but it results in an AES-encrypted file being uploaded to the FTP server
    # which can only be decrypted by Alfen support :-/
    async def send_get_diagnostics(self, delay=0):
        await asyncio.sleep(delay)
        #  Obviously change the FTP server location to something sensible for your environment
        request = call.GetDiagnosticsPayload(
            location='ftp://USER:PASSWORD@FTPSERVER/DIRECTORY'
        )
        logging.info("sending get diagnostics request")

    async def send_unlock_connector(self, delay=0):
        await asyncio.sleep(delay)
        request = call.UnlockConnectorPayload(
            connector_id=1
        )
        return await self.send_request("unlock connector", request)

    async def send_get_composite_schedule(self, delay=0):
        await asyncio.sleep(delay)
        request = call.GetCompositeSchedulePayload(
            connector_id=0,
            duration=86400
        )
        return await self.send_request("get composite schedule", request)

    async def send_clear_charging_profile(self, id=None, criteria=None, delay=0):
        await asyncio.sleep(delay)
        args = {}
        if id is not None:
            try:
                args['id'] = int(id)
            except ValueError:
                #  passing _ or some other non-int nonsense means search by id isn't needed
                pass
        if criteria is not None:
            criteria = json.loads(criteria)
            if criteria.get('connectorId') is not None:
                args['connector_id'] = criteria['connectorId']
        return await self.send_request("clear charging profile", call.ClearChargingProfilePayload(**args))

    async def send_set_charging_profile(self, evse=0, profile="", delay=0):
        await asyncio.sleep(delay)
        request = call.SetChargingProfilePayload(
            connector_id=int(evse),
            cs_charging_profiles=json.loads(profile)
        )
        return await self.send_request("set charging profile", request)

    async def send_update_firmware(self, filename=None, delay=0):
        await asyncio.sleep(delay)
        # url='ftp://USER:PASS@DOMAIN/' + filename
        url = filename
        request = call.UpdateFirmwarePayload(
            location=url,
            retrieve_date='2023-01-01T00:00:00Z'
        )
        return await self.send_request("update firmware", request)

    async def poll_variable(self, variable=None, delay=0, interval=60):
        await asyncio.sleep(delay)
        await self.send_get_configuration(key=variable, delay=0)
        if (variable, interval) in self.variables_to_poll:
            self.queue_task(self.poll_variable, variable=variable, interval=interval, delay=interval)

    def start_polling(self, variable, interval):
        self.variables_to_poll.add((variable, int(interval)))
        self.queue_task(self.poll_variable, variable=variable, interval=int(interval), delay=int(interval))

    def stop_polling(self, variable, interval):
        self.variables_to_poll.remove((variable, int(interval)))
