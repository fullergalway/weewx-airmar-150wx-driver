#!/usr/bin/env python


from __future__ import with_statement
import serial
import syslog
import time

import weewx.drivers

DRIVER_NAME = 'Airmar'
DRIVER_VERSION = '0.26'

INHG_PER_BAR = 29.5333727
METER_PER_FOOT = 0.3048
MILE_PER_KM = 0.621371

DEBUG_SERIAL = 0

def loader(config_dict, _):
    return Airmar(**config_dict[DRIVER_NAME])

def confeditor_loader():
    return AirmarConfEditor()


DEFAULT_PORT = '/dev/ttyS0'

def logmsg(level, msg):
    syslog.syslog(level, 'airmar: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)


class Airmar(weewx.drivers.AbstractDevice):
    """weewx driver that communicates with an Airmar Weather Station

    model: station model, e.g., 'Airmar 150WX'
    [Optional. Default is 'Airmar']

    port - serial port
    [Required. Default is /dev/ttyS0]

    max_tries - how often to retry serial communication before giving up
    [Optional. Default is 10]
    """
    def __init__(self, **stn_dict):
        self.model = stn_dict.get('model', 'Airmar')
        self.port = stn_dict.get('port', DEFAULT_PORT)
        self.max_tries = int(stn_dict.get('max_tries', 10))
        self.retry_wait = int(stn_dict.get('retry_wait', 10))
        self.last_rain = None

        global DEBUG_SERIAL
        DEBUG_SERIAL = int(stn_dict.get('debug_serial', 0))

        loginf('driver version is %s' % DRIVER_VERSION)
        loginf('using serial port %s' % self.port)
        self.station = Station(self.port)
        self.station.open()

    def closePort(self):
        if self.station is not None:
            self.station.close()
            self.station = None

    @property
    def hardware_name(self):
        return self.model

    def genLoopPackets(self):
        while True:
            packet = {'dateTime': int(time.time() + 0.5),
                      'usUnits': weewx.US}
            readings = self.station.get_readings_with_retry(self.max_tries,
                                                            self.retry_wait)
            #data = Station.parse_readings(readings)
            data = self.station.parse_readings(readings)
            packet.update(data)
            self._augment_packet(packet)
            yield packet

    def _augment_packet(self, packet):
        # calculate the rain delta from rain total
        if self.last_rain is not None:
            packet['rain'] = packet['long_term_rain'] - self.last_rain
        else:
            packet['rain'] = None
        self.last_rain = packet['long_term_rain']

        # no wind direction when wind speed is zero
        if 'windSpeed' in packet and not packet['windSpeed']:
            packet['windDir'] = None

class Station(object):
    def __init__(self, port):
        self.port = port
        self.baudrate = 4800
        self.timeout = 3 # seconds
        self.serial_port = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, _, value, traceback):
        self.close()

    def open(self):
        logdbg("open serial port %s" % self.port)
        if "://" in self.port:
           self.serial_port = serial.serial_for_url(self.port,
                                baudrate=self.baudrate,timeout=self.timeout)
        else:
          self.serial_port = serial.Serial(self.port, self.baudrate,
                                         timeout=self.timeout)

    def close(self):
        if self.serial_port is not None:
            logdbg("close serial port %s" % self.port)
            self.serial_port.close()
            self.serial_port = None

    def get_readings(self):
        buf = self.serial_port.readline()
        if DEBUG_SERIAL:
            logdbg("station said: %s" % buf)
        buf = buf.strip() # FIXME: is this necessary?
        return buf

    def validate_string(self, buf):
        if buf[0:1] != '$':
            loginf("Unexpected header byte '%s'" % buf[0:1])
        if buf[-3:-2] != '*':
            loginf("Unexpected footer byte '%s'" % buf[-2:])
        [mess, cs]  = buf.split("*")
        mess = mess[1:]
        cs_new = 0
        for d in mess:
            cs_new = cs_new ^ ord(d)
        cs_new = "%2X" % cs_new
        if cs_new != cs and cs_new != "0%s" % cs:
            loginf("Unexpected checksum error %s, %s" % (cs_new, cs))
        return buf

    def get_readings_with_retry(self, max_tries=5, retry_wait=10):
        for ntries in range(0, max_tries):
            try:
                buf = self.get_readings()
                self.validate_string(buf)
                return buf
            except (serial.serialutil.SerialException, weewx.WeeWxIOError), e:
                loginf("Failed attempt %d of %d to get readings: %s" %
                       (ntries + 1, max_tries, e))
                time.sleep(retry_wait)
        else:
            msg = "Max retries (%d) exceeded for readings" % max_tries
            logerr(msg)
            raise weewx.RetriesExceeded(msg)

    def calc_WCHR(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "windchill_rel"
        except (ValueError):
            loginf("Wrong data format for windchill_rel '%s'" % buf[idx-2])
        return data
    
    def calc_WCHT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "windchill"
        except (ValueError):
            loginf("Wrong data format for widnchill '%s'" % buf[idx-2])
        return data
    
    def calc_HINX(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "heatindex"
        except (ValueError):
            loginf("Wrong data format for heatindex '%s'" % buf[idx-2])
        return data
    
    def calc_STNP(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * INHG_PER_BAR
            data['name'] = "pressure"
        except (ValueError):
            loginf("Wrong data format for pressure '%s'" % buf[idx-2])
        return data
    
    def calc_PTCH(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "pitch"
        except (ValueError):
            loginf("Wrong data format for ptich '%s'" % buf[idx-2])
        return data
    
    def calc_ROLL(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "roll"
        except (ValueError):
            loginf("Wrong data format for roll '%s'" % buf[idx-2])
        return data

    def calc_XACC(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "x_accel"
        except (ValueError):
            loginf("Wrong data format for x_accel '%s'" % buf[idx-2])
        return data

    def calc_YACC(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "y_accel"
        except (ValueError):
            loginf("Wrong data format for y_accel '%s'" % buf[idx-2])
        return data

    def calc_ZACC(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "z_accel"
        except (ValueError):
            loginf("Wrong data format for z_accel '%s'" % buf[idx-2])
        return data
    
    def calc_RRAT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "rollRate"
        except (ValueError):
            loginf("Wrong data format for rollRate '%s'" % buf[idx-2])
        return data
    
    def calc_PRAT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "pitchRate"
        except (ValueError):
            loginf("Wrong data format for pitchRate '%s'" % buf[idx-2])
        return data
    
    def calc_YRAT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "yawRate"
        except (ValueError):
            loginf("Wrong data format for yawRate '%s'" % buf[idx-2])
        return data
    
    def calc_RRTR(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "rollRate_raw"
        except (ValueError):
            loginf("Wrong data format for rollRate_raw '%s'" % buf[idx-2])
        return data
    
    def calc_PRTR(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "pitchRate_raw"
        except (ValueError):
            loginf("Wrong data format for pitchRate_raw '%s'" % buf[idx-2])
        return data
    
    def calc_YRTR(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "yawRate_raw"
        except (ValueError):
            loginf("Wrong data format for yawRate_raw '%s'" % buf[idx-2])
        return data
    
    def calc_PLAT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "heatingTemp_plate"
        except (ValueError):
            loginf("Wrong data format for heatingTemp_plate '%s'" % buf[idx-2])
        return data
    
    def calc_CAPT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "heatingTemp_cap"
        except (ValueError):
            loginf("Wrong data format for heatingTemp_cap '%s'" % buf[idx-2])
        return data
    
    def calc_PLAV(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "heatingVoltage_pl"
        except (ValueError):
            loginf("Wrong data format for heatingVoltage_pl '%s'" % buf[idx-2])
        return data
    
    def calc_CAPV(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "heatingVoltage_cap"
        except (ValueError):
            loginf("Wrong data format for heatingVoltage_cap '%s'" % buf[idx-2])
        return data
    
    def calc_HUMT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "inTemp"
        except (ValueError):
            loginf("Wrong data format for inTemp '%s'" % buf[idx-2])
        return data
    
    def calc_BRDT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "mbTemp"
        except (ValueError):
            loginf("Wrong data format for mbTemp '%s'" % buf[idx-2])
        return data
    
    def calc_UPPT(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2]) * 1.8 + 32
            data['name'] = "upTemp"
        except (ValueError):
            loginf("Wrong data format for upTemp '%s'" % buf[idx-2])
        return data
    
    def calc_BRDV(self, buf, idx):
        data = dict()
        try:
            data['value'] = float(buf[idx-2])
            data['name'] = "supplyVoltage"
        except (ValueError):
            loginf("Wrong data format for supplyVoltage '%s'" % buf[idx-2])
        return data

    #@staticmethod
    def parse_readings(self, raw):
        """Airmar.......
        """
        print raw
        data = dict()
        yx_data = dict()
        data['long_term_rain'] = None
        (interm, cs) = raw.split("*")
        buf = interm.split(",")
        if buf[0] == '$HCHDG':
            try:
                data['heading_magn'] = float(buf[1])
                data['deviation_magn'] = float(buf[2])
                data['variation_magn'] = float(buf[4])
            except (ValueError):
                loginf("Wrong data format for $HCHDG '%s, %s, %s'" % (buf[1], buf[2], buf[4]))
            if buf[3] == 'E':
                data['deviation_magn'] *= -1 #?????????????
            if buf[5] == 'E':
                data['variation_magn'] *= -1 #?????????????
        elif buf[0] == '$HCHDT':
            try:
                data['heading_true'] = float(buf[1])
            except (ValueError):
                loginf("Wrong data format for $HCHDT '%s'" % buf[1])
        elif buf[0] == '$WIMDA':
            try:
                data['altimeter'] = float(buf[1])
                data['outTemp'] = float(buf[5]) * 1.8 + 32
                data['outHumidity'] = float(buf[9])
                data['dewpoint'] = float(buf[11]) * 1.8 + 32
                data['windDir_true_mda'] = float(buf[13])
                data['windDir_magn_mda'] = float(buf[15])
                data['windSpeed_mda'] = float(buf[17]) / 1.15077945 #Wind speed, mph
            except (ValueError):
                loginf("Wrong data format for $WIMDA '%s, %s, %s, %s, %s, %s, %s'" % (buf[1], buf[5], buf[9], buf[11], buf[13], buf[15], buf[17]))
        elif buf[0] == '$WIMWD':
            try:
                data['windDir_true_mwd'] = float(buf[1])
                data['windDir_magn_mwd'] = float(buf[3])
                data['windSpeed_mwd'] = float(buf[5]) / 1.15077945
            except (ValueError):
                loginf("Wrong data format for $WIMWD '%s, %s, %s'" % (buf[1], buf[3], buf[5]))
        elif buf[0] == '$WIMWV':
            if buf[5] == 'A':
                if buf[2] == 'R':
                    try:
                        data['windAngle_rel_mwv'] = float(buf[1])
                        data['windSpeed_rel_mwv'] = float(buf[3]) / 1.15077945
                    except (ValueError):
                        loginf("Wrong data format for $WIMWV A-R '%s, %s'" % (buf[1], buf[3]))
                elif buf[2] == 'T':
                    try:
                        data['windAngle_theor_mwv'] = float(buf[1])
                        data['windSpeed_theor_mwv'] = float(buf[3]) / 1.15077945
                    except (ValueError):
                        loginf("Wrong data format for $WIMWV A-T '%s, %s'" % (buf[1], buf[3]))
        elif buf[0] == '$TIROT':
            if buf[2] == 'A':
                try:
                    data['tiRot'] = float(buf[1])
                except (ValueError):
                    loginf("Wrong data format for $TIROT '%s'" % buf[1])
        elif buf[0] == '$HCTHS':
            if buf[2] == 'A':
                try:
                    data['true_north_heading'] = float(buf[1])
                except (ValueError):
                    loginf("Wrong data format for $HCTHS '%s'" % buf[1])
        elif buf[0] == '$WIVWR':
            try:
                data['windAngle_rel_vess'] = float(buf[1])
                data['windSpeed_rel_vess'] = float(buf[3]) / 1.15077945
            except (ValueError):
                    loginf("Wrong data format for $WIVWR '%s, %s'" % (buf[1], buf[3]))
            if buf[2] == 'R': #R = right
                data['windAngle_rel_vess'] *= -1 #???????????????????
        elif buf[0] == '$WIVWT':
            try:
                data['windAngle_true_vess'] = float(buf[1])
                data['windSpeed_true_vess'] = float(buf[3]) / 1.15077945
            except (ValueError):
                    loginf("Wrong data format for $WIVWT '%s, %s'" % (buf[1], buf[3]))
            if buf[2] == 'R': #R = right
                data['windAngle_true_vess'] *= -1 #???????????????????????
        elif buf[0] == '$YXXDR':
            for idx in [4, 8, 12, 16]:
                yx_data.clear()
                try:
                    typestr = buf[idx]
                    yx_data = getattr(self, 'calc_'+typestr)(buf, idx)
                    if 'value' in yx_data:
                        data[yx_data['name']] = yx_data['value']
                except (IndexError):
                    break
        elif buf[0] == '$WIXDR':
            if buf[4] == 'RAIN':
                try:
                    data['long_term_rain'] = float(buf[2]) * 0.03937007874015748
                    data['duration_of_rain'] = float(buf[6])
                    data['rain_intensity'] = float(buf[10]) * 0.03937007874015748
                    data['peak_rain_intensity'] = float(buf[14]) * 0.03937007874015748
                except (ValueError):
                    loginf("Wrong data format for $WIXDR RAIN '%s, %s, %s, %s'" % (buf[2], buf[6], buf[10], buf[14]))
            if buf[4] == 'WNDA':
                try:
                    data['windAngle_unfilt'] = float(buf[2])
                    data['windSpeed_unfilt'] = float(buf[6]) / 1.15077945
                except (ValueError):
                    loginf("Wrong data format for $WIXDR WNDA '%s, %s'" % (buf[2], buf[6]))
        #else: #Processing of other data sentences
        if 'windDir_true_mwd' in data and data['windDir_true_mwd'] is not None:
            data['windDir'] = data['windDir_true_mwd']
        elif 'windDir_true_mda' in data and data['windDir_true_mda'] is not None:
            data['windDir'] = data['windDir_true_mda']

        if 'windSpeed_mwd' in data and data['windSpeed_mwd'] is not None:
            data['windSpeed'] = data['windSpeed_mwd']
        elif 'windSpeed_mda' in data and data['windSpeed_mda'] is not None:
            data['windSpeed'] = data['windSpeed_mda']

        return data

class AirmarConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[Airmar]
    # This section is for the Airmar series of weather stations.

    # Serial port such as /dev/ttyS0, /dev/ttyUSB0, or /dev/cuaU0
    port = /dev/ttyUSB0

    # The station model, e.g., Airmar 150WX
    model = Airmar

    # The driver to use:
    driver = weewx.drivers.airmar
"""

    def prompt_for_settings(self):
        print "Specify the serial port on which the station is connected, for"
        print "example /dev/ttyUSB0 or /dev/ttyS0."
        port = self._prompt('port', '/dev/ttyUSB0')
        return {'port': port}


# define a main entry point for basic testing of the station without weewx
# engine and service overhead.  invoke this as follows from the weewx root dir:
#
# PYTHONPATH=bin python bin/weewx/drivers/airmar.py

if __name__ == '__main__':
    import optparse

    usage = """%prog [options] [--help]"""

    syslog.openlog('airmar', syslog.LOG_PID | syslog.LOG_CONS)
    syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))
    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', dest='version', action='store_true',
                      help='display driver version')
    parser.add_option('--port', dest='port', metavar='PORT',
                      help='serial port to which the station is connected',
                      default=DEFAULT_PORT)
    (options, args) = parser.parse_args()

    if options.version:
        print "airmar driver version %s" % DRIVER_VERSION
        exit(0)

    with Station(options.port) as s:
        s.set_logger_mode()
        while True:
            print time.time(), s.get_readings()
