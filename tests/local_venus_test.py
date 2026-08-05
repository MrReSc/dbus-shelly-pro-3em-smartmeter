#!/usr/bin/env python3

import configparser
import importlib.util
import math
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
FIXTURE_DEVICE_INSTANCE = 99
FIXTURE_SERVICE_NAME = 'com.victronenergy.grid.http_{:02d}'.format(FIXTURE_DEVICE_INSTANCE)

POWER_FACTOR_PATHS = (
  '/Ac/PowerFactor',
  '/Ac/L1/PowerFactor',
  '/Ac/L2/PowerFactor',
  '/Ac/L3/PowerFactor',
)

REQUIRED_PATHS = {
  '/Connected',
  '/ProductId',
  '/DeviceType',
  '/ErrorCode',
  '/Serial',
  '/UpdateIndex',
  '/Ac/Power',
  '/Ac/PowerFactor',
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
  '/Ac/L1/PowerFactor',
  '/Ac/L2/PowerFactor',
  '/Ac/L3/PowerFactor',
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


def is_invalid(value):
  return isinstance(value, (list, tuple)) and not value


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
  paths = {str(path) for path in items}
  missing = sorted(REQUIRED_PATHS.difference(paths))
  if missing:
    raise TestFailure('D-Bus service is missing paths: %s' % ', '.join(missing))
  if '/Position' in paths:
    raise TestFailure('/Position must not be published by the grid service')
  if int(value(items, '/ProductId')) != 0xB034:
    raise TestFailure('/ProductId is not 0xB034')
  if int(value(items, '/DeviceType')) != 0:
    raise TestFailure('/DeviceType is not 0')
  if int(value(items, '/ErrorCode')) != 0:
    raise TestFailure('/ErrorCode is not 0')
  if int(value(items, '/Connected')) != 1:
    raise TestFailure('/Connected is not 1 after service registration')
  if not str(value(items, '/Serial')):
    raise TestFailure('/Serial is empty')

  for path in POWER_FACTOR_PATHS:
    raw_value = value(items, path)
    if is_invalid(raw_value):
      continue
    try:
      power_factor = float(raw_value)
    except (TypeError, ValueError):
      raise TestFailure('%s is not numeric or invalid' % path)
    if not math.isfinite(power_factor) or not -1 <= power_factor <= 1:
      raise TestFailure('%s is outside the valid range [-1, 1]' % path)

    text = str(items[path]['Text'])
    try:
      float(text)
    except ValueError:
      raise TestFailure('%s text contains a unit or is not numeric: %s' % (path, text))
    decimal_part = text.lstrip('-').partition('.')[2]
    if len(decimal_part) > 3:
      raise TestFailure('%s text has more than three decimal places: %s' % (path, text))


def fixture_meter_data(invalid_total_power_factor=False):
  return {
    'sys': {'mac': 'FIXTURE-SHELLY-PRO-3EM'},
    'em:0': {
      'a_voltage': 111.1,
      'b_voltage': 222.2,
      'c_voltage': 233.3,
      'a_current': 1.1,
      'b_current': 2.2,
      'c_current': 3.3,
      'a_act_power': 11.0,
      'b_act_power': 22.0,
      'c_act_power': 33.0,
      'a_pf': 0.111,
      'b_pf': 0.222,
      'c_pf': 0.333,
      'total_act_power': -500.0,
      'total_aprt_power': 0.0 if invalid_total_power_factor else 625.0,
    },
    'emdata:0': {
      'a_total_act_energy': 1100.0,
      'b_total_act_energy': 2200.0,
      'c_total_act_energy': 3300.0,
      'a_total_act_ret_energy': 110.0,
      'b_total_act_ret_energy': 220.0,
      'c_total_act_ret_energy': 330.0,
      'total_act': 9876.5,
      'total_act_ret': 765.4,
    },
  }


def run_fixture_driver(fixture_name):
  if fixture_name not in ('valid', 'invalid-total-power-factor'):
    raise TestFailure('Unknown fixture: %s' % fixture_name)

  spec = importlib.util.spec_from_file_location('dbus_shelly_3em_smartmeter', DRIVER)
  driver_module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(driver_module)
  meter_data = fixture_meter_data(fixture_name == 'invalid-total-power-factor')

  class FixtureService(driver_module.DbusShelly3emService):
    def _getConfig(self):
      return {
        'DEFAULT': {
          'DeviceInstance': str(FIXTURE_DEVICE_INSTANCE),
          'CustomName': 'Shelly Pro 3EM fixture',
          'Role': 'grid',
          'SignOfLifeLog': '1',
        },
        'ONPREMISE': {'L1Position': '2'},
      }

    def _getShellyData(self):
      return meter_data

  driver_module.DbusShelly3emService = FixtureService
  driver_module.main()
  return 0


def wait_for_service(bus, service_name, driver, timeout=5):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    exit_code = driver.poll()
    if exit_code is not None:
      raise TestFailure('Fixture driver exited unexpectedly with status %d' % exit_code)
    try:
      return get_items(bus, service_name)
    except dbus.DBusException:
      time.sleep(0.1)
  raise TestFailure('Fixture service did not register within %s seconds' % timeout)


def assert_value(items, path, expected):
  try:
    actual = float(value(items, path))
  except (TypeError, ValueError):
    raise TestFailure('%s is not numeric' % path)
  if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9):
    raise TestFailure('%s is %s, expected %s' % (path, actual, expected))


