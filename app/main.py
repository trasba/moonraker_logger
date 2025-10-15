# app/main.py

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List

import websockets
from dotenv import load_dotenv

# ==============================================================================
# Data Handling
# ==============================================================================
class DataHandler:
    """Handles reading and writing data to a JSON file."""
    def __init__(self, filename: str):
        self.filename = filename

    def load_data(self) -> List[Dict]:
        """Loads data from the JSON file, returning an empty list if not found."""
        if not os.path.exists(self.filename): return []
        try:
            with open(self.filename, 'r') as f: return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError): return []

    def save_data(self, data: List[Dict]):
        """Saves the provided data list to the JSON file."""
        with open(self.filename, 'w') as f: json.dump(data, f, indent=2)
        logging.info(f"💾 Data saved to {self.filename}")

# ==============================================================================
# Moonraker WebSocket Client
# ==============================================================================
class MoonrakerClient:
    """A modular client for interacting with a Moonraker instance via WebSockets."""
    def __init__(self, host: str, port: int):
        self.uri = f"ws://{host}:{port}/websocket"
        self._websocket = None
        self._request_id = 0
        logging.info(f"✅ Client configured for Moonraker at: {self.uri}")

    async def connect(self):
        """Establishes a WebSocket connection to Moonraker with a timeout."""
        try:
            self._websocket = await websockets.connect(self.uri, open_timeout=10)
            logging.info("🔗 Connected to Moonraker.")
        except Exception as e:
            logging.error(f"❌ Connection attempt failed: {e}")
            self._websocket = None
            raise

    async def close(self):
        """Closes the WebSocket connection if it is open."""
        if self._websocket:
            try:
                await self._websocket.close()
                logging.info("\n🔌 Connection closed.")
            except websockets.exceptions.ConnectionClosed:
                logging.info("\n🔌 Connection was already closed.")

    async def _send_request(self, method: str, params: Dict = None) -> int:
        """Sends a JSON-RPC request to the Moonraker server."""
        if not self._websocket: raise ConnectionError("Not connected.")
        self._request_id += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": self._request_id}
        await self._websocket.send(json.dumps(request))
        return self._request_id

    async def _receive_response(self) -> Dict:
        """Waits for and receives a single JSON response from Moonraker."""
        if not self._websocket: raise ConnectionError("Not connected.")
        return json.loads(await self._websocket.recv())

    async def get_probe_data(self) -> List[Dict]:
        """Fetches the entire G-code history to extract all probe points."""
        logging.info("Requesting G-code store for probe data...")
        request_id = await self._send_request("server.gcode_store")
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id: break
        probe_data, pattern = [], re.compile(r"probe at ([\d\.]+),([\d\.]+) is z=([-\d\.]+)")
        for item in response.get("result", {}).get("gcode_store", []):
            match = pattern.match(item.get("message", ""))
            if match:
                x, y, z = map(float, match.groups())
                probe_data.append({"x": x, "y": y, "z": z, "timestamp": item.get("time")})
        logging.info(f"🔬 Found {len(probe_data)} total probe points in gcode_store.")
        return probe_data

    async def get_bed_mesh_data(self) -> Dict[str, Any] | None:
        """Fetches the current bed mesh state from the printer."""
        logging.info("Requesting bed mesh data...")
        params = {"objects": {"bed_mesh": None}}
        request_id = await self._send_request("printer.objects.query", params)
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id: break
        status, bed_mesh = response.get("result", {}).get("status", {}), None
        if status: bed_mesh = status.get("bed_mesh")
        if not bed_mesh or 'probed_matrix' not in bed_mesh: return None
        logging.info(f"🕸️ Found bed mesh '{bed_mesh.get('profile_name')}'.")
        return {
            "timestamp": time.time(), "profile_name": bed_mesh.get("profile_name"),
            "mesh_min": bed_mesh.get("mesh_min"), "mesh_max": bed_mesh.get("mesh_max"),
            "probed_matrix": bed_mesh.get("probed_matrix")
        }
    
    async def get_z_offset_data(self) -> List[Dict]:
        """Fetches the G-code history to extract all Z-Offset measurements."""
        logging.info("Requesting G-code store for Z-Offset data...")
        request_id = await self._send_request("server.gcode_store")
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id: break
        
        offset_data = []
        pattern = re.compile(r"probe: z_offset: ([-\d\.]+)")
        for item in response.get("result", {}).get("gcode_store", []):
            # The message can have newlines, so we search instead of matching start
            match = pattern.search(item.get("message", ""))
            if match:
                z_offset = float(match.group(1))
                offset_data.append({"z_offset": z_offset, "timestamp": item.get("time")})
        logging.info(f"📏 Found {len(offset_data)} Z-Offset entries in gcode_store.")
        return offset_data

