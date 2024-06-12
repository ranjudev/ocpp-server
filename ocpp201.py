import asyncio
import logging
import json
from datetime import datetime
from typing import Optional, List

from ocpp.routing import on
from ocpp.v201 import ChargePoint as cp
from ocpp.v201.enums import Action, RegistrationStatusType, ConnectorStatusType, InstallCertificateUseType, \
    ReportBaseType, ResetType, OperationalStatusType, MessageTriggerType, ChargingLimitSourceType, OCPPVersionType, \
    OCPPTransportType, OCPPInterfaceType, TransactionEventType, AuthorizationStatusType, IdTokenType as IdTokenEnumType, \
    ChargingProfilePurposeType, ChargingProfileKindType, RecurrencyKindType, MonitorType
from ocpp.v201.datatypes import SetVariableDataType, ComponentType, VariableType, EVSEType, MeterValueType, \
    ChargingProfileCriterionType, NetworkConnectionProfileType, IdTokenType, IdTokenInfoType, ChargingProfileType, \
    ChargingScheduleType, ChargingSchedulePeriodType, SetMonitoringDataType, GetVariableDataType, APNType
from ocpp.v201 import call, call_result

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


class ChargingStation(cp):
    def __init__(self, cp_id, websocket, managers):
        super().__init__(cp_id, websocket)
        self.cp_id = cp_id
        self.websocket = websocket
        self.managers = managers
        self.heartbeats = 0
        self.request_id = 0
        self.current_transaction_id = None
        self.background_tasks = set()
        self.variables_to_poll = set()

    def add_manager(self, manager):
        self.managers.add(manager)

    def queue_task(self, fn, **kwargs):
        task = asyncio.create_task(fn(**kwargs))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def get_next_request_id(self):
        self.request_id += 1
        return self.request_id

    async def close(self):
        await self.websocket.close()

    @on(Action.BootNotification)
    def on_boot_notification(self, charging_station: str, reason: str, **kwargs):
        # on Alfen setting interval to a number < 30 stops heartbeat messages altogether
        # if self.cp_id == 'HIVE_006500':
        #     return call_result.BootNotificationPayload(
        #         current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]+'Z',
        #         interval=900,
        #         status=RegistrationStatusType.pending
        #     )

        return call_result.BootNotificationPayload(
            current_time=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            interval=900,
            status=RegistrationStatusType.accepted
        )

    @on(Action.FirmwareStatusNotification)
    def on_firmware_status(self, status: str, request_id: Optional[str] = None):
        return call_result.FirmwareStatusNotificationPayload()

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
    async def on_status_notification(self, timestamp: str, connector_status: ConnectorStatusType, evse_id: int,
                                     connector_id: int, **kwargs):
        logging.info("timestamp: %s, connector_id: %d, status: %s, evse_id: %d", timestamp, connector_id,
                     connector_status, evse_id)
        await self.managers.send("timestamp: %s, connector_id: %d, status: %s, evse_id: %d" % (
        timestamp, connector_id, connector_status, evse_id))
        return call_result.StatusNotificationPayload()

    @on(Action.NotifyReport)
    async def on_notify_report(self, request_id: int, generated_at: str, seq_no: int, tbc: Optional[bool] = None,
                               report_data: Optional[List] = None):
        logging.info("notify report: id=%d generated_at=%s seq_no=%d tbc=%s report_data=%s", request_id, generated_at,
                     seq_no, tbc, report_data)
        await self.managers.send(json.dumps({
            'messageType': 'Config2.0.1',
            'payload': report_data}))
        return call_result.NotifyReportPayload()

    @on(Action.MeterValues)
    async def on_meter_values(self, evse_id: int, meter_value: MeterValueType):
        # await self.managers.send("meter values for evse id %d: %s" % (evse_id, meter_value))
        # logging.info("meter values evse_id: %s, meter_value: %s", evse_id, meter_value)
        return call_result.MeterValuesPayload()

    @on(Action.ReportChargingProfiles)
    async def on_report_charging_profiles(self, request_id: int, charging_limit_source: ChargingLimitSourceType,
                                          **kwargs):
        logging.info("request_id: %d, charging_limit_source: %s, other: %s", request_id, charging_limit_source, kwargs)
        await self.managers.send(
            "request_id: %d, charging_limit_source: %s, other: %s" % (request_id, charging_limit_source, kwargs))
        return call_result.StatusNotificationPayload()

    @on(Action.TransactionEvent)
    async def on_transaction_event(self, event_type: TransactionEventType, **kwargs):
        # logging.info("transaction event: type:%s, other: %s", event_type, kwargs)
        self.current_transaction_id = kwargs.get('transaction_info', {}).get('transaction_id')
        # await self.managers.send("transaction_event type:%s, other: %s" % (event_type, kwargs))
        if event_type == TransactionEventType.started and kwargs.get('id_token', {}).get('id_token', '') != 'CENT2020':
            return call_result.TransactionEventPayload(
                id_token_info=IdTokenInfoType(status=AuthorizationStatusType.unknown)
            )
        return call_result.TransactionEventPayload()

    @on(Action.Authorize)
    async def on_authorize(self, id_token: IdTokenType, **kwargs):
        logging.info("authorization requested: %s %s", id_token, kwargs)
        await self.managers.send("authorization requested: %s %s" % (id_token, kwargs))
        if id_token['id_token'] == 'CENT2020':
            payload = call_result.AuthorizePayload(
                id_token_info=IdTokenInfoType(
                    status=AuthorizationStatusType.accepted
                )
            )
        else:
            payload = call_result.AuthorizePayload(
                id_token_info=IdTokenInfoType(
                    status=AuthorizationStatusType.invalid
                )
            )
        return payload

    async def set_protocol_version(self, version='1.6'):
        return await self.send_set_variables(component='AlfenStation', key='ProtocolVersion', value=version)

    async def reset(self, reset_type='Immediate', evse_id=None):
        return await self.send_reset(reset_type=ResetType.immediate if reset_type == 'Immediate' else ResetType.on_idle,
                                     evse_id=evse_id)

    async def send_request(self, description, request):
        logging.info("sending %s request", description)
        response = await self.call(request)
        if response is not None:
            await self.managers.send("%s response: %s" % (description, response))
            logging.info("%s response status: %s", description, response)
        else:
            await self.managers.send("%s error response." % description)
            logging.warning("%s error response", description)
        return response

    async def send_trigger_boot_notification(self, delay=0):
        await asyncio.sleep(delay)
        # request = call.TriggerMessagePayload(
        #     requested_message=MessageTriggerType.boot_notification,
        #     evse=EVSEType(id=0)
        # )
        request = call.TriggerMessagePayload(
            requested_message=MessageTriggerType.boot_notification
        )
        return await self.send_request("trigger boot notification", request)

    async def send_trigger_message(self, message_type=None, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=message_type
        )
        return await self.send_request("trigger message", request)

    async def send_trigger_status_notification(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=MessageTriggerType.status_notification
        )
        return await self.send_request("trigger status notification", request)

    async def send_trigger_transaction_event(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=MessageTriggerType.transaction_event
        )
        return await self.send_request("trigger transaction event", request)

    async def send_trigger_meter_values(self, delay=0):
        await asyncio.sleep(delay)
        request = call.TriggerMessagePayload(
            requested_message=MessageTriggerType.meter_values
        )
        return await self.send_request("trigger meter values", request)

    async def send_install_certificate(self, delay=0):
        await asyncio.sleep(delay)
        with open('AmazonRootCA1.pem', 'r') as certfile:
            cert = certfile.read()  #  results in "Failed"

        request = call.InstallCertificatePayload(
            certificate_type=InstallCertificateUseType.csms_root_certificate,
            certificate=cert
        )
        return await self.send_request("install certficate", request)

    async def send_get_base_report(self, reporttype=ReportBaseType.full_inventory, delay=0):
        await asyncio.sleep(delay)
        request = call.GetBaseReportPayload(
            request_id=self.get_next_request_id(),
            report_base=reporttype
        )
        return await self.send_request("get base report", request)

    async def send_get_charging_profiles(self, delay=0):
        await asyncio.sleep(delay)
        request = call.GetChargingProfilesPayload(
            request_id=self.get_next_request_id(),
            charging_profile=ChargingProfileCriterionType()
        )
        return await self.send_request("get charging profiles", request)

    async def send_clear_charging_profile(self, id=None, criteria=None, delay=0):
        await asyncio.sleep(delay)
        args = {}
        if id is not None:
            try:
                args['charging_profile_id'] = int(id)
            except ValueError:
                #  passing _ or some other non-int nonsense means search by id isn't needed
                pass
        if criteria is not None:
            args['charging_profile_criteria'] = json.loads(criteria)
        request = call.ClearChargingProfilePayload(**args)
        return await self.send_request("clear charging profile", request)

    async def send_set_charging_profile(self, evse=0, profile="", delay=0):
        await asyncio.sleep(delay)
        request = call.SetChargingProfilePayload(
            evse_id=int(evse),
            charging_profile=json.loads(profile)
        )
        return await self.send_request("set charging profile", request)

    async def send_set_charging_profile_orig(self, delay=0):
        await asyncio.sleep(delay)
        request = call.SetChargingProfilePayload(
            evse_id=1,
            charging_profile=ChargingProfileType(
                id=3333,
                stack_level=5,
                charging_profile_purpose=ChargingProfilePurposeType.tx_profile,
                charging_profile_kind=ChargingProfileKindType.recurring,
                transaction_id=self.current_transaction_id,
                recurrency_kind=RecurrencyKindType.daily,
                charging_schedule=[ChargingScheduleType(
                    id=3334,
                    start_schedule='2022-01-01T00:00:00.000Z',
                    duration=86400,
                    charging_rate_unit='W',
                    charging_schedule_period=[ChargingSchedulePeriodType(
                        start_period=0,
                        limit=0
                    )]
                )]
            )
        )
        return await self.send_request("set charging profile", request)

    async def send_get_composite_schedule(self, delay=0):
        await asyncio.sleep(delay)
        request = call.GetCompositeSchedulePayload(
            duration=86400,
            evse_id=0
        )
        return await self.send_request("get composite schedule", request)

    async def send_set_variables(self, component='OCPPCommCtrlr', key='HeartbeatInterval', value='900', delay=0):
        await asyncio.sleep(delay)
        variable_data = [
            self.variable_data(component, key, value)
        ]
        request = call.SetVariablesPayload(
            set_variable_data=variable_data
        )
        return await self.send_request("set variables", request)

    def variable_data(self, component, variable, value):
        return SetVariableDataType(
            component=ComponentType(name=component),
            variable=VariableType(name=variable),
            attribute_value=value)

    async def send_get_variables(self, component, variable, delay=0):
        await asyncio.sleep(delay)
        request = call.GetVariablesPayload(
            get_variable_data=[
                GetVariableDataType(component=ComponentType(name=component), variable=VariableType(name=variable))
            ]
        )
        return await self.send_request("get variables", request)

    async def send_reset(self, reset_type=ResetType.immediate, evse_id=None, delay=0):
        await asyncio.sleep(delay)
        if evse_id is None:
            request = call.ResetPayload(type=reset_type)
        else:
            request = call.ResetPayload(type=reset_type, evse_id=evse_id)
        return await self.send_request("reset", request)

    async def send_change_availability(self, available=True, delay=0):
        await asyncio.sleep(delay)
        request = call.ChangeAvailabilityPayload(
            operational_status=(OperationalStatusType.operative if available else OperationalStatusType.inoperative),
        )
        return await self.send_request("change availability", request)

    async def send_get_installed_certificates(self, delay=0):
        await asyncio.sleep(delay)
        request = call.GetInstalledCertificateIdsPayload()
        return await self.send_request("get installed certificate ids", request)

    async def send_get_transaction_status(self, delay=0):
        await asyncio.sleep(delay)
        request = call.GetTransactionStatusPayload()
        return await self.send_request("get transaction status", request)

    async def send_set_network_profile_request(self, slot, version, url, security_profile, interface, delay=0):
        await asyncio.sleep(delay)
        request = call.SetNetworkProfilePayload(
            configuration_slot=int(slot),
            connection_data=NetworkConnectionProfileType(
                ocpp_version=version,
                ocpp_transport=OCPPTransportType.json,
                ocpp_csms_url=url,
                message_timeout=30,
                security_profile=int(security_profile),
                ocpp_interface=interface,
                apn=APNType(apn='stream.co.uk', apn_user_name='default', apn_password='void', apn_authentication='AUTO')
            )
        )
        return await self.send_request("set network profile", request)

    async def send_request_start_transaction(self, delay=0):
        await asyncio.sleep(delay)
        # evse_id should be optional but Alfen barfs if omitted
        request = call.RequestStartTransactionPayload(
            evse_id=1,
            remote_start_id=self.get_next_request_id(),
            id_token=IdTokenType(
                id_token='CENT2020',
                type=IdTokenEnumType.central,
            )
        )
        return await self.send_request("request start transaction", request)

    async def send_request_stop_transaction(self, delay=0):
        await asyncio.sleep(delay)
        request = call.RequestStopTransactionPayload(
            transaction_id=self.current_transaction_id
        )
        return await self.send_request("request stop transaction", request)

    async def send_remote_unlock(self, delay=0):
        await asyncio.sleep(delay)
        request = call.UnlockConnectorPayload(
            evse_id=1,
            connector_id=1
        )
        return await self.send_request("request unlock", request)

    async def send_set_variable_monitoring(self, delay=0):
        await asyncio.sleep(delay)
        request = call.SetVariableMonitoringPayload(
            set_monitoring_data=[
                SetMonitoringDataType(
                    type=MonitorType.periodic,
                    component=ComponentType(name='AlfenStation'),
                    variable=VariableType(name='APN-SignalStrength'),
                    value=60,
                    severity=8
                )
            ]
        )
        return await self.send_request("set variable monitoring", request)

    async def poll_variable(self, component=None, variable=None, delay=0, interval=60):
        await asyncio.sleep(delay)
        await self.send_get_variables(component=component, variable=variable, delay=0)
        if (component, variable, interval) in self.variables_to_poll:
            self.queue_task(self.poll_variable, component=component, variable=variable, interval=interval,
                            delay=interval)

    def start_polling(self, component, variable, interval):
        self.variables_to_poll.add((component, variable, int(interval)))
        self.queue_task(self.poll_variable, component=component, variable=variable, interval=int(interval),
                        delay=int(interval))

    def stop_polling(self, component, variable, interval):
        self.variables_to_poll.remove((component, variable, int(interval)))
