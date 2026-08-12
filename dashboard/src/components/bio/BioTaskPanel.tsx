import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getBioTask,
  listBioTasks,
  type BioCandidateChain,
  type BioFileSlot,
  type BioTask,
} from "../../api/modules/bio";
import { onBioEvent } from "../../pages/Chat/hooks/chatStore";
import PathSelectionCard from "./PathSelectionCard";
import SlotUploadPanel from "./SlotUploadPanel";
import CodePreviewCard from "./CodePreviewCard";
import ExecutionLogPanel from "./ExecutionLogPanel";

const TERMINAL = new Set(["completed", "failed"]);

interface Props {
  agentId: string;
  threadId: string;
}

function parseJson<T>(raw: string, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/**
 * Bio task panel rendered under the chat message list. Task state always
 * comes from REST (custom chunks only trigger a refetch), so the panel
 * rebuilds correctly on session reload.
 */
export default function BioTaskPanel({ agentId, threadId }: Props) {
  const { t } = useTranslation();
  const [task, setTask] = useState<BioTask | null>(null);

  const refresh = useCallback(
    async (taskId?: string) => {
      try {
        if (taskId) {
          setTask(await getBioTask(agentId, taskId));
          return;
        }
        const tasks = await listBioTasks(agentId, threadId);
        const active = tasks.find((tk) => !TERMINAL.has(tk.status)) ?? tasks[0] ?? null;
        setTask(active);
      } catch {
        /* agent may not exist yet / no tasks */
      }
    },
    [agentId, threadId],
  );

  useEffect(() => {
    setTask(null);
    void refresh();
  }, [refresh]);

  useEffect(
    () =>
      onBioEvent((ev) => {
        if (ev.sessionId !== threadId) return;
        void refresh(ev.taskId);
      }),
    [threadId, refresh],
  );

  if (!task) return null;

  const candidates = parseJson<BioCandidateChain[]>(task.candidates_json, []);
  const slots = parseJson<BioFileSlot[]>(task.required_files_json, []);

  return (
    <div style={{ padding: "0 16px 12px" }} aria-label={t("bio.panel", "生信分析任务")}>
      {task.status === "pathSelection" && candidates.length > 0 && (
        <PathSelectionCard
          agentId={agentId}
          task={task}
          candidates={candidates}
          onUpdated={setTask}
        />
      )}
      {task.status === "uploading" && (
        <SlotUploadPanel agentId={agentId} task={task} slots={slots} onUpdated={setTask} />
      )}
      {(task.status === "generating" || task.status === "validating") &&
        task.generated_code && (
          <CodePreviewCard agentId={agentId} task={task} onUpdated={setTask} />
        )}
      {(task.status === "executing" || TERMINAL.has(task.status)) && task.run_dir && (
        <ExecutionLogPanel agentId={agentId} task={task} onUpdated={setTask} />
      )}
    </div>
  );
}