def validate_fixture_values(items):
  expected_phase_values = {
    '/Ac/L1/Voltage': 222.2,
    '/Ac/L1/Current': 2.2,
    '/Ac/L1/Power': 22.0,
    '/Ac/L1/PowerFactor': 0.222,
    '/Ac/L1/Energy/Forward': 2.2,
    '/Ac/L1/Energy/Reverse': 0.22,
    '/Ac/L2/Voltage': 111.1,
    '/Ac/L2/Current': 1.1,
    '/Ac/L2/Power': 11.0,
    '/Ac/L2/PowerFactor': 0.111,
    '/Ac/L2/Energy/Forward': 1.1,
    '/Ac/L2/Energy/Reverse': 0.11,
    '/Ac/L3/Voltage': 233.3,
    '/Ac/L3/Current': 3.3,
    '/Ac/L3/Power': 33.0,
    '/Ac/L3/PowerFactor': 0.333,
    '/Ac/L3/Energy/Forward': 3.3,
    '/Ac/L3/Energy/Reverse': 0.33,
  }
  for path, expected in expected_phase_values.items():
    assert_value(items, path, expected)

  assert_value(items, '/Ac/Energy/Forward', 9.8765)
  assert_value(items, '/Ac/Energy/Reverse', 0.7654)
  phase_forward = sum(float(value(items, '/Ac/L%d/Energy/Forward' % phase)) for phase in (1, 2, 3))
  phase_reverse = sum(float(value(items, '/Ac/L%d/Energy/Reverse' % phase)) for phase in (1, 2, 3))
  if math.isclose(float(value(items, '/Ac/Energy/Forward')), phase_forward):
    raise TestFailure('Fixture forward total does not distinguish the Shelly total from the phase sum')
  if math.isclose(float(value(items, '/Ac/Energy/Reverse')), phase_reverse):
    raise TestFailure('Fixture reverse total does not distinguish the Shelly total from the phase sum')


def run_fixture_case(bus, fixture_name):
  driver = subprocess.Popen(
    [sys.executable, str(Path(__file__).resolve()), '--fixture-driver', fixture_name],
    cwd=str(PROJECT_DIR),
    env=os.environ.copy(),
    start_new_session=True,
  )
  try:
    items = wait_for_service(bus, FIXTURE_SERVICE_NAME, driver)
    validate_service(items)
    validate_fixture_values(items)
    if fixture_name == 'valid':
      assert_value(items, '/Ac/PowerFactor', -0.8)
    else:
      if not is_invalid(value(items, '/Ac/PowerFactor')):
        raise TestFailure('/Ac/PowerFactor is not an invalid D-Bus value')
      if str(items['/Ac/PowerFactor']['Text']) != '---':
        raise TestFailure('Invalid /Ac/PowerFactor text is not ---')
  finally:
    stop_driver(driver)


def run_fixture_tests():
  print('Running deterministic D-Bus fixtures...', flush=True)
  bus = dbus.SessionBus()
  run_fixture_case(bus, 'valid')
  print('[PASS] Identity, paths, total energy and phase remapping fixture.', flush=True)
  run_fixture_case(bus, 'invalid-total-power-factor')
  print('[PASS] Invalid total power factor fixture.', flush=True)


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
  if len(sys.argv) == 3 and sys.argv[1] == '--fixture-driver':
    return run_fixture_driver(sys.argv[2])

  fixtures_only = sys.argv[1:] == ['--fixtures-only']
  if sys.argv[1:] and not fixtures_only:
    print('Usage: %s [--fixtures-only]' % Path(__file__).name, file=sys.stderr)
    return 2

  signal.signal(signal.SIGTERM, stop_on_signal)
  try:
    run_fixture_tests()
    if fixtures_only:
      return 0
    return run_test()
  except TestFailure as error:
    print('[FAIL] %s' % error, file=sys.stderr)
    return 1


if __name__ == '__main__':
  sys.exit(main())
