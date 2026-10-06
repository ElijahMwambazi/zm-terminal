const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type SystemStatus = {
  api: boolean;
  database: boolean;
  openbb: boolean;
};

export async function getSystemStatus(): Promise<SystemStatus> {
  const response = await fetch(`${API_URL}/api/v1/system`);
  if (!response.ok) throw new Error("Unable to load system status");
  return response.json();
}
