import { memo, useMemo } from "react";
import { ReactFlow, Background, Handle, Position } from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { FileInput, FileOutput, Code2 } from "lucide-react";
import type { BioCandidateChain } from "../../api/modules/bio";

/**
 * Read-only visualization of one candidate analysis chain:
 * input → script₁ → … → scriptₙ → output (vertical flow).
 * Ported from bioinformatics-frontend flow-nodes.tsx, restyled for AntD.
 */

type NodeData = Record<string, unknown>;

const nodeBox: React.CSSProperties = {
  padding: "6px 10px",
  borderRadius: 8,
  background: "var(--color-bg-container, #fff)",
  border: "1px solid var(--color-border, #d9d9d9)",
  fontSize: 12,
  minWidth: 140,
  maxWidth: 200,
};

function ChainNode({ data }: NodeProps<Node<NodeData>>) {
  const kind = data.kind as "input" | "script" | "output";
  const color =
    kind === "input" ? "#1677ff" : kind === "output" ? "#52c41a" : "#722ed1";
  const Icon = kind === "input" ? FileInput : kind === "output" ? FileOutput : Code2;
  return (
    <div style={{ ...nodeBox, borderLeft: `3px solid ${color}` }}>
      {kind !== "input" && <Handle type="target" position={Position.Top} />}
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Icon size={13} color={color} />
        <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>
          {(data.label as string) ?? ""}
        </span>
      </div>
      {kind === "script" && typeof data.toolId === "string" && (
        <div style={{ color: "#999", fontSize: 10, marginTop: 2 }}>{data.toolId}</div>
      )}
      {kind !== "output" && <Handle type="source" position={Position.Bottom} />}
    </div>
  );
}

const MemoNode = memo(ChainNode);
const nodeTypes = { bioChain: MemoNode };

export function chainToFlow(chain: BioCandidateChain): {
  nodes: Node<NodeData>[];
  edges: Edge[];
} {
  const nodes: Node<NodeData>[] = [];
  const edges: Edge[] = [];
  const stepIds = chain.tool_chain.map((t, i) => `s-${i}-${t.id}`);
  const ids = ["__input__", ...stepIds, "__output__"];

  nodes.push({
    id: ids[0],
    type: "bioChain",
    position: { x: 0, y: 0 },
    data: { kind: "input", label: "input" },
  });
  chain.tool_chain.forEach((tool, i) => {
    nodes.push({
      id: stepIds[i],
      type: "bioChain",
      position: { x: 0, y: (i + 1) * 80 },
      data: { kind: "script", label: tool.name, toolId: tool.id },
    });
  });
  nodes.push({
    id: ids[ids.length - 1],
    type: "bioChain",
    position: { x: 0, y: (chain.tool_chain.length + 1) * 80 },
    data: { kind: "output", label: "output" },
  });
  for (let i = 0; i < ids.length - 1; i++) {
    edges.push({ id: `e-${i}`, source: ids[i], target: ids[i + 1] });
  }
  return { nodes, edges };
}

export default function BioFlowGraph({ chain }: { chain: BioCandidateChain }) {
  const { nodes, edges } = useMemo(() => chainToFlow(chain), [chain]);
  const height = (chain.tool_chain.length + 2) * 80 + 20;
  return (
    <div style={{ width: "100%", height, minHeight: 180 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnDrag={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
      </ReactFlow>
    </div>
  );
}
