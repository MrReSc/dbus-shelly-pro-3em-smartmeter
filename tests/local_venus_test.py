#!/usr/bin/env python3

import configparser
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import dbus


BUS_ITEM_INTERFACE = 'com.victronenergy.BusItem'
POLL_INTERVAL_SECONDS = 0.5
TABLE_MAX_COLUMNS = 118
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


def printable(value):
  if isinstance(value, (list, tuple)) and not value:
    return '[]'
  return str(value).replace('\r', '\\r').replace('\n', '\\n')


def dbus_type(value):
  return type(value).__name__


def shortened(value, width):
  if len(value) <= width:
    return value
  if width <= 3:
    return value[:width]
  return value[:width - 3] + '...'


def table_lines(items, terminal_width=None):
  rows = []
  for path in sorted(items, key=str):
    item = items[path]
    item_value = item['Value']
    rows.append((
      str(path),
      printable(item_value),
      printable(item['Text']),
      dbus_type(item_value),
    ))

  headers = ('Path', 'Value', 'Text', 'Type')
  if terminal_width is None:
    terminal_width = shutil.get_terminal_size(fallback=(TABLE_MAX_COLUMNS, 24)).columns
  table_width = min(max(terminal_width, 72), TABLE_MAX_COLUMNS)

  path_width = min(max([len(headers[0])] + [len(row[0]) for row in rows]), 24)
  type_width = min(max([len(headers[3])] + [len(row[3]) for row in rows]), 10)
  value_and_text_width = table_width - path_width - type_width - 9
  value_width = value_and_text_width // 2
  text_width = value_and_text_width - value_width
  widths = (path_width, value_width, text_width, type_width)

  def format_row(row):
    return ' | '.join(shortened(column, widths[index]).ljust(widths[index]) for index, column in enumerate(row))

  separator = '-+-'.join('-' * width for width in widths)
  return [format_row(headers), separator] + [format_row(row) for row in rows]


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


def test_summary(state):
  return 'startup=%s  updates=%s  disconnect=%s  recovery=%s' % (
    'PASS' if state['online'] else 'WAITING',
    'PASS' if state['updates'] else 'WAITING',
    'PASS' if state['disconnected'] else 'NOT TESTED',
    'PASS' if state['recovered'] else 'NOT TESTED',
  )


class Output:
  def __init__(self):
    self._interactive = sys.stdout.isatty()
    self._last_event = 'Waiting for the driver to register its D-Bus service.'

  def render(self, service_name, status, state, items=None):
    if self._interactive:
      lines = [
        'Local Venus D-Bus view',
        'Service:   %s' % service_name,
        'Interface: %s' % BUS_ITEM_INTERFACE,
        'Status:    %s    Time: %s    Paths: %d' % (
          status,
          time.strftime('%H:%M:%S'),
          len(items) if items is not None else 0,
        ),
        'Tests:     %s' % test_summary(state),
        'Last event: %s' % self._last_event,
        'Disconnect the Shelly for more than 2 seconds, reconnect it, or press Ctrl+C to stop.',
        '',
      ]
      if items is None:
        lines.append('No D-Bus paths are available while the service is %s.' % status)
      else:
        lines.extend(table_lines(items))
      sys.stdout.write('\033[2J\033[H' + '\n'.join(lines) + '\n')
      sys.stdout.flush()
    elif items is None:
      print('%s  %s  no D-Bus paths available' % (time.strftime('%H:%M:%S'), status), flush=True)
    else:
      print(live_line(items), flush=True)

  def snapshot(self, title, service_name, state, items):
    if self._interactive:
      return
    print('', flush=True)
    print('D-Bus snapshot: %s' % title, flush=True)
    print('Service: %s' % service_name, flush=True)
    print('Interface: %s' % BUS_ITEM_INTERFACE, flush=True)
    print('Paths: %d' % len(items), flush=True)
    print('Tests: %s' % test_summary(state), flush=True)
    for line in table_lines(items):
      print(line, flush=True)
    print('', flush=True)

  def event(self, text):
    self._last_event = text
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

        previous_online = False
        if state['online']:
          output.render(service_name, 'OFFLINE', state)
        else:
          output.render(service_name, 'STARTING', state)
        time.sleep(POLL_INTERVAL_SECONDS)
        continue

      validate_service(items)
      update_index = int(value(items, '/UpdateIndex'))
      startup_snapshot = False
      recovery_snapshot = False

      if not state['online']:
        output.event('[PASS] Service registered with all required paths and /Connected=1.')
        state['online'] = True
        startup_snapshot = True
      elif not previous_online and state['disconnected']:
        output.event('[PASS] Service recovered with current values and /Connected=1.')
        state['recovered'] = True
        recovery_snapshot = True

      if last_update_index is not None and update_index != last_update_index and not state['updates']:
        output.event('[PASS] UpdateIndex changes with successful measurements.')
        state['updates'] = True

      last_update_index = update_index
      previous_online = True
      if startup_snapshot:
        output.snapshot('startup', service_name, state, items)
      elif recovery_snapshot:
        output.snapshot('recovery', service_name, state, items)
      output.render(service_name, 'ONLINE', state, items)
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
