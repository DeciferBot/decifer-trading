"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import * as d3 from "d3";
import type { EnrichedNode, GraphEdge, Cluster } from "@/lib/types";
import { EDGE_COLORS } from "@/lib/types";

const SUBCLUSTER_COLORS: Record<string, string> = {
  compute:        "#6366f1",
  software:       "#818cf8",
  foundry:        "#4338ca",
  memory:         "#7c3aed",
  networking:     "#06b6d4",
  systems:        "#a78bfa",
  power:          "#f97316",
  infrastructure: "#8b5cf6",
  photonics:      "#38bdf8",
  launch:         "#f59e0b",
  earth_obs:      "#84cc16",
  defence:        "#ef4444",
  comms:          "#22d3ee",
  components:     "#d97706",
  materials:      "#a3e635",
};

interface Props {
  nodes: EnrichedNode[];        // already filtered by cluster
  edges: GraphEdge[];
  clusters: Cluster[];
  onSelect: (node: EnrichedNode | null) => void;
  selected: EnrichedNode | null;
}

interface SimNode extends EnrichedNode {
  x?: number; y?: number; vx?: number; vy?: number;
  fx?: number | null; fy?: number | null;
}

export default function MarketGraph({ nodes, edges, clusters, onSelect, selected }: Props) {
  const svgRef  = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const simRef  = useRef<d3.Simulation<SimNode, undefined> | null>(null);
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

  const nodeColor = useCallback((n: EnrichedNode) => {
    const base = SUBCLUSTER_COLORS[n.subcluster] ?? clusters.find(c => c.id === n.cluster)?.color ?? "#6366f1";
    const p = n.price?.change_pct ?? 0;
    if (p > 2)    return "#10b981";
    if (p > 0.5)  return "#34d399";
    if (p < -2)   return "#ef4444";
    if (p < -0.5) return "#f87171";
    return base;
  }, [clusters]);

  const nodeRadius = useCallback((n: EnrichedNode) => {
    const base = n.tier === 0 ? 32 : n.tier === 1 ? 18 : 11;
    return base + (n.brightness / 100) * 10;
  }, []);

  useEffect(() => {
    if (!svgRef.current || nodes.length === 0 || !dims) return;
    const { w: W, h: H } = dims;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    // ── Connected set for isolation ───────────────────────────────────────
    const connectedIds = new Set<string>();
    if (selected) {
      connectedIds.add(selected.id);
      edges.forEach(e => {
        if (e.source === selected.id) connectedIds.add(e.target as string);
        if (e.target === selected.id) connectedIds.add(e.source as string);
      });
    }
    const isolated = selected !== null;

    // ── Defs ─────────────────────────────────────────────────────────────
    const defs = svg.append("defs");

    // Glow filters
    const addGlow = (id: string, std: number, color: string) => {
      const f = defs.append("filter").attr("id", id)
        .attr("x", "-100%").attr("y", "-100%").attr("width", "300%").attr("height", "300%");
      f.append("feGaussianBlur").attr("stdDeviation", std).attr("result", "blur");
      const flood = defs.append("filter").attr("id", id + "-flood");
      void flood; // just use simple blur merge
      const m = f.append("feMerge");
      m.append("feMergeNode").attr("in", "blur");
      m.append("feMergeNode").attr("in", "SourceGraphic");
    };
    addGlow("glow-node",   6,  "#fff");
    addGlow("glow-select", 16, "#fff");

    // Central nucleus radial gradient (Z.E.R.O. effect)
    const rg = defs.append("radialGradient").attr("id", "nucleus-glow")
      .attr("cx", "50%").attr("cy", "50%").attr("r", "50%");
    rg.append("stop").attr("offset", "0%").attr("stop-color", "#ffffff").attr("stop-opacity", 0.12);
    rg.append("stop").attr("offset", "60%").attr("stop-color", "#6366f1").attr("stop-opacity", 0.04);
    rg.append("stop").attr("offset", "100%").attr("stop-color", "#000").attr("stop-opacity", 0);

    // ── Zoom + root ───────────────────────────────────────────────────────
    const root = svg.append("g");
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.15, 5])
        .on("zoom", ({ transform }) => root.attr("transform", transform))
    );
    svg.on("click", (event) => {
      if (event.target === svgRef.current) onSelect(null);
    });

    // Nucleus glow background
    root.append("ellipse")
      .attr("cx", W / 2).attr("cy", H / 2)
      .attr("rx", W * 0.45).attr("ry", H * 0.45)
      .attr("fill", "url(#nucleus-glow)")
      .attr("pointer-events", "none");

    // ── Sim nodes ─────────────────────────────────────────────────────────
    const sn: SimNode[] = nodes.map(n => ({
      ...n,
      x: W / 2 + (Math.random() - 0.5) * W * 0.5,
      y: H / 2 + (Math.random() - 0.5) * H * 0.5,
    }));
    const nodeById = new Map(sn.map(n => [n.id, n]));

    const ld = edges
      .map(e => ({
        ...e,
        source: nodeById.get(e.source as string)!,
        target: nodeById.get(e.target as string)!,
      }))
      .filter(e => e.source && e.target);

    // ── Simulation ────────────────────────────────────────────────────────
    const sim = d3.forceSimulation<SimNode>(sn)
      .force("link", d3.forceLink<SimNode, typeof ld[0]>(ld)
        .id(d => d.id)
        .distance(d => 160 + (1 - d.strength) * 80)
        .strength(0.4))
      .force("charge", d3.forceManyBody().strength(-600))
      .force("collide", d3.forceCollide<SimNode>().radius(d => nodeRadius(d) + 20))
      .force("x", d3.forceX(W / 2).strength(0.04))
      .force("y", d3.forceY(H / 2).strength(0.04))
      .alphaDecay(0.02);
    simRef.current = sim;

    // ── Edges ─────────────────────────────────────────────────────────────
    const edgeG = root.append("g");
    const linkSel = edgeG.selectAll<SVGLineElement, typeof ld[0]>("line")
      .data(ld)
      .join("line")
      .attr("stroke", d => EDGE_COLORS[d.type] ?? "#555")
      .attr("stroke-width", d => Math.max(2, d.strength * 4))
      .attr("stroke-linecap", "round")
      .attr("stroke-opacity", d => {
        if (!isolated) return 0.35;
        const s = (d.source as SimNode).id, t = (d.target as SimNode).id;
        return connectedIds.has(s) && connectedIds.has(t) ? 0.9 : 0.03;
      });

    // ── Nodes ─────────────────────────────────────────────────────────────
    const nodeG = root.append("g");
    const nodeSel = nodeG.selectAll<SVGGElement, SimNode>("g")
      .data(sn, d => d.id)
      .join("g")
      .attr("class", "node-group")
      .style("cursor", "pointer")
      .on("click", (event, d) => { event.stopPropagation(); onSelect(d as EnrichedNode); });

    nodeSel.call(
      d3.drag<SVGGElement, SimNode>()
        .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

    // Outer glow ring (selected node only)
    nodeSel.append("circle")
      .attr("r", d => selected?.id === d.id ? nodeRadius(d) + 14 : 0)
      .attr("fill", "none")
      .attr("stroke", d => nodeColor(d))
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.5)
      .attr("filter", "url(#glow-select)");

    // Main circle
    nodeSel.append("circle")
      .attr("r", d => nodeRadius(d))
      .attr("fill", d => nodeColor(d))
      .attr("fill-opacity", d => {
        if (!isolated) return 0.25 + (d.brightness / 100) * 0.6;
        return connectedIds.has(d.id) ? 0.85 : 0.04;
      })
      .attr("stroke", d => selected?.id === d.id ? "#fff" : nodeColor(d))
      .attr("stroke-width", d => selected?.id === d.id ? 2.5 : 1)
      .attr("stroke-opacity", d => {
        if (!isolated) return 0.6;
        return connectedIds.has(d.id) ? 1 : 0.05;
      })
      .attr("filter", d => selected?.id === d.id ? "url(#glow-select)" : d.brightness > 50 ? "url(#glow-node)" : "none");

    // Ticker label
    nodeSel.append("text")
      .text(d => d.id)
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-size", d => d.tier === 0 ? 12 : d.tier === 1 ? 9 : 7.5)
      .attr("font-weight", d => d.tier === 0 ? "700" : "500")
      .attr("fill", "#fff")
      .attr("fill-opacity", d => {
        if (!isolated) return 0.6 + (d.brightness / 100) * 0.4;
        return connectedIds.has(d.id) ? 1 : 0.04;
      })
      .attr("pointer-events", "none");

    // Company name below (tier-0 always, others when connected/selected)
    nodeSel.append("text")
      .text(d => {
        const show = !isolated ? (d.tier === 0 || d.brightness > 50) : connectedIds.has(d.id);
        return show ? d.label : "";
      })
      .attr("text-anchor", "middle")
      .attr("dy", d => nodeRadius(d) + 14)
      .attr("font-size", 8)
      .attr("fill", "#9ca3af")
      .attr("fill-opacity", d => {
        if (!isolated) return 0.4 + (d.brightness / 100) * 0.4;
        return connectedIds.has(d.id) ? 0.85 : 0;
      })
      .attr("pointer-events", "none");

    // ── Tick ──────────────────────────────────────────────────────────────
    sim.on("tick", () => {
      linkSel
        .attr("x1", d => (d.source as SimNode).x ?? 0)
        .attr("y1", d => (d.source as SimNode).y ?? 0)
        .attr("x2", d => (d.target as SimNode).x ?? 0)
        .attr("y2", d => (d.target as SimNode).y ?? 0);
      nodeSel.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => { sim.stop(); };
  }, [nodes, edges, clusters, selected, onSelect, dims, nodeColor, nodeRadius]);

  return (
    <div ref={wrapRef} className="w-full h-full">
      <svg ref={svgRef} className="w-full h-full" style={{ background: "transparent" }} />
    </div>
  );
}
