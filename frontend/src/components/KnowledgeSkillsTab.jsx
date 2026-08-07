import React, {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import axios from "axios";
import { useLocation } from "react-router-dom";

import { GlobalContext } from "../main";
import {
  FALLBACK_WORKFLOW_PROFILES,
  isWorkflowSelectableAsDefault,
  normalizeWorkflowProfiles,
  resolveSelectableWorkflowId,
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
const MAX_PORTABLE_SKILL_ID_LENGTH = 120;
const WINDOWS_DEVICE_NAMES = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
]);

const casefoldId = (value) => String(value || "").trim().toLowerCase();

const portableSkillIdReason = (value, { knownIds = [], excludeId = "" } = {}) => {
  const normalized = String(value || "").trim();
  if (!normalized || !SKILL_ID_PATTERN.test(normalized)) {
    return "Use only letters, numbers, dots, dashes, and underscores.";
  }
  if (normalized.length > MAX_PORTABLE_SKILL_ID_LENGTH) {
    return `Skill ids must be ${MAX_PORTABLE_SKILL_ID_LENGTH} characters or fewer.`;
  }
  if (casefoldId(normalized) === "readme") {
    return "README is reserved for directory documentation.";
  }
  if (normalized.startsWith(".") || normalized.endsWith(".")) {
    return "Skill ids cannot start or end with a dot.";
  }
  const windowsStem = casefoldId(normalized.split(".", 1)[0]);
  if (WINDOWS_DEVICE_NAMES.has(windowsStem)) {
    return "That skill id is reserved by Windows.";
  }
  const excluded = String(excludeId || "").trim();
  const collision = knownIds.find(
    (existing) => existing !== excluded && casefoldId(existing) === casefoldId(normalized),
  );
  return collision ? `A skill named '${collision}' already exists.` : "";
};

const apiErrorDetail = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item?.msg === "string" ? item.msg : ""))
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return fallback;
};

const MODULE_SOURCE_DETAILS = {
  base: {
    label: "Built-in / read-only",
    description: "Ships with Float. You can enable it here, but its package definition is read-only.",
  },
  repo: {
    label: "Packaged add-on / read-only",
    description: "Ships in Float's add-on directory. This page can enable it but does not edit its package.",
  },
  custom: {
    label: "Local add-on",
    description: "Loaded from the local add-on directory. This page can enable it but does not edit its package.",
  },
  local: {
    label: "Local add-on",
    description: "Loaded from the local add-on directory. This page can enable it but does not edit its package.",
  },
};

const SKILL_SOURCE_DETAILS = {
  repo: {
    label: "Packaged / read-only",
    description: "Ships with Float. Editing its text creates a separate user-owned local override.",
  },
  local: {
    label: "Local override / editable",
    description: "User-owned guidance stored in Float's local data directory and used instead of the packaged copy.",
  },
  missing: {
    label: "No saved document",
    description: "This id has no packaged or local document yet. Saving creates a user-owned local file.",
  },
  new: {
    label: "Unsaved local draft",
    description: "This draft exists only in the editor until you explicitly save it.",
  },
};

const sourceDetails = (source, details, fallbackLabel) => {
  const normalized = String(source || "").trim().toLowerCase();
  return details[normalized] || {
    label: fallbackLabel || (normalized ? normalized.replace(/[_-]+/g, " ") : "Unknown source"),
    description: "Float reported this source without additional ownership metadata.",
  };
};

const moduleStatusLabel = (status) => {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "live") return "Available";
  if (normalized === "experimental") return "Experimental";
  if (normalized === "planned") return "Planned / unavailable";
  if (normalized === "unavailable") return "Catalog unavailable";
  return normalized ? normalized.replace(/[_-]+/g, " ") : "Status unknown";
};

