# Klipper Bed Mesh & Probe Data Logger

A simple, automated service to collect and store bed mesh and probe data from a Klipper 3D printer running Moonraker. This tool runs continuously in a Docker container, listening for events and periodically syncing data for long-term analysis and visualization.

## Features

* **⚙️ Fully Automated:** Runs in the background and requires no manual intervention after initial setup.
* **🔌 Reliable Trigger-Based Sync:** Detects when a `BED_MESH_CALIBRATE` command completes and immediately triggers a full refresh of both probe and bed mesh data.
* **🔄 Resilient Auto-Reconnect:** If the connection to the printer is lost (e.g., printer is turned off), the service will automatically try to reconnect at a configurable interval without crashing.
* **⏰ Periodic Sync:** Includes a configurable fallback timer (e.g., every 6 hours) to sync data, ensuring no information is missed.
* **🧠 Smart Duplicate Handling:** Prevents redundant entries by checking timestamps for probe points and the entire data matrix for bed meshes.
* **💾 Persistent Storage:** Saves all collected data into clean, human-readable JSON files.
* **🐳 Dockerized:** The entire application is containerized for easy, cross-platform deployment, dependency management, and timezone synchronization with the host.
* **📝 Timestamped Logging:** All actions are logged to the Docker console with timestamps for easy monitoring and debugging.
* **🔧 Configurable:** All parameters (host IP, file paths, sync intervals) are managed via a simple `.env` file.

***

## Project Structure

The project is organized to separate the application code from configuration and persistent data.

```
moonraker-logger/
├── app/                  # Python application code
│   ├── main.py
│   ├── pyproject.toml    # Poetry dependencies
│   └── poetry.lock
├── data/                 # Mounted volume for persistent JSON files (created automatically)
├── .env                  # Environment configuration
├── Dockerfile            # Optimized, multi-stage Dockerfile
└── docker-compose.yml    # Docker Compose orchestration
```

***

## Prerequisites

You must have **Docker** and **Docker Compose** installed on your system.

***

## Setup & Configuration

Follow these steps to get the logger running.

### Step 1: Prepare Project Files

Clone this repository or create the files as shown in the project structure above. The `poetry.lock` file is included, so you do not need to generate it.

### Step 2: Configure Environment Variables

In the root directory of the project, create a file named `.env`. Copy the content below and **edit the values** to match your network setup.

```env
# .env

# --- Moonraker Connection Settings ---
MOONRAKER_HOST=192.168.1.100  # IMPORTANT: Change this to your printer's IP address
MOONRAKER_PORT=7125

# --- Data File Paths (inside the container's /app/data volume) ---
PROBE_DATA_FILE=data/probe_data.json
MESH_DATA_FILE=data/bed_mesh_data.json

# --- Sync Interval ---
# The time in hours for the periodic background sync.
SYNC_INTERVAL_HOURS=6

# --- Connection Resilience ---
# Delay in seconds before retrying to connect after a failure.
RETRY_DELAY_SECONDS=300
```

***

## Usage

### Running the Service

With your `.env` file configured, you can build and run the container in detached mode.

1.  Navigate to the root `moonraker-logger/` directory.
2.  Run the following command:
    ```bash
    docker-compose up --build -d
    ```
    * `--build`: Builds the Docker image from the `Dockerfile`. You only need this the first time or after making changes to the code.
    * `-d`: Runs the container in the background (detached mode).

### Viewing Logs

To see the real-time, timestamped logs from the application, run:
```bash
docker logs moonraker_logger -f
```
* `-f`: Follows the log output, showing new messages as they appear.

### Stopping the Service

To stop the container, run:
```bash
docker-compose down
```

***

## Output

The service will generate and continuously update two files inside the `data/` directory. This folder is **persistently mounted** to your host machine, so your collected data is safe even if the container is stopped or removed.

* **`probe_data.json`**: A list of all unique probe points collected, each with its `x`, `y`, `z` coordinates and the original `timestamp`.
* **`bed_mesh_data.json`**: A list of all unique bed meshes, including the profile name, mesh boundaries, and the full `probed_matrix` of Z-values.