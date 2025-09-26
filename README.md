# Proxmox LLM Controller

Цей репозиторій містить повноцінний приклад інтеграції локальної LLM‑системи з Proxmox VE. Він складається з:

* **docker‑compose.yml** – піднімає Ollama, OpenWebUI та Python‑сервіс для керування Proxmox. Ці контейнери взаємодіють між собою через внутрішню мережу.
* **controller/** – вихідний код Python‑сервісу. Це FastAPI застосунок, що підключається до API Proxmox за допомогою бібліотеки `proxmoxer` та надає прості REST‑ендпоінти для списку вузлів, списку LXC‑контейнерів, створення нових LXC і запуску/зупинки контейнерів.

## Використання

1. Скопіюйте репозиторій на свій хост Proxmox або будь‑який інший сервер із доступом до Proxmox API.
2. Створіть файл `.env` у корені каталогу та заповніть такі змінні:

   ```env
   PROXMOX_HOST=your-proxmox-host:8006
   PROXMOX_USER=root@pam!WebUI
   # PROXMOX_TOKEN_NAME=WebUI  # не обов'язково, автоматично з PROXMOX_USER
   PROXMOX_TOKEN_VALUE=superSecret
   PROXMOX_VERIFY_SSL=False

   # (необов'язково) BlissOS через ADB
   # BLISS_ADB_ADDRESS=192.168.1.218:5555
   # або окремо:
   # BLISS_ADB_HOST=192.168.1.218
   # BLISS_ADB_PORT=5555
   # BLISS_ADB_SERIAL=RQCT30W45KM
   # BLISS_OPENAPI_PATH=/app/openapi_bliss.json
   # ADB_BINARY=/usr/bin/adb  # adb вже входить до образу, змінюйте лише за потреби
   ```

   > Щоб згенерувати API‑токен у Proxmox, відкрийте *Datacenter* → *Permissions* → *API Tokens*.
   > Якщо в `PROXMOX_USER` вказано `root@pam!WebUI`, частина після `!` автоматично
   > використовується як `PROXMOX_TOKEN_NAME`.

3. Додайте приватний SSH‑ключ, який має доступ до вашого вузла Proxmox:

   * покладіть файл ключа у каталог `keys/pve_id_rsa` (він монтується в контейнер як `/keys/pve_id_rsa`);
   * контейнер під час збірки автоматично створює симлінк `~/.ssh/id_rsa` → `/keys/pve_id_rsa`, тому для ручного підключення достатньо `ssh root@IP` (за бажання можна налаштувати `~/.ssh/config`);
   * `proxmox-controller` читає цей файл автоматично через змінну середовища `PVE_SSH_KEY_PATH`.

4. (Необов'язково) Додайте дефолтні параметри SSH для REST-ендпоінтів:

   * `DEFAULT_SSH_HOST` — адреса SSH-хоста. Якщо не задано, використовується `PVE_SSH_HOST`.
   * `DEFAULT_SSH_USER` — користувач (за замовчуванням `root`). Також успадковує `PVE_SSH_USER`.
   * `DEFAULT_SSH_PORT` — порт SSH (типово `22`).
   * `DEFAULT_SSH_KEY_PATH` або `DEFAULT_SSH_KEY_B64` — шлях до ключа або приватний ключ у base64-рядку.
   * `DEFAULT_SSH_PASSWORD` — пароль, якщо ключ не використовується.
   * `DEFAULT_SSH_STRICT_HOST_KEY` — `true/false`, щоб увімкнути сувору перевірку відбитку хоста.

   Після встановлення цих змінних ендпоінти `/ssh/run`, `/apps/launch` та `/browser/open` можуть викликатися без явного зазначення `host`, `user`, `port` чи автентифікації — значення підставляються автоматично. У конфігурації docker-compose достатньо додати змінні до `.env` поряд із уже наявними `PVE_SSH_*`.

5. Запустіть стек командою:

   ```sh
   docker-compose up -d
   ```

   Після запуску:
   * Ollama API доступний на порті `11434`.
   * OpenWebUI доступна на порті `3000`.
   * Контролер Proxmox доступний на порті `8000` (використовується FastAPI).

## Виклик API

Використовуйте будь‑який HTTP‑клієнт (curl, Postman, Python `requests`) для взаємодії з контролером:

* **Список вузлів**

  ```sh
  curl http://localhost:8000/nodes
  ```

* **Список LXC на вузлі**

  ```sh
  curl http://localhost:8000/lxc/pve
  ```

* **Створення LXC**

  ```sh
  curl -X POST http://localhost:8000/lxc \
    -H "Content-Type: application/json" \
    -d '{
          "node": "pve",          
          "vmid": 105,
          "ostemplate": "local:vztmpl/debian-12-standard.tar.zst",
          "cores": 2,
          "memory": 2048,
          "hostname": "test-lxc",
          "net0": "name=eth0,bridge=vmbr0,ip=192.168.1.105/24,gw=192.168.1.1",
          "password": "password",
          "features": {"nesting": 1, "keyctl": 1}
      }'
  ```

### BlissOS 16.9.7 через ADB

Для керування BlissOS (Android x86) контролер виконує команди `adb`. Налаштуйте TCP-підключення через змінні середовища:

```env
# host:port TCP-підключення до BlissOS (значення можна одразу додати в .env)
BLISS_ADB_ADDRESS=192.168.1.218:5555
# або окремо
BLISS_ADB_HOST=192.168.1.218
BLISS_ADB_PORT=5555

# Для USB-пристроїв можна задати серійник напряму
BLISS_ADB_SERIAL=RQCT30W45KM

# (необов'язково) власний шлях до OpenAPI або двійки adb
# BLISS_OPENAPI_PATH=/app/openapi_bliss.json
ADB_BINARY=/usr/bin/adb  # adb вже встановлений в образі, змінюйте лише за потреби
```

API-ендпоінти:

* **Перелік пристроїв** – `GET /bliss/adb/devices`
* **Підключення до BlissOS** – `POST /bliss/adb/connect {"host": "192.168.1.218", "port": 5555}`
* **Виконання shell-команд** –

  ```json
  POST /bliss/adb/shell
  {
    "cmd": "input keyevent 26"
  }
  ```

  Для кількох команд використовуйте масив `"commands": ["wm size", "wm density"]`. Параметр `"use_su": true` дозволяє виконувати `su -c`.

* **Будь-які інші adb-операції** – `POST /bliss/adb/command {"args": ["install", "/tmp/app.apk"]}` або

  ```json
  POST /bliss/adb/command
  {
    "command": "shell am start -a android.intent.action.VIEW -d https://example.com"
  }
  ```

* **Роз'єднання** – `POST /bliss/adb/disconnect {"all": true}` або з конкретною адресою.

#### Як сформулювати запит до ЛЛМ для дії в BlissOS

Щоб агент правильно зрозумів завдання, опиши:

1. **Яку дію треба виконати.** Наприклад, «відкрий панель швидких налаштувань» або «запусти застосунок Firefox».
2. **Де її виконувати.** Зазнач поточний екран, потрібне меню або координати для тапу/свайпу.
3. **Що вважати успіхом.** Поясни, який стан треба перевірити після дії (поява вікна, ввімкнений перемикач тощо).

Приклади запитів:

- «Під'єднайся до BlissOS через ADB, відкрий налаштування Wi-Fi та переконайся, що перемикач увімкнено.»
- «Зроби тап по координатах 960x540, щоб підтвердити діалог, і повідом, чи з'явилось повідомлення про успішне виконання.»
- «Виконай `adb shell am start -n org.mozilla.firefox/.App`, зачекай доки відкриється браузер і надішли скріншот головного екрана.»

Можна формулювати завдання звичайною мовою, але додавай конкретику (ADB-команди, координати, очікувані перевірки), якщо це важливо для точності.

## Архітектура

Локальна LLM, розгорнута через Ollama, може спілкуватися з FastAPI‑контролером і віддавати завдання (наприклад, створити контейнер) у вигляді JSON. Контролер виконує виклики до Proxmox API з допомогою `proxmoxer` й повертає результат у зручному форматі. Такий підхід дозволяє відокремити LLM від прямого root‑доступу на хості, реалізувати фільтрацію та логування, і легко розширювати функціональність.

## LLM агент

У каталозі [`controller/agent_profile`](controller/agent_profile) зібрані готові артефакти для запуску автономного агента:

- `system_prompt.md` — базова інструкція для моделі з описом місії, правил автономності та доступних інструментів.
- `action_recipes.md` — стислий довідник із прикладами запитів до контролера.
- Python‑модуль `agent_profile` експонує метадані через ендпоінт `GET /agent/profile` (JSON містить промпт, довідник та дефолтні параметри інфраструктури).

### Як підключити в OpenWebUI

1. Запустіть стек `docker-compose up -d`.
2. У OpenWebUI відкрийте **Tools → API Specifications** та додайте адресу `http://proxmox-controller:8000/openapi.json`.
3. Створіть новий **Persona** і вставте текст із `controller/agent_profile/system_prompt.md` у поле *System Prompt*.
4. За бажанням додайте `controller/agent_profile/action_recipes.md` як *Knowledge* або вбудуйте у prompt.
5. Після цього модель розумітиме, що має повний доступ до контролера і може виконувати задачі автономно. Додаткову інформацію можна також отримати через `GET http://proxmox-controller:8000/agent/profile`.

### Використання в інших фреймворках

Ендпоінт `/agent/profile` повертає всі необхідні текстові ресурси, тож його можна підвантажувати в будь-який orchestration-пайплайн (OpenAI Assistants, Ollama API, LangChain тощо) і автоматично формувати системне повідомлення для агента.

## Dependency management

Python dependencies for the controller reside in `controller/requirements.txt` and use compatible release specifiers (e.g., `fastapi~=0.111`). After modifying versions, reinstall the environment:

```bash
pip install -r controller/requirements.txt
```

To generate a fully pinned lock file with all transitive dependencies, use `pip-tools`:

```bash
pip install pip-tools
pip-compile controller/requirements.txt --output-file controller/requirements.lock
```
