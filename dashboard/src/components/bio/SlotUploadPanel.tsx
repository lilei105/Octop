import { useState } from "react";
import { Button, Card, Space, Tag, Typography, Upload, message } from "antd";
import { Upload as UploadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  confirmBioUpload,
  uploadBioFile,
  type BioFileMapping,
  type BioFileSlot,
  type BioTask,
} from "../../api/modules/bio";

interface Props {
  agentId: string;
  task: BioTask;
  slots: BioFileSlot[];
  onUpdated: (task: BioTask) => void;
}

/** uploading status — one upload button per declared slot, then confirm. */
export default function SlotUploadPanel({ agentId, task, slots, onUpdated }: Props) {
  const { t } = useTranslation();
  // slot label -> uploaded mapping
  const [filled, setFilled] = useState<Record<string, BioFileMapping>>({});
  const [uploading, setUploading] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const allFilled = slots.every((s) => filled[s.label]);

  const doUpload = async (slot: BioFileSlot, file: File) => {
    setUploading(slot.label);
    try {
      const resp = await uploadBioFile(agentId, file);
      setFilled((prev) => ({
        ...prev,
        [slot.label]: {
          slot_label: slot.label,
          workspace_path: resp.workspace_path,
          original_name: resp.filename,
        },
      }));
    } catch (err) {
      void message.error(String(err));
    } finally {
      setUploading(null);
    }
  };

  const confirm = async () => {
    setSubmitting(true);
    try {
      const updated = await confirmBioUpload(agentId, task.id, Object.values(filled));
      onUpdated(updated);
    } catch (err) {
      void message.error(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card size="small" title={t("bio.upload.title", "上传输入文件")}>
      <Space direction="vertical" style={{ width: "100%" }}>
        {slots.map((slot) => {
          const mapping = filled[slot.label];
          return (
            <div key={slot.label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Typography.Text style={{ minWidth: 120 }}>{slot.label}</Typography.Text>
              {slot.extensions?.length > 0 && (
                <Tag>{slot.extensions.join(" / ")}</Tag>
              )}
              {mapping ? (
                <Tag color="green">{mapping.original_name}</Tag>
              ) : (
                <Upload
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void doUpload(slot, file);
                    return false;
                  }}
                >
                  <Button
                    size="small"
                    icon={<UploadIcon size={13} />}
                    loading={uploading === slot.label}
                  >
                    {t("bio.upload.pick", "选择文件")}
                  </Button>
                </Upload>
              )}
            </div>
          );
        })}
        <Button type="primary" disabled={!allFilled} loading={submitting} onClick={confirm}>
          {t("bio.upload.confirm", "确认上传")}
        </Button>
      </Space>
    </Card>
  );
}