# ==============================================================================
# Helper Functions for Syncing Data
# ==============================================================================
async def sync_probe_data(client: MoonrakerClient, handler: DataHandler):
    """Fetches the entire gcode_store and saves any new probe points."""
    fetched_probes = await client.get_probe_data()
    if not fetched_probes: return
    existing_data = handler.load_data()
    existing_timestamps = {p['timestamp'] for p in existing_data}
    points_to_add = [p for p in fetched_probes if p['timestamp'] not in existing_timestamps]
    if points_to_add:
        logging.info(f"✨ Found {len(points_to_add)} new probe points to add.")
        all_points = existing_data + points_to_add
        all_points.sort(key=lambda p: p.get('timestamp', 0))
        handler.save_data(all_points)
    else:
        logging.info("👍 Probe data file is already up-to-date.")

async def sync_mesh_data(client: MoonrakerClient, handler: DataHandler):
    """Fetches the current bed mesh and saves it if it's unique."""
    fetched_mesh = await client.get_bed_mesh_data()
    if not fetched_mesh: return
    existing_meshes = handler.load_data()
    # Check for uniqueness by comparing the new matrix to the most recent one.
    is_new = not existing_meshes or existing_meshes[-1]['probed_matrix'] != fetched_mesh['probed_matrix']
    if is_new:
        logging.info("✨ Bed mesh is new. Adding it to the file.")
        updated_meshes = existing_meshes + [fetched_mesh]
        updated_meshes.sort(key=lambda m: m.get('timestamp', 0))
        handler.save_data(updated_meshes)
    else:
        logging.info("👍 Bed mesh is identical to the last saved version.")
        
async def sync_z_offset_data(client: MoonrakerClient, handler: DataHandler):
    """Fetches G-code history and saves any new Z-Offset entries."""
    fetched_offsets = await client.get_z_offset_data()
    if not fetched_offsets: return

    existing_data = handler.load_data()
    existing_timestamps = {p['timestamp'] for p in existing_data}
    offsets_to_add = [p for p in fetched_offsets if p['timestamp'] not in existing_timestamps]

    if offsets_to_add:
        logging.info(f"✨ Found {len(offsets_to_add)} new Z-Offset entries to add.")
        all_offsets = existing_data + offsets_to_add
        all_offsets.sort(key=lambda p: p.get('timestamp', 0))
        handler.save_data(all_offsets)
    else:
        logging.info("👍 Z-Offset data file is already up-to-date.")

# ==============================================================================
# Concurrent Asynchronous Tasks
# ==============================================================================
async def listen_for_triggers_task(client: MoonrakerClient, probe_handler: DataHandler, mesh_handler: DataHandler, z_offset_handler: DataHandler):
    """A long-running task that listens for the 'Mesh Bed Leveling Complete' trigger."""
    logging.info("📡 Listening for 'Mesh Bed Leveling Complete' trigger...")
    while True:
        update = await client._receive_response()
        if update.get("method") == "notify_gcode_response":
            line = update["params"][0]
            if "Mesh Bed Leveling Complete" in line:
                logging.info(f"🔴 TRIGGER DETECTED: {line.strip()}")
                logging.info("... Waiting 30 seconds for mesh data to stabilize ...")
                await asyncio.sleep(30)
                logging.info("--- Starting Full Data Refresh ---")
                await sync_probe_data(client, probe_handler)
                await sync_mesh_data(client, mesh_handler)
                await sync_z_offset_data(client, z_offset_handler)
                logging.info("--- Data Refresh Complete ---")
                logging.info("📡 Resuming listening for trigger...")

