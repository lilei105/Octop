import { request, requestUpload } from "../request";
import type { UploadResponse } from "./upload";

/** Mirror of the backend ``bio_analysis_tasks`` row (snake_case as served). */
export interface BioTask {
  id: string;
  agent_id: string;
  user_id: number;
  thread_id: string;
  title: string;
  status:
    | "planning"
    | "pathSelection"
    | "uploading"
    | "generating"
    | "validating"
    | "executing"
    | "completed"
    | "failed";
  need_text: string;
  candidates_json: string;
  selected_candidate_json: string;
  required_files_json: string;
  file_mappings_json: string;
  generated_code: string;
  code_origin: string;
  run_dir: string;
  exec_status: string;
  exec_started_at: number | null;
  exec_finished_at: number | null;
  exec_exit_code: number | null;
  exec_error: string;
  created_at: string;
  updated_at: string;
}

export interface BioToolRef {
  id: string;
  name: string;
}

export interface BioCandidateChain {
  id: string;
  tool_chain: BioToolRef[];
  previously_used?: boolean;
  total_runtime?: number;
  total_cost?: number;
}

export interface BioFileSlot {
  label: string;
  extensions: string[];
  multiple: boolean;
}

export interface BioFileMapping {
  slot_label: string;
  workspace_path: string;
  original_name: string;
}

export interface BioExecutionLogs {
  logs: string;
  offset: number;
  completed: boolean;
  status: string;
  exec_status: string;
  error_message: string;
}

