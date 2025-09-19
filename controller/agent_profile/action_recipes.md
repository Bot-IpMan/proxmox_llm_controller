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
- **Install Docker** – `POST /lxc/exec` and run either `apt-get update && apt-get install -y docker.io` or pipe the official convenience script `curl -fsSL https://get.docker.com | sh` (wrap commands with `bash -lc`).
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

## Advanced automation examples
- **Price scraping workflow**
  1. Create a fresh Debian container with `/lxc` and ensure outbound network access.
  2. Install dependencies via `/lxc/exec` using `pip install requests beautifulsoup4` (install `python3-pip` first if needed).
  3. Upload or cat a Python script that fetches target pages and parses prices with BeautifulSoup.
  4. Execute the script through `/deploy` commands or a direct `/lxc/exec` call like `python3 /opt/scraper.py`.
- **Bring up a full development stack**
  1. Provision three containers (e.g., `frontend`, `backend`, `database`) with `/lxc`, assigning them to the same bridge or VLAN for inter-container networking.
  2. Use `/lxc/exec` to install Docker and Docker Compose in each container (or a central orchestrator), configuring environment variables and volumes.
  3. Deploy a shared `docker-compose.yml` that defines services for the frontend, backend, and database, and launch it with `docker compose up -d` from the designated container.

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
