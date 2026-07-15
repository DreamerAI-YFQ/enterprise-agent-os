import { useAuthStore } from "./auth-store";
import { getApiBaseUrl, resolveBackendUrl } from "./backend-url";
import type { AttachmentRef } from "../types/agent-event";

export interface UploadResponse {
  file_id: string;
  url: string;
  type: "image" | "file";
  name: string;
  mime_type: string;
  size_bytes: number;
}

/** Upload a file to POST /upload and return an AttachmentRef for invoke. */
export async function uploadFile(file: File): Promise<AttachmentRef> {
  const token = useAuthStore.getState().token;
  const formData = new FormData();
  formData.append("file", file);

  const resp = await fetch(`${getApiBaseUrl()}/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(text || `Upload failed: ${resp.status}`);
  }

  const data: UploadResponse = await resp.json();
  return {
    file_id: data.file_id,
    url: resolveBackendUrl(data.url),
    type: data.type,
    name: data.name,
    mime_type: data.mime_type,
  };
}
