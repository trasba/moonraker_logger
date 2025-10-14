import asyncio
import json
import re
from typing import List, Dict, Any
import websockets
import os
import time

# ==============================================================================
# DataHandler and MoonrakerClient Classes (unchanged)
# ==============================================================================
class DataHandler:
    def __init__(self, filename: str):
        self.filename = filename

    def load_data(self) -> List[Dict]:
        if not os.path.exists(self.filename): return []
        try:
            with open(self.filename, 'r') as f: return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError): return []

    def save_data(self, data: List[Dict]):
        with open(self.filename, 'w') as f: json.dump(data, f, indent=2)
        print(f"💾 Data saved to {self.filename}")

class MoonrakerClient:
    def __init__(self, host: str, port: int = 7125):
        self.uri = f"ws://{host}:{port}/websocket"
        self._websocket = None
        self._request_id = 0
        print(f"✅ Client initialized for Moonraker at: {self.uri}")

    async def connect(self):
        try:
            self._websocket = await websockets.connect(self.uri)
            print("🔗 Connected to Moonraker.")
        except Exception as e:
            print(f"❌ Error connecting: {e}")
            self._websocket = None
            raise

    async def close(self):
        if self._websocket:
            try:
                await self._websocket.close()
                print("\n🔌 Connection closed.")
            except websockets.exceptions.ConnectionClosed:
                print("\n🔌 Connection was already closed.")

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
        print("\nRequesting G-code store for probe data...")
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
        print(f"🔬 Found {len(probe_data)} total probe points in gcode_store.")
        return probe_data

    async def get_bed_mesh_data(self) -> Dict[str, Any] | None:
        print("Requesting bed mesh data...")
        params = {"objects": {"bed_mesh": None}}
        request_id = await self._send_request("printer.objects.query", params)
        while True:
            response = await self._receive_response()
            if response.get("id") == request_id: break
        status, bed_mesh = response.get("result", {}).get("status", {}), None
        if status: bed_mesh = status.get("bed_mesh")
        if not bed_mesh or 'probed_matrix' not in bed_mesh: return None
        print(f"🕸️ Found bed mesh '{bed_mesh.get('profile_name')}'.")
        return {
            "timestamp": time.time(), "profile_name": bed_mesh.get("profile_name"),
            "mesh_min": bed_mesh.get("mesh_min"), "mesh_max": bed_mesh.get("mesh_max"),
            "probed_matrix": bed_mesh.get("probed_matrix")
        }

# ==============================================================================
# Helper Functions for Syncing Data
# ==============================================================================
async def sync_probe_data(client: MoonrakerClient, handler: DataHandler):
    """Fetches the entire gcode_store and saves any new probe points."""
    fetched_probes = await client.get_probe_data()
    if not fetched_probes:
        return

    existing_data = handler.load_data()
    existing_timestamps = {p['timestamp'] for p in existing_data}
    
    points_to_add = [p for p in fetched_probes if p['timestamp'] not in existing_timestamps]
    
    if points_to_add:
        print(f"✨ Found {len(points_to_add)} new probe points to add.")
        all_points = existing_data + points_to_add
        all_points.sort(key=lambda p: p.get('timestamp', 0))
        handler.save_data(all_points)
    else:
        print("👍 Probe data file is already up-to-date.")

async def sync_mesh_data(client: MoonrakerClient, handler: DataHandler):
    """Fetches the current bed mesh and saves it if it's unique."""
    fetched_mesh = await client.get_bed_mesh_data()
    if not fetched_mesh:
        return

    existing_meshes = handler.load_data()
    is_new = not existing_meshes or existing_meshes[-1]['probed_matrix'] != fetched_mesh['probed_matrix']

    if is_new:
        print("✨ Bed mesh is new. Adding it to the file.")
        updated_meshes = existing_meshes + [fetched_mesh]
        updated_meshes.sort(key=lambda m: m.get('timestamp', 0))
        handler.save_data(updated_meshes)
    else:
        print("👍 Bed mesh is identical to the last saved version.")

# ==============================================================================
# Main Execution Logic
# ==============================================================================
async def main():
    MOONRAKER_HOST = "192.168.176.236"
    probe_handler = DataHandler(filename="probe_data.json")
    mesh_handler = DataHandler(filename="bed_mesh_data.json")
    client = MoonrakerClient(host=MOONRAKER_HOST)

    try:
        await client.connect()

        # --- 1. Perform an Initial Sync on Startup ---
        print("--- Performing Initial Data Sync ---")
        await sync_probe_data(client, probe_handler)
        await sync_mesh_data(client, mesh_handler)
        print("--- Initial Sync Complete ---")

        # --- 2. Listen Forever for the Trigger ---
        print("\n📡 Now listening for 'Mesh Bed Leveling Complete' trigger...")
        while True:
            update = await client._receive_response()
            
            if update.get("method") == "notify_gcode_response":
                line = update["params"][0]
                
                # We only care about the completion message now
                if "Mesh Bed Leveling Complete" in line:
                    print(f"\n🔴 TRIGGER DETECTED: {line.strip()}")
                    print("--- Starting Full Data Refresh ---")
                    await sync_probe_data(client, probe_handler)
                    await sync_mesh_data(client, mesh_handler)
                    print("--- Data Refresh Complete ---")
                    print("\n📡 Resuming listening for trigger...")

    except KeyboardInterrupt:
        print("\n🛑 User interrupted. Shutting down.")
    except websockets.exceptions.ConnectionClosedError:
        print("\n⚠️ Connection to Moonraker lost. Exiting.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())