function base(agentId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/bio`;
}

export async function listBioTasks(
  agentId: string,
  threadId: string,
): Promise<BioTask[]> {
  const data = await request<{ tasks: BioTask[] }>(
    `${base(agentId)}/tasks?thread_id=${encodeURIComponent(threadId)}`,
  );
  return data.tasks;
}

export async function getBioTask(
  agentId: string,
  taskId: string,
): Promise<BioTask> {
  const data = await request<{ task: BioTask }>(
    `${base(agentId)}/tasks/${encodeURIComponent(taskId)}`,
  );
  return data.task;
}

export async function confirmBioPath(
  agentId: string,
  taskId: string,
  candidateId: string,
  candidateData = "",
): Promise<BioTask> {
  const data = await request<{ task: BioTask }>(
    `${base(agentId)}/tasks/${encodeURIComponent(taskId)}/confirm-path`,
    {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId, candidate_data: candidateData }),
    },
  );
  return data.task;
}

export async function confirmBioUpload(
  agentId: string,
  taskId: string,
  fileMappings: BioFileMapping[],
): Promise<BioTask> {
  const data = await request<{ task: BioTask }>(
    `${base(agentId)}/tasks/${encodeURIComponent(taskId)}/confirm-upload`,
    { method: "POST", body: JSON.stringify({ file_mappings: fileMappings }) },
  );
  return data.task;
}

export async function startBioExecution(
  agentId: string,
  taskId: string,
): Promise<BioTask> {
  const data = await request<{ task: BioTask }>(
    `${base(agentId)}/tasks/${encodeURIComponent(taskId)}/start-execution`,
    { method: "POST" },
  );
  return data.task;
}

export async function getBioExecutionLogs(
  agentId: string,
  taskId: string,
  offset = 0,
): Promise<BioExecutionLogs> {
  return request<BioExecutionLogs>(
    `${base(agentId)}/tasks/${encodeURIComponent(taskId)}/execution-logs?offset=${offset}`,
  );
}

/** Chunked big-file upload for bio inputs (fastq/bam/vcf/…, up to 2GB). */
export async function uploadBioFile(
  agentId: string,
  file: File,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return requestUpload<UploadResponse>(
    `/agents/${encodeURIComponent(agentId)}/upload-bio`,
    formData,
  );
}

// ── admin: script library ────────────────────────────────────────────────

export interface BioScriptFolder {
  id: string;
  name: string;
  parent_id: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface BioScript {
  id: string;
  folder_id: string;
  name: string;
  description: string;
  tool_id: string;
  category: string;
  version: string;
  file_path: string;
  md_content: string;
  inputs: string;
  outputs: string;
  runtime_min: number;
  cost: number;
  weight: number;
  verified: boolean;
  is_active: boolean;
  valid_from: number | null;
  valid_until: number | null;
  uploaded_by: number | null;
  verified_by: number | null;
  created_at: string;
  updated_at: string;
}

const ADMIN_BASE = "/admin/bio";

export const bioAdminApi = {
  async listFolders(): Promise<BioScriptFolder[]> {
    return request<BioScriptFolder[]>(`${ADMIN_BASE}/script-folders`);
  },
  async createFolder(body: {
    name: string;
    parent_id?: string;
    sort_order?: number;
  }): Promise<BioScriptFolder> {
    return request<BioScriptFolder>(`${ADMIN_BASE}/script-folders`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  async updateFolder(
    id: string,
    body: { name?: string; sort_order?: number },
  ): Promise<BioScriptFolder> {
    return request<BioScriptFolder>(
      `${ADMIN_BASE}/script-folders/${encodeURIComponent(id)}`,
      { method: "PUT", body: JSON.stringify(body) },
    );
  },
  async deleteFolder(id: string): Promise<void> {
    await request(`${ADMIN_BASE}/script-folders/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },

  async listScripts(folderId?: string): Promise<BioScript[]> {
    const qs = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
    return request<BioScript[]>(`${ADMIN_BASE}/scripts${qs}`);
  },
  async getScript(id: string): Promise<BioScript> {
    return request<BioScript>(`${ADMIN_BASE}/scripts/${encodeURIComponent(id)}`);
  },
  async createScript(form: FormData): Promise<BioScript> {
    return requestUpload<BioScript>(`${ADMIN_BASE}/scripts`, form);
  },
  /** Bulk-import a directory upload: files carry webkitRelativePath filenames. */
  async importScripts(
    files: { file: File; relPath: string }[],
    folderName: string,
  ): Promise<{ folder_id: string; created: string[]; skipped: { path: string; reason: string }[] }> {
    const formData = new FormData();
    for (const { file, relPath } of files) formData.append("files", file, relPath);
    formData.append("folder_name", folderName);
    return requestUpload(`${ADMIN_BASE}/scripts/import`, formData);
  },
  /** Parse a Markdown doc into script metadata (upload-form pre-fill). */
  async parseMd(file: File): Promise<{
    name: string;
    version: string;
    description: string;
    category: string;
    valid_from: number | null;
    valid_until: number | null;
    runtime_min: number | null;
    cost: number | null;
    inputs: string;
    outputs: string;
  }> {
    const formData = new FormData();
    formData.append("md_file", file);
    return requestUpload(`${ADMIN_BASE}/scripts/parse-md`, formData);
  },
  async updateScript(
    id: string,
    body: Partial<
      Pick<
        BioScript,
        | "folder_id"
        | "name"
        | "description"
        | "tool_id"
        | "category"
        | "version"
        | "md_content"
        | "inputs"
        | "outputs"
        | "runtime_min"
        | "cost"
        | "weight"
        | "valid_from"
        | "valid_until"
      >
    >,
  ): Promise<BioScript> {
    return request<BioScript>(`${ADMIN_BASE}/scripts/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  },
  async verifyScript(id: string): Promise<BioScript> {
    return request<BioScript>(
      `${ADMIN_BASE}/scripts/${encodeURIComponent(id)}/verify`,
      { method: "PATCH" },
    );
  },
  async toggleScript(id: string): Promise<BioScript> {
    return request<BioScript>(
      `${ADMIN_BASE}/scripts/${encodeURIComponent(id)}/toggle`,
      { method: "PATCH" },
    );
  },
  async deleteScript(id: string): Promise<void> {
    await request(`${ADMIN_BASE}/scripts/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
};
