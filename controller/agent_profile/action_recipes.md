# Quick action recipes

This cheat sheet helps you translate natural-language tasks into concrete controller calls.  It complements the main system prompt.

## Container lifecycle
- **List containers on default node**
  - `GET /lxc`
- **List containers on a specific node**
  - `GET /lxc?node=pve`
- **Start / stop a container**
  - `POST /lxc/start {"vmid": 101}`
  - `POST /lxc/stop {"vmid": 101}` (add `?force=true` only if graceful shutdown fails)
- **Create a new Debian LXC**
  ```json
  POST /lxc
  {
    "node": "pve",
    "vmid": 120,
    "hostname": "web-120",
    "cores": 2,
    "memory": 2048,
    "storage": "local-lvm",
    "rootfs_gb": 16,
    "bridge": "vmbr0",
    "ip_cidr": "192.168.1.120/24",
    "gateway": "192.168.1.1",
    "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    "unprivileged": true,
    "start": true
  }
  ```

## Software installation & configuration
- **Install a package** – `POST /lxc/exec` with `"cmd": "apt-get update && apt-get install -y nginx"` (chain commands inside `bash -lc`).
- **Write configuration files** – use `cat <<'EOF' > /path` inside `/lxc/exec` or clone repositories with `/deploy`.
- **Enable and start services** – `systemctl enable --now <service>` via `/lxc/exec`.

## Code deployment
- **Git based project**
  ```json
  POST /deploy
  {
    "target_vmid": 120,
    "repo_url": "https://github.com/example/app.git",
    "workdir": "/opt/app",
    "commands": [
      "cd {{workdir}} && cp env.example .env && nano .env",
      "cd {{workdir}} && docker compose up -d"
    ]
  }
  ```
  Adjust `setup`/`commands` arrays depending on project requirements.

## Diagnostics
- **Check container status** – `GET /lxc` and inspect `status`/`cpu` fields.
- **Inspect logs** – `/lxc/exec` with `journalctl -u <service> -n 200` or `tail -n 200 /var/log/<file>.log`.
- **Network troubleshooting** – `/lxc/exec` with `ping`, `curl -I`, or `ss -tulpn`.

## Browser automation
- **Capture screenshot**
  ```json
  POST /browser/open
  {
    "url": "https://example.com",
    "action": "screenshot",
    "output_path": "/tmp/example.png"
  }
  ```
- **PDF export** – set `"action": "pdf"` and provide `output_path`.

## SSH to external hosts
Specify only the overrides you need; the controller fills defaults from the environment.
```json
POST /ssh/run
{
  "host": "192.168.1.50",
  "cmd": "docker ps"
}
```

## Safety reminders
- Confirm before deleting containers or altering cluster-wide configuration.
- Avoid long-running background tasks without monitoring; tail logs or check exit codes.
- Always report the final state, including IP addresses, credentials, file locations, and artifacts produced.
