import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import * as d3 from "d3";

import {
  VISUALIZATION_HEIGHT,
  VISUALIZATION_WIDTH,
  buildCombinedGraphData,
  buildThreadGraph,
  getBaseNodeRadius,
  getNodeFocus,
  hydrateMemoryGraph,
} from "../utils/knowledgeVisualization";

const clampLevel = (value, maxLevel) => {
  const parsed = Number(value || 0);
  const boundedMax = Number.isFinite(maxLevel) ? Math.max(0, Number(maxLevel)) : 0;
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(Math.round(parsed), boundedMax));
};

const colorByType = new Map([
  ["thread", "#56b68b"],
  ["conversation", "#6f92ff"],
  ["memory", "#f6b66a"],
  ["conversation_anchor", "#8da8ff"],
  ["file_anchor", "#8fd3ba"],
  ["tool_anchor", "#f090c5"],
  ["namespace_anchor", "#d4b1ff"],
]);

const strokeByLinkType = {
  projection: "rgba(124, 152, 232, 0.34)",
  semantic: "rgba(245, 181, 108, 0.4)",
  explicit: "rgba(112, 210, 171, 0.48)",
  cross: "rgba(228, 129, 203, 0.62)",
};

const KnowledgeVisualizationsTab = () => {
  const [threadSummary, setThreadSummary] = useState(null);
  const [memoryGraphPayload, setMemoryGraphPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [includeThreadProjection, setIncludeThreadProjection] = useState(true);
  const [includeMemoryProjection, setIncludeMemoryProjection] = useState(false);
  const [includeKnowledgeOverlay, setIncludeKnowledgeOverlay] = useState(false);
  const [levels, setLevels] = useState({ threads: 0, memory: 0, knowledge: 0 });
  const [planeOffset, setPlaneOffset] = useState(0.2);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const svgRef = useRef(null);

  useEffect(() => {
    let mounted = true;
    const loadGraphs = async () => {
      setLoading(true);
      setError("");
      try {
        const [threadsRes, memoryRes] = await Promise.all([
          axios.get("/api/threads/summary"),
          axios.get("/api/memory/graph", {
            params: {
              limit: 72,
              include_thread_projection: false,
            },
          }),
        ]);
        if (!mounted) return;
        setThreadSummary(threadsRes?.data?.summary || null);
        setMemoryGraphPayload(memoryRes?.data?.graph || null);
      } catch (_err) {
        if (!mounted) return;
        setError("Unable to load visualization data.");
      } finally {
        if (mounted) setLoading(false);
      }
    };
    loadGraphs();
    return () => {
      mounted = false;
    };
  }, []);

  const threadGraph = useMemo(() => buildThreadGraph(threadSummary), [threadSummary]);
  const memoryGraph = useMemo(
    () => hydrateMemoryGraph(memoryGraphPayload),
    [memoryGraphPayload],
  );

  const maxLevels = useMemo(
    () => ({
      threads: Number(threadGraph?.metadata?.maxLevel || 0),
      memory: Number(memoryGraph?.metadata?.maxLevel || 0),
      knowledge: 0,
    }),
    [memoryGraph, threadGraph],
  );

  useEffect(() => {
    setLevels((current) => ({
      threads: clampLevel(current.threads, maxLevels.threads),
      memory: clampLevel(current.memory, maxLevels.memory),
      knowledge: clampLevel(current.knowledge, maxLevels.knowledge),
    }));
  }, [maxLevels.knowledge, maxLevels.memory, maxLevels.threads]);

  const graphData = useMemo(
    () =>
      buildCombinedGraphData({
        threadGraph,
        memoryGraph,
        includeThreadProjection,
        includeMemoryProjection,
        includeKnowledgeOverlay,
        levels,
        planeOffset,
      }),
    [
      includeKnowledgeOverlay,
      includeMemoryProjection,
      includeThreadProjection,
      levels,
      memoryGraph,
      planeOffset,
      threadGraph,
    ],
  );

  const selectedNode = useMemo(
    () => graphData.nodes.find((node) => node.id === selectedNodeId) || null,
    [graphData.nodes, selectedNodeId],
  );

  useEffect(() => {
    if (!selectedNodeId) return;
    const exists = graphData.nodes.some((node) => node.id === selectedNodeId);
    if (!exists) setSelectedNodeId("");
  }, [graphData.nodes, selectedNodeId]);

  useEffect(() => {
    const svgElement = svgRef.current;
    if (!svgElement) return;

    const svg = d3.select(svgElement);
    svg.selectAll("*").remove();
    svg.attr("viewBox", [0, 0, VISUALIZATION_WIDTH, VISUALIZATION_HEIGHT]);

    const { nodes, links, crossLinks, metadata } = graphData;
    if (!nodes.length) {
      svg
        .append("text")
        .attr("x", VISUALIZATION_WIDTH / 2)
        .attr("y", VISUALIZATION_HEIGHT / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "currentColor")
        .attr("opacity", 0.75)
        .text("Select at least one graph layer to populate this view.");
      return;
    }

    const focusForNode = (node) => getNodeFocus(node, levels, selectedNodeId);
    const nodeLookup = new Map(nodes.map((node) => [node.id, node]));

    const internalLinkSelection = svg
      .append("g")
      .attr("class", "knowledge-viz-links")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", (link) => strokeByLinkType[link.type] || "rgba(138, 154, 199, 0.42)")
      .attr("stroke-width", (link) => {
        if (link.type === "projection") return 1.2;
        if (link.type === "explicit") return 1.35;
        return 1.6 + Math.min(Number(link.weight || 0), 1.4);
      })
      .attr("stroke-dasharray", (link) => (link.type === "explicit" ? "4 5" : null))
      .attr("stroke-linecap", "round");

    const crossLinkSelection = svg
      .append("g")
      .attr("class", "knowledge-viz-cross-links")
      .selectAll("line")
      .data(crossLinks)
      .join("line")
      .attr("stroke", strokeByLinkType.cross)
      .attr("stroke-width", 1.25)
      .attr("stroke-dasharray", "2 6")
      .attr("stroke-linecap", "round");

    const nodeSelection = svg
      .append("g")
      .attr("class", "knowledge-viz-nodes")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (node) => {
        const focus = focusForNode(node);
        return getBaseNodeRadius(node) * focus.scale;
      })
      .attr("fill", (node) => colorByType.get(node.type) || "#9ab0d9")
      .attr("opacity", (node) => focusForNode(node).opacity)
      .attr("stroke", (node) =>
        node.id === selectedNodeId ? "rgba(255, 255, 255, 0.88)" : "rgba(9, 11, 20, 0.7)",
      )
      .attr("stroke-width", (node) => (node.id === selectedNodeId ? 1.6 : 0.85))
      .on("click", (_event, node) => setSelectedNodeId(node.id));

    const labelSelection = svg
      .append("g")
      .attr("class", "knowledge-viz-labels")
      .attr("fill", "currentColor")
      .attr("pointer-events", "none")
      .selectAll("text")
      .data(nodes)
      .join("text")
      .attr("text-anchor", "middle")
      .attr("font-size", (node) => (node.type === "thread" ? 11 : 10))
      .attr("dy", (node) => (node.level === 0 ? -14 : 14))
      .attr("opacity", (node) => {
        const focus = focusForNode(node);
        return focus.opacity >= 0.3 ? Math.min(1, focus.opacity + 0.08) : 0;
      })
      .text((node) => String(node.label || ""));

    const simulation = d3
      .forceSimulation(nodes)
      .force(
        "link",
        d3
          .forceLink(links)
          .id((node) => node.id)
          .distance((link) => {
            if (link.type === "projection") return 120;
            if (link.type === "explicit") return 88;
            const weight = Number(link.weight || 0);
            return Math.max(42, 128 - Math.min(weight * 90, 74));
          })
          .strength((link) => (link.type === "semantic" ? 0.28 : 0.45)),
      )
      .force(
        "charge",
        d3.forceManyBody().strength((node) => {
          if (node.type === "thread") return -200;
          if (node.type === "memory") return -160;
          return -95;
        }),
      )
      .force(
        "x",
        d3
          .forceX((node) => Number(node.anchorX || VISUALIZATION_WIDTH / 2))
          .strength(metadata.activeGraphCount > 1 ? 0.22 : 0.12),
      )
      .force(
        "y",
        d3
          .forceY(
            (node) =>
              Number(node.anchorY || VISUALIZATION_HEIGHT / 2) +
              (Number(node.level || 0) - Number(levels?.[node.graphKey] || 0)) * 34,
          )
          .strength(0.18),
      )
      .force(
        "collision",
        d3.forceCollide().radius((node) => getBaseNodeRadius(node) + 11),
      )
      .on("tick", () => {
        internalLinkSelection
          .attr("x1", (link) => link.source.x)
          .attr("y1", (link) => link.source.y)
          .attr("x2", (link) => link.target.x)
          .attr("y2", (link) => link.target.y)
          .attr("opacity", (link) => {
            const sourceFocus = focusForNode(link.source);
            const targetFocus = focusForNode(link.target);
            return Math.max(0.16, Math.min(sourceFocus.opacity, targetFocus.opacity) * 0.92);
          });

        crossLinkSelection
          .attr("x1", (link) => nodeLookup.get(link.source)?.x || 0)
          .attr("y1", (link) => nodeLookup.get(link.source)?.y || 0)
          .attr("x2", (link) => nodeLookup.get(link.target)?.x || 0)
          .attr("y2", (link) => nodeLookup.get(link.target)?.y || 0)
          .attr("opacity", (link) => {
            const sourceFocus = focusForNode(nodeLookup.get(link.source) || {});
            const targetFocus = focusForNode(nodeLookup.get(link.target) || {});
            return Math.max(0.12, Math.min(sourceFocus.opacity, targetFocus.opacity) * 0.78);
          });

        nodeSelection
          .attr("cx", (node) => node.x)
          .attr("cy", (node) => node.y)
          .attr("r", (node) => {
            const focus = focusForNode(node);
            return getBaseNodeRadius(node) * focus.scale;
          })
          .attr("opacity", (node) => focusForNode(node).opacity);

        labelSelection
          .attr("x", (node) => node.x)
          .attr("y", (node) => node.y)
          .attr("opacity", (node) => {
            const focus = focusForNode(node);
            return focus.opacity >= 0.3 ? Math.min(1, focus.opacity + 0.08) : 0;
          });
      });

    const drag = d3
      .drag()
      .on("start", (event, node) => {
        if (!event.active) simulation.alphaTarget(0.25).restart();
        node.fx = node.x;
        node.fy = node.y;
      })
      .on("drag", (event, node) => {
        node.fx = event.x;
        node.fy = event.y;
      })
      .on("end", (event, node) => {
        if (!event.active) simulation.alphaTarget(0);
        node.fx = null;
        node.fy = null;
      });
    nodeSelection.call(drag);

    return () => {
      simulation.stop();
    };
  }, [graphData, levels, selectedNodeId]);

  const activeGraphCount = graphData.metadata.activeGraphCount;

  const changeLevel = (graphKey, next) => {
    setLevels((current) => ({
      ...current,
      [graphKey]: clampLevel((current?.[graphKey] || 0) + next, maxLevels?.[graphKey]),
    }));
  };

  return (
    <section className="knowledge-viz-tab">
      <header className="knowledge-viz-head">
        <div>
          <h3>Visualizations (experimental)</h3>
          <p className="status-note">
            Thread and memory projections can be layered together. Level focus uses
            opacity and size falloff, and cross-graph bridges connect shared
            conversation anchors.
          </p>
        </div>
        <div className="knowledge-viz-controls">
          <div className="knowledge-viz-toggle">
            <label>
              <input
                type="checkbox"
                checked={includeThreadProjection}
                onChange={(event) => setIncludeThreadProjection(event.target.checked)}
              />
              thread cluster projection
            </label>
            <div className="knowledge-viz-level-control">
              <button
                type="button"
                aria-label="Decrease threads level"
                onClick={() => changeLevel("threads", -1)}
                disabled={levels.threads <= 0}
              >
                &darr;
              </button>
              <span>
                level {levels.threads + 1}/{maxLevels.threads + 1}
              </span>
              <button
                type="button"
                aria-label="Increase threads level"
                onClick={() => changeLevel("threads", 1)}
                disabled={levels.threads >= maxLevels.threads}
              >
                &uarr;
              </button>
            </div>
          </div>

          <div className="knowledge-viz-toggle">
            <label>
              <input
                type="checkbox"
                checked={includeMemoryProjection}
                onChange={(event) => setIncludeMemoryProjection(event.target.checked)}
              />
              memory relation projection
            </label>
            <div className="knowledge-viz-level-control">
              <button
                type="button"
                aria-label="Decrease memory level"
                onClick={() => changeLevel("memory", -1)}
                disabled={levels.memory <= 0}
              >
                &darr;
              </button>
              <span>
                level {levels.memory + 1}/{maxLevels.memory + 1}
              </span>
              <button
                type="button"
                aria-label="Increase memory level"
                onClick={() => changeLevel("memory", 1)}
                disabled={levels.memory >= maxLevels.memory}
              >
                &uarr;
              </button>
            </div>
          </div>

          <div className="knowledge-viz-toggle">
            <label>
              <input
                type="checkbox"
                checked={includeKnowledgeOverlay}
                onChange={(event) => setIncludeKnowledgeOverlay(event.target.checked)}
              />
              knowledge graph overlay (stub)
            </label>
            <div className="knowledge-viz-level-control">
              <button type="button" aria-label="Decrease knowledge level" disabled>
                &darr;
              </button>
              <span>level 1/1</span>
              <button type="button" aria-label="Increase knowledge level" disabled>
                &uarr;
              </button>
            </div>
          </div>
        </div>
      </header>

      {activeGraphCount > 1 ? (
        <div className="knowledge-viz-plane">
          <label htmlFor="knowledge-viz-plane-offset">plane offset</label>
          <input
            id="knowledge-viz-plane-offset"
            type="range"
            min="-1"
            max="1"
            step="0.05"
            value={planeOffset}
            onChange={(event) => setPlaneOffset(Number(event.target.value))}
          />
          <span>{planeOffset.toFixed(2)}</span>
        </div>
      ) : null}

      <div className="knowledge-viz-legend" aria-label="Graph legend">
        <span className="legend-chip semantic">semantic relation</span>
        <span className="legend-chip explicit">explicit provenance</span>
        <span className="legend-chip cross">cross-graph bridge</span>
      </div>

      {loading ? <p className="status-note">Loading graph layers...</p> : null}
      {error ? <p className="status-note warn">{error}</p> : null}
      {includeKnowledgeOverlay ? (
        <p className="status-note">
          Knowledge-graph overlay remains a placeholder. The live layer today is the
          canonical vector/provenance view for threads plus memories.
        </p>
      ) : null}
      {includeMemoryProjection && memoryGraph?.metadata?.embeddings_source ? (
        <p className="status-note">
          Memory graph signal: {memoryGraph.metadata.signal_mode} blend using{" "}
          {memoryGraph.metadata.embeddings_source}
          {" + "}SAE-style sparse proxy. Explicit edges come from current memory
          provenance fields.
        </p>
      ) : null}

      <div className="knowledge-viz-layout">
        <div className="knowledge-viz-canvas-shell">
          <svg ref={svgRef} className="knowledge-viz-canvas" aria-label="Knowledge graph" />
        </div>
        <aside className="knowledge-viz-side">
          <h4>Node details</h4>
          {selectedNode ? (
            <dl>
              <dt>label</dt>
              <dd>{selectedNode.label}</dd>
              <dt>graph</dt>
              <dd>{selectedNode.graphKey}</dd>
              <dt>type</dt>
              <dd>{selectedNode.type}</dd>
              <dt>level</dt>
              <dd>{Number(selectedNode.level || 0) + 1}</dd>
              <dt>weight</dt>
              <dd>{Number(selectedNode.weight || 0)}</dd>
              {selectedNode.category ? (
                <>
                  <dt>category</dt>
                  <dd>{selectedNode.category}</dd>
                </>
              ) : null}
              {selectedNode.importance != null ? (
                <>
                  <dt>importance</dt>
                  <dd>{Number(selectedNode.importance || 0).toFixed(2)}</dd>
                </>
              ) : null}
              {selectedNode.conversationCount != null ? (
                <>
                  <dt>conversations</dt>
                  <dd>{Number(selectedNode.conversationCount || 0)}</dd>
                </>
              ) : null}
              {selectedNode.explicit_ref_count != null ? (
                <>
                  <dt>explicit refs</dt>
                  <dd>{Number(selectedNode.explicit_ref_count || 0)}</dd>
                </>
              ) : null}
              {selectedNode.matchKey ? (
                <>
                  <dt>bridge key</dt>
                  <dd>{selectedNode.matchKey}</dd>
                </>
              ) : null}
              {selectedNode.latestDate ? (
                <>
                  <dt>latest date</dt>
                  <dd>{selectedNode.latestDate}</dd>
                </>
              ) : null}
            </dl>
          ) : (
            <p className="status-note">Click a node to inspect details.</p>
          )}
        </aside>
      </div>
    </section>
  );
};

export default KnowledgeVisualizationsTab;
