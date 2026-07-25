import React, { useCallback, useContext, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { useLocation } from "react-router-dom";

import { GlobalContext } from "../main";
import {
  FALLBACK_WORKFLOW_PROFILES,
  normalizeWorkflowProfiles,
} from "../utils/workflowCatalog";
import "../styles/KnowledgeSkillsTab.css";

const DEFAULT_WORKFLOW_CATALOG = {
  workflows: FALLBACK_WORKFLOW_PROFILES,
  modules: [
    {
      id: "computer_use",
      label: "Computer Use",
      description: "Browser, desktop, camera, capture, and approval-gated host actions.",
      status: "live",
      source: "base",
      enabled: false,
      skill_id: "computer_use",
      doc_id: "skills:computer_use",
      tool_names: [],
    },
  ],
  addons: [],
  addons_root: "data/modules/addons",
};

const EMPTY_SKILL_CATALOG = {
  skills: [],
  count: 0,
  skills_root: "",
  skills_roots: [],
};

const SKILL_ID_PATTERN = /^[A-Za-z0-9_.-]+$/;

const normalizeIds = (values) =>
  Array.from(
    new Set(
      (Array.isArray(values) ? values : [])
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    ),
  );

const KnowledgeSkillsTab = () => {
  const location = useLocation();
  const globalContext = useContext(GlobalContext) || {};
  const state = globalContext.state || {};
  const setState = globalContext.setState || (() => {});

  const [workflowCatalog, setWorkflowCatalog] = useState(DEFAULT_WORKFLOW_CATALOG);
  const [skillCatalog, setSkillCatalog] = useState(EMPTY_SKILL_CATALOG);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogMessage, setCatalogMessage] = useState("");
  const requestedView = new URLSearchParams(location.search).get("view");
  const [workspaceView, setWorkspaceView] = useState(
    requestedView === "workflows" ? "workflows" : "skills",
  );
  const [defaultWorkflow, setDefaultWorkflow] = useState(state.workflowProfile || "default");
  const [enabledModules, setEnabledModules] = useState(
    normalizeIds(state.enabledWorkflowModules),
  );
  const [workflowSaving, setWorkflowSaving] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState("");
  const [moduleDetailsId, setModuleDetailsId] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [newSkillId, setNewSkillId] = useState("");
  const [skillDoc, setSkillDoc] = useState(null);
  const [skillDraft, setSkillDraft] = useState("");
  const [skillLoading, setSkillLoading] = useState(false);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillDrafting, setSkillDrafting] = useState(false);
  const [skillMessage, setSkillMessage] = useState("");

  useEffect(() => {
    setDefaultWorkflow(state.workflowProfile || "default");
    setEnabledModules(normalizeIds(state.enabledWorkflowModules));
  }, [state.enabledWorkflowModules, state.workflowProfile]);

  useEffect(() => {
    setWorkspaceView(requestedView === "workflows" ? "workflows" : "skills");
  }, [requestedView]);

  const workflowProfiles = useMemo(
    () =>
      normalizeWorkflowProfiles(workflowCatalog.workflows),
    [workflowCatalog.workflows],
  );
  const workflowModules = useMemo(
    () =>
      workflowCatalog.modules.length
        ? workflowCatalog.modules
        : DEFAULT_WORKFLOW_CATALOG.modules,
    [workflowCatalog.modules],
  );
  const workflowProfileMap = useMemo(
    () => new Map(workflowProfiles.map((workflow) => [workflow.id, workflow])),
    [workflowProfiles],
  );
  const workflowModuleMap = useMemo(
    () => new Map(workflowModules.map((module) => [module.id, module])),
    [workflowModules],
  );
  const workflowSkills = useMemo(
    () => (Array.isArray(skillCatalog.skills) ? skillCatalog.skills : []),
    [skillCatalog.skills],
  );
  const skillModuleMap = useMemo(() => {
    const next = new Map();
    workflowModules.forEach((module) => {
      const skillId = String(module.skill_id || module.id || "").trim();
      if (!skillId) return;
      const refs = next.get(skillId) || [];
      refs.push({
        id: String(module.id || "").trim(),
        label: String(module.label || module.id || skillId).trim(),
      });
      next.set(skillId, refs);
    });
    return next;
  }, [workflowModules]);
  const skillOptions = useMemo(() => {
    const catalogById = new Map(
      workflowSkills
        .map((skill) => {
          const id = String(skill?.id || "").trim();
          return id ? [id, skill] : null;
        })
        .filter(Boolean),
    );
    const optionsById = new Map();
    workflowModules.forEach((module) => {
      const id = String(module.skill_id || module.id || "").trim();
      if (!id || optionsById.has(id)) return;
      const skill = catalogById.get(id);
      optionsById.set(id, {
        id,
        label: String(skill?.label || module.label || id.replace(/[_-]+/g, " ")).trim(),
        source: String(skill?.source || (skill ? "" : "missing")).trim(),
        moduleRefs: skillModuleMap.get(id) || [],
      });
    });
    workflowSkills.forEach((skill) => {
      const id = String(skill?.id || "").trim();
      if (!id || optionsById.has(id)) return;
      optionsById.set(id, {
        id,
        label: String(skill?.label || id.replace(/[_-]+/g, " ")).trim(),
        source: String(skill?.source || "").trim(),
        moduleRefs: skillModuleMap.get(id) || [],
      });
    });
    if (selectedSkillId && !optionsById.has(selectedSkillId)) {
      optionsById.set(selectedSkillId, {
        id: selectedSkillId,
        label: selectedSkillId.replace(/[_-]+/g, " "),
        source: "new",
        moduleRefs: skillModuleMap.get(selectedSkillId) || [],
      });
    }
    return Array.from(optionsById.values());
  }, [selectedSkillId, skillModuleMap, workflowModules, workflowSkills]);
  const selectedSkillModules = useMemo(
    () => skillModuleMap.get(String(selectedSkillId || "").trim()) || [],
    [selectedSkillId, skillModuleMap],
  );
  const selectedWorkflow =
    workflowProfileMap.get(defaultWorkflow) || workflowProfiles[0] || null;
  const selectedModule =
    workflowModuleMap.get(moduleDetailsId) || workflowModules[0] || {};
  const workflowDirty =
    defaultWorkflow !== (state.workflowProfile || "default") ||
    [...normalizeIds(enabledModules)].sort().join("|") !==
      [...normalizeIds(state.enabledWorkflowModules)].sort().join("|");
  const savedSkillBody = String(skillDoc?.active?.body || "");
  const skillDirty = skillDraft !== savedSkillBody;
  const normalizedNewSkillId = String(newSkillId || "").trim();
  const newSkillIdError =
    normalizedNewSkillId && !SKILL_ID_PATTERN.test(normalizedNewSkillId)
      ? "Use only letters, numbers, dots, dashes, and underscores."
      : "";

  const confirmDiscardSkillDraft = () =>
    !skillDirty ||
    typeof window === "undefined" ||
    window.confirm("Discard the unsaved changes to this skill document?");

  const refreshCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogMessage("");
    try {
      const [catalogResult, skillsResult] = await Promise.allSettled([
        axios.get("/api/workflows/catalog"),
        axios.get("/api/workflows/skills"),
      ]);
      if (catalogResult.status === "fulfilled") {
        const payload = catalogResult.value?.data || {};
        setWorkflowCatalog({
          workflows: normalizeWorkflowProfiles(payload),
          modules: Array.isArray(payload.modules)
            ? payload.modules
            : DEFAULT_WORKFLOW_CATALOG.modules,
          addons: Array.isArray(payload.addons) ? payload.addons : [],
          addons_root:
            typeof payload.addons_root === "string" && payload.addons_root.trim()
              ? payload.addons_root.trim()
              : DEFAULT_WORKFLOW_CATALOG.addons_root,
        });
      }
      if (skillsResult.status === "fulfilled") {
        const payload = skillsResult.value?.data || {};
        setSkillCatalog({
          skills: Array.isArray(payload.skills) ? payload.skills : [],
          count: Number(payload.count) || 0,
          skills_root: typeof payload.skills_root === "string" ? payload.skills_root : "",
          skills_roots: Array.isArray(payload.skills_roots) ? payload.skills_roots : [],
        });
      }
      if (catalogResult.status === "rejected" && skillsResult.status === "rejected") {
        setCatalogMessage("Skills and workflow metadata could not be refreshed.");
      } else if (catalogResult.status === "rejected") {
        setCatalogMessage("Workflow profiles are unavailable; skill documents are still usable.");
      } else if (skillsResult.status === "rejected") {
        setCatalogMessage("Skill documents are unavailable; workflow profiles are still usable.");
      }
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    const moduleIds = workflowModules.map((module) => String(module.id || "").trim()).filter(Boolean);
    if (moduleIds.length && (!moduleDetailsId || !moduleIds.includes(moduleDetailsId))) {
      setModuleDetailsId(moduleIds[0]);
    }
    const knownSkillIds = skillOptions.map((option) => option.id).filter(Boolean);
    if (knownSkillIds.length && (!selectedSkillId || !knownSkillIds.includes(selectedSkillId))) {
      setSelectedSkillId(knownSkillIds[0]);
    }
  }, [moduleDetailsId, selectedSkillId, skillOptions, workflowModules]);

  const fetchSkillDoc = useCallback(async (skillId) => {
    const normalized = String(skillId || "").trim();
    if (!normalized) {
      setSkillDoc(null);
      setSkillDraft("");
      return;
    }
    setSkillLoading(true);
    setSkillMessage("");
    try {
      const response = await axios.get(
        `/api/workflows/skills/${encodeURIComponent(normalized)}`,
      );
      const payload = response?.data || null;
      setSkillDoc(payload);
      setSkillDraft(String(payload?.active?.body || ""));
    } catch {
      setSkillDoc(null);
      setSkillDraft("");
      setSkillMessage("Could not load that skill document.");
    } finally {
      setSkillLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkillDoc(selectedSkillId);
  }, [fetchSkillDoc, selectedSkillId]);

  useEffect(() => {
    if (!skillDirty || typeof window === "undefined") return undefined;
    const warnBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [skillDirty]);

  const handleWorkflowSave = async () => {
    const nextWorkflow = String(defaultWorkflow || "default").trim() || "default";
    const nextModules = normalizeIds(enabledModules);
    setWorkflowSaving(true);
    setWorkflowMessage("");
    try {
      await axios.post("/api/user-settings", {
        default_workflow: nextWorkflow,
        enabled_workflow_modules: nextModules,
      });
      setState((previous) => ({
        ...previous,
        workflowProfile: nextWorkflow,
        enabledWorkflowModules: nextModules,
      }));
      setWorkflowMessage("Workflow defaults saved.");
    } catch {
      setWorkflowMessage("Failed to save workflow defaults.");
    } finally {
      setWorkflowSaving(false);
    }
  };

  const handleNewSkillStart = () => {
    const normalized = normalizedNewSkillId;
    if (!SKILL_ID_PATTERN.test(normalized)) {
      setSkillMessage("Skill ids may only use letters, numbers, dots, dashes, and underscores.");
      return;
    }
    if (!confirmDiscardSkillDraft()) return;
    setSelectedSkillId(normalized);
    setNewSkillId("");
    setSkillMessage("");
  };

  const handleSkillSave = async () => {
    const normalized = String(selectedSkillId || "").trim();
    if (!normalized) return;
    setSkillSaving(true);
    setSkillMessage("");
    try {
      const response = await axios.put(
        `/api/workflows/skills/${encodeURIComponent(normalized)}`,
        { body: skillDraft },
      );
      const payload = response?.data || null;
      setSkillDoc(payload);
      setSkillDraft(String(payload?.active?.body || ""));
      setSkillMessage("Local skill override saved.");
      await refreshCatalog();
    } catch {
      setSkillMessage("Failed to save local skill override.");
    } finally {
      setSkillSaving(false);
    }
  };

  const handleSkillDelete = async () => {
    const normalized = String(selectedSkillId || "").trim();
    if (!normalized) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this local override and restore the packaged skill when available?")
    ) {
      return;
    }
    setSkillSaving(true);
    setSkillMessage("");
    try {
      const response = await axios.delete(
        `/api/workflows/skills/${encodeURIComponent(normalized)}`,
      );
      const payload = response?.data || null;
      setSkillDoc(payload);
      setSkillDraft(String(payload?.active?.body || ""));
      setSkillMessage(
        payload?.active
          ? "Local skill override removed; base doc is active again."
          : "Local skill doc removed. Save to recreate it locally.",
      );
      await refreshCatalog();
    } catch {
      setSkillMessage("Failed to remove local skill override.");
    } finally {
      setSkillSaving(false);
    }
  };

  const handleSkillReflectionDraft = async () => {
    const normalized = String(selectedSkillId || "").trim();
    if (!normalized || !confirmDiscardSkillDraft()) return;
    setSkillDrafting(true);
    setSkillMessage("Running one bounded reflection pass...");
    try {
      const response = await axios.post(
        `/api/workflows/skills/${encodeURIComponent(normalized)}/draft`,
        { model: String(state.transformerModel || "").trim() },
      );
      const body = String(response?.data?.proposal?.body || "");
      const taskId = String(response?.data?.audit?.task_id || "").trim();
      const traceEntries = Number(response?.data?.audit?.reasoning_trace?.entries) || 0;
      if (!body) {
        setSkillMessage("Reflection did not produce a usable proposal. No skill file was changed.");
        return;
      }
      setSkillDraft(body);
      setSkillMessage(
        `Reflection proposal loaded as an unsaved draft${taskId ? ` (audit: ${taskId})` : ""}. ${traceEntries ? `${traceEntries} reasoning trace part${traceEntries === 1 ? "" : "s"} preserved. ` : ""}Review it before saving.`,
      );
    } catch {
      setSkillMessage("Reflection proposal failed. No skill file was changed.");
    } finally {
      setSkillDrafting(false);
    }
  };

  const selectedTools = Array.isArray(selectedModule.tool_names)
    ? selectedModule.tool_names
    : [];
  const selectedAssets = Array.isArray(selectedModule.assets) ? selectedModule.assets : [];
  const selectedConfig =
    selectedModule.config && typeof selectedModule.config === "object"
      ? selectedModule.config
      : {};

  return (
    <section className="knowledge-skills-tab" aria-labelledby="knowledge-skills-title">
      <header className="knowledge-skills-hero">
        <div>
          <span className="knowledge-skills-eyebrow">Capability knowledge</span>
          <h2 id="knowledge-skills-title">Skills &amp; workflows</h2>
          <p>
            Choose the default response profile, control optional capability modules, and maintain
            local markdown guidance without changing packaged files.
          </p>
        </div>
        <button
          type="button"
          className="icon-btn knowledge-skills-refresh"
          onClick={refreshCatalog}
          disabled={catalogLoading}
        >
          {catalogLoading ? "Refreshing..." : "Refresh catalog"}
        </button>
      </header>

      <div className="knowledge-skills-summary" aria-label="Skills and workflow summary">
        <span>
          Default <strong>{selectedWorkflow?.label || defaultWorkflow}</strong>
        </span>
        <span>
          Modules <strong>{enabledModules.length}/{workflowModules.length}</strong>
        </span>
        <span>
          Skill docs <strong>{workflowSkills.length}</strong>
        </span>
      </div>
      {catalogMessage && <p className="knowledge-skills-message warn" role="status">{catalogMessage}</p>}

      <div className="knowledge-skills-view-tabs" role="tablist" aria-label="Skills workspace view">
        <button
          type="button"
          role="tab"
          aria-selected={workspaceView === "skills"}
          className={workspaceView === "skills" ? "is-active" : ""}
          onClick={() => setWorkspaceView("skills")}
        >
          Skill docs
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={workspaceView === "workflows"}
          className={workspaceView === "workflows" ? "is-active" : ""}
          onClick={() => setWorkspaceView("workflows")}
        >
          Workflows &amp; modules
        </button>
      </div>

      {workspaceView === "workflows" && <div className="knowledge-skills-grid">
        <section className="knowledge-skills-card" aria-labelledby="workflow-defaults-heading">
          <div className="knowledge-skills-card-head">
            <div>
              <span className="knowledge-skills-step">01</span>
              <h3 id="workflow-defaults-heading">Run defaults</h3>
            </div>
            <span className="knowledge-skills-badge">user setting</span>
          </div>

          <label htmlFor="knowledge-default-workflow">Default workflow profile</label>
          <select
            id="knowledge-default-workflow"
            value={defaultWorkflow}
            onChange={(event) => {
              setDefaultWorkflow(event.target.value);
              setWorkflowMessage("");
            }}
            disabled={workflowSaving}
          >
            {workflowProfiles.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>{workflow.label}</option>
            ))}
          </select>
          <p className="knowledge-skills-help">
            {selectedWorkflow?.description || "Profiles control reasoning depth and tool posture."}
          </p>

          <details className="knowledge-skills-disclosure">
            <summary>Compare workflow profiles</summary>
            <div className="knowledge-skills-profile-list">
              {workflowProfiles.map((workflow) => (
                <article
                  key={workflow.id}
                  className={defaultWorkflow === workflow.id ? "is-selected" : ""}
                >
                  <div>
                    <strong>{workflow.label}</strong>
                    {defaultWorkflow === workflow.id && <span>current</span>}
                  </div>
                  <p>{workflow.description}</p>
                  <small>
                    Thinking: {workflow.thinking_default || "auto"} · Continue: {workflowProfileMap.get(workflow.preferred_continue)?.label || workflow.preferred_continue || "active workflow"}
                  </small>
                </article>
              ))}
            </div>
          </details>

          <fieldset className="knowledge-skills-modules">
            <legend>Enabled modules</legend>
            {workflowModules.map((module) => {
              const toolCount = Array.isArray(module.tool_names) ? module.tool_names.length : 0;
              return (
                <label key={module.id} className="knowledge-skills-module-row">
                  <span>
                    <span className="knowledge-skills-module-title">
                      <strong>{module.label || module.id}</strong>
                      <em>{String(module.source || "base").toLowerCase()}</em>
                      {toolCount > 0 && <small>{toolCount} tools</small>}
                    </span>
                    <span>{module.description || "No module description supplied."}</span>
                  </span>
                  <input
                    type="checkbox"
                    aria-label={`${module.label || module.id} enabled`}
                    checked={enabledModules.includes(module.id)}
                    disabled={workflowSaving}
                    onChange={(event) => {
                      setWorkflowMessage("");
                      setEnabledModules((current) =>
                        event.target.checked
                          ? normalizeIds([...current, module.id])
                          : current.filter((item) => item !== module.id),
                      );
                    }}
                  />
                </label>
              );
            })}
          </fieldset>

          <div className="knowledge-skills-actions">
            <button
              type="button"
              className="icon-btn"
              onClick={handleWorkflowSave}
              disabled={workflowSaving || !workflowDirty}
            >
              {workflowSaving ? "Saving..." : "Save workflow defaults"}
            </button>
            {workflowMessage && <span role="status">{workflowMessage}</span>}
          </div>
        </section>

        <section className="knowledge-skills-card" aria-labelledby="module-details-heading">
          <div className="knowledge-skills-card-head">
            <div>
              <span className="knowledge-skills-step">02</span>
              <h3 id="module-details-heading">Module details</h3>
            </div>
            <span className="knowledge-skills-badge">
              source: {String(selectedModule.source || "base").toLowerCase()}
            </span>
          </div>
          <label htmlFor="knowledge-module-details">Inspect module</label>
          <select
            id="knowledge-module-details"
            value={moduleDetailsId}
            onChange={(event) => setModuleDetailsId(event.target.value)}
          >
            {workflowModules.map((module) => (
              <option key={module.id} value={module.id}>{module.label || module.id}</option>
            ))}
          </select>
          <div className="knowledge-skills-detail-list">
            <div><span>Skill</span><p>{selectedModule.skill_id || "No skill linked"}</p></div>
            <div><span>Tools</span><p>{selectedTools.length ? selectedTools.join(", ") : "No tools listed"}</p></div>
            <div>
              <span>Assets</span>
              <p>
                {selectedAssets.length
                  ? selectedAssets.map((asset) => typeof asset === "string" ? asset : asset?.label || asset?.path || "asset").join(", ")
                  : "No assets listed"}
              </p>
            </div>
            <div>
              <span>Config</span>
              {Object.keys(selectedConfig).length ? (
                <pre>{JSON.stringify(selectedConfig, null, 2)}</pre>
              ) : (
                <p>No custom config for this module.</p>
              )}
            </div>
          </div>
          <p className="knowledge-skills-help">
            Custom add-ons are discovered from <code>{workflowCatalog.addons_root || DEFAULT_WORKFLOW_CATALOG.addons_root}</code>.
          </p>
        </section>
      </div>}

      {workspaceView === "skills" && <section className="knowledge-skills-card knowledge-skills-editor" aria-label="Skill documents">
        <div className="knowledge-skills-card-head">
          <div>
            <span className="knowledge-skills-step">Skill editor</span>
            <h3>Skill documents</h3>
            <p>Local files are editable. Packaged skill docs remain read-only.</p>
          </div>
          {skillDoc?.active?.source && (
            <span className="knowledge-skills-badge">active: {skillDoc.active.source}</span>
          )}
        </div>

        <div className="knowledge-skills-editor-layout">
          <div className="knowledge-skills-editor-controls">
            <label htmlFor="knowledge-skill-select">Skill document</label>
            <select
              id="knowledge-skill-select"
              value={selectedSkillId}
              onChange={(event) => {
                if (confirmDiscardSkillDraft()) {
                  setSelectedSkillId(event.target.value);
                }
              }}
              disabled={skillLoading || skillSaving || skillDrafting}
            >
              {skillOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.moduleRefs.length
                    ? `${option.moduleRefs[0].label} (${option.id})`
                    : `${option.label} (${option.id})`}
                </option>
              ))}
            </select>

            <div className="knowledge-skills-meta">
              <span>Known docs <strong>{workflowSkills.length}</strong></span>
              <span>Local <strong>{skillDoc?.local_exists ? "yes" : "no"}</strong></span>
              <span>Base <strong>{skillDoc?.repo_exists ? "available" : "missing"}</strong></span>
              <span>Linked modules <strong>{selectedSkillModules.length ? selectedSkillModules.map((module) => module.label).join(", ") : "none"}</strong></span>
            </div>

            <div className="knowledge-skills-new">
              <label htmlFor="knowledge-new-skill-id">New local skill id</label>
              <input
                id="knowledge-new-skill-id"
                type="text"
                placeholder="incident_triage"
                value={newSkillId}
                onChange={(event) => {
                  setNewSkillId(event.target.value);
                  setSkillMessage("");
                }}
                disabled={skillLoading || skillSaving || skillDrafting}
                aria-invalid={newSkillIdError ? "true" : undefined}
                aria-describedby={newSkillIdError ? "knowledge-new-skill-error" : undefined}
              />
              <button
                type="button"
                className="icon-btn"
                onClick={handleNewSkillStart}
                disabled={
                  skillLoading ||
                  skillSaving ||
                  skillDrafting ||
                  !normalizedNewSkillId
                }
              >
                Start blank doc
              </button>
              {newSkillIdError && (
                <small id="knowledge-new-skill-error" className="knowledge-skills-inline-error">
                  {newSkillIdError}
                </small>
              )}
              <small>The file is created only when you save.</small>
            </div>
          </div>

          <div className="knowledge-skills-editor-body">
            <div className="knowledge-skills-editor-label">
              <label htmlFor="knowledge-skill-editor">Markdown guidance</label>
              <span>
                {skillDirty ? "unsaved changes" : skillDoc?.active ? "active body" : "empty draft"}
              </span>
            </div>
            <textarea
              id="knowledge-skill-editor"
              aria-label="Skill markdown editor"
              value={skillDraft}
              onChange={(event) => setSkillDraft(event.target.value)}
              disabled={skillLoading || skillSaving || skillDrafting || !selectedSkillId}
              spellCheck={false}
            />
            {skillDoc?.local_path && (
              <p className="knowledge-skills-path" title={skillDoc.local_path}>
                Save path <code>{skillDoc.local_path}</code>
              </p>
            )}
            <div className="knowledge-skills-actions">
              <button type="button" className="icon-btn" onClick={handleSkillSave} disabled={skillLoading || skillSaving || skillDrafting || !selectedSkillId || !skillDirty}>
                {skillSaving ? "Saving..." : "Save local override"}
              </button>
              <button type="button" className="icon-btn" onClick={handleSkillReflectionDraft} disabled={skillLoading || skillSaving || skillDrafting || !selectedSkillId}>
                {skillDrafting ? "Drafting..." : "Propose with reflection"}
              </button>
              <button type="button" className="icon-btn" onClick={handleSkillDelete} disabled={skillLoading || skillSaving || skillDrafting || !selectedSkillId || !skillDoc?.local_exists}>
                Delete local override
              </button>
              <button type="button" className="icon-btn" onClick={() => fetchSkillDoc(selectedSkillId)} disabled={skillLoading || skillSaving || skillDrafting || !selectedSkillId}>
                Reload
              </button>
              {skillMessage && <span role="status">{skillMessage}</span>}
            </div>
            <p className="knowledge-skills-help">
              Reflection proposals are audited and load here as unsaved text. Float never writes
              the skill file until you choose Save local override.
            </p>
          </div>
        </div>
      </section>}
    </section>
  );
};

export default KnowledgeSkillsTab;
