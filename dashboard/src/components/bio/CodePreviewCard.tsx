import { useState } from "react";
import { Button, Card, Space, Tag, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import { startBioExecution, type BioTask } from "../../api/modules/bio";

interface Props {
  agentId: string;
  task: BioTask;
  onUpdated: (task: BioTask) => void;
}

/** validating status — read-only code preview + explicit approval (HITL gate). */
export default function CodePreviewCard({ agentId, task, onUpdated }: Props) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);

  const approve = async () => {
    setSubmitting(true);
    try {
      const updated = await startBioExecution(agentId, task.id);
      onUpdated(updated);
    } catch (err) {
      void message.error(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card
      size="small"
      title={
        <Space>
          {t("bio.codePreview.title", "代码审批")}
          <Tag color={task.code_origin === "library" ? "blue" : "orange"}>
            {task.code_origin === "library"
              ? t("bio.codePreview.originLibrary", "脚本库组装")
              : t("bio.codePreview.originLlm", "模型生成")}
          </Tag>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t(
          "bio.codePreview.hint",
          "请检查以下将要执行的代码。确认后将立即在本机运行。",
        )}
      </Typography.Paragraph>
      <pre
        style={{
          maxHeight: 320,
          overflow: "auto",
          background: "rgba(127,127,127,0.08)",
          padding: 12,
          borderRadius: 8,
          fontSize: 12,
        }}
      >
        <code>{task.generated_code}</code>
      </pre>
      <Button type="primary" loading={submitting} onClick={approve} style={{ marginTop: 8 }}>
        {t("bio.codePreview.approve", "批准并执行")}
      </Button>
    </Card>
  );
}
