"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import * as d3 from "d3";
import type { IntelligenceNode, IntelligenceEdge } from "@/lib/intelligence-types";
import {
  DRIVER_COLOR_ACTIVE, DRIVER_COLOR_INACTIVE, DRIVER_COLOR_BLOCKED,
  THEME_COLORS, THEME_COLOR_DEFAULT,
  SYMBOL_COLOR_HOT, SYMBOL_COLOR_ACTIVE, SYMBOL_COLOR_IDLE,
} from "@/lib/intelligence-types";

interface Props {
  nodes: IntelligenceNode[];
  edges: IntelligenceEdge[];
  activeDriverIds: Set<string>;
  blockedIds: Set<string>;
  hotSymbols: Set<string>;
  onSelect: (node: IntelligenceNode | null) => void;
  selected: IntelligenceNode | null;
}

interface SimNode extends IntelligenceNode {
  x?: number; y?: number; vx?: number; vy?: number;
  fx?: number | null; fy?: number | null;
}

// SimEdge uses string source/target for input; D3 resolves them to SimNode at runtime
interface SimEdge extends d3.SimulationLinkDatum<SimNode> {
  source: string | SimNode;
  target: string | SimNode;
  type: "activates" | "exposes";
  strength?: number;
  driver_id?: string;
  exposure_type?: string;
  bucket_id?: string;
}

function nodeColor(n: IntelligenceNode, activeDriverIds: Set<string>, blockedIds: Set<string>, hotSymbols: Set<string>): string {
  if (n.type === "driver") {
    if (blockedIds.has(n.id)) return DRIVER_COLOR_BLOCKED;
    if (activeDriverIds.has(n.id)) return DRIVER_COLOR_ACTIVE;
    return DRIVER_COLOR_INACTIVE;
  }
  if (n.type === "theme") {
    const hasActive = n.status === "active" || (n.driver_ids ?? []).some(d => activeDriverIds.has(d));
    const base = THEME_COLORS[n.id] ?? THEME_COLOR_DEFAULT;
    return hasActive ? base : "#1e293b";
  }
  // symbol
  if (hotSymbols.has(n.id) && (n.driver_ids ?? []).some(d => activeDriverIds.has(d))) return SYMBOL_COLOR_HOT;
  if ((n.driver_ids ?? []).some(d => activeDriverIds.has(d))) return SYMBOL_COLOR_ACTIVE;
  return SYMBOL_COLOR_IDLE;
}

function nodeRadius(n: IntelligenceNode): number {
  if (n.type === "driver") return 22;
  if (n.type === "theme")  return 15;
  const conf = n.confidence ?? 0.7;
  return 6 + conf * 8; // 6–14
}

function glowId(n: IntelligenceNode, activeDriverIds: Set<string>, blockedIds: Set<string>, hotSymbols: Set<string>): string {
  if (n.type === "driver" && activeDriverIds.has(n.id)) return "glow-driver";
  if (n.type === "theme"  && (n.driver_ids ?? []).some(d => activeDriverIds.has(d))) return "glow-theme";
  if (n.type === "symbol" && hotSymbols.has(n.id) && (n.driver_ids ?? []).some(d => activeDriverIds.has(d))) return "glow-hot";
  return "none";
}

