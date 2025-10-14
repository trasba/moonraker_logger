# app/main.py

import asyncio
import json
import logging # <<< NEW: Import the logging module
import os
import re
import time
from typing import Any, Dict, List

import websockets
from dotenv import load_dotenv

# ==============================================================================
# DataHandler and MoonrakerClient Classes (unchanged)
# ==============================================================================
class DataHandler:
    """Handles reading and writing data to a JSON file."""
    def __init__(self, filename: str):
        self.filename = filename

    def load_data(self) -> List[Dict]:
        if not os.path.exists(self.filename): return []
        try:
            with open(self.filename, 'r') as f: return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError): return []

    def save_data(self, data: List[Dict]):
        with open(self.filename, 'w') as f: json.dump(data, f, indent=2)
        logging.info(f"💾 Data saved to {self.filename}") # <<< CHANGED to logging

class MoonrakerClient:
    """A modular client for interacting with a Moonraker instance via WebSockets."""
    def __init__(self, host: str, port: int):
        self.uri = f"ws://{host}:{port}/websocket"
        self._websocket = None
        self._request_id = 0
        logging.info(f"✅ Client configured for Moonraker at: {self.uri}") # <<< CHANGED

    async def connect(self):
        try:
            self._websocket = await websockets.connect(self.uri)
            logging.info("🔗 Connected to Moonraker.") # <<< CHANGED
        except Exception as e:
            logging.error(f"❌ Error connecting: {e}") # <<< CHANGED
            self._websocket = None
            raise

    async def close(self):
        if self._websocket:
            try:
                await self._websocket.close()
                logging.info("\n🔌 Connection closed.") # <<< CHANGED
            except websockets.exceptions.ConnectionClosed:
                logging.info("\n🔌 Connection was already closed.") # <<< CHANGED

    async def _send_request(self, method: str, params: Dict = None) -> int:
        if not self._websocket: raise ConnectionError("Not connected.")
        self._request_id += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": self._request_id}
        await self._websocket.send(json.dumps(request))
        return self._request_id

    async def _receive_response(self) -> Dict:
        if not self._websocket: raise ConnectionError("Not connected.")
        return json.loads(await self._websocket.recv())

    async def get_probe_data(self) -> List[Dict]:
        logging.info("Requesting G-code store for probe data...") # <<< CHANGED
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
        logging.info(f"🔬 Found {len(probe_data)} total probe points in gcode_store.") # <<< CHANGED
        return probe_data

    async def get_bed_mesh_data(self) -> Dict[str, Any] | None:
        logging.info("Requesting bed mesh data...") # <<< CHANGED
        params = {"objects": {"bed_mesh": None}}
        request_id = await self._send_request("printer.objects.query", params)
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id: break
        status, bed_mesh = response.get("result", {}).get("status", {}), None
        if status: bed_mesh = status.get("bed_mesh")
        if not bed_mesh or 'probed_matrix' not in bed_mesh: return None
        logging.info(f"🕸️ Found bed mesh '{bed_mesh.get('profile_name')}'.") # <<< CHANGED
        return {
            "timestamp": time.time(), "profile_name": bed_mesh.get("profile_name"),
            "mesh_min": bed_mesh.get("mesh_min"), "mesh_max": bed_mesh.get("mesh_max"),
            "probed_matrix": bed_mesh.get("probed_matrix")
        }

# ==============================================================================
# Helper Functions for Syncing Data
# ==============================================================================
async def sync_probe_data(client: MoonrakerClient, handler: DataHandler):
    fetched_probes = await client.get_probe_data()
    if not fetched_probes: return
    existing_data = handler.load_data()
    existing_timestamps = {p['timestamp'] for p in existing_data}
    points_to_add = [p for p in fetched_probes if p['timestamp'] not in existing_timestamps]
    if points_to_add:
        logging.info(f"✨ Found {len(points_to_add)} new probe points to add.") # <<< CHANGED
        all_points = existing_data + points_to_add
        all_points.sort(key=lambda p: p.get('timestamp', 0))
        handler.save_data(all_points)
    else:
        logging.info("👍 Probe data file is already up-to-date.") # <<< CHANGED

