import { useState } from "react";
import { Button, Card, Modal, Space, Tag, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import {
  confirmBioPath,
  type BioCandidateChain,
  type BioTask,
} from "../../api/modules/bio";
import BioFlowGraph from "./BioFlowGraph";

interface Props {
  agentId: string;
  task: BioTask;
  candidates: BioCandidateChain[];
  onUpdated: (task: BioTask) => void;
}

/** pathSelection status — user picks one candidate chain and confirms. */
export default function PathSelectionCard({ agentId, task, candidates, onUpdated }: Props) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<BioCandidateChain | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const confirm = async () => {
    if (!selected) return;
    setSubmitting(true);
    try {
      const chain = candidates.find((c) => c.id === selected);
      const updated = await confirmBioPath(
        agentId,
        task.id,
        selected,
        chain ? JSON.stringify(chain) : "",
      );
      onUpdated(updated);
    } catch (err) {
      void message.error(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card size="small" title={t("bio.pathSelection.title", "选择分析路径")}>
      <Space direction="vertical" style={{ width: "100%" }}>
        {candidates.map((cand, idx) => (
          <Card
            key={cand.id || idx}
            size="small"
            hoverable
            onClick={() => setSelected(cand.id)}
            style={{
              borderColor: selected === cand.id ? "#1677ff" : undefined,
              borderWidth: selected === cand.id ? 2 : 1,
            }}
          >
            <Space wrap>
              <Typography.Text strong>
                {t("bio.pathSelection.chain", "路径 {{n}}", { n: idx + 1 })}
              </Typography.Text>
              {cand.previously_used && (
                <Tag color="green">{t("bio.pathSelection.usedBefore", "之前用过")}</Tag>
              )}
              {typeof cand.total_runtime === "number" && cand.total_runtime > 0 && (
                <Tag>{t("bio.pathSelection.runtime", "约 {{m}} 分钟", { m: cand.total_runtime })}</Tag>
              )}
            </Space>
            <div style={{ marginTop: 6 }}>
              {cand.tool_chain.map((tool, i) => (
                <Tag key={`${tool.id}-${i}`} color="purple">
                  {i + 1}. {tool.name}
                </Tag>
              ))}
            </div>
            <Button
              type="link"
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setPreview(cand);
              }}
            >
              {t("bio.pathSelection.previewGraph", "预览流程图")}
            </Button>
          </Card>
        ))}
        <Button
          type="primary"
          disabled={!selected}
          loading={submitting}
          onClick={confirm}
        >
          {t("bio.pathSelection.confirm", "确认所选路径")}
        </Button>
      </Space>
      <Modal
        open={preview !== null}
        footer={null}
        onCancel={() => setPreview(null)}
        title={t("bio.pathSelection.graphTitle", "分析流程")}
        width={420}
      >
        {preview && <BioFlowGraph chain={preview} />}
      </Modal>
    </Card>
  );
}