const safeDomId = (value) => String(value || "item").replace(/[^A-Za-z0-9_-]/g, "-");

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
  const [skillPreviewPending, setSkillPreviewPending] = useState(false);
  const [skillPreviewSaveMode, setSkillPreviewSaveMode] = useState("");
  const [lifecycleAction, setLifecycleAction] = useState("");
  const [duplicateTargetId, setDuplicateTargetId] = useState("");
  const [renameTargetId, setRenameTargetId] = useState("");
  const [importTargetId, setImportTargetId] = useState("");
  const [importFile, setImportFile] = useState(null);
  const [moduleHandoffId, setModuleHandoffId] = useState("");
  const [skillMessage, setSkillMessage] = useState("");
  const workspaceTabRefs = useRef({});
  const moduleInspectRefs = useRef({});
  const skipNextSkillFetchRef = useRef("");
  const pendingNewSkillCreateOnlyRef = useRef("");

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
  const defaultWorkflowProfiles = useMemo(
    () => workflowProfiles.filter(isWorkflowSelectableAsDefault),
    [workflowProfiles],
  );
  const selectedWorkflowId = resolveSelectableWorkflowId(
    workflowProfiles,
    defaultWorkflow,
    { selection: "default" },
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
    () => new Map(workflowModules.map((module) => [casefoldId(module.id), module])),
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
      const foldedSkillId = casefoldId(skillId);
      const refs = next.get(foldedSkillId) || [];
      refs.push({
        id: String(module.id || "").trim(),
        label: String(module.label || module.id || skillId).trim(),
      });
      next.set(foldedSkillId, refs);
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
        moduleRefs: skillModuleMap.get(casefoldId(id)) || [],
      });
    });
    workflowSkills.forEach((skill) => {
      const id = String(skill?.id || "").trim();
      if (!id || optionsById.has(id)) return;
      optionsById.set(id, {
        id,
        label: String(skill?.label || id.replace(/[_-]+/g, " ")).trim(),
        source: String(skill?.source || "").trim(),
        moduleRefs: skillModuleMap.get(casefoldId(id)) || [],
      });
    });
    if (selectedSkillId && !optionsById.has(selectedSkillId)) {
      optionsById.set(selectedSkillId, {
        id: selectedSkillId,
        label: selectedSkillId.replace(/[_-]+/g, " "),
        source: "new",
        moduleRefs: skillModuleMap.get(casefoldId(selectedSkillId)) || [],
      });
    }
    return Array.from(optionsById.values());
  }, [selectedSkillId, skillModuleMap, workflowModules, workflowSkills]);
  const selectedSkillModules = useMemo(() => {
    const payloadMatchesSelection =
      casefoldId(skillDoc?.id) === casefoldId(selectedSkillId);
    const payloadLinks = payloadMatchesSelection && Array.isArray(skillDoc?.linked_modules)
      ? skillDoc.linked_modules
      : null;
    const refs = payloadLinks === null
      ? skillModuleMap.get(casefoldId(selectedSkillId)) || []
      : payloadLinks;
    const seen = new Set();
    return refs.flatMap((link) => {
      const payloadLink = typeof link === "string" ? { id: link } : link || {};
      const rawId = String(payloadLink.id || "").trim();
      const foldedId = casefoldId(rawId);
      if (!foldedId || seen.has(foldedId)) return [];
      seen.add(foldedId);
      const catalogModule = workflowModuleMap.get(foldedId) || {};
      return [{
        ...catalogModule,
        ...payloadLink,
        id: String(catalogModule.id || rawId).trim(),
        label: String(payloadLink.label || catalogModule.label || rawId).trim(),
      }];
    });
  }, [selectedSkillId, skillDoc, skillModuleMap, workflowModuleMap]);
  const linkedModulesMissingFromCatalog = useMemo(
    () => selectedSkillModules
      .filter((module) => !workflowModuleMap.has(casefoldId(module.id)))
      .map((module) => ({
        ...module,
        description: module.description ||
          "This module is linked by the selected skill document, but its catalog metadata is unavailable.",
        status: "unavailable",
        source: module.source || "",
        skill_id: selectedSkillId,
        tool_names: [],
        catalog_available: false,
      })),
    [selectedSkillId, selectedSkillModules, workflowModuleMap],
  );
  const inspectableModules = useMemo(
    () => [...workflowModules, ...linkedModulesMissingFromCatalog],
    [linkedModulesMissingFromCatalog, workflowModules],
  );
  const inspectableModuleMap = useMemo(
    () => new Map(inspectableModules.map((module) => [casefoldId(module.id), module])),
    [inspectableModules],
  );
  const linkedMissingModuleMap = useMemo(
    () => new Map(
      linkedModulesMissingFromCatalog.map((module) => [casefoldId(module.id), module]),
    ),
    [linkedModulesMissingFromCatalog],
  );
  const selectedWorkflow =
    workflowProfileMap.get(selectedWorkflowId) || defaultWorkflowProfiles[0] || null;
  const selectedModule =
    inspectableModuleMap.get(casefoldId(moduleDetailsId)) || inspectableModules[0] || {};
  const savedEnabledModules = normalizeIds(state.enabledWorkflowModules);
  const knownModuleIds = useMemo(
    () => new Set(workflowModules.map((module) => casefoldId(module.id)).filter(Boolean)),
    [workflowModules],
  );
  const unavailableModuleIds = normalizeIds([
    ...savedEnabledModules,
    ...enabledModules,
  ]).filter((id) => !knownModuleIds.has(casefoldId(id)));
  const unavailableModuleIdSet = useMemo(
    () => new Set(unavailableModuleIds.map(casefoldId)),
    [unavailableModuleIds],
  );
  const visibleEnabledModuleCount = normalizeIds(enabledModules).filter((id) =>
    knownModuleIds.has(casefoldId(id)),
  ).length;
  const unavailableEnabledCount = normalizeIds(enabledModules).filter(
    (id) => !knownModuleIds.has(casefoldId(id)),
  ).length;
  const workflowDirty =
    selectedWorkflowId !== (state.workflowProfile || "default") ||
    [...normalizeIds(enabledModules)].sort().join("|") !==
      [...savedEnabledModules].sort().join("|");
  const savedSkillBody = String(skillDoc?.active?.body || "");
  const skillDirty = skillPreviewPending || skillDraft !== savedSkillBody;
  const knownSkillIds = useMemo(
    () => workflowSkills.map((skill) => String(skill?.id || "").trim()).filter(Boolean),
    [workflowSkills],
  );
  const normalizedNewSkillId = String(newSkillId || "").trim();
  const newSkillIdError = normalizedNewSkillId
    ? portableSkillIdReason(normalizedNewSkillId, { knownIds: knownSkillIds })
    : "";
  const normalizedDuplicateTargetId = String(duplicateTargetId || "").trim();
  const duplicateTargetIdError = normalizedDuplicateTargetId
    ? portableSkillIdReason(normalizedDuplicateTargetId, { knownIds: knownSkillIds })
    : "";
  const normalizedRenameTargetId = String(renameTargetId || "").trim();
  const renameTargetIdError = normalizedRenameTargetId &&
    casefoldId(normalizedRenameTargetId) === casefoldId(selectedSkillId)
    ? "The new skill id must differ from the current id."
    : normalizedRenameTargetId
      ? portableSkillIdReason(normalizedRenameTargetId, {
        knownIds: knownSkillIds,
        excludeId: selectedSkillId,
      })
      : "";
  const normalizedImportTargetId = String(importTargetId || "").trim();
  const importTargetIdError = normalizedImportTargetId
    ? portableSkillIdReason(normalizedImportTargetId, { knownIds: knownSkillIds })
    : "";
  const renameAllowed = skillDoc?.rename_allowed === true;
  const skillBusy = Boolean(
    skillLoading || skillSaving || skillDrafting || lifecycleAction,
  );
  const selectedModuleSource = sourceDetails(
    selectedModule.source,
    MODULE_SOURCE_DETAILS,
    "Module source unknown",
  );
  const selectedModuleEnabled = enabledModules.includes(selectedModule.id);
  const selectedModuleSavedEnabled = savedEnabledModules.includes(selectedModule.id);
  const selectedSkillOption = skillOptions.find((option) => option.id === selectedSkillId);
  const selectedSkillOptionSource = String(selectedSkillOption?.source || "missing").trim();
  const selectedSkillSource = sourceDetails(
    skillDoc?.active?.source || selectedSkillOptionSource,
    SKILL_SOURCE_DETAILS,
    "Skill source unknown",
  );
  const skillEditorState = skillDirty
    ? skillPreviewSaveMode === "create_only"
      ? "Unsaved create-only draft"
      : "Unsaved local draft"
    : skillDoc?.local_exists
      ? "Saved local override"
      : skillDoc?.active
        ? "Viewing packaged source"
        : selectedSkillOptionSource === "new"
          ? "Empty unsaved draft"
          : "No saved document";

  const confirmDiscardSkillDraft = () =>
    !skillDirty ||
    typeof window === "undefined" ||
    window.confirm("Discard the unsaved changes to this skill document?");

  const selectWorkspaceView = (nextView, focus = false) => {
    setWorkspaceView(nextView);
    if (focus) {
      const focusTab = () => workspaceTabRefs.current[nextView]?.focus();
      if (typeof window.requestAnimationFrame === "function") {
        window.requestAnimationFrame(focusTab);
      } else {
        focusTab();
      }
    }
  };

  const handleWorkspaceTabKeyDown = (event) => {
    const orderedViews = ["skills", "workflows"];
    const currentIndex = orderedViews.indexOf(workspaceView);
    let nextView = "";
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextView = orderedViews[(currentIndex + 1) % orderedViews.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextView = orderedViews[(currentIndex - 1 + orderedViews.length) % orderedViews.length];
    } else if (event.key === "Home") {
      nextView = orderedViews[0];
    } else if (event.key === "End") {
      nextView = orderedViews[orderedViews.length - 1];
    }
    if (!nextView) return;
    event.preventDefault();
    selectWorkspaceView(nextView, true);
  };

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
    const moduleIds = inspectableModules
      .map((module) => String(module.id || "").trim())
      .filter(Boolean);
    const hasSelectedModule = moduleIds.some(
      (moduleId) => casefoldId(moduleId) === casefoldId(moduleDetailsId),
    );
    if (moduleIds.length && (!moduleDetailsId || !hasSelectedModule)) {
      setModuleDetailsId(moduleIds[0]);
    }
    const knownSkillIds = skillOptions.map((option) => option.id).filter(Boolean);
    if (knownSkillIds.length && (!selectedSkillId || !knownSkillIds.includes(selectedSkillId))) {
      setSelectedSkillId(knownSkillIds[0]);
    }
  }, [inspectableModules, moduleDetailsId, selectedSkillId, skillOptions]);

  const fetchSkillDoc = useCallback(async (skillId) => {
    const normalized = String(skillId || "").trim();
    const requestedCreateOnly =
      casefoldId(pendingNewSkillCreateOnlyRef.current) === casefoldId(normalized);
    if (!normalized) {
      setSkillDoc(null);
      setSkillDraft("");
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode("");
      pendingNewSkillCreateOnlyRef.current = "";
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
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode(requestedCreateOnly ? "create_only" : "");
      pendingNewSkillCreateOnlyRef.current = "";
    } catch (error) {
      setSkillDoc(null);
      setSkillDraft("");
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode("");
      pendingNewSkillCreateOnlyRef.current = "";
      setSkillMessage(apiErrorDetail(error, "Could not load that skill document."));
    } finally {
      setSkillLoading(false);
    }
  }, []);

  useEffect(() => {
    if (skipNextSkillFetchRef.current === selectedSkillId) {
      skipNextSkillFetchRef.current = "";
      return;
    }
    fetchSkillDoc(selectedSkillId);
  }, [fetchSkillDoc, selectedSkillId]);

  useEffect(() => {
    if (workspaceView !== "workflows" || !moduleHandoffId) return;
    const targetKey = Object.keys(moduleInspectRefs.current).find(
      (moduleId) => casefoldId(moduleId) === casefoldId(moduleHandoffId),
    );
    const target = targetKey ? moduleInspectRefs.current[targetKey] : null;
    if (target) {
      target.focus();
      target.scrollIntoView?.({ block: "nearest" });
    }
    setModuleHandoffId("");
  }, [moduleHandoffId, workspaceView]);

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
    const nextWorkflow = selectedWorkflowId;
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
    if (!normalized || newSkillIdError) {
      setSkillMessage(newSkillIdError || "Enter an id for the new local skill draft.");
      return;
    }
    if (!confirmDiscardSkillDraft()) return;
    setSkillPreviewPending(false);
    setSkillPreviewSaveMode("");
    pendingNewSkillCreateOnlyRef.current = normalized;
    if (normalized === selectedSkillId) {
      fetchSkillDoc(normalized);
    } else {
      setSelectedSkillId(normalized);
    }
    setNewSkillId("");
    setSkillMessage("");
  };

  const handleSkillSave = async () => {
    const normalized = String(selectedSkillId || "").trim();
    if (!normalized) return;
    setSkillSaving(true);
    setSkillMessage("");
    try {
      const savePayload = {
        body: skillDraft,
        ...(skillPreviewSaveMode === "create_only" ? { create_only: true } : {}),
      };
      const response = await axios.put(
        `/api/workflows/skills/${encodeURIComponent(normalized)}`,
        savePayload,
      );
      const payload = response?.data || null;
      setSkillDoc(payload);
      setSkillDraft(String(payload?.active?.body || ""));
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode("");
      setSkillMessage("Local skill override saved.");
      await refreshCatalog();
    } catch (error) {
      setSkillMessage(apiErrorDetail(error, "Failed to save local skill override."));
    } finally {
      setSkillSaving(false);
    }
  };

  const handleSkillDelete = async () => {
    const normalized = String(selectedSkillId || "").trim();
    if (!normalized) return;
    const linkedModuleNames = selectedSkillModules.map((module) => module.label).join(", ");
    const confirmation = skillDoc?.repo_exists
      ? `Restore the packaged version of "${normalized}"? This permanently deletes the user-owned local override. The packaged guidance will become active again${linkedModuleNames ? ` for linked module${selectedSkillModules.length === 1 ? "" : "s"} ${linkedModuleNames}` : ""}.`
      : `Delete local skill "${normalized}"? This permanently deletes the user-owned file.${linkedModuleNames ? ` Linked module${selectedSkillModules.length === 1 ? "" : "s"} ${linkedModuleNames} will no longer have this skill guidance.` : " This skill is not linked to a module."}`;
    if (typeof window !== "undefined" && !window.confirm(confirmation)) {
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
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode("");
      setSkillMessage(
        payload?.active
          ? "Local override removed; packaged version is active again."
          : "Local skill deleted. Start a new draft to recreate it.",
      );
      await refreshCatalog();
    } catch (error) {
      setSkillMessage(apiErrorDetail(error, "Failed to remove local skill override."));
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
      setSkillPreviewPending(true);
      setSkillPreviewSaveMode("");
      setSkillMessage(
        `Reflection proposal loaded as an unsaved draft${taskId ? ` (audit: ${taskId})` : ""}. ${traceEntries ? `${traceEntries} reasoning trace part${traceEntries === 1 ? "" : "s"} preserved. ` : ""}Review it before saving.`,
      );
    } catch (error) {
      setSkillMessage(
        apiErrorDetail(error, "Reflection proposal failed. No skill file was changed."),
      );
    } finally {
      setSkillDrafting(false);
    }
  };

  const handleSkillReload = () => {
    if (!confirmDiscardSkillDraft()) return;
    fetchSkillDoc(selectedSkillId);
  };

  const applyUnsavedPreview = (payload, fallbackTargetId, message) => {
    const targetId = String(
      payload?.target_id || payload?.document?.id || fallbackTargetId || "",
    ).trim();
    const proposal = payload?.proposal;
    if (
      !SKILL_ID_PATTERN.test(targetId) ||
      !proposal ||
      !Object.prototype.hasOwnProperty.call(proposal, "body") ||
      payload?.audit?.wrote_skill_file !== false
    ) {
      throw new Error("Preview response did not confirm a safe unsaved draft.");
    }
    const nextDocument = payload?.document || {
      id: targetId,
      doc_id: `skills:${targetId}`,
      repo_exists: false,
      local_exists: false,
      active: null,
    };
    if (targetId !== selectedSkillId) {
      skipNextSkillFetchRef.current = targetId;
    }
    setSelectedSkillId(targetId);
    setSkillDoc(nextDocument);
    setSkillDraft(String(proposal.body || ""));
    setSkillPreviewPending(true);
    const previewSaveMode = String(
      proposal.save_mode || payload?.save_mode || "",
    ).trim();
    setSkillPreviewSaveMode(
      previewSaveMode === "create_only" || proposal.create_only === true
        ? "create_only"
        : "",
    );
    setSkillMessage(message);
  };

  const handleDuplicatePreview = async () => {
    if (!normalizedDuplicateTargetId || duplicateTargetIdError) {
      setSkillMessage(duplicateTargetIdError || "Enter a target id for the duplicate.");
      return;
    }
    if (!confirmDiscardSkillDraft()) return;
    setLifecycleAction("duplicate");
    setSkillMessage("");
    try {
      const response = await axios.post(
        `/api/workflows/skills/${encodeURIComponent(selectedSkillId)}/duplicate-preview`,
        { target_id: normalizedDuplicateTargetId },
      );
      applyUnsavedPreview(
        response?.data,
        normalizedDuplicateTargetId,
        `Duplicate preview loaded for ${normalizedDuplicateTargetId}. No skill file was written.`,
      );
      setDuplicateTargetId("");
    } catch (error) {
      setSkillMessage(
        apiErrorDetail(
          error,
          "Could not prepare a duplicate preview. No skill file was written.",
        ),
      );
    } finally {
      setLifecycleAction("");
    }
  };

  const handleImportPreview = async () => {
    if (!normalizedImportTargetId || importTargetIdError || !importFile) {
      setSkillMessage(
        importTargetIdError || "Choose a text or markdown file and enter its target skill id.",
      );
      return;
    }
    if (!confirmDiscardSkillDraft()) return;
    setLifecycleAction("import");
    setSkillMessage("");
    try {
      const body = typeof importFile.text === "function"
        ? await importFile.text()
        : await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => reject(reader.error);
          reader.readAsText(importFile);
        });
      const response = await axios.post("/api/workflows/skills/import-preview", {
        filename: importFile.name,
        body,
        target_id: normalizedImportTargetId,
      });
      applyUnsavedPreview(
        response?.data,
        normalizedImportTargetId,
        `Import preview loaded for ${normalizedImportTargetId}. No skill file was written.`,
      );
      setImportTargetId("");
      setImportFile(null);
    } catch (error) {
      setSkillMessage(
        apiErrorDetail(
          error,
          "Could not prepare the import preview. No skill file was written.",
        ),
      );
    } finally {
      setLifecycleAction("");
    }
  };

  const handleSkillRename = async () => {
    if (!renameAllowed || skillDirty) return;
    if (!normalizedRenameTargetId || renameTargetIdError) {
      setSkillMessage(renameTargetIdError || "Enter a new id for this local skill.");
      return;
    }
    setLifecycleAction("rename");
    setSkillMessage("");
    try {
      const response = await axios.post(
        `/api/workflows/skills/${encodeURIComponent(selectedSkillId)}/rename`,
        { target_id: normalizedRenameTargetId },
      );
      const newId = String(response?.data?.new_id || normalizedRenameTargetId).trim();
      const nextDocument = response?.data?.document || null;
      if (!SKILL_ID_PATTERN.test(newId) || !nextDocument) {
        throw new Error("Rename response did not include the renamed document.");
      }
      if (newId !== selectedSkillId) {
        skipNextSkillFetchRef.current = newId;
      }
      setSelectedSkillId(newId);
      setSkillDoc(nextDocument);
      setSkillDraft(String(nextDocument?.active?.body || ""));
      setSkillPreviewPending(false);
      setSkillPreviewSaveMode("");
      setRenameTargetId("");
      setSkillMessage(`Local skill renamed to ${newId}.`);
      await refreshCatalog();
    } catch (error) {
      setSkillMessage(
        apiErrorDetail(error, "Could not rename this local skill. Its current id was kept."),
      );
    } finally {
      setLifecycleAction("");
    }
  };

  const handleSkillExport = async () => {
    if (!selectedSkillId || !skillDoc?.active) return;
    setLifecycleAction("export");
    setSkillMessage("");
    let objectUrl = "";
    try {
      const response = await axios.get(
        `/api/workflows/skills/${encodeURIComponent(selectedSkillId)}/export`,
        { responseType: "blob" },
      );
      const blob = response?.data instanceof Blob
        ? response.data
        : new Blob([response?.data || ""], { type: "text/markdown" });
      const disposition = String(response?.headers?.["content-disposition"] || "");
      const filenameMatch = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)/i);
      const filename = filenameMatch?.[1]
        ? decodeURIComponent(filenameMatch[1].replace(/"$/, ""))
        : `${selectedSkillId}.md`;
      objectUrl = window.URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      window.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setSkillMessage(`Exported ${filename}.`);
    } catch (error) {
      setSkillMessage(apiErrorDetail(error, "Could not export this skill document."));
    } finally {
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
      setLifecycleAction("");
    }
  };

  const handleManageLinkedModules = () => {
    const firstModule = selectedSkillModules[0];
    if (!firstModule) return;
    setModuleDetailsId(firstModule.id);
    setModuleHandoffId(firstModule.id);
    selectWorkspaceView("workflows");
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
            Choose saved behavior for future chat turns, control which optional tools are available
            across chats, and maintain user-owned guidance without changing packaged files.
          </p>
        </div>
        <div className="knowledge-skills-refresh-group">
          <button
            type="button"
            className="icon-btn knowledge-skills-refresh"
            onClick={refreshCatalog}
            disabled={catalogLoading}
            aria-describedby="knowledge-skills-refresh-help"
          >
            {catalogLoading ? "Refreshing catalog..." : "Refresh available items"}
          </button>
          <small id="knowledge-skills-refresh-help">
            Rereads profiles, modules, and document names. It does not save or replace this draft.
          </small>
        </div>
      </header>

      <div className="knowledge-skills-summary" aria-label="Skills and workflow summary">
        <span>
          Default workflow <strong>{selectedWorkflow?.label || defaultWorkflow}</strong>
        </span>
        <span>
          Global modules <strong>{visibleEnabledModuleCount} of {workflowModules.length} enabled</strong>
          {unavailableEnabledCount > 0 && (
            <small>{` + ${unavailableEnabledCount} unavailable saved`}</small>
          )}
        </span>
        <span>
          Saved docs <strong>{workflowSkills.length}</strong>
        </span>
        <span
          className={workflowDirty ? "is-pending" : "is-saved"}
          aria-live="polite"
        >
          Workflow settings <strong>{workflowDirty ? "Unsaved changes" : "Saved"}</strong>
        </span>
      </div>
      {catalogMessage && <p className="knowledge-skills-message warn" role="status">{catalogMessage}</p>}

      <div className="knowledge-skills-view-tabs" role="tablist" aria-label="Skills workspace view">
        <button
          id="knowledge-skills-tab-skills"
          ref={(node) => { workspaceTabRefs.current.skills = node; }}
          type="button"
          role="tab"
          aria-selected={workspaceView === "skills"}
          aria-controls="knowledge-skills-panel-skills"
          tabIndex={workspaceView === "skills" ? 0 : -1}
          className={workspaceView === "skills" ? "is-active" : ""}
          onClick={() => selectWorkspaceView("skills")}
          onKeyDown={handleWorkspaceTabKeyDown}
        >
          Skill docs
        </button>
        <button
          id="knowledge-skills-tab-workflows"
          ref={(node) => { workspaceTabRefs.current.workflows = node; }}
          type="button"
          role="tab"
          aria-selected={workspaceView === "workflows"}
          aria-controls="knowledge-skills-panel-workflows"
          tabIndex={workspaceView === "workflows" ? 0 : -1}
          className={workspaceView === "workflows" ? "is-active" : ""}
          onClick={() => selectWorkspaceView("workflows")}
          onKeyDown={handleWorkspaceTabKeyDown}
        >
          Workflows &amp; modules
        </button>
      </div>

      {workspaceView === "workflows" && <div
        id="knowledge-skills-panel-workflows"
        className="knowledge-skills-grid"
        role="tabpanel"
        aria-labelledby="knowledge-skills-tab-workflows"
        tabIndex={0}
      >
        <section className="knowledge-skills-card" aria-labelledby="workflow-defaults-heading">
          <div className="knowledge-skills-card-head">
            <div>
              <span className="knowledge-skills-step">01</span>
              <h3 id="workflow-defaults-heading">Saved chat defaults</h3>
            </div>
            <span className="knowledge-skills-badge">Scope: all chats</span>
          </div>

          <label htmlFor="knowledge-default-workflow">Default chat workflow for future turns</label>
          <select
            id="knowledge-default-workflow"
            value={selectedWorkflowId}
            onChange={(event) => {
              setDefaultWorkflow(event.target.value);
              setWorkflowMessage("");
            }}
            disabled={workflowSaving}
            aria-describedby="knowledge-default-workflow-help"
          >
            {defaultWorkflowProfiles.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>{workflow.label}</option>
            ))}
          </select>
          <p id="knowledge-default-workflow-help" className="knowledge-skills-help">
            {selectedWorkflow?.description || "Profiles set prompt guidance and default reasoning."}
          </p>
          <p className="knowledge-skills-help">
            Saving changes prompt and reasoning guidance for later turns. It does not launch workers,
            switch models, or grant tools.
          </p>

          <details className="knowledge-skills-disclosure">
            <summary>Compare workflow profiles</summary>
            <div className="knowledge-skills-profile-list">
              {workflowProfiles.map((workflow) => (
                <article
                  key={workflow.id}
                  className={selectedWorkflowId === workflow.id ? "is-selected" : ""}
                >
                  <div>
                    <strong>{workflow.label}</strong>
                    <span className="knowledge-skills-profile-flags">
                      {selectedWorkflowId === workflow.id && <em>selected</em>}
                      {(state.workflowProfile || "default") === workflow.id && <em>saved default</em>}
                      {workflow.profile_kind === "system" && <em>system-only / inspect</em>}
                    </span>
                  </div>
                  <p>{workflow.description}</p>
                  <small>
                    Guidance: {workflow.guidance_style || "balanced"} / Reasoning: {workflow.thinking_default || "auto"}
                  </small>
                </article>
              ))}
            </div>
          </details>

          <fieldset className="knowledge-skills-modules">
            <legend>Optional tools available across chats</legend>
            <p className="knowledge-skills-help knowledge-skills-fieldset-help">
              A module switch changes which grouped tools Float may offer on future turns. Changes
              remain a draft until you save below; normal tool approval rules still apply.
            </p>
            {workflowModules.map((module) => {
              const toolCount = Array.isArray(module.tool_names) ? module.tool_names.length : 0;
              const enabled = enabledModules.includes(module.id);
              const savedEnabled = savedEnabledModules.includes(module.id);
              const pending = enabled !== savedEnabled;
              const moduleSource = sourceDetails(
                module.source,
                MODULE_SOURCE_DETAILS,
                "Module source unknown",
              );
              const moduleDescriptionId = `knowledge-module-${safeDomId(module.id)}-description`;
              const moduleStateId = `knowledge-module-${safeDomId(module.id)}-state`;
              return (
                <div
                  key={module.id}
                  className={`knowledge-skills-module-row${casefoldId(moduleDetailsId) === casefoldId(module.id) ? " is-inspected" : ""}`}
                >
                  <span>
                    <button
                      ref={(node) => { moduleInspectRefs.current[module.id] = node; }}
                      type="button"
                      className="knowledge-skills-module-inspect"
                      aria-label={`Inspect ${module.label || module.id} module package`}
                      aria-pressed={casefoldId(moduleDetailsId) === casefoldId(module.id)}
                      onClick={() => setModuleDetailsId(module.id)}
                    >
                      <span className="knowledge-skills-module-title">
                        <strong>{module.label || module.id}</strong>
                        <em>{moduleSource.label}</em>
                        <small>{moduleStatusLabel(module.status)}</small>
                        {toolCount > 0 && <small>{toolCount} tools</small>}
                      </span>
                    </button>
                    <span id={moduleDescriptionId}>
                      {module.description || "No module description supplied."} {moduleSource.description}
                    </span>
                  </span>
                  <label className="knowledge-skills-module-toggle">
                    <input
                      type="checkbox"
                      aria-label={`${enabled ? "Disable" : "Enable"} ${module.label || module.id} tools across chats`}
                      aria-describedby={`${moduleDescriptionId} ${moduleStateId}`}
                      checked={enabled}
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
                    <small id={moduleStateId} className={pending ? "is-pending" : ""}>
                      {pending
                        ? `Will ${enabled ? "enable" : "disable"} after save`
                        : enabled ? "Enabled globally" : "Off globally"}
                    </small>
                  </label>
                </div>
              );
            })}
            {linkedModulesMissingFromCatalog
              .filter((module) => !unavailableModuleIdSet.has(casefoldId(module.id)))
              .map((module) => {
                const descriptionId = `knowledge-module-${safeDomId(module.id)}-description`;
                return (
                  <div
                    key={`linked-${module.id}`}
                    className={`knowledge-skills-module-row is-unavailable${casefoldId(moduleDetailsId) === casefoldId(module.id) ? " is-inspected" : ""}`}
                  >
                    <span>
                      <button
                        ref={(node) => { moduleInspectRefs.current[module.id] = node; }}
                        type="button"
                        className="knowledge-skills-module-inspect"
                        aria-label={`Inspect ${module.label || module.id} module package`}
                        aria-pressed={casefoldId(moduleDetailsId) === casefoldId(module.id)}
                        onClick={() => setModuleDetailsId(module.id)}
                      >
                        <span className="knowledge-skills-module-title">
                          <strong>{module.label || module.id}</strong>
                          <em>Linked module</em>
                          <small>Catalog unavailable</small>
                        </span>
                      </button>
                      <span id={descriptionId}>{module.description}</span>
                    </span>
                    <span className="knowledge-skills-module-toggle is-static">
                      <small>Access unavailable</small>
                    </span>
                  </div>
                );
              })}
            {unavailableModuleIds.map((moduleId) => {
              const enabled = enabledModules.includes(moduleId);
              const savedEnabled = savedEnabledModules.includes(moduleId);
              const pending = enabled !== savedEnabled;
              const linkedModule = linkedMissingModuleMap.get(casefoldId(moduleId));
              const moduleLabel = linkedModule?.label || moduleId;
              const descriptionId = `knowledge-module-${safeDomId(moduleId)}-description`;
              const stateId = `knowledge-module-${safeDomId(moduleId)}-state`;
              return (
                <div
                  key={moduleId}
                  className={`knowledge-skills-module-row is-unavailable${casefoldId(moduleDetailsId) === casefoldId(moduleId) ? " is-inspected" : ""}`}
                >
                  <span>
                    {linkedModule ? (
                      <button
                        ref={(node) => { moduleInspectRefs.current[moduleId] = node; }}
                        type="button"
                        className="knowledge-skills-module-inspect"
                        aria-label={`Inspect ${moduleLabel} module package`}
                        aria-pressed={casefoldId(moduleDetailsId) === casefoldId(moduleId)}
                        onClick={() => setModuleDetailsId(moduleId)}
                      >
                        <span className="knowledge-skills-module-title">
                          <strong>{moduleLabel}</strong>
                          <em>Linked module</em>
                          <small>Unavailable saved module</small>
                        </span>
                      </button>
                    ) : (
                      <span className="knowledge-skills-module-title">
                        <strong>{moduleId}</strong>
                        <em>Unavailable saved module</em>
                      </span>
                    )}
                    <span id={descriptionId}>
                      This saved module id is not present in the current catalog. Disable it here
                      to remove the stale setting; Float cannot offer its tools while unavailable.
                    </span>
                  </span>
                  <label className="knowledge-skills-module-toggle">
                    <input
                      type="checkbox"
                      aria-label={`${enabled ? "Disable" : "Keep disabled"} unavailable ${moduleId} module`}
                      aria-describedby={`${descriptionId} ${stateId}`}
                      checked={enabled}
                      disabled={workflowSaving}
                      onChange={(event) => {
                        setWorkflowMessage("");
                        setEnabledModules((current) =>
                          event.target.checked
                            ? normalizeIds([...current, moduleId])
                            : current.filter((item) => item !== moduleId),
                        );
                      }}
                    />
                    <small id={stateId} className={pending ? "is-pending" : ""}>
                      {pending ? "Will remove after save" : "Saved but unavailable"}
                    </small>
                  </label>
                </div>
              );
            })}
          </fieldset>

          <div className="knowledge-skills-actions">
            <button
              type="button"
              className="icon-btn"
              onClick={handleWorkflowSave}
              disabled={workflowSaving || !workflowDirty}
              aria-describedby="knowledge-workflow-save-help"
            >
              {workflowSaving ? "Saving settings..." : "Save workflow & module settings"}
            </button>
            {workflowMessage && <span role="status">{workflowMessage}</span>}
          </div>
          <p id="knowledge-workflow-save-help" className="knowledge-skills-help">
            Saves the selected workflow and module access together as user settings. It does not
            edit packaged workflow or module definitions.
          </p>
        </section>

        <section className="knowledge-skills-card" aria-labelledby="module-details-heading">
          <div className="knowledge-skills-card-head">
            <div>
              <span className="knowledge-skills-step">02</span>
              <h3 id="module-details-heading">Inspect module package</h3>
            </div>
            <span className="knowledge-skills-badge">
              {selectedModuleSource.label}
            </span>
          </div>
          <label htmlFor="knowledge-module-details">Module to inspect</label>
          <select
            id="knowledge-module-details"
            value={moduleDetailsId}
            onChange={(event) => setModuleDetailsId(event.target.value)}
            aria-describedby="knowledge-module-details-help"
          >
            {inspectableModules.map((module) => (
              <option key={module.id} value={module.id}>{module.label || module.id}</option>
            ))}
          </select>
          <p id="knowledge-module-details-help" className="knowledge-skills-help">
            This is an inspection-only view. Selecting a module here does not enable or edit it.
          </p>
          <div className="knowledge-skills-detail-list">
            <div>
              <span>Global access after save</span>
              <p>
                {selectedModule.catalog_available === false
                  ? "Catalog unavailable; access cannot be changed here"
                  : selectedModuleEnabled ? "Enabled across chats" : "Off across chats"}
                {selectedModule.catalog_available !== false &&
                  selectedModuleEnabled !== selectedModuleSavedEnabled
                  ? " (unsaved change)"
                  : ""}
              </p>
            </div>
            <div>
              <span>Package ownership</span>
              <p><strong>{selectedModuleSource.label}.</strong> {selectedModuleSource.description}</p>
            </div>
            <div><span>Availability</span><p>{moduleStatusLabel(selectedModule.status)}</p></div>
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
            User-owned add-on packages are discovered from <code>{workflowCatalog.addons_root || DEFAULT_WORKFLOW_CATALOG.addons_root}</code>.
            Package creation and editing are not available on this page yet.
          </p>
        </section>
      </div>}

      {workspaceView === "skills" && <section
        id="knowledge-skills-panel-skills"
        className="knowledge-skills-card knowledge-skills-editor"
        role="tabpanel"
        aria-labelledby="knowledge-skills-tab-skills"
        aria-label="Skill documents"
        tabIndex={0}
      >
        <div className="knowledge-skills-card-head">
          <div>
            <span className="knowledge-skills-step">Local guidance editor</span>
            <h3>Skill documents</h3>
            <p>
              View the guidance Float can read, then create a user-owned local override when you
              need changes. Packaged documents always remain read-only.
            </p>
          </div>
          <span className="knowledge-skills-badge">Guidance source: {selectedSkillSource.label}</span>
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
              disabled={skillBusy}
              aria-describedby="knowledge-skill-source-help"
            >
              {skillOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {`${option.moduleRefs.length
                    ? `${option.moduleRefs[0].label} (${option.id})`
                    : `${option.label} (${option.id})`} - ${sourceDetails(
                    option.id === selectedSkillId && skillDoc?.active?.source
                      ? skillDoc.active.source
                      : option.source,
                    SKILL_SOURCE_DETAILS,
                    option.source ? `${option.source} source` : "Source unknown",
                  ).label}`}
                </option>
              ))}
            </select>

            <p id="knowledge-skill-source-help" className="knowledge-skills-ownership-note">
              <strong>{selectedSkillSource.label}.</strong> {selectedSkillSource.description}
            </p>

            <div className="knowledge-skills-meta">
              <span>Saved docs <strong>{workflowSkills.length}</strong></span>
              <span>Local override <strong>{skillDoc?.local_exists ? "Saved" : "None"}</strong></span>
              <span>Packaged fallback <strong>{skillDoc?.repo_exists ? "Available" : "None"}</strong></span>
              <span>Linked modules <strong>{selectedSkillModules.length ? selectedSkillModules.map((module) => module.label).join(", ") : "none"}</strong></span>
            </div>

            <div className="knowledge-skills-new">
              <label htmlFor="knowledge-new-skill-id">Create a local skill draft by id</label>
              <input
                id="knowledge-new-skill-id"
                type="text"
                placeholder="incident_triage"
                value={newSkillId}
                onChange={(event) => {
                  setNewSkillId(event.target.value);
                  setSkillMessage("");
                }}
                disabled={skillBusy}
                aria-invalid={newSkillIdError ? "true" : undefined}
                aria-describedby={
                  newSkillIdError
                    ? "knowledge-new-skill-error knowledge-new-skill-help"
                    : "knowledge-new-skill-help"
                }
              />
              <button
                type="button"
                className="icon-btn"
                onClick={handleNewSkillStart}
                disabled={
                  skillBusy ||
                  !normalizedNewSkillId ||
                  Boolean(newSkillIdError)
                }
              >
                Start unsaved local draft
              </button>
              {newSkillIdError && (
                <small id="knowledge-new-skill-error" className="knowledge-skills-inline-error">
                  {newSkillIdError}
                </small>
              )}
              <small id="knowledge-new-skill-help">
                This opens unlinked guidance in the editor. Saving creates a local document, but a
                module must be configured separately before it can use the guidance; there is no
                skill enable switch. Its first save is create-only and will not replace a document
                created elsewhere in the meantime.
              </small>
            </div>
          </div>

          <div className="knowledge-skills-editor-body">
            <div className="knowledge-skills-editor-label">
              <label htmlFor="knowledge-skill-editor">Markdown guidance</label>
              <span className={skillDirty ? "is-pending" : ""}>{skillEditorState}</span>
            </div>
            <textarea
              id="knowledge-skill-editor"
              aria-label="Skill markdown editor"
              aria-describedby="knowledge-skill-editor-help"
              value={skillDraft}
              onChange={(event) => setSkillDraft(event.target.value)}
              disabled={skillBusy || !selectedSkillId}
              spellCheck={false}
            />
            {skillDoc?.local_path && (
              <p className="knowledge-skills-path" title={skillDoc.local_path}>
                {skillDoc?.local_exists ? "Saved at" : "Will save to"} <code>{skillDoc.local_path}</code>
              </p>
            )}
            <p id="knowledge-skill-editor-help" className="knowledge-skills-help">
              Typing never changes the packaged copy. Saving writes only this skill&apos;s user-owned
              local document. {selectedSkillModules.length
                ? `It is linked to ${selectedSkillModules.map((module) => module.label).join(", ")}; module access is managed separately.`
                : "This guidance is currently unlinked, so saving it does not enable a capability."}
              {skillPreviewSaveMode === "create_only"
                ? " This draft is create-only: if its target now exists, Save stops without overwriting it."
                : ""}
            </p>
            <div className="knowledge-skills-actions">
              <button type="button" className="icon-btn knowledge-skills-action is-primary" onClick={handleSkillSave} disabled={skillBusy || !selectedSkillId || !skillDirty} aria-describedby="knowledge-skill-action-help">
                {skillSaving
                  ? "Saving..."
                  : skillDoc?.local_exists
                    ? "Save changes"
                    : skillDoc?.repo_exists ? "Save local override" : "Save local skill"}
              </button>
              {selectedSkillModules.length > 0 && (
                <button type="button" className="icon-btn knowledge-skills-action" onClick={handleManageLinkedModules} disabled={skillBusy} aria-describedby="knowledge-skill-action-help">
                  Manage linked module{selectedSkillModules.length === 1 ? "" : "s"}
                </button>
              )}
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleSkillReflectionDraft} disabled={skillBusy || !selectedSkillId} aria-describedby="knowledge-skill-action-help">
                {skillDrafting ? "Drafting suggestion..." : "Draft suggestion (no save)"}
              </button>
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleSkillReload} disabled={skillBusy || !selectedSkillId} aria-describedby="knowledge-skill-action-help">
                {skillDirty ? "Discard changes & reload" : "Reload saved source"}
              </button>
              <button type="button" className="icon-btn knowledge-skills-action is-danger" onClick={handleSkillDelete} disabled={skillBusy || !selectedSkillId || !skillDoc?.local_exists} aria-describedby="knowledge-skill-action-help">
                {skillDoc?.repo_exists ? "Restore packaged version" : "Delete local skill"}
              </button>
              {skillMessage && <span role="status">{skillMessage}</span>}
            </div>
            <p id="knowledge-skill-action-help" className="knowledge-skills-help">
              Draft suggestion runs one audited reflection with the selected model and loads only
              unsaved text. Restore deletes only the local override; deleting a local-only skill
              removes its user-owned file. Reload discards editor changes after confirmation.
              {skillPreviewSaveMode === "create_only"
                ? " Create-only Save preserves this draft if the target conflicts."
                : ""}
            </p>
          </div>
        </div>

        <details className="knowledge-skills-lifecycle">
          <summary>Duplicate, rename, import &amp; export</summary>
          <p className="knowledge-skills-help">
            Duplicate and import load reviewable unsaved drafts. Only the main Save action writes
            those drafts to the local skills directory.
          </p>
          <div className="knowledge-skills-lifecycle-grid">
            <section aria-labelledby="knowledge-skill-duplicate-heading">
              <h4 id="knowledge-skill-duplicate-heading">Duplicate as a draft</h4>
              <label htmlFor="knowledge-skill-duplicate-id">New target id</label>
              <input
                id="knowledge-skill-duplicate-id"
                type="text"
                value={duplicateTargetId}
                placeholder="computer_use_custom"
                onChange={(event) => {
                  setDuplicateTargetId(event.target.value);
                  setSkillMessage("");
                }}
                disabled={skillBusy}
                aria-invalid={duplicateTargetIdError ? "true" : undefined}
                aria-describedby="knowledge-skill-duplicate-help"
              />
              <small id="knowledge-skill-duplicate-help" className={duplicateTargetIdError ? "knowledge-skills-inline-error" : ""}>
                {duplicateTargetIdError || "Copies the current guidance into an unsaved target; it does not create a file."}
              </small>
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleDuplicatePreview} disabled={skillBusy || !skillDoc?.active || !normalizedDuplicateTargetId || Boolean(duplicateTargetIdError)}>
                {lifecycleAction === "duplicate" ? "Preparing preview..." : "Preview duplicate"}
              </button>
            </section>

            <section aria-labelledby="knowledge-skill-rename-heading">
              <h4 id="knowledge-skill-rename-heading">Rename local skill</h4>
              <label htmlFor="knowledge-skill-rename-id">New id</label>
              <input
                id="knowledge-skill-rename-id"
                type="text"
                value={renameTargetId}
                placeholder="incident_response"
                onChange={(event) => {
                  setRenameTargetId(event.target.value);
                  setSkillMessage("");
                }}
                disabled={skillBusy || !renameAllowed}
                aria-invalid={renameTargetIdError ? "true" : undefined}
                aria-describedby="knowledge-skill-rename-help"
              />
              <small id="knowledge-skill-rename-help" className={renameTargetIdError ? "knowledge-skills-inline-error" : ""}>
                {renameTargetIdError || (renameAllowed
                  ? skillDirty
                    ? "Save or discard the current draft before renaming."
                    : "The server will rename only when ownership and module references make it safe."
                  : skillDoc?.rename_reason || skillDoc?.rename_block_reason || "Rename is unavailable for this document.")}
              </small>
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleSkillRename} disabled={skillBusy || !renameAllowed || skillDirty || !normalizedRenameTargetId || Boolean(renameTargetIdError)}>
                {lifecycleAction === "rename" ? "Renaming..." : "Rename local skill"}
              </button>
            </section>

            <section aria-labelledby="knowledge-skill-import-heading">
              <h4 id="knowledge-skill-import-heading">Import as a draft</h4>
              <label htmlFor="knowledge-skill-import-id">Target id</label>
              <input
                id="knowledge-skill-import-id"
                type="text"
                value={importTargetId}
                placeholder="incident_triage"
                onChange={(event) => {
                  setImportTargetId(event.target.value);
                  setSkillMessage("");
                }}
                disabled={skillBusy}
                aria-invalid={importTargetIdError ? "true" : undefined}
                aria-describedby="knowledge-skill-import-help"
              />
              <label htmlFor="knowledge-skill-import-file">Markdown or text file</label>
              <input
                id="knowledge-skill-import-file"
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={(event) => {
                  setImportFile(event.target.files?.[0] || null);
                  setSkillMessage("");
                }}
                disabled={skillBusy}
                aria-describedby="knowledge-skill-import-help"
              />
              <small id="knowledge-skill-import-help" className={importTargetIdError ? "knowledge-skills-inline-error" : ""}>
                {importTargetIdError || "The selected file is validated by the server and returned as an unsaved preview."}
              </small>
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleImportPreview} disabled={skillBusy || !importFile || !normalizedImportTargetId || Boolean(importTargetIdError)}>
                {lifecycleAction === "import" ? "Preparing preview..." : "Preview import"}
              </button>
            </section>

            <section aria-labelledby="knowledge-skill-export-heading">
              <h4 id="knowledge-skill-export-heading">Export active guidance</h4>
              <p className="knowledge-skills-help">
                Downloads the active packaged or local guidance as a markdown file. It does not
                change ownership or module access.
              </p>
              <button type="button" className="icon-btn knowledge-skills-action is-quiet" onClick={handleSkillExport} disabled={skillBusy || !skillDoc?.active}>
                {lifecycleAction === "export" ? "Exporting..." : "Export markdown"}
              </button>
            </section>
          </div>
        </details>
      </section>}
    </section>
  );
};

export default KnowledgeSkillsTab;
