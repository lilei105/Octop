import { useEffect, useRef, useState } from "react";
import { Card, Space, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";
import {
  getBioExecutionLogs,
  type BioExecutionLogs,
  type BioTask,
} from "../../api/modules/bio";

const POLL_MS = 5000;

interface Props {
  agentId: string;
  task: BioTask;
  onUpdated: (task: BioTask) => void;
}

/** executing/completed/failed status — incremental run.log with 5s polling. */
export default function ExecutionLogPanel({ agentId, task, onUpdated }: Props) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState("");
  const [state, setState] = useState<BioExecutionLogs | null>(null);
  const offsetRef = useRef(0);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const resp = await getBioExecutionLogs(agentId, task.id, offsetRef.current);
        if (cancelled) return;
        offsetRef.current = resp.offset;
        if (resp.logs) setLogs((prev) => prev + resp.logs);
        setState(resp);
        if (resp.completed) {
          onUpdated({ ...task, status: resp.status as BioTask["status"] });
          return; // stop polling
        }
      } catch {
        /* transient — keep polling */
      }
      if (!cancelled) timer = setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, task.id]);

  useEffect(() => {
    preRef.current?.scrollTo({ top: preRef.current.scrollHeight });
  }, [logs]);

  const running = !state?.completed;
  const statusColor =
    state?.status === "completed" ? "green" : state?.status === "failed" ? "red" : "processing";

  return (
    <Card
      size="small"
      title={
        <Space>
          {t("bio.execution.title", "执行日志")}
          <Tag color={statusColor}>
            {t(`bio.execution.status.${state?.status ?? task.status}`, state?.status ?? task.status)}
          </Tag>
          {running && <Spin size="small" />}
        </Space>
      }
    >
      <pre
        ref={preRef}
        style={{
          maxHeight: 320,
          minHeight: 80,
          overflow: "auto",
          background: "rgba(127,127,127,0.08)",
          padding: 12,
          borderRadius: 8,
          fontSize: 12,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}
      >
        {logs || t("bio.execution.waiting", "等待日志输出…")}
      </pre>
      {state?.error_message && (
        <div style={{ color: "#ff4d4f", marginTop: 8, fontSize: 12 }}>
          {state.error_message}
        </div>
      )}
    </Card>
  );
}
