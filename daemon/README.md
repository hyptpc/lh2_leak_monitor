# LH2 service controls

Run from the project root (sudo asks for your password):

```bash
./daemon/install-service.sh  # Install/reload only; no backup or startup
./daemon/status.sh           # Read-only service state
./daemon/start.sh            # Start live monitoring; no boot enable
./daemon/stop.sh             # Stop monitoring; does not restore equipment
```

These scripts always select /home/sks/monitor_tools/lh2_leak_monitor.
Service definitions and controls belong to this tool's daemon/ directory.
