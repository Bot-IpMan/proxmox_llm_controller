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

- **Provision dedicated frontend/backend/database containers** – repeat `POST /lxc` with tailored payloads, for example:
  ```json
  POST /lxc
  {
    "vmid": 201,
    "hostname": "frontend",
    "cores": 2,
    "memory": 2048,
    "bridge": "vmbr0",
    "ip_cidr": "192.168.1.201/24",
    "gateway": "192.168.1.1",
    "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    "storage": "local-lvm",
    "rootfs_gb": 16,
    "start": true
  }
  POST /lxc
  {
    "vmid": 202,
    "hostname": "backend",
    "cores": 4,
    "memory": 4096,
    "bridge": "vmbr0",
    "ip_cidr": "192.168.1.202/24",
    "gateway": "192.168.1.1",
    "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    "storage": "local-lvm",
    "rootfs_gb": 32,
    "start": true
  }
  POST /lxc
  {
    "vmid": 203,
    "hostname": "database",
    "cores": 2,
    "memory": 4096,
    "bridge": "vmbr0",
    "ip_cidr": "192.168.1.203/24",
    "gateway": "192.168.1.1",
    "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    "storage": "local-lvm",
    "rootfs_gb": 64,
    "start": true
  }
  ```

## Software installation & configuration
- **Install a package** – `POST /lxc/exec` with `"cmd": "apt-get update && apt-get install -y nginx"` (chain commands inside `bash -lc`).
- **Run multiple steps** – use the `commands` array to execute sequential actions with `/lxc/exec`:
  ```json
  POST /lxc/exec
  {
    "vmid": 202,
    "commands": [
      "apt-get update",
      "apt-get install -y python3 python3-pip",
      "pip3 install -r /opt/app/requirements.txt"
    ]
  }
  ```
- **Install Docker** – `POST /lxc/exec` and run either `apt-get update && apt-get install -y docker.io` or pipe the official convenience script `curl -fsSL https://get.docker.com | sh` (wrap commands with `bash -lc`).
- **Write configuration files** – use `cat <<'EOF' > /path` inside `/lxc/exec` or clone repositories with `/deploy`.
- **Enable and start services** – `systemctl enable --now <service>` via `/lxc/exec`.

## Code deployment
- **Python service with requirements & Docker Compose**
  ```json
  POST /deploy
  {
    "target_vmid": 202,
    "repo_url": "https://github.com/example/app.git",
    "workdir": "/opt/app",
    "setup": [
      "apt-get update",
      "apt-get install -y git python3 python3-pip",
      "curl -fsSL https://get.docker.com | sh"
    ],
    "commands": [
      "git clone {{repo_url}} {{workdir}}",
      "cd {{workdir}} && pip3 install -r requirements.txt",
      "cd {{workdir}} && docker compose up -d"
    ]
  }
  ```
  Use templated variables (`{{repo_url}}`, `{{workdir}}`) to avoid repeating strings, and extend the `commands` array with migrations, tests, or health-checks as needed.

## Advanced automation examples
- **Price scraping workflow**
  1. Create a fresh Debian container with `/lxc` and ensure outbound network access.
  2. Install dependencies via `/lxc/exec` using `pip install requests beautifulsoup4` (install `python3-pip` first if needed).
  3. Upload or cat a Python script that fetches target pages and parses prices with BeautifulSoup.
  4. Execute the script through `/deploy` commands or a direct `/lxc/exec` call like `python3 /opt/scraper.py`.
- **Bring up a full development stack**
  1. Create the `frontend`, `backend`, and `database` containers with the multi-payload `POST /lxc` examples above so that each tier has the right CPU/RAM/disk profile.
  2. On the backend container (VMID 202), execute the multi-step `/lxc/exec` request to install Python, pip, and other prerequisites. Install Docker/Docker Compose on the nodes that will run containers (typically backend and database).
  3. Deploy the application code using the Python `POST /deploy` example to clone the repository, install dependencies from `requirements.txt`, and start the stack with `docker compose up -d`.
  4. Optionally add post-deploy checks (e.g., `curl http://frontend.local/health`) via extra `commands` entries or standalone `/lxc/exec` calls.

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

## BlissOS automation (ADB)
- **Check connected devices** – `GET /bliss/adb/devices`
- **Connect to BlissOS over TCP**
  ```json
  POST /bliss/adb/connect
  {
    "host": "192.168.1.218",
    "port": 5555,
    "force_disconnect": true
  }
  ```
- **Run shell actions** – `POST /bliss/adb/shell {"cmd": "input tap 960 540"}` (use `commands` array for sequential steps, or set `"use_su": true` to wrap commands with `su -c`).
- **Launch Android activities** –
  ```json
  POST /bliss/adb/command
  {
    "command": "shell am start -a android.intent.action.VIEW -d https://example.com"
  }
  ```
- **Disconnect** – `POST /bliss/adb/disconnect {"all": true}` to drop all TCP sessions or pass a `host`/`port` pair to close a single target.

## Safety reminders
- Confirm before deleting containers or altering cluster-wide configuration.
- Avoid long-running background tasks without monitoring; tail logs or check exit codes.
- Always report the final state, including IP addresses, credentials, file locations, and artifacts produced.
