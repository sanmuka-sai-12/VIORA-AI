import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import type { MindMapGraph } from "../../api/client";
import { RichAcademicContent } from "../common/RichAcademicContent";
import { StatusBadge } from "../common/UiPrimitives";
import { buildMindMapLayout, getNodeBreadcrumb } from "./mindMapLayout";

const DEPTH_PALETTE: ReadonlyArray<{
  fill: string;
  stroke: string;
  text: string;
}> = [
  { fill: "#1d4ed8", stroke: "#1e40af", text: "#ffffff" },
  { fill: "#eff6ff", stroke: "#60a5fa", text: "#1e3a8a" },
  { fill: "#f8fafc", stroke: "#94a3b8", text: "#0f172a" },
  { fill: "#f0fdf4", stroke: "#4ade80", text: "#14532d" },
  { fill: "#faf5ff", stroke: "#c084fc", text: "#581c87" },
];

export interface MindMapViewerProps {
  graph: MindMapGraph;
}

export function MindMapViewer({ graph }: MindMapViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());
  const [selectedNodeId, setSelectedNodeId] = useState<string>(graph.root_id);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);

  const dragRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    origPanX: number;
    origPanY: number;
  }>({ active: false, startX: 0, startY: 0, origPanX: 0, origPanY: 0 });

  useEffect(() => {
    setCollapsedIds(new Set());
    setSelectedNodeId(graph.root_id);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [graph]);

  useEffect(() => {
    const onFsChange = () => {
      const fsEl = document.fullscreenElement;
      setIsFullscreen(Boolean(fsEl && fsEl === containerRef.current));
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  const layout = useMemo(
    () => buildMindMapLayout(graph, collapsedIds),
    [graph, collapsedIds],
  );

  const selectedNode = useMemo(
    () =>
      graph.nodes.find((n) => n.node_id === selectedNodeId) ??
      graph.nodes.find((n) => n.node_id === graph.root_id) ??
      graph.nodes[0],
    [graph, selectedNodeId],
  );

  const breadcrumb = useMemo(
    () => (selectedNode ? getNodeBreadcrumb(graph, selectedNode.node_id) : []),
    [graph, selectedNode],
  );

  const toggleCollapse = (nodeId: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  };

  const handleCollapseBranches = () => {
    const next = new Set<string>();
    for (const node of graph.nodes) {
      if (node.depth >= 1) {
        const hasChild = graph.nodes.some((c) => c.parent_id === node.node_id);
        if (hasChild) {
          next.add(node.node_id);
        }
      }
    }
    setCollapsedIds(next);
  };

  const handleExpandAll = () => {
    setCollapsedIds(new Set());
  };

  const handleResetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const handleToggleFullscreen = async () => {
    const el = containerRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement === el) {
        await document.exitFullscreen();
      } else if (el.requestFullscreen) {
        await el.requestFullscreen();
      } else {
        setIsFullscreen((prev) => !prev);
      }
    } catch {
      setIsFullscreen((prev) => !prev);
    }
  };

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if ((e.target as HTMLElement).closest("[data-mindmap-interactive='true']")) {
      return;
    }
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      origPanX: pan.x,
      origPanY: pan.y,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!dragRef.current.active) return;
    setPan({
      x: dragRef.current.origPanX + (e.clientX - dragRef.current.startX),
      y: dragRef.current.origPanY + (e.clientY - dragRef.current.startY),
    });
  };

  const handlePointerUp = () => {
    dragRef.current.active = false;
  };

  const handleWheel = (e: ReactWheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    setZoom((prev) => Math.max(0.45, Math.min(2.2, Number((prev * factor).toFixed(2)))));
  };

  return (
    <div
      ref={containerRef}
      className={`viora-mindmap-shell${isFullscreen ? " is-fullscreen" : ""}`}
    >
      <div className="viora-mindmap-toolbar">
        <div className="viora-row" style={{ flexWrap: "wrap", gap: "0.35rem" }}>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setZoom((z) => Math.min(2.2, Number((z + 0.15).toFixed(2))))}
          >
            Zoom +
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setZoom((z) => Math.max(0.45, Number((z - 0.15).toFixed(2))))}
          >
            Zoom −
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={handleResetView}
          >
            Reset ({Math.round(zoom * 100)}%)
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={handleExpandAll}
          >
            Expand All
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={handleCollapseBranches}
          >
            Collapse Branches
          </button>
        </div>

        <button
          type="button"
          className="viora-btn viora-btn-sm"
          onClick={() => void handleToggleFullscreen()}
        >
          {isFullscreen ? "Exit Fullscreen" : "Fullscreen"}
        </button>
      </div>

      <div className="viora-mindmap-canvas-wrap">
        <svg
          className="viora-mindmap-svg"
          role="img"
          aria-label={`Interactive mind map: ${graph.title}`}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onWheel={handleWheel}
        >
          <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
            {layout.edges.map((edge) => {
              const midX = (edge.fromX + edge.toX) / 2;
              const d = `M ${edge.fromX} ${edge.fromY} C ${midX} ${edge.fromY}, ${midX} ${edge.toY}, ${edge.toX} ${edge.toY}`;
              return (
                <g key={edge.id}>
                  <path
                    d={d}
                    fill="none"
                    stroke="#94a3b8"
                    strokeWidth={2}
                  />
                  {edge.relation && (
                    <text
                      x={midX}
                      y={(edge.fromY + edge.toY) / 2 - 5}
                      textAnchor="middle"
                      fontSize="10"
                      fill="#475569"
                      fontWeight="500"
                    >
                      {edge.relation}
                    </text>
                  )}
                </g>
              );
            })}

            {layout.nodes.map((placed) => {
              const { node } = placed;
              const palette =
                DEPTH_PALETTE[Math.min(node.depth, DEPTH_PALETTE.length - 1)];
              const isSelected = node.node_id === selectedNode?.node_id;
              const labelShort =
                node.label.length > 26
                  ? `${node.label.slice(0, 23)}...`
                  : node.label;

              return (
                <g
                  key={node.node_id}
                  transform={`translate(${placed.x}, ${placed.y})`}
                  data-mindmap-interactive="true"
                  style={{ cursor: "pointer" }}
                  onClick={() => setSelectedNodeId(node.node_id)}
                >
                  <rect
                    width={placed.width}
                    height={placed.height}
                    rx={10}
                    ry={10}
                    fill={palette.fill}
                    stroke={isSelected ? "#0f172a" : palette.stroke}
                    strokeWidth={isSelected ? 2.6 : 1.5}
                  />
                  <text
                    x={placed.hasChildren ? placed.width / 2 - 10 : placed.width / 2}
                    y={placed.height / 2 + 4}
                    textAnchor="middle"
                    fontSize="12.5"
                    fontWeight={node.depth === 0 ? "700" : "600"}
                    fill={palette.text}
                  >
                    {labelShort}
                  </text>

                  {placed.hasChildren && (
                    <g
                      transform={`translate(${placed.width - 22}, ${placed.height / 2 - 10})`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleCollapse(node.node_id);
                      }}
                    >
                      <rect
                        width={32}
                        height={20}
                        rx={10}
                        fill={placed.isCollapsed ? "#1d4ed8" : "#ffffff"}
                        stroke="#1d4ed8"
                        strokeWidth={1.2}
                      />
                      <text
                        x={16}
                        y={14}
                        textAnchor="middle"
                        fontSize="10.5"
                        fontWeight="700"
                        fill={placed.isCollapsed ? "#ffffff" : "#1d4ed8"}
                      >
                        {placed.isCollapsed
                          ? `+${placed.hiddenDescendantCount}`
                          : "−"}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {selectedNode && (
        <div className="viora-mindmap-detail">
          <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <div>
              <div className="viora-field-hint" style={{ marginBottom: "0.2rem" }}>
                {breadcrumb.map((b) => b.label).join(" → ")}
              </div>
              <h4 style={{ fontSize: "0.96rem", fontWeight: 700, color: "var(--color-brand-primary)" }}>
                {selectedNode.label}
              </h4>
            </div>
            {selectedNode.relation && (
              <StatusBadge tone="brand">{selectedNode.relation}</StatusBadge>
            )}
          </div>

          <RichAcademicContent content={selectedNode.explanation} />

          {selectedNode.key_formula && (
            <div style={{ marginTop: "0.35rem" }}>
              <RichAcademicContent content={`$$\n${selectedNode.key_formula}\n$$`} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