export default function IntelligenceGraph({ nodes, edges, activeDriverIds, blockedIds, hotSymbols, onSelect, selected }: Props) {
  const svgRef  = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const simRef  = useRef<d3.Simulation<SimNode, SimEdge> | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) setDims({ w: rect.width, h: rect.height });
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setDims({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const build = useCallback(() => {
    if (!svgRef.current || nodes.length === 0 || !dims) return;
    const { w: W, h: H } = dims;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    // ─── Defs: glow filters ───────────────────────────────────────────────
    const defs = svg.append("defs");
    const addGlow = (id: string, color: string, std: number, opacity: number) => {
      const f = defs.append("filter").attr("id", id)
        .attr("x", "-100%").attr("y", "-100%").attr("width", "300%").attr("height", "300%");
      f.append("feGaussianBlur").attr("in", "SourceGraphic").attr("stdDeviation", std)
        .attr("result", "blur");
      const comp = f.append("feComponentTransfer").attr("in", "blur").attr("result", "colored");
      comp.append("feFuncR").attr("type", "linear").attr("slope", 0);
      comp.append("feFuncG").attr("type", "linear").attr("slope", 0);
      comp.append("feFuncB").attr("type", "linear").attr("slope", 0);
      const flood = f.append("feFlood").attr("flood-color", color).attr("flood-opacity", opacity).attr("result", "flood");
      f.append("feComposite").attr("in", "flood").attr("in2", "blur").attr("operator", "in").attr("result", "glow");
      const merge = f.append("feMerge");
      merge.append("feMergeNode").attr("in", "glow");
      merge.append("feMergeNode").attr("in", "SourceGraphic");
    };
    addGlow("glow-driver", DRIVER_COLOR_ACTIVE, 12, 1.2);
    addGlow("glow-theme",  THEME_COLOR_DEFAULT,  8, 1.0);
    addGlow("glow-hot",    SYMBOL_COLOR_HOT,     10, 1.3);

    // ─── Zoom container ───────────────────────────────────────────────────
    const root = svg.append("g").attr("class", "root");
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.2, 4])
        .on("zoom", ({ transform }) => root.attr("transform", transform))
    );

    // ─── Simulation ───────────────────────────────────────────────────────
    const simNodes: SimNode[] = nodes.map(n => ({ ...n }));
    const idMap = new Map(simNodes.map(n => [n.id, n]));

    const simEdges: SimEdge[] = edges
      .filter(e => idMap.has(e.source as string) && idMap.has(e.target as string))
      .map(e => ({ ...e, source: e.source as string, target: e.target as string }));

    // Tier-based Y positions
    const tierY: Record<number, number> = { 0: H * 0.15, 1: H * 0.45, 2: H * 0.78 };

    simRef.current?.stop();
    const sim = d3.forceSimulation<SimNode>(simNodes)
      .force("link", d3.forceLink<SimNode, SimEdge>(simEdges)
        .id(n => n.id)
        .distance(d => (d.type === "activates" ? 130 : 80))
        .strength(d => (d.type === "activates" ? 0.6 : 0.4))
      )
      .force("charge", d3.forceManyBody<SimNode>().strength(n => n.type === "driver" ? -600 : n.type === "theme" ? -200 : -60))
      .force("collide",  d3.forceCollide<SimNode>().radius(n => nodeRadius(n) + 6))
      .force("cx",       d3.forceX(W / 2).strength(0.04))
      .force("cy",       d3.forceY<SimNode>(n => tierY[n.tier] ?? H / 2).strength(0.25))
      .alphaDecay(0.025);
    simRef.current = sim;

    // ─── Edges ────────────────────────────────────────────────────────────
    const edgeG = root.append("g").attr("class", "edges");
    const linkSel = edgeG.selectAll<SVGLineElement, SimEdge>("line")
      .data(simEdges)
      .join("line")
      .attr("stroke", d => {
        if (d.type === "activates") return DRIVER_COLOR_ACTIVE + "55";
        const src = typeof d.source === "object" ? (d.source as SimNode) : null;
        if (!src) return "#1e293b";
        const active = src.driver_ids?.some(id => activeDriverIds.has(id)) ||
                       (src.type === "theme" && activeDriverIds.size > 0);
        return active ? (THEME_COLORS[src.id] ?? THEME_COLOR_DEFAULT) + "44" : "#1e293b";
      })
      .attr("stroke-width", d => d.type === "activates" ? 1.5 : (d.strength ?? 0.7) * 1.5)
      .attr("stroke-linecap", "round");

    // ─── Node groups ──────────────────────────────────────────────────────
    const nodeG = root.append("g").attr("class", "nodes");
    const nodeSel = nodeG.selectAll<SVGGElement, SimNode>("g")
      .data(simNodes, d => d.id)
      .join("g")
      .attr("class", "node-group")
      .style("cursor", "pointer")
      .on("click", (_, d) => onSelect(d as IntelligenceNode))
      .call(
        d3.drag<SVGGElement, SimNode>()
          .on("start", (event, d) => { if (!event.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end",   (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
      );

    // Outer glow ring (animated)
    nodeSel.append("circle")
      .attr("class", "glow-ring")
      .attr("r", d => nodeRadius(d) + 8)
      .attr("fill", "none")
      .attr("stroke", d => nodeColor(d, activeDriverIds, blockedIds, hotSymbols))
      .attr("stroke-width", 1.5)
      .attr("opacity", d => {
        const gId = glowId(d, activeDriverIds, blockedIds, hotSymbols);
        return gId !== "none" ? 0.5 : 0;
      });

    // Main circle
    nodeSel.append("circle")
      .attr("r", d => nodeRadius(d))
      .attr("fill", d => nodeColor(d, activeDriverIds, blockedIds, hotSymbols))
      .attr("filter", d => {
        const gId = glowId(d, activeDriverIds, blockedIds, hotSymbols);
        return gId !== "none" ? `url(#${gId})` : "none";
      })
      .attr("stroke", d => {
        if (selected?.id === d.id) return "#fff";
        return nodeColor(d, activeDriverIds, blockedIds, hotSymbols) + "99";
      })
      .attr("stroke-width", d => selected?.id === d.id ? 2.5 : 1);

    // Labels
    nodeSel.append("text")
      .text(d => {
        if (d.type === "driver") return d.label;
        if (d.type === "theme")  return d.label.replace(/ /g, " ");
        return d.id; // symbol ticker
      })
      .attr("text-anchor", "middle")
      .attr("dy", d => d.type === "symbol" ? nodeRadius(d) + 10 : nodeRadius(d) + 12)
      .attr("font-size", d => d.type === "driver" ? 10 : d.type === "theme" ? 9 : 8)
      .attr("font-weight", d => d.type === "driver" ? "600" : "400")
      .attr("fill", d => {
        if (d.type === "symbol") {
          const active = (d.driver_ids ?? []).some(id => activeDriverIds.has(id));
          return active ? "#e2e8f0" : "#475569";
        }
        const col = nodeColor(d, activeDriverIds, blockedIds, hotSymbols);
        return col === DRIVER_COLOR_INACTIVE || col === "#1e293b" ? "#374151" : "#f1f5f9";
      })
      .attr("pointer-events", "none");

    // ─── Tick ─────────────────────────────────────────────────────────────
    sim.on("tick", () => {
      linkSel
        .attr("x1", d => (d.source as SimNode).x ?? 0)
        .attr("y1", d => (d.source as SimNode).y ?? 0)
        .attr("x2", d => (d.target as SimNode).x ?? 0)
        .attr("y2", d => (d.target as SimNode).y ?? 0);
      nodeSel.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => { sim.stop(); };
  }, [nodes, edges, activeDriverIds, blockedIds, hotSymbols, selected, onSelect, dims]);

  useEffect(() => {
    const cleanup = build();
    return cleanup;
  }, [build]);

  return (
    <div ref={wrapRef} className="w-full h-full relative overflow-hidden">
      <svg ref={svgRef} className="w-full h-full" style={{ background: "transparent" }} />
      <style>{`
        @keyframes pulse-glow {
          0%, 100% { opacity: 0.4; transform: scale(1); }
          50%       { opacity: 0.9; transform: scale(1.15); }
        }
        @keyframes pulse-hot {
          0%, 100% { opacity: 0.5; transform: scale(1); }
          50%       { opacity: 1;   transform: scale(1.25); }
        }
        .node-group:has(.glow-ring[opacity="0.5"]) .glow-ring {
          animation: pulse-glow 2.2s ease-in-out infinite;
          transform-origin: center;
          transform-box: fill-box;
        }
      `}</style>
    </div>
  );
}
