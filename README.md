# hive-ev-migration-experimental

Minimal OCPP server supporting 1.6 and 2.0.1 protocol, useful for quick and dirty investigation of real charger behaviour.

Originally derived from [https://github.com/mobilityhouse/ocpp/blob/master/examples/v16/central_system.py]

Please read the code before running it.  Not for use in production!

Author: Chris Malley

# Install

```shell
./install.sh
```

# Run

```shell
nohup ./start_server.sh >server.out 2>&1 &
```

# Admin UI

Starts a very basic websocket server and javascript UI on port 9001.