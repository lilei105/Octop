import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Switch,
  Table,
  Tag,
  Tree,
  Upload,
  message,
} from "antd";
import type { DataNode } from "antd/es/tree";
import { CheckCircle, FilePlus, FlaskConical, FolderOpen, FolderPlus, Pencil, Trash2, Upload as UploadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import {
  bioAdminApi,
  type BioScript,
  type BioScriptFolder,
} from "../../../api/modules/bio";

export default function AdminBioScriptsPage() {
  const { t } = useTranslation();
  const [folders, setFolders] = useState<BioScriptFolder[]>([]);
  const [scripts, setScripts] = useState<BioScript[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [folderModal, setFolderModal] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState<string[]>([]);
  const [uploadMode, setUploadMode] = useState<"single" | "folder">("single");
  const [dirFiles, setDirFiles] = useState<{ file: File; relPath: string }[]>([]);
  const [editing, setEditing] = useState<BioScript | null>(null);
  const [form] = Form.useForm();
  // native File handles captured at pick time — UploadFile.originFileObj is
  // not reliable across rc-upload versions and cost us a silent 422 once
  const scriptFileRef = useRef<File | null>(null);
  const mdFileRef = useRef<File | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [flds, scrs] = await Promise.all([
        bioAdminApi.listFolders(),
        bioAdminApi.listScripts(selectedFolder ?? undefined),
      ]);
      setFolders(flds);
      setScripts(scrs);
    } catch (err) {
      void message.error(String(err));
    } finally {
      setLoading(false);
    }
  }, [selectedFolder]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const treeData = useMemo<DataNode[]>(() => {
    const byParent = new Map<string, BioScriptFolder[]>();
    for (const f of folders) {
      const key = f.parent_id || "";
      byParent.set(key, [...(byParent.get(key) ?? []), f]);
    }
    const build = (parentKey: string): DataNode[] =>
      (byParent.get(parentKey) ?? [])
        .sort((a, b) => a.sort_order - b.sort_order)
        .map((f) => ({ key: f.id, title: f.name, children: build(f.id) }));
    return build("");
  }, [folders]);

  const createFolder = async () => {
    if (!folderName.trim()) return;
    try {
      await bioAdminApi.createFolder({
        name: folderName.trim(),
        parent_id: selectedFolder ?? "",
      });
      setFolderModal(false);
      setFolderName("");
      await reload();
    } catch (err) {
      void message.error(String(err));
    }
  };

  const saveScript = async (values: Record<string, unknown>) => {
    if (!editing && uploadMode === "folder") {
      await saveFolderImport();
      return;
    }
    try {
      if (editing) {
        await bioAdminApi.updateScript(editing.id, values);
      } else {
        const formData = new FormData();
        for (const [k, v] of Object.entries(values)) {
          // scalars only — UploadFile objects are appended from the refs below
          if (v === undefined || v === null || typeof v === "object") continue;
          formData.append(k, String(v));
        }
        if (scriptFileRef.current) formData.append("script_file", scriptFileRef.current);
        if (mdFileRef.current) formData.append("md_file", mdFileRef.current);
        await bioAdminApi.createScript(formData);
      }
      setDrawerOpen(false);
      setEditing(null);
      form.resetFields();
      scriptFileRef.current = null;
      mdFileRef.current = null;
      await reload();
    } catch (err) {
      void message.error(String(err));
    }
  };

  // ── folder bulk import ────────────────────────────────────────────────
  const IMPORT_EXTS = /\.(sh|py|pl|r|md)$/i;
  const IMPORT_SKIP_DIRS = /(^|\/)(examples?|data|tests?)(\/|$)/i;

  /** Directory upload: keep only script/doc files, remember relative paths. */
  const onDirFilePicked = (file: File) => {
    const rel =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    const base = rel.split("/").pop() ?? rel;
    if (
      !IMPORT_EXTS.test(base) ||
      base.toLowerCase().startsWith("readme") ||
      IMPORT_SKIP_DIRS.test(rel)
    ) {
      return false;
    }
    setDirFiles((prev) => [...prev, { file, relPath: rel }]);
    const root = rel.includes("/") ? rel.split("/")[0] : "";
    if (root && !form.getFieldValue("folder_name")) {
      form.setFieldsValue({ folder_name: root });
    }
    return false;
  };

  const saveFolderImport = async () => {
    if (dirFiles.length === 0) {
      void message.warning(t("adminBio.folderEmpty", "请先选择包含脚本的文件夹"));
      return;
    }
    try {
      const res = await bioAdminApi.importScripts(
        dirFiles,
        (form.getFieldValue("folder_name") as string) ?? "",
      );
      setDrawerOpen(false);
      setDirFiles([]);
      form.resetFields();
      Modal.info({
        title: t("adminBio.importResult", "导入结果"),
        content: (
          <div>
            <p>{t("adminBio.importCreated", "已导入 {{count}} 个脚本", { count: res.created.length })}</p>
            {res.created.length > 0 && (
              <p style={{ maxHeight: 160, overflow: "auto", fontSize: 12 }}>{res.created.join("、")}</p>
            )}
            {res.skipped.length > 0 && (
              <p style={{ color: "#999", fontSize: 12 }}>
                {t("adminBio.importSkipped", "跳过 {{count}} 个（重名或非脚本/文档文件）", {
                  count: res.skipped.length,
                })}
              </p>
            )}
          </div>
        ),
      });
      await reload();
    } catch (err) {
      void message.error(String(err));
    }
  };

  /** Picking a .md doc pre-fills the form from its declared metadata. */
  const onMdFilePicked = async (file: File) => {
    let meta;
    try {
      meta = await bioAdminApi.parseMd(file);
    } catch {
      void message.warning(t("adminBio.mdParseFailed", "说明文档解析失败，请手动填写"));
      return;
    }
    const hasMeta =
      meta.name ||
      meta.version ||
      meta.description ||
      meta.category ||
      meta.runtime_min ||
      meta.cost ||
      (meta.inputs && meta.inputs !== "[]") ||
      (meta.outputs && meta.outputs !== "[]");
    if (!hasMeta) {
      void message.info(
        t(
          "adminBio.mdNothingParsed",
          "未从文档解析到元数据，请确认包含「## 名称:」「## 输入」等小节",
        ),
      );
      return;
    }
    const patch: Record<string, unknown> = {};
    const current = form.getFieldsValue();
    if (meta.name && !current.name) patch.name = meta.name;
    if (meta.version && !current.version) patch.version = meta.version;
    if (meta.description && !current.description) patch.description = meta.description;
    if (meta.category && !current.category) patch.category = meta.category;
    if (meta.runtime_min && !current.runtime_min) patch.runtime_min = meta.runtime_min;
    if (meta.cost && !current.cost) patch.cost = meta.cost;
    if (meta.inputs && meta.inputs !== "[]" && !current.inputs) patch.inputs = meta.inputs;
    if (meta.outputs && meta.outputs !== "[]" && !current.outputs) patch.outputs = meta.outputs;
    if (Object.keys(patch).length > 0) {
      form.setFieldsValue(patch);
      setAdvancedOpen(["advanced"]); // show the user what was parsed
      void message.success(t("adminBio.mdParsed", "已从说明文档解析元数据"));
    } else {
      void message.info(t("adminBio.mdAlreadyFilled", "文档中的字段表单里已有值，未覆盖"));
    }
  };

  const expired = (s: BioScript) =>
    s.valid_until !== null && s.valid_until * 1000 < Date.now();

  const columns = [
    {
      title: t("adminBio.colName", "名称"),
      dataIndex: "name",
      render: (name: string, s: BioScript) => (
        <Space>
          <span style={{ opacity: expired(s) || !s.is_active ? 0.45 : 1 }}>{name}</span>
          {expired(s) && <Tag color="red">{t("adminBio.expired", "已过期")}</Tag>}
        </Space>
      ),
    },
    { title: t("adminBio.colCategory", "分类"), dataIndex: "category", width: 110 },
    { title: t("adminBio.colVersion", "版本"), dataIndex: "version", width: 90 },
    {
      title: t("adminBio.colVerified", "已验证"),
      dataIndex: "verified",
      width: 90,
      render: (v: boolean, s: BioScript) =>
        v ? (
          <Tag color="green">{t("adminBio.verified", "已验证")}</Tag>
        ) : (
          <Button size="small" onClick={() => void bioAdminApi.verifyScript(s.id).then(reload)}>
            {t("adminBio.verify", "验证")}
          </Button>
        ),
    },
    {
      title: t("adminBio.colActive", "启用"),
      dataIndex: "is_active",
      width: 80,
      render: (v: boolean, s: BioScript) => (
        <Switch
          size="small"
          checked={v}
          onChange={() => void bioAdminApi.toggleScript(s.id).then(reload)}
        />
      ),
    },
    {
      title: t("adminBio.colActions", "操作"),
      width: 110,
      render: (_: unknown, s: BioScript) => (
        <Space>
          <Button
            size="small"
            type="text"
            icon={<Pencil size={14} />}
            onClick={() => {
              setEditing(s);
              form.setFieldsValue(s);
              setDrawerOpen(true);
            }}
          />
          <Popconfirm
            title={t("adminBio.deleteConfirm", "删除该脚本？")}
            onConfirm={() => void bioAdminApi.deleteScript(s.id).then(reload)}
          >
            <Button size="small" type="text" danger icon={<Trash2 size={14} />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <PageShell
      title={t("adminBio.title", "生信脚本库")}
      subtitle={t("adminBio.subtitle", "管理可供分析路径规划使用的脚本")}
      actions={
        <Space>
          <Button icon={<FolderPlus size={14} />} onClick={() => setFolderModal(true)}>
            {t("adminBio.newFolder", "新建文件夹")}
          </Button>
          <Button
            type="primary"
            icon={<FilePlus size={14} />}
            onClick={() => {
              setEditing(null);
              form.resetFields();
              setAdvancedOpen([]);
              setUploadMode("single");
              setDirFiles([]);
              scriptFileRef.current = null;
              mdFileRef.current = null;
              setDrawerOpen(true);
            }}
          >
            {t("adminBio.newScript", "上传脚本")}
          </Button>
        </Space>
      }
    >
      <div style={{ display: "flex", gap: 16, minHeight: 400 }}>
        <div style={{ width: 220, borderRight: "1px solid rgba(127,127,127,0.15)", paddingRight: 8 }}>
          <Tree
            treeData={treeData}
            selectedKeys={selectedFolder ? [selectedFolder] : []}
            onSelect={(keys) => setSelectedFolder((keys[0] as string) ?? null)}
            defaultExpandAll
          />
          {folders.length === 0 && (
            <div style={{ color: "#999", fontSize: 12, padding: 8 }}>
              <FlaskConical size={14} style={{ marginRight: 4 }} />
              {t("adminBio.noFolders", "暂无文件夹")}
            </div>
          )}
          {selectedFolder && (
            <Button size="small" type="link" onClick={() => setSelectedFolder(null)}>
              {t("adminBio.showAll", "显示全部")}
            </Button>
          )}
        </div>
        <div style={{ flex: 1 }}>
          <Table<BioScript>
            rowKey="id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={scripts}
            pagination={false}
          />
        </div>
      </div>

      <Modal
        open={folderModal}
        title={t("adminBio.newFolder", "新建文件夹")}
        onOk={() => void createFolder()}
        onCancel={() => setFolderModal(false)}
      >
        <Input
          value={folderName}
          onChange={(e) => setFolderName(e.target.value)}
          placeholder={t("adminBio.folderName", "文件夹名称")}
        />
      </Modal>

      <Drawer
        open={drawerOpen}
        width={480}
        title={editing ? t("adminBio.editScript", "编辑脚本") : t("adminBio.newScript", "上传脚本")}
        onClose={() => {
          setDrawerOpen(false);
          setEditing(null);
        }}
      >
        <Form form={form} layout="vertical" onFinish={(v) => void saveScript(v)}>
          {!editing && (
            <Segmented
              block
              style={{ marginBottom: 16 }}
              value={uploadMode}
              onChange={(v) => setUploadMode(v as "single" | "folder")}
              options={[
                { label: t("adminBio.modeSingle", "单个脚本"), value: "single" },
                { label: t("adminBio.modeFolder", "文件夹批量导入"), value: "folder" },
              ]}
            />
          )}
          {!editing && uploadMode === "folder" ? (
            <>
              <Form.Item
                label={t("adminBio.scriptDir", "脚本库目录")}
                required
                tooltip={t(
                  "adminBio.scriptDirHint",
                  "选择包含 scripts/（.sh）和 reference/（.md）的目录，同名文件自动配对；示例数据目录会被忽略",
                )}
              >
                <Upload
                  directory
                  multiple
                  beforeUpload={(f) => onDirFilePicked(f)}
                  showUploadList={false}
                >
                  <Button icon={<FolderOpen size={13} />}>
                    {t("adminBio.pickFolder", "选择文件夹")}
                  </Button>
                </Upload>
                {dirFiles.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Tag color="blue">
                      {t("adminBio.filesSelected", "已选 {{count}} 个脚本/文档", {
                        count: dirFiles.length,
                      })}
                    </Tag>
                    <Button size="small" type="link" onClick={() => setDirFiles([])}>
                      {t("adminBio.clearFiles", "清空重选")}
                    </Button>
                  </div>
                )}
              </Form.Item>
              <Form.Item
                name="folder_name"
                label={t("adminBio.libFolderName", "库文件夹")}
                tooltip={t("adminBio.libFolderHint", "脚本导入到该文件夹下；不存在则自动创建")}
              >
                <Input placeholder="Amplicon" />
              </Form.Item>
            </>
          ) : (
            <>
              {!editing && (
                <>
                  <Form.Item
                    name="script_file"
                    label={t("adminBio.scriptFile", "脚本文件")}
                    valuePropName="file"
                    getValueFromEvent={(e) => e?.file}
                    rules={[{ required: true }]}
                  >
                    <Upload
                      beforeUpload={(file) => {
                        scriptFileRef.current = file;
                        if (!form.getFieldValue("name")) {
                          form.setFieldsValue({ name: file.name.replace(/\.\w+$/, "") });
                        }
                        return false;
                      }}
                      maxCount={1}
                    >
                      <Button icon={<UploadIcon size={13} />}>{t("adminBio.pickFile", "选择文件")}</Button>
                    </Upload>
                  </Form.Item>
                  <Form.Item
                    name="md_file"
                    label={t("adminBio.mdFile", "说明文档（Markdown）")}
                    valuePropName="file"
                    getValueFromEvent={(e) => e?.file}
                    tooltip={t(
                      "adminBio.mdHint",
                      "自动解析：## 名称/版本 等键值标题、基本信息表（Tool Name/估算机时/报价分数）、输入输出表（文件名+格式）",
                    )}
                  >
                    <Upload
                      beforeUpload={(file) => {
                        mdFileRef.current = file;
                        void onMdFilePicked(file);
                        return false;
                      }}
                      maxCount={1}
                    >
                      <Button icon={<UploadIcon size={13} />}>{t("adminBio.pickFile", "选择文件")}</Button>
                    </Upload>
                  </Form.Item>
                  <Form.Item name="folder_id" initialValue={selectedFolder ?? ""} hidden>
                    <Input />
                  </Form.Item>
                </>
              )}
              {editing && (
                <Form.Item name="md_content" label={t("adminBio.mdFile", "说明文档（Markdown）")}>
                  <Input.TextArea rows={6} />
                </Form.Item>
              )}
              <Form.Item
                name="name"
                label={t("adminBio.colName", "名称")}
                tooltip={t("adminBio.nameHint", "留空则从说明文档或脚本文件名推导")}
              >
                <Input />
              </Form.Item>
          <Collapse
            ghost
            activeKey={advancedOpen}
            onChange={(keys) => setAdvancedOpen(keys as string[])}
            items={[
              {
                key: "advanced",
                label: t("adminBio.advanced", "高级选项（通常无需填写）"),
                children: (
                  <>
                    <Form.Item name="description" label={t("adminBio.description", "描述")}>
                      <Input.TextArea rows={2} />
                    </Form.Item>
                    <Form.Item name="category" label={t("adminBio.colCategory", "分类")}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="version" label={t("adminBio.colVersion", "版本")}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="tool_id" label="tool_id">
                      <Input />
                    </Form.Item>
                    <Form.Item name="runtime_min" label={t("adminBio.runtimeMin", "预计耗时（分钟）")}>
                      <InputNumber min={0} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item name="cost" label={t("adminBio.cost", "报价分数")}>
                      <InputNumber min={0} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item name="weight" label={t("adminBio.weight", "权重")}>
                      <InputNumber min={0} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name="inputs"
                      label={t("adminBio.inputs", "输入槽位（JSON）")}
                      tooltip='[{"label":"reads","extensions":["fastq"],"multiple":false}]'
                    >
                      <Input.TextArea rows={3} />
                    </Form.Item>
                    <Form.Item
                      name="outputs"
                      label={t("adminBio.outputs", "输出文件（JSON）")}
                      tooltip='[{"label":"report","extensions":["html","json"],"multiple":false}]'
                    >
                      <Input.TextArea rows={3} />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
            </>
          )}
          <Button type="primary" htmlType="submit" block icon={<CheckCircle size={14} />}>
            {uploadMode === "folder" && !editing
              ? t("adminBio.importSubmit", "开始导入")
              : t("common.save", "保存")}
          </Button>
        </Form>
      </Drawer>
    </PageShell>
  );
}