async def periodic_sync_task(client: MoonrakerClient, probe_handler: DataHandler, mesh_handler: DataHandler, z_offset_handler: DataHandler, interval_hours: float):
    """A long-running task that periodically syncs data every N hours as a fallback."""
    interval_seconds = interval_hours * 3600
    while True:
        logging.info(f"⏳ Periodic sync sleeping for {interval_hours} hours...")
        await asyncio.sleep(interval_seconds)
        logging.info(f"⏰ Waking up for scheduled {interval_hours}-hour sync.")
        logging.info("--- Starting Scheduled Data Refresh ---")
        try:
            await sync_probe_data(client, probe_handler)
            await sync_mesh_data(client, mesh_handler)
            await sync_z_offset_data(client, z_offset_handler)
            logging.info("--- Scheduled Refresh Complete ---")
        except Exception as e:
            logging.error(f"❌ Error during scheduled sync: {e}")

# ==============================================================================
# Main Execution Logic
# ==============================================================================
async def main():
    """Initializes and runs the main application logic."""
    # Configure logging to provide timestamped output.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # Load configuration from the .env file.
    load_dotenv()
    host = os.getenv("MOONRAKER_HOST")
    port = int(os.getenv("MOONRAKER_PORT", 7125))
    probe_file = os.getenv("PROBE_DATA_FILE")
    mesh_file = os.getenv("MESH_DATA_FILE")
    z_offset_file = os.getenv("Z_OFFSET_DATA_FILE")
    sync_interval = float(os.getenv("SYNC_INTERVAL_HOURS", 6))
    retry_delay = int(os.getenv("RETRY_DELAY_SECONDS", 30))

    if not all([host, probe_file, mesh_file, z_offset_file]):
        logging.critical("❌ CRITICAL: One or more environment variables are not set. Please check your .env file.")
        return

    # Main application loop with automatic reconnection logic.
    while True:
        try:
            # Initialize components for this connection attempt.
            probe_handler = DataHandler(filename=probe_file)
            mesh_handler = DataHandler(filename=mesh_file)
            z_offset_handler = DataHandler(filename=z_offset_file)
            client = MoonrakerClient(host=host, port=port)

            # Establish connection and perform an initial data sync.
            await client.connect()
            logging.info("--- Performing Initial Data Sync ---")
            await sync_probe_data(client, probe_handler)
            await sync_mesh_data(client, mesh_handler)
            await sync_z_offset_data(client, z_offset_handler)
            logging.info("--- Initial Sync Complete ---")

            # Create and run the two main concurrent tasks.
            listener_task = asyncio.create_task(listen_for_triggers_task(client, probe_handler, mesh_handler, z_offset_handler))
            timer_task = asyncio.create_task(periodic_sync_task(client, probe_handler, mesh_handler, z_offset_handler, sync_interval))
            
            # This will run until one of the tasks raises an exception (e.g., connection loss).
            await asyncio.gather(listener_task, timer_task)

        except KeyboardInterrupt:
            logging.info("\n🛑 User interrupted. Shutting down.")
            break
        except (OSError, websockets.exceptions.WebSocketException) as e:
            logging.warning(f"Connection to Moonraker lost or failed: {e}")
            logging.info(f"Retrying connection in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logging.error(f"\nAn unexpected error occurred: {e}", exc_info=True)
            logging.info(f"Retrying connection in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)

if __name__ == "__main__":
    asyncio.run(main())