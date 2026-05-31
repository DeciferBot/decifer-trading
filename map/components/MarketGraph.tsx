"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import * as d3 from "d3";
import type { EnrichedNode, GraphEdge, Cluster } from "@/lib/types";
import { EDGE_COLORS } from "@/lib/types";

interface Props {
  nodes: EnrichedNode[];
  edges: GraphEdge[];
  clusters: Cluster[];
  onSelect: (node: EnrichedNode | null) => void;
  selected: EnrichedNode | null;
}

interface SimNode extends EnrichedNode {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export default function MarketGraph({ nodes, edges, clusters, onSelect, selected }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const simRef = useRef<d3.Simulation<SimNode, undefined> | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);

  // Observe real SVG dimensions — clientWidth is 0 at mount before layout
  useEffect(() => {
    if (!svgRef.current) return;
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setDims({ w: width, h: height });
    });
    ro.observe(svgRef.current);
    return () => ro.disconnect();
  }, []);

  const getNodeRadius = useCallback((n: EnrichedNode) => {
    const base = n.tier === 0 ? 18 : n.tier === 1 ? 13 : 9;
    return base + (n.brightness / 100) * 6;
  }, []);

  const getNodeColor = useCallback((n: EnrichedNode) => {
    const cluster = clusters.find(c => c.id === n.cluster);
    const base = cluster?.color ?? "#6366f1";
    const price = n.price?.change_pct ?? 0;
    if (price > 1)  return "#10b981";
    if (price < -1) return "#ef4444";
    return base;
  }, [clusters]);

  useEffect(() => {
    if (!svgRef.current || nodes.length === 0 || !dims) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const W = dims.w;
    const H = dims.h;

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.15, 4])
      .on("zoom", (e) => g.attr("transform", e.transform));
    svg.call(zoom);

    const g = svg.append("g");

    // Cluster hulls background
    const clusterMap = new Map<string, SimNode[]>();
    const simNodes: SimNode[] = nodes.map(n => ({ ...n }));
    simNodes.forEach(n => {
      const arr = clusterMap.get(n.cluster) ?? [];
      arr.push(n);
      clusterMap.set(n.cluster, arr);
    });

    const hullG = g.append("g").attr("class", "hulls");

    // Pre-seed node positions at cluster centers so graph starts correct immediately
    const clusterCenters: Record<string, { x: number; y: number }> = {
      ai:    { x: W * 0.35, y: H * 0.46 },
      space: { x: W * 0.72, y: H * 0.46 },
    };
    simNodes.forEach(n => {
      const cx = clusterCenters[n.cluster]?.x ?? W / 2;
      const cy = clusterCenters[n.cluster]?.y ?? H / 2;
      const spread = 80 + n.tier * 60;
      n.x = cx + (Math.random() - 0.5) * spread;
      n.y = cy + (Math.random() - 0.5) * spread;
    });

    const linkData = edges.map(e => ({
      ...e,
      source: simNodes.find(n => n.id === e.source)!,
      target: simNodes.find(n => n.id === e.target)!,
    })).filter(e => e.source && e.target);

    const link = g.append("g")
      .selectAll("line")
      .data(linkData)
      .join("line")
      .attr("stroke", d => EDGE_COLORS[d.type] ?? "#444")
      .attr("stroke-opacity", 0.35)
      .attr("stroke-width", d => Math.max(0.5, d.strength * 2));

    const nodeG = g.append("g")
      .selectAll<SVGGElement, SimNode>("g.node")
      .data(simNodes)
      .join("g")
      .attr("class", "node")
      .style("cursor", "pointer");

    nodeG.call(
      d3.drag<SVGGElement, SimNode>()
        .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );
    nodeG.on("click", (_e, d) => onSelect(d));

    // Glow filter
    const defs = svg.append("defs");
    const filter = defs.append("filter").attr("id", "glow").attr("x", "-50%").attr("y", "-50%").attr("width", "200%").attr("height", "200%");
    filter.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "blur");
    const merge = filter.append("feMerge");
    merge.append("feMergeNode").attr("in", "blur");
    merge.append("feMergeNode").attr("in", "SourceGraphic");

    // Outer glow ring for hot nodes
    nodeG.append("circle")
      .attr("r", d => getNodeRadius(d) + 6)
      .attr("fill", "none")
      .attr("stroke", d => getNodeColor(d))
      .attr("stroke-width", d => d.brightness > 60 ? 2 : 0)
      .attr("stroke-opacity", d => (d.brightness - 60) / 80)
      .attr("filter", "url(#glow)");

    // Main circle
    nodeG.append("circle")
      .attr("r", d => getNodeRadius(d))
      .attr("fill", d => getNodeColor(d))
      .attr("fill-opacity", d => 0.4 + (d.brightness / 100) * 0.5)
      .attr("stroke", d => selected?.id === d.id ? "#fff" : getNodeColor(d))
      .attr("stroke-width", d => selected?.id === d.id ? 2.5 : 1);

    // Ticker label
    nodeG.append("text")
      .text(d => d.id)
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-size", d => d.tier === 0 ? 11 : 9)
      .attr("font-weight", d => d.tier === 0 ? "700" : "500")
      .attr("fill", "#fff")
      .attr("pointer-events", "none");

    // Company name below
    nodeG.append("text")
      .text(d => d.label)
      .attr("text-anchor", "middle")
      .attr("dy", d => getNodeRadius(d) + 12)
      .attr("font-size", 8)
      .attr("fill", "#9ca3af")
      .attr("pointer-events", "none");

    // Force simulation — tier-based radial positioning
    const centerX = W / 2;
    const centerY = H / 2;

    const sim = d3.forceSimulation<SimNode>(simNodes)
      .force("link", d3.forceLink<SimNode, typeof linkData[0]>(linkData)
        .id(d => d.id)
        .distance(d => 50 + (1 - d.strength) * 40))
      .force("charge", d3.forceManyBody().strength(-100))
      .force("collide", d3.forceCollide<SimNode>().radius(d => getNodeRadius(d) + 12))
      .force("cluster_x", d3.forceX<SimNode>(d => clusterCenters[d.cluster]?.x ?? centerX).strength(0.4))
      .force("cluster_y", d3.forceY<SimNode>(d => clusterCenters[d.cluster]?.y ?? centerY).strength(0.4))
      .force("tier_radial", d3.forceRadial<SimNode>(
        d => d.tier * 90,
        d => clusterCenters[d.cluster]?.x ?? centerX,
        d => clusterCenters[d.cluster]?.y ?? centerY,
      ).strength(0.35))
      .on("tick", () => {
        link
          .attr("x1", d => (d.source as SimNode).x ?? 0)
          .attr("y1", d => (d.source as SimNode).y ?? 0)
          .attr("x2", d => (d.target as SimNode).x ?? 0)
          .attr("y2", d => (d.target as SimNode).y ?? 0);

        nodeG.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);

        // Update hulls every tick
        hullG.selectAll("path").remove();
        clusterMap.forEach((cnodes, cid) => {
          const pts = cnodes.filter(n => n.x !== undefined).map(n => [n.x!, n.y!] as [number, number]);
          if (pts.length < 3) return;
          const hull = d3.polygonHull(pts);
          if (!hull) return;
          const cluster = clusters.find(c => c.id === cid);
          const padded = hull.map(([x, y]) => {
            const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
            const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
            const dx = x - cx; const dy = y - cy;
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            return [x + dx / len * 40, y + dy / len * 40] as [number, number];
          });
          hullG.append("path")
            .attr("d", `M${padded.join("L")}Z`)
            .attr("fill", cluster?.color ?? "#6366f1")
            .attr("fill-opacity", 0.04)
            .attr("stroke", cluster?.color ?? "#6366f1")
            .attr("stroke-opacity", 0.12)
            .attr("stroke-width", 1.5)
            .attr("stroke-dasharray", "4 4");
        });
      });

    simRef.current = sim;

    // After simulation cools, zoom to fit the actual bounding box
    sim.on("end", () => {
      const settled = simNodes.filter(n => n.x !== undefined && n.y !== undefined);
      if (!settled.length) return;
      const xs = settled.map(n => n.x!);
      const ys = settled.map(n => n.y!);
      const pad = 80;
      const x0 = Math.min(...xs) - pad;
      const y0 = Math.min(...ys) - pad;
      const bw = Math.max(...xs) + pad - x0;
      const bh = Math.max(...ys) + pad - y0;
      const s = Math.min(0.9, W / bw, H / bh);
      const tx = (W - bw * s) / 2 - x0 * s;
      const ty = (H - bh * s) / 2 - y0 * s;
      svg.transition().duration(800)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(s));
    });

    return () => { sim.stop(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, clusters, onSelect, getNodeColor, getNodeRadius, dims]);

  // Lightweight selection highlight — no redraw, just update stroke on already-rendered nodes
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll<SVGCircleElement, unknown>("g.node circle:nth-child(2)")
      .attr("stroke", function() {
        const parent = (this as Element).parentElement;
        const ticker = parent?.querySelector("text")?.textContent;
        return selected?.id === ticker ? "#fff" : "transparent";
      })
      .attr("stroke-width", function() {
        const parent = (this as Element).parentElement;
        const ticker = parent?.querySelector("text")?.textContent;
        return selected?.id === ticker ? 2.5 : 1;
      });
  }, [selected]);

  return (
    <svg
      ref={svgRef}
      className="w-full h-full"
      style={{ background: "transparent" }}
    />
  );
}
