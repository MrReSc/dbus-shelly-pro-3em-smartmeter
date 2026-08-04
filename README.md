Shelly Pro 3EM into Victron DBUS
based on: https://github.com/fabian-lauer/dbus-shelly-3em-smartmeter

Installation:
```
wget https://github.com/MrReSc/dbus-shelly-pro-3em-smartmeter/archive/refs/heads/main.zip
unzip main.zip "dbus-shelly-pro-3em-smartmeter-main/*" -d /data
mv /data/dbus-shelly-pro-3em-smartmeter-main /data/dbus-shelly-pro-3em-smartmeter
chmod a+x /data/dbus-shelly-pro-3em-smartmeter/*.sh
/data/dbus-shelly-pro-3em-smartmeter/install.sh
rm main.zip
```

Local Fedora test:

```sh
sudo dnf install python3-dbus python3-gobject python3-requests dbus-daemon
./local-test.sh
```

Run the test from a Git checkout. The script initializes the `velib_python` submodule when needed, starts the unchanged driver on an isolated D-Bus session and displays the values read back from D-Bus. It does not use the laptop's system D-Bus.

Start the test while the Shelly is reachable. To verify the communication handling, disconnect the Shelly for more than two seconds and reconnect it. The test reports the D-Bus service disappearing and returning with current values. Press `Ctrl+C` to stop and display a summary.
