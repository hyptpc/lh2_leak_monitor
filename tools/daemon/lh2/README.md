# LH2 service controls

Run from the project root (sudo asks for your password):

```bash
./tools/daemon/lh2/install-service.sh  # Install/reload only; no backup or startup
./tools/daemon/lh2/status.sh           # Read-only service state
./tools/daemon/lh2/start.sh            # Start live monitoring; no boot enable
./tools/daemon/lh2/stop.sh             # Stop monitoring; does not restore equipment
```

These scripts always select /home/sks/monitor_tools/lh2_leak_monitor.
The scripts in the parent directory are legacy multi-monitor controls with
different boot-enable/backup behavior. Use this directory for this installation.
