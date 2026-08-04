#!/usr/bin/env python
# vim: ts=2 sw=2 et

# import normal packages
import platform 
import logging
import logging.handlers
import sys
import os
import sys
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file

POLL_INTERVAL_MS = 500
HTTP_TIMEOUT = (0.5, 0.5)
DISCONNECT_AFTER_SECONDS = 2
monotonic_time = getattr(time, 'monotonic', time.time)

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class Phase:
  def __init__(self, voltage, current, power, total_act_energy, total_act_ret_energy):
    self.voltage = voltage
    self.current = current
    self.power = power
    self.total_act_energy = total_act_energy / 1000
    self.total_act_ret_energy = total_act_ret_energy / 1000



class DbusShelly3emService:
  def __init__(self, paths, productname='Shelly Pro 3EM', connection='Shelly Pro 3EM HTTP JSON service'):
    config = self._getConfig()
    self._paths = paths
    self._deviceinstance = int(config['DEFAULT']['DeviceInstance'])
    self._customname = config['DEFAULT']['CustomName']
    self._role = config['DEFAULT']['Role']
    self._productname = productname
    self._connection = connection

    if self._role != 'grid':
        raise ValueError("Configured Role: %s is not supported. Only grid is supported." % self._role)

    self._servicename = 'com.victronenergy.grid'
    self._productid = 45069
    self._position = self._getShellyPosition()
    self._serial = None
    self._updateIndex = 0
    self._lastPower = None
    self._dbusservice = None

    logging.debug("%s /DeviceInstance = %d" % (self._servicename, self._deviceinstance))

    # last update
    self._lastUpdate = 0
    self._lastSuccessfulUpdate = None
    self._communicationError = False

    # add _update function 'timer'
    gobject.timeout_add(POLL_INTERVAL_MS, self._update)
    
    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)


  def _createDbusService(self, measurements):
    dbusservice = VeDbusService("{}.http_{:02d}".format(self._servicename, self._deviceinstance), register=False)

    # Create the management objects, as specified in the ccgx dbus-api document
    dbusservice.add_path('/Mgmt/ProcessName', __file__)
    dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    dbusservice.add_path('/Mgmt/Connection', self._connection)

    # Create the mandatory objects
    dbusservice.add_path('/DeviceInstance', self._deviceinstance)
    dbusservice.add_path('/ProductId', self._productid)
    dbusservice.add_path('/DeviceType', 345) # found on https://www.sascha-curth.de/projekte/005_Color_Control_GX.html#experiment - should be an ET340 Engerie Meter
    dbusservice.add_path('/ProductName', self._productname)
    dbusservice.add_path('/CustomName', self._customname)
    dbusservice.add_path('/Latency', None)
    dbusservice.add_path('/FirmwareVersion', 0.2)
    dbusservice.add_path('/HardwareVersion', 0)
    dbusservice.add_path('/Connected', 1)
    dbusservice.add_path('/Role', self._role)
    dbusservice.add_path('/Position', self._position)
    dbusservice.add_path('/Serial', self._serial)
    dbusservice.add_path('/UpdateIndex', self._updateIndex)

    # add path values to dbus
    for path, settings in self._paths.items():
      dbusservice.add_path(path, measurements[path], gettextcallback=settings['textformat'])

    # Erst NACH allen add_path-Aufrufen registrieren
    dbusservice.register()
    return dbusservice
 
  def _getShellySerial(self, meter_data):
    if not meter_data['sys']['mac']:
        raise ValueError("Response does not contain 'mac' attribute")
    
    serial = meter_data['sys']['mac']
    return serial
 
 
  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;
 
 
  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']
    
    if not value: 
        value = 0
    
    return int(value)
 
 
  def _getShellyPosition(self):
    config = self._getConfig()
    value = config['DEFAULT']['Position']
    
    if not value: 
        value = 0
    
    return int(value)
 
 
  def _getShellyStatusUrl(self):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']
    
    if accessType == 'OnPremise': 
        URL = "http://%s:%s@%s/rpc/Shelly.GetStatus" % (config['ONPREMISE']['Username'], config['ONPREMISE']['Password'], config['ONPREMISE']['Host'])
        URL = URL.replace(":@", "")
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
    
    return URL
    
 
  def _getShellyData(self):
    URL = self._getShellyStatusUrl()
    meter_r = requests.get(url = URL, timeout=HTTP_TIMEOUT)
    
    # check for response
    if not meter_r:
        raise ConnectionError("No response from Shelly Pro 3EM - %s" % (URL))
    
    meter_data = meter_r.json()     
    
    # check for Json
    if not meter_data:
        raise ValueError("Converting response to JSON failed")
    
    return meter_data
 
 
  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._lastPower))
    logging.info("--- End: sign of life ---")
    return True
 

  def _getPhases(self, meter_data):
    em0 = meter_data['em:0']
    emdata0 = meter_data['emdata:0']
    l1 = Phase(em0['a_voltage'], em0['a_current'], em0['a_act_power'], emdata0['a_total_act_energy'], emdata0['a_total_act_ret_energy'])
    l2 = Phase(em0['b_voltage'], em0['b_current'], em0['b_act_power'], emdata0['b_total_act_energy'], emdata0['b_total_act_ret_energy'])
    l3 = Phase(em0['c_voltage'], em0['c_current'], em0['c_act_power'], emdata0['c_total_act_energy'], emdata0['c_total_act_ret_energy'])

    phases = [l1, l2, l3]

    # Remap the L1 phase?
    try:
      config = self._getConfig()
      remapL1 = int(config['ONPREMISE']['L1Position'])
    except KeyError:
      remapL1 = 1

    if remapL1 > 1:
      old_l1 = phases[0]
      phases[0] = phases[remapL1 - 1]
      phases[remapL1-1] = old_l1

    return phases


  def _getMeasurements(self, meter_data, phases):
    return {
      '/Ac/Power': meter_data['em:0']['total_act_power'],
      '/Ac/L1/Voltage': phases[0].voltage,
      '/Ac/L2/Voltage': phases[1].voltage,
      '/Ac/L3/Voltage': phases[2].voltage,
      '/Ac/L1/Current': phases[0].current,
      '/Ac/L2/Current': phases[1].current,
      '/Ac/L3/Current': phases[2].current,
      '/Ac/L1/Power': phases[0].power,
      '/Ac/L2/Power': phases[1].power,
      '/Ac/L3/Power': phases[2].power,
      '/Ac/L1/Energy/Forward': phases[0].total_act_energy,
      '/Ac/L2/Energy/Forward': phases[1].total_act_energy,
      '/Ac/L3/Energy/Forward': phases[2].total_act_energy,
      '/Ac/L1/Energy/Reverse': phases[0].total_act_ret_energy,
      '/Ac/L2/Energy/Reverse': phases[1].total_act_ret_energy,
      '/Ac/L3/Energy/Reverse': phases[2].total_act_ret_energy,
      '/Ac/Energy/Forward': sum(phase.total_act_energy for phase in phases),
      '/Ac/Energy/Reverse': sum(phase.total_act_ret_energy for phase in phases),
    }


  def _updateDbusValues(self, measurements):
    for path, value in measurements.items():
      self._dbusservice[path] = value
    self._dbusservice['/UpdateIndex'] = self._updateIndex


  def _update(self):   
    try:
       try:
          #get data from Shelly Pro 3EM
          meter_data = self._getShellyData()
          phases = self._getPhases(meter_data)
          measurements = self._getMeasurements(meter_data, phases)
          if self._dbusservice is None:
             serial = self._getShellySerial(meter_data)
       except (ValueError, KeyError, TypeError, requests.exceptions.RequestException, ConnectionError) as e:
          if not self._communicationError:
             logging.warning('Error getting data from Shelly - check network or Shelly status. Details: %s', e)
             self._communicationError = True

          if (self._dbusservice is not None and
              self._lastSuccessfulUpdate is not None and
              monotonic_time() - self._lastSuccessfulUpdate > DISCONNECT_AFTER_SECONDS):
             self._dbusservice['/Connected'] = 0
             self._dbusservice.__del__()
             self._dbusservice = None
             logging.error('No successful measurement from Shelly for more than %s seconds. Setting /Connected to 0 and disconnecting D-Bus service.', DISCONNECT_AFTER_SECONDS)

          return True
       
       self._updateIndex = (self._updateIndex + 1) % 256
       if self._dbusservice is None:
          self._serial = serial
          self._dbusservice = self._createDbusService(measurements)
       else:
          self._updateDbusValues(measurements)
          if self._dbusservice['/Connected'] != 1:
             self._dbusservice['/Connected'] = 1

       self._lastPower = measurements['/Ac/Power']
       self._lastSuccessfulUpdate = monotonic_time()
       if self._communicationError:
          logging.info('Communication with Shelly restored.')
          self._communicationError = False

       
       #logging
       logging.debug("House Consumption (/Ac/Power): %s" % (self._dbusservice['/Ac/Power']))
       logging.debug("House Forward (/Ac/Energy/Forward): %s" % (self._dbusservice['/Ac/Energy/Forward']))
       logging.debug("House Reverse (/Ac/Energy/Reverse): %s" % (self._dbusservice['/Ac/Energy/Reverse']))
       logging.debug("---");

       #update lastupdate vars
       self._lastUpdate = time.time()
    except Exception as e:
       logging.critical('Error at %s', '_update', exc_info=e)
       
    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True
 
def getLogLevel():
  config = configparser.ConfigParser()
  config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
  logLevelString = config['DEFAULT']['LogLevel']
  
  if logLevelString:
    level = logging.getLevelName(logLevelString)
  else:
    level = logging.INFO
    
  return level


def main():
  #configure logging
  logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=getLogLevel(),
                            handlers=[
                                logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                logging.StreamHandler()
                            ])
 
  try:
      logging.info("Start");
  
      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)
     
      #formatting 
      _kwh = lambda p, v: (str(round(v, 2)) + ' kWh')
      _a = lambda p, v: (str(round(v, 1)) + ' A')
      _w = lambda p, v: (str(round(v, 1)) + ' W')
      _v = lambda p, v: (str(round(v, 1)) + ' V')   
     
      #start our main-service
      pvac_output = DbusShelly3emService(
        paths={
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh}, # energy bought from the grid
          '/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh}, # energy sold to the grid
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          
          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L3/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L2/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L3/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L1/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
          '/Ac/L2/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
          '/Ac/L3/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
        })
     
      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()            
  except (ValueError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
    logging.critical('Error in main type %s', str(e))
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()