async def sync_mesh_data(client: MoonrakerClient, handler: DataHandler):
    fetched_mesh = await client.get_bed_mesh_data()
    if not fetched_mesh: return
    existing_meshes = handler.load_data()
    is_new = not existing_meshes or existing_meshes[-1]['probed_matrix'] != fetched_mesh['probed_matrix']
    if is_new:
        logging.info("✨ Bed mesh is new. Adding it to the file.") # <<< CHANGED
        updated_meshes = existing_meshes + [fetched_mesh]
        updated_meshes.sort(key=lambda m: m.get('timestamp', 0))
        handler.save_data(updated_meshes)
    else:
        logging.info("👍 Bed mesh is identical to the last saved version.") # <<< CHANGED

# ==============================================================================
# Concurrent Tasks for Listening and Periodic Sync
# ==============================================================================
async def listen_for_triggers_task(client: MoonrakerClient, probe_handler: DataHandler, mesh_handler: DataHandler):
    logging.info("📡 Listening for 'Mesh Bed Leveling Complete' trigger...") # <<< CHANGED
    while True:
        update = await client._receive_response()
        if update.get("method") == "notify_gcode_response":
            line = update["params"][0]
            if "Mesh Bed Leveling Complete" in line:
                logging.info(f"🔴 TRIGGER DETECTED: {line.strip()}") # <<< CHANGED
                logging.info("--- Starting Full Data Refresh ---") # <<< CHANGED
                await sync_probe_data(client, probe_handler)
                await sync_mesh_data(client, mesh_handler)
                logging.info("--- Data Refresh Complete ---") # <<< CHANGED
                logging.info("📡 Resuming listening for trigger...") # <<< CHANGED

async def periodic_sync_task(client: MoonrakerClient, probe_handler: DataHandler, mesh_handler: DataHandler, interval_hours: float):
    interval_seconds = interval_hours * 3600
    while True:
        logging.info(f"⏳ Periodic sync sleeping for {interval_hours} hours...") # <<< CHANGED
        await asyncio.sleep(interval_seconds)
        logging.info(f"⏰ Waking up for scheduled {interval_hours}-hour sync.") # <<< CHANGED
        logging.info("--- Starting Scheduled Data Refresh ---") # <<< CHANGED
        try:
            await sync_probe_data(client, probe_handler)
            await sync_mesh_data(client, mesh_handler)
            logging.info("--- Scheduled Refresh Complete ---") # <<< CHANGED
        except Exception as e:
            logging.error(f"❌ Error during scheduled sync: {e}") # <<< CHANGED

# ==============================================================================
# Main Execution Logic
# ==============================================================================
async def main():
    # <<< NEW: Configure logging format and level
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # --- Load Configuration from Environment ---
    load_dotenv()
    host = os.getenv("MOONRAKER_HOST")
    port = int(os.getenv("MOONRAKER_PORT", 7125))
    probe_file = os.getenv("PROBE_DATA_FILE")
    mesh_file = os.getenv("MESH_DATA_FILE")
    sync_interval = float(os.getenv("SYNC_INTERVAL_HOURS", 6))
    if not all([host, probe_file, mesh_file]):
        logging.critical("❌ CRITICAL: One or more environment variables are not set. Please check your .env file.") # <<< CHANGED
        return

    # --- Initialize Components ---
    probe_handler = DataHandler(filename=probe_file)
    mesh_handler = DataHandler(filename=mesh_file)
    client = MoonrakerClient(host=host, port=port)

    try:
        await client.connect()
        logging.info("--- Performing Initial Data Sync ---") # <<< CHANGED
        await sync_probe_data(client, probe_handler)
        await sync_mesh_data(client, mesh_handler)
        logging.info("--- Initial Sync Complete ---") # <<< CHANGED
        listener_task = asyncio.create_task(listen_for_triggers_task(client, probe_handler, mesh_handler))
        timer_task = asyncio.create_task(periodic_sync_task(client, probe_handler, mesh_handler, sync_interval))
        await asyncio.gather(listener_task, timer_task)
    except KeyboardInterrupt:
        logging.info("\n🛑 User interrupted. Shutting down.") # <<< CHANGED
    except websockets.exceptions.ConnectionClosedError:
        logging.warning("\n⚠️ Connection to Moonraker lost. Exiting.") # <<< CHANGED
    except Exception as e:
        logging.error(f"\nAn unexpected error occurred: {e}", exc_info=True) # <<< CHANGED
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())