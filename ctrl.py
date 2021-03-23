"""Sample code to interact with a Netdata instance."""
import asyncio
import os
import re
import subprocess
import time
import numpy
import aiohttp
from netdata import Netdata


class IpmiMessage():
    def __init__(self, host, username, password, cmd):
        self.__cmd = cmd.split(' ')
        self.__host = host
        self.__username = username
        self.__password = password

        self.__command = [
                             'ipmitool',
                             '-I', 'lanplus',
                             '-H', self.__host,
                             '-U', self.__username,
                             '-P', self.__password,
                         ] + self.__cmd

    @property
    def out(self):
        return self.__out.decode('UTF-8')

    def send(self):
        try:
            self.__out = subprocess.check_output(
                self.__command
            )
        except subprocess.CalledProcessError as e:
            print(e)
            err = f'[IPMI] - There was an error in the IPMI message, is ipmitool installed?'
            raise ValueError(err)
        return self


class Server():
    def __init__(self, host=None, username=None, password=None):
        """
        Server class for communicating with Dell R710 server.
        Credentials are provided as kwargs but fallback to env variables.
        As a last resort, username and password are the default root/calvin.

        Keyword arguments:
        host -- The IP address or hostname of the remote server
        username -- The username for ipmi remote management - default root
        password -- The password for ipmi remote management - default calvin
        """

        self.__host = host
        self.__username = username
        self.__password = password

        if self.__host == None:
            self.__host = os.environ.get('IDRAC_HOST')

        if self.__username == None:
            self.__username = os.environ.get('IDRAC_USERNAME', 'root')

        if self.__password == None:
            self.__password = os.environ.get('IDRAC_PASSWORD', 'calvin')

        if self.__host == None or self.__username == None or self.__password == None:
            err = f"""
            [IPMI] - Credentials were not supplied.
            Set IDRAC_HOST, IDRAC_USERNAME and IDRAC_PASSWORD as env variables, or pass them as kwargs.
            """
            raise ValueError(err)

        self.__password_redacted = '*' * len(self.__password)
        print(f'[IPMI] - ({self.__host}) - User: {self.__username}, Password: {self.__password_redacted}')


    def do_cmd(self, cmd):
        """
        Run a ipmi command against the remote host
        Returns: the response (if any) from the server
        """
        m = IpmiMessage(cmd=cmd, host=self.__host, username=self.__username, password=self.__password)
        m.send()
        return m.out

    def get_power_status(self):
        """
        Gets the power status of the server
        Returns: the power status of the server
        """
        out = self.do_cmd(cmd='power status')
        if re.search('off', out):
            power_status = 'OFF'
        else:
            power_status = 'ON'
        return power_status

    def get_power_level(self):
        """return int watt"""
        out = self.do_cmd(cmd='sdr type current')
        for line in out.split("\n"):
            if "System Level" in line:
                return line.split("|")[-1]

    def get_temp(self):
        """
        Gets the ambient temperature from the server
        Returns: the ambient temperature from the server
        """
        out = self.do_cmd(cmd='sdr type temperature')
        ambient_line = None
        for line in out.split('\n'):
            if 'ambient' in line.lower():
                ambient_line = line

        if ambient_line is None:
            raise ValueError(f'[IPMI] - ({self.__host}) - Could not find ambient temp')

        r = re.search('[0-9]{2}', ambient_line)
        if r:
            temp = int(r.group(0))
        else:
            temp = 0
        return temp

    def set_fan_speed_auto(self):
        """
        Sets the server fan speed to automatic.
        Returns: the response (if any) from the server
        """
        print(f'[IPMI] - ({self.__host}) - Returning to auto fan control.')
        out = self.do_cmd(cmd='raw 0x30 0x30 0x01 0x01')
        return out

    def set_fan_speed_manual(self, fan_speed_pct):
        """
        Sets the server fan speed to the percentage given in fan_speed_pct argument
        Returns: the response (if any) from the server
        """
        print(f'[IPMI] - ({self.__host}) - Activating manual fan control, fan speed: {fan_speed_pct}%.')
        out = self.do_cmd(cmd='raw 0x30 0x30 0x01 0x00')
        out = self.do_cmd(cmd=f'raw 0x30 0x30 0x02 0xff {hex(fan_speed_pct)}')
        return out

    def get_fan_speed(self):
        """
        Gets the current fan speed from the server
        Returns: The servers current fan speed
        """
        out = self.do_cmd('sdr type fan')
        r = re.findall(r'(\d{3,})(?= RPM)', out)
        if len(r) == 0:
            r = [0]
        return int(max(r))


class ctrl():
    def __init__(self):
        self.data = numpy.zeros(8)
        self.prev = 0
        self.slope = {}
        self.curfan = 1
        self.curpower = 0
        self.s = Server(host='192.168.1.201', username='root', password='123456')
        self.target = 52
        self.cpu_power_level = 0
    def inject(self, data, cpu_util):
        self.curpower = self.s.get_power_level()
        self.cpu_power_level = cpu_util * 1.6
        # E5620x2 MAX TDP about 160w
        self.data = numpy.asarray(data)

    def run(self):
        self.curfan = min(max(self.curfan, 1), 100)
        self.s.set_fan_speed_manual(fan_speed_pct=int(self.curfan))

    def step(self):
        avgtemp = sum(self.data) / len(self.data)

        # detect emergency
        if self.data.any() > 68:
            self.curfan += 10
        else:
            if avgtemp > self.target:
                slope = avgtemp - self.prev
                if slope > 0:
                    self.curfan += 5 * slope
            else:
                self.curfan -= self.curfan * 10
        rest_power = min(20, self.curpower - self.cpu_power_level)
        self.curfan += rest_power/600

        self.run()
        self.prev = avgtemp  # AVG coretemp


async def main():
    c = ctrl()
    """Get the data from a Netdata instance."""
    async with aiohttp.ClientSession() as session:
        data = Netdata("1.ya1.top", loop, session,port=21197)
        # Get data for the CPU
        while True:
            time.sleep(1)
            cores = []
            cpu_util = 0
            await data.get_data("sensors.coretemp-isa-0001_temperature")
            for name in data.values:
                if 'Core' in name:
                    cores.append(data.values[name])
            await data.get_data("sensors.coretemp-isa-0000_temperature")
            for name in data.values:
                if 'Core' in name:
                    cores.append(data.values[name])

            await data.get_data("system.cpu")
            for name in data.values:
                if name != 'time':
                    cpu_util += data.values[name]
            c.inject(cores,cpu_util)
            c.step()


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
