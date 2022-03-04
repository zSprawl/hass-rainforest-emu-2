import asyncio
import serial_asyncio
import itertools
import logging
from xml.etree import ElementTree
from serial import SerialException

#from .emu2_entities import *
from . import emu2_entities

LOGGER = logging.getLogger(__name__)

class Emu2:

    def __init__(
        self, 
        device
    ):
        self._device = device
        self._connected = False
        self._serial_loop_exit = False
        self._callback = None
        self._writer = None
        self._reader = None

        self._data = {}

    def get_data(self, klass):
        return self._data.get(klass.tag_name())

    def register_callback(self, callback):
        self._callback = callback

    def connected(self) -> bool:
        return self._connected

    # Exit the read loop
    def stop_serial(self) -> None:
        self._connected = False
        self._serial_loop_exit = True

    async def connect(self) -> bool:
        if self._connected == True:
            return True

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url = self._device,
                baudrate = 115200,
            )
        except SerialException as ex:
            LOGGER.error(ex)
            self._connected = False
        else:
            self._connected = True
        
        return self._connected

    async def serial_read(self):
        LOGGER.info("Starting serial_read loop")
        while True:
            # Wait until connected
            while await self.connect() == False:
                if self._serial_loop_exit == True:
                    return

                await asyncio.sleep(5)

            xml_str = ''
            while True:
                if self._serial_loop_exit == True:
                    return     
            
                try:
                    line = await self._reader.readline()
                    line = line.decode("utf-8").strip()

                    LOGGER.debug("received %s", line)
                    xml_str += line

                except SerialException as ex:
                    LOGGER.error("Error while reading serial device %s: %s", self._device, ex)
                    self._connected = False
                    break

                if line.startswith('</'):
                    self._process_reply(xml_str)
                    xml_str = ''

    def _process_reply(self, xml_str: str) -> None:
        try:
            wrapped = itertools.chain('<Root>', xml_str, '</Root>')
            root = ElementTree.fromstringlist(wrapped)
        except ElementTree.ParseError:
            LOGGER.error("Malformed XML: %s", xml_str)
            return

        for tree in root:
            response_type = tree.tag
            klass = emu2_entities.Entity.tag_to_class(response_type)
            if klass is None:
                LOGGER.debug("Unsupported tag: %s", response_type)
                continue

            self._data[response_type] = klass(tree)

            # trigger callback
            if self._callback is not None:
                LOGGER.debug("serial_read callback for response %s", response_type)
                self._callback(response_type, klass(tree))        

    # Issue a command to the device. Pass the command name as the first
    # argument, and any additional params as a dict. Will return immediately
    # unless the synchronous attribute on the library is true, in which case
    # it will return data when available, or None if the timeout has elapsed.
    async def issue_command(self, command, params = None, return_class = None) -> bool:
        if await self.connect() == False:
            return False

        root = ElementTree.Element('Command')
        name_field = ElementTree.SubElement(root, 'Name')
        name_field.text = command

        if params is not None:
            for k, v in params.items():
                if v is not None:
                    field = ElementTree.SubElement(root, k)
                    field.text = v

        bin_string = ElementTree.tostring(root)

        LOGGER.debug("XML out %s", ElementTree.dump(root))

        try:
            self._writer.write(bin_string)
        except SerialException as ex:
            return False

        return True

    # Convert boolean to Y/N for commands
    def _format_yn(self, value):
        if value is None:
            return None
        if value:
            return 'Y'
        else:
            return 'N'

    # Convert an integer into a hex string
    def _format_hex(self, num, digits=8):
        return "0x{:0{digits}x}".format(num, digits=digits)

    # Check if an event is a valid value
    def _check_valid_event(self, event, allow_none=True):
        enum = ['time', 'summation', 'billing_period', 'block_period',
                'message', 'price', 'scheduled_prices', 'demand']
        if allow_none:
            enum.append(None)
        if event not in enum:
            raise ValueError('Invalid event specified')

    # The following are convenience methods for sending commands. Commands
    # can also be sent manually using the generic issue_command method.

    #################################
    #         Raven Commands        #
    #################################

    async def restart(self):
        return await self.issue_command('restart')

    async def get_connection_status(self):
        return await self.issue_command('get_connection_status', return_class=emu2_entities.ConnectionStatus)

    async def get_device_info(self):
        return await self.issue_command('get_device_info', return_class=emu2_entities.DeviceInfo)

    async def get_schedule(self, mac=None, event=None):
        self._check_valid_event(event)
        opts = {'MeterMacId': mac, 'Event': event}
        return await self.issue_command('get_schedule', opts, return_class=emu2_entities.ScheduleInfo)

    async def set_schedule(self, mac=None, event=None, frequency=10, enabled=True):
        self._check_valid_event(event, allow_none=False)
        opts = {
            'MeterMacId': mac,
            'Event': event,
            'Frequency': self._format_hex(frequency),
            'Enabled': self._format_yn(enabled)
        }
        return await self.issue_command('set_schedule', opts)

    async def set_schedule_default(self, mac=None, event=None):
        self._check_valid_event(event)
        opts = {'MeterMacId': mac, 'Event': event}
        return await self.issue_command('set_schedule_default', opts)

    async def get_meter_list(self):
        return await self.issue_command('get_meter_list', return_class=emu2_entities.MeterList)

    ##########################
    #     Meter Commands     #
    ##########################

    async def get_meter_info(self, mac=None):
        opts = {'MeterMacId': mac}
        return await self.issue_command('get_meter_info', opts, return_class=emu2_entities.MeterInfo)

    async def get_network_info(self):
        return await self.issue_command('get_network_info', return_class=emu2_entities.NetworkInfo)

    async def set_meter_info(self, mac=None, nickname=None, account=None, auth=None, host=None, enabled=None):

        opts = {
            'MeterMacId': mac,
            'NickName': nickname,
            'Account': account,
            'Auth': auth,
            'Host': host,
            'Enabled': self._format_yn(enabled)
        }
        return await self.issue_command('set_meter_info', opts)

    ############################
    #       Time Commands      #
    ############################

    async def get_time(self, mac=None, refresh=True):
        opts = {'MeterMacId': mac, 'Refresh': self._format_yn(refresh)}
        return await self.issue_command('get_time', opts, return_class=emu2_entities.TimeCluster)

    async def get_message(self, mac=None, refresh=True):
        opts = {'MeterMacId': mac, 'Refresh': self._format_yn(refresh)}
        return await self.issue_command('get_message', opts, return_class=emu2_entities.MessageCluster)

    async def confirm_message(self, mac=None, message_id=None):
        if message_id is None:
            raise ValueError('Message id is required')

        opts = {'MeterMacId': mac, 'Id': self._format_hex(message_id)}
        return await self.issue_command('confirm_message', opts)

    #########################
    #     Price Commands    #
    #########################

    async def get_current_price(self, mac=None, refresh=True):
        opts = {'MeterMacId': mac, 'Refresh': self._format_yn(refresh)}
        return await self.issue_command('get_current_price', opts, return_class=emu2_entities.PriceCluster)

    # Price is in cents, w/ decimals (e.g. "24.373")
    async def set_current_price(self, mac=None, price="0.0"):

        parts = price.split(".", 1)
        if len(parts) == 1:
            trailing = 2
            price = int(parts[0])
        else:
            trailing = len(parts[1]) + 2
            price = int(parts[0] + parts[1])

        opts = {
            'MeterMacId': mac,
            'Price': self._format_hex(price),
            'TrailingDigits': self._format_hex(trailing, digits=2)
        }
        return await self.issue_command('set_current_price', opts)

    ###############################
    #   Simple Metering Commands  #
    ###############################

    async def get_instantaneous_demand(self, mac=None, refresh=True):
        opts = {'MeterMacId': mac, 'Refresh': self._format_yn(refresh)}
        return await self.issue_command('get_instantaneous_demand', opts, return_class=emu2_entities.InstantaneousDemand)

    async def get_current_summation_delivered(self, mac=None, refresh=True):
        opts = {'MeterMacId': mac, 'Refresh': self._format_yn(refresh)}
        return await self.issue_command('get_current_summation_delivered', opts, return_class=emu2_entities.CurrentSummationDelivered)

    async def get_current_period_usage(self, mac=None):
        opts = {'MeterMacId': mac}
        return await self.issue_command('get_current_period_usage', opts, return_class=emu2_entities.CurrentPeriodUsage)

    async def get_last_period_usage(self, mac=None):
        opts = {'MeterMacId': mac}
        return await self.issue_command('get_last_period_usage', opts, return_class=emu2_entities.LastPeriodUsage)

    async def close_current_period(self, mac=None):
        opts = {'MeterMacId': mac}
        return await self.issue_command('close_current_period', opts)

    async def set_fast_poll(self, mac=None, frequency=4, duration=20):
        opts = {
            'MeterMacId': mac,
            'Frequency': self._format_hex(frequency, digits=4),
            'Duration': self._format_hex(duration, digits=4)
        }
        return await self.issue_command('set_fast_poll', opts)