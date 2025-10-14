import asyncio
import json
import re
from typing import List, Dict, Any
import websockets
from datetime import datetime
import os
import time # <<< NEW: Import the time module

# ==============================================================================
# DataHandler Class (unchanged)
# ==============================================================================
class DataHandler:
    """Handles reading and writing data to a JSON file."""
    def __init__(self, filename: str):
        self.filename = filename

    def load_data(self) -> List[Dict]:
        """Loads data from the JSON file."""
        if not os.path.exists(self.filename):
            return []
        try:
            with open(self.filename, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def save_data(self, data: List[Dict]):
        """Saves data to the JSON file."""
        with open(self.filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"💾 Data successfully saved to {self.filename}")

# ==============================================================================
# MoonrakerClient Class (method for bed mesh is updated)
# ==============================================================================
class MoonrakerClient:
    """A modular client for interacting with a Moonraker instance via WebSockets."""
    def __init__(self, host: str, port: int = 7125):
        self.uri = f"ws://{host}:{port}/websocket"
        self._websocket = None
        self._request_id = 0
        print(f"✅ Client initialized for Moonraker at: {self.uri}")

    async def connect(self):
        try:
            self._websocket = await websockets.connect(self.uri)
            print("🔗 Successfully connected to Moonraker.")
        except Exception as e:
            print(f"❌ Error connecting to Moonraker: {e}")
            self._websocket = None
            raise

    async def close(self):
        if self._websocket:
            try:
                await self._websocket.close()
                print("🔌 Connection closed.")
            except websockets.exceptions.ConnectionClosed:
                print("🔌 Connection was already closed.")

    async def _send_request(self, method: str, params: Dict = None) -> int:
        if not self._websocket:
            raise ConnectionError("Not connected. Call connect() first.")
        self._request_id += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": self._request_id}
        await self._websocket.send(json.dumps(request))
        return self._request_id

    async def _receive_response(self) -> Dict:
        if not self._websocket:
            raise ConnectionError("Not connected. Call connect() first.")
        return json.loads(await self._websocket.recv())

    async def get_probe_data(self) -> List[Dict]:
        print("\nRequesting G-code store for probe data...")
        request_id = await self._send_request("server.gcode_store")
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id:
                break
        
        probe_data = []
        probe_pattern = re.compile(r"probe at ([\d\.]+),([\d\.]+) is z=([-\d\.]+)")
        gcode_items = response.get("result", {}).get("gcode_store", [])
        for item in gcode_items:
            message = item.get("message", "")
            match = probe_pattern.match(message)
            if match:
                x, y, z = map(float, match.groups())
                timestamp = item.get("time")
                probe_data.append({"x": x, "y": y, "z": z, "timestamp": timestamp})
        print(f"🔬 Found {len(probe_data)} probe points in gcode_store.")
        return probe_data

    async def get_bed_mesh_data(self) -> Dict[str, Any] | None:
        """Fetches the current bed mesh state from the printer."""
        print("\nRequesting printer objects for bed mesh data...")
        params = {"objects": {"bed_mesh": None}}
        request_id = await self._send_request("printer.objects.query", params)
        
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id:
                break
        
        status = response.get("result", {}).get("status", {})
        bed_mesh = status.get("bed_mesh")
        
        # <<< CHANGED: Use the current epoch time instead of Moonraker's eventtime
        timestamp = time.time()

        if not bed_mesh or 'probed_matrix' not in bed_mesh:
            print("❌ Bed mesh data not found in the response.")
            return None
        
        print(f"🕸️ Found bed mesh '{bed_mesh.get('profile_name')}' from printer.")
        
        return {
            "timestamp": timestamp,
            "profile_name": bed_mesh.get("profile_name"),
            "mesh_min": bed_mesh.get("mesh_min"),
            "mesh_max": bed_mesh.get("mesh_max"),
            "probed_matrix": bed_mesh.get("probed_matrix")
        }

# ==============================================================================
# Main Execution Logic (unchanged)
# ==============================================================================
async def main():
    """Main function to run the client, fetch all data, and save new entries."""
    MOONRAKER_HOST = "192.168.176.236" 
    
    probe_handler = DataHandler(filename="probe_data.json")
    mesh_handler = DataHandler(filename="bed_mesh_data.json")
    
    client = MoonrakerClient(host=MOONRAKER_HOST)

    try:
        await client.connect()

        # --- 1. Process Individual Probe Data ---
        existing_probes = probe_handler.load_data()
        existing_probe_timestamps = {point['timestamp'] for point in existing_probes}
        newly_fetched_probes = await client.get_probe_data()
        probes_to_add = [p for p in newly_fetched_probes if p.get('timestamp') not in existing_probe_timestamps]
        
        if probes_to_add:
            print(f"✨ Found {len(probes_to_add)} new probe points to add.")
            updated_probes = existing_probes + probes_to_add
            updated_probes.sort(key=lambda p: p.get('timestamp', 0))
            probe_handler.save_data(updated_probes)
        else:
            print("👍 Probe data file is already up-to-date.")

        # --- 2. Process Bed Mesh Data ---
        existing_meshes = mesh_handler.load_data()
        newly_fetched_mesh = await client.get_bed_mesh_data()

        if newly_fetched_mesh:
            is_new_mesh = False
            if not existing_meshes:
                is_new_mesh = True
            else:
                last_saved_mesh = existing_meshes[-1]
                if last_saved_mesh['probed_matrix'] != newly_fetched_mesh['probed_matrix']:
                    is_new_mesh = True

            if is_new_mesh:
                print("✨ Bed mesh data is new and unique. Adding it to the file.")
                updated_meshes = existing_meshes + [newly_fetched_mesh]
                updated_meshes.sort(key=lambda m: m.get('timestamp', 0))
                mesh_handler.save_data(updated_meshes)
            else:
                print("👍 Bed mesh data is identical to the last saved version. No changes made.")

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())