#!/usr/bin/env python3

import configparser
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import dbus


BUS_ITEM_INTERFACE = 'com.victronenergy.BusItem'
POLL_INTERVAL_SECONDS = 0.5
STARTUP_TIMEOUT_SECONDS = 5
PROJECT_DIR = Path(__file__).resolve().parent.parent
DRIVER = PROJECT_DIR / 'dbus-shelly-3em-smartmeter.py'
CONFIG = PROJECT_DIR / 'config.ini'

REQUIRED_PATHS = {
  '/Connected',
  '/Serial',
  '/UpdateIndex',
  '/Ac/Power',
  '/Ac/Energy/Forward',
  '/Ac/Energy/Reverse',
  '/Ac/L1/Voltage',
  '/Ac/L2/Voltage',
  '/Ac/L3/Voltage',
  '/Ac/L1/Current',
  '/Ac/L2/Current',
  '/Ac/L3/Current',
  '/Ac/L1/Power',
  '/Ac/L2/Power',
  '/Ac/L3/Power',
  '/Ac/L1/Energy/Forward',
  '/Ac/L2/Energy/Forward',
  '/Ac/L3/Energy/Forward',
  '/Ac/L1/Energy/Reverse',
  '/Ac/L2/Energy/Reverse',
  '/Ac/L3/Energy/Reverse',
}


class TestFailure(Exception):
  pass


def get_service_name():
  config = configparser.ConfigParser()
  if not config.read(CONFIG):
    raise TestFailure('Could not read %s' % CONFIG)

  role = config['DEFAULT']['Role']
  if role != 'grid':
    raise TestFailure('Local test supports Role=grid, configured role is %s' % role)

  device_instance = int(config['DEFAULT']['DeviceInstance'])
  return 'com.victronenergy.grid.http_{:02d}'.format(device_instance)


def get_items(bus, service_name):
  root = bus.get_object(service_name, '/', introspect=False)
  interface = dbus.Interface(root, dbus_interface=BUS_ITEM_INTERFACE)
  return interface.GetItems(timeout=1)


def value(items, path):
  return items[path]['Value']


def number(items, path, decimals=1):
  try:
    return ('%.' + str(decimals) + 'f') % float(value(items, path))
  except (TypeError, ValueError):
    return '---'


def live_line(items):
  phases = []
  for phase in (1, 2, 3):
    phases.append(
      'L%d %s W %s V %s A' % (
        phase,
        number(items, '/Ac/L%d/Power' % phase),
        number(items, '/Ac/L%d/Voltage' % phase),
        number(items, '/Ac/L%d/Current' % phase),
      )
    )

  return (
    '%s  ONLINE  idx=%s  P=%s W  %s  E+=%s kWh  E-=%s kWh' % (
      time.strftime('%H:%M:%S'),
      value(items, '/UpdateIndex'),
      number(items, '/Ac/Power'),
      ' | '.join(phases),
      number(items, '/Ac/Energy/Forward', 3),
      number(items, '/Ac/Energy/Reverse', 3),
    )
  )


class Output:
  def __init__(self):
    self._interactive = sys.stdout.isatty()
    self._line_width = 0
    self._live_line_visible = False

  def live(self, text):
    if self._interactive:
      self._line_width = max(self._line_width, len(text))
      sys.stdout.write('\r' + text.ljust(self._line_width))
      sys.stdout.flush()
      self._live_line_visible = True
    else:
      print(text, flush=True)

  def event(self, text):
    if self._interactive and self._live_line_visible:
      sys.stdout.write('\n')
      self._live_line_visible = False
    print(text, flush=True)


def validate_service(items):
  missing = sorted(REQUIRED_PATHS.difference(str(path) for path in items))
  if missing:
    raise TestFailure('D-Bus service is missing paths: %s' % ', '.join(missing))
  if int(value(items, '/Connected')) != 1:
    raise TestFailure('/Connected is not 1 after service registration')
  if not str(value(items, '/Serial')):
    raise TestFailure('/Serial is empty')


def stop_driver(driver):
  if driver.poll() is not None:
    return
  driver.terminate()
  try:
    driver.wait(timeout=3)
  except subprocess.TimeoutExpired:
    driver.kill()
    driver.wait()


def stop_on_signal(signum, frame):
  raise KeyboardInterrupt


def run_test():
  service_name = get_service_name()
  output = Output()
  state = {
    'online': False,
    'updates': False,
    'disconnected': False,
    'recovered': False,
  }
  previous_online = False
  last_update_index = None
  startup_deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS

  output.event('Starting driver on private D-Bus: %s' % service_name)
  output.event('Disconnect the Shelly for more than 2 seconds, then reconnect it. Press Ctrl+C to stop.')

  driver = subprocess.Popen(
    [sys.executable, str(DRIVER)],
    cwd=str(PROJECT_DIR),
    env=os.environ.copy(),
    start_new_session=True,
  )

  try:
    bus = dbus.SessionBus()
    while True:
      exit_code = driver.poll()
      if exit_code is not None:
        raise TestFailure('Driver exited unexpectedly with status %d' % exit_code)

      try:
        items = get_items(bus, service_name)
      except dbus.DBusException:
        if previous_online:
          output.event('[PASS] D-Bus service disappeared after communication loss.')
          state['disconnected'] = True
        elif not state['online'] and time.monotonic() > startup_deadline:
          raise TestFailure('D-Bus service did not appear within %d seconds' % STARTUP_TIMEOUT_SECONDS)

        previous_online = False
        if state['online']:
          output.live('%s  OFFLINE  waiting for Shelly recovery' % time.strftime('%H:%M:%S'))
        else:
          output.live('%s  STARTING  waiting for D-Bus service' % time.strftime('%H:%M:%S'))
        time.sleep(POLL_INTERVAL_SECONDS)
        continue

      validate_service(items)
      update_index = int(value(items, '/UpdateIndex'))

      if not state['online']:
        output.event('[PASS] Service registered with all required paths and /Connected=1.')
        state['online'] = True
      elif not previous_online and state['disconnected']:
        output.event('[PASS] Service recovered with current values and /Connected=1.')
        state['recovered'] = True

      if last_update_index is not None and update_index != last_update_index and not state['updates']:
        output.event('[PASS] UpdateIndex changes with successful measurements.')
        state['updates'] = True

      last_update_index = update_index
      previous_online = True
      output.live(live_line(items))
      time.sleep(POLL_INTERVAL_SECONDS)
  except KeyboardInterrupt:
    output.event('Stopping local test...')
  finally:
    stop_driver(driver)

  output.event('Summary: startup=%s, updates=%s, disconnect=%s, recovery=%s' % (
    'PASS' if state['online'] else 'FAIL',
    'PASS' if state['updates'] else 'NOT SEEN',
    'PASS' if state['disconnected'] else 'NOT TESTED',
    'PASS' if state['recovered'] else 'NOT TESTED',
  ))
  return 0 if state['online'] and state['updates'] else 1


def main():
  signal.signal(signal.SIGTERM, stop_on_signal)
  try:
    return run_test()
  except TestFailure as error:
    print('[FAIL] %s' % error, file=sys.stderr)
    return 1


if __name__ == '__main__':
  sys.exit(main())
