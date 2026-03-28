import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Normalize rag matches to a predictable shape so they can be rendered safely.
 */
export const normalizeRagMatches = (rawMatches) => {
  if (!Array.isArray(rawMatches)) return [];
  return rawMatches
    .map((entry, index) => {
      if (!entry || typeof entry !== "object") return null;
      const meta =
        entry.metadata && typeof entry.metadata === "object"
          ? { ...entry.metadata }
          : {};
      const textVal =
        typeof entry.text === "string"
          ? entry.text.replace(/\s+/g, " ").trim()
          : "";
      const cleanedSource =
        typeof entry.source === "string" && entry.source.trim()
          ? entry.source.trim()
          : typeof meta.source === "string" && meta.source.trim()
            ? meta.source.trim()
            : "";
      const cleanedId =
        typeof entry.id === "string" && entry.id.trim()
          ? entry.id.trim()
          : typeof meta.id === "string" && meta.id.trim()
            ? meta.id.trim()
            : `match-${index + 1}`;
      const scoreVal =
        typeof entry.score === "number" && Number.isFinite(entry.score)
          ? entry.score
          : typeof meta.score === "number" && Number.isFinite(meta.score)
            ? meta.score
            : null;
      const urlCandidate =
        typeof entry.url === "string" && entry.url.trim()
          ? entry.url.trim()
          : typeof meta.url === "string" && meta.url.trim()
            ? meta.url.trim()
            : typeof meta.href === "string" && meta.href.trim()
              ? meta.href.trim()
              : null;
      return {
        id: cleanedId,
        source: cleanedSource || `doc-${index + 1}`,
        text: textVal,
        score: scoreVal,
        url: urlCandidate,
        metadata: meta,
      };
    })
    .filter(Boolean);
};

const RagContextPanel = ({ matches, defaultOpen = false }) => {
  const normalized = useMemo(
    () => normalizeRagMatches(matches),
    [matches],
  );
  const [expanded, setExpanded] = useState(
    () => (defaultOpen ? true : false),
  );
  const navigate = useNavigate();

  if (normalized.length === 0) return null;

  const toggleExpanded = () => setExpanded((value) => !value);

  return (
    <div className={`rag-context ${expanded ? "open" : ""}`}>
      <button
        type="button"
        className="rag-toggle"
        aria-expanded={expanded}
        onClick={toggleExpanded}
      >
        <span className="rag-toggle-label">
          Retrieved context ({normalized.length})
        </span>
        <span className="rag-toggle-chevron" aria-hidden="true">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <ul className="rag-context-list">
          {normalized.map((match, idx) => {
            const snippet =
              typeof match.text === "string" && match.text.length > 400
                ? `${match.text.slice(0, 397)}…`
                : match.text;
            const safeUrl =
              typeof match.url === "string" &&
              match.url.trim() &&
              (/^https?:\/\//i.test(match.url) || match.url.startsWith("/"))
                ? match.url
                : null;
            const similarity =
              typeof match.score === "number" && Number.isFinite(match.score)
                ? Math.max(0, Math.min(1, match.score))
                : null;
            const embeddingModel =
              match.metadata && typeof match.metadata.embedding_model === "string"
                ? match.metadata.embedding_model
                : null;
            const memoryKey =
              (match.metadata && (match.metadata.key || match.metadata.memory_key)) ||
              null;
            const eventId =
              (match.metadata && match.metadata.event_id) || null;
            const openKnowledge = () => {
              if (memoryKey) {
                navigate(`/knowledge?tab=memory&key=${encodeURIComponent(memoryKey)}`);
                return;
              }
              if (eventId) {
                navigate(`/knowledge?tab=calendar&event_id=${encodeURIComponent(String(eventId))}`);
                return;
              }
              if (match.id) {
                navigate(`/knowledge?tab=documents&id=${encodeURIComponent(match.id)}`);
                return;
              }
              navigate("/knowledge?tab=documents");
            };
            return (
              <li key={match.id || idx} className="rag-context-item">
                <div className="rag-context-meta">
                  <button
                    type="button"
                    className="rag-source rag-source-link"
                    onClick={openKnowledge}
                    title="View in Knowledge browser"
                  >
                    {match.source || `doc-${idx + 1}`}
                  </button>
                  {similarity !== null && (
                    <span
                      className="rag-score"
                      title={
                        embeddingModel
                          ? `Similarity (1 = exact match, 0 = unrelated). Embedding: ${embeddingModel}`
                          : "Similarity (1 = exact match, 0 = unrelated)"
                      }
                    >
                      sim {similarity.toFixed(2)}
                    </span>
                  )}
                  {match.id && (
                    <span className="rag-id" title="Document identifier">
                      #{match.id}
                    </span>
                  )}
                  {safeUrl && (
                    <a
                      className="rag-link"
                      href={safeUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      open file
                    </a>
                  )}
                  {(memoryKey || eventId || match.id) && (
                    <button
                      type="button"
                      className="rag-link rag-view"
                      onClick={openKnowledge}
                      title="Open this match in Knowledge"
                    >
                      view
                    </button>
                  )}
                </div>
                {snippet && <p className="rag-snippet">{snippet}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default RagContextPanel;
