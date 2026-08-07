import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import axios from "axios";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../main", async () => {
  const ReactModule = await import("react");
  return {
    GlobalContext: ReactModule.createContext({ state: {}, setState: vi.fn() }),
  };
});

import { GlobalContext } from "../../main";

let KnowledgeSkillsTab;
let skillCatalog;
let computerSkillDoc;
let incidentSkillDoc;
let skillCatalogError;

const workflowCatalog = {
  workflows: [
    {
      id: "default",
      label: "Default",
      description: "Balanced guidance for ordinary foreground chat.",
      profile_kind: "foreground",
      guidance_style: "balanced",
      thinking_default: "auto",
      selectable_in_chat: true,
      selectable_as_default: true,
      automatic_delegation: false,
      tool_scope: "global",
      module_scope: "global",
      enabled_modules: [],
    },
    {
      id: "mini_execution",
      label: "Mini Execution",
      description: "Short execution bursts.",
      profile_kind: "foreground",
      guidance_style: "execution",
      thinking_default: "low",
      selectable_in_chat: true,
      selectable_as_default: true,
      automatic_delegation: false,
      tool_scope: "global",
      module_scope: "global",
      enabled_modules: [],
    },
    {
      id: "background_reflection",
      label: "Background Reflection",
      description: "System-only reflection guidance.",
      profile_kind: "system",
      guidance_style: "reflection",
      thinking_default: "low",
      selectable_in_chat: false,
      selectable_as_default: false,
      automatic_delegation: false,
      tool_scope: "global",
      module_scope: "global",
      enabled_modules: [],
    },
  ],
  modules: [
    {
      id: "computer_use",
      label: "Computer Use",
      description: "Browser and desktop actions.",
      status: "live",
      source: "base",
      skill_id: "computer_use",
      doc_id: "skills:computer_use",
      tool_names: ["computer.observe", "computer.act"],
    },
    {
      id: "container_orchestration",
      label: "Container Orchestration",
      description: "Manage local container jobs.",
      status: "experimental",
      source: "custom",
      skill_id: "container_orchestration",
      doc_id: "skills:container_orchestration",
      tool_names: ["containers.list"],
    },
  ],
  addons: [],
  addons_root: "data/modules/addons",
};

const renderTab = (
  setState = vi.fn(),
  route = "/knowledge?tab=skills",
  stateOverrides = {},
) =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <GlobalContext.Provider
        value={{
          state: {
            workflowProfile: "default",
            enabledWorkflowModules: ["computer_use"],
            transformerModel: "thinkingmachines/Inkling",
            ...stateOverrides,
          },
          setState,
        }}
      >
        <KnowledgeSkillsTab />
      </GlobalContext.Provider>
    </MemoryRouter>,
  );

beforeAll(async () => {
  KnowledgeSkillsTab = (await import("../KnowledgeSkillsTab")).default;
});

describe("KnowledgeSkillsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    skillCatalogError = null;
    computerSkillDoc = {
      id: "computer_use",
      doc_id: "skills:computer_use",
      repo_exists: true,
      local_exists: false,
      rename_allowed: false,
      rename_reason: "Packaged skills keep their id.",
      local_path: "D:/notebooks/float_dev/data/modules/skills/computer_use.md",
      active: {
        id: "computer_use",
        label: "computer use",
        source: "repo",
        body: "# Computer Use\n\nBase guidance.",
      },
    };
    incidentSkillDoc = {
      id: "incident_triage",
      doc_id: "skills:incident_triage",
      repo_exists: false,
      local_exists: false,
      rename_allowed: false,
      rename_reason: "Save this local skill before renaming it.",
      local_path: "D:/notebooks/float_dev/data/modules/skills/incident_triage.md",
      active: null,
    };
    skillCatalog = {
      count: 2,
      skills_root: "D:/notebooks/float_dev/modules/skills",
      skills: [
        {
          id: "computer_use",
          label: "computer use",
          source: "repo",
        },
        {
          id: "troubleshooting_notes",
          label: "troubleshooting notes",
          source: "local",
        },
      ],
    };

    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/workflows/catalog") {
        return Promise.resolve({ data: workflowCatalog });
      }
      if (url === "/api/workflows/skills") {
        return skillCatalogError
          ? Promise.reject(skillCatalogError)
          : Promise.resolve({ data: skillCatalog });
      }
      if (url === "/api/workflows/skills/computer_use") {
        return Promise.resolve({ data: computerSkillDoc });
      }
      if (url === "/api/workflows/skills/incident_triage") {
        return Promise.resolve({ data: incidentSkillDoc });
      }
      return Promise.resolve({ data: { active: null } });
    });
    vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/workflows/skills/computer_use/draft") {
        return Promise.resolve({
          data: {
            status: "drafted",
            proposal: {
              body: "# Computer Use\n\n## Core loop\n\n- Observe before acting.",
              source: "background_reflection",
              requires_user_save: true,
            },
            audit: {
              task_id: "thought-skill-draft",
              run_id: "run-skill-draft",
              wrote_skill_file: false,
              reasoning_trace: {
                preserved: true,
                entries: 2,
                characters: 48,
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    vi.spyOn(axios, "put").mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/computer_use") {
        computerSkillDoc = {
          ...computerSkillDoc,
          local_exists: true,
          active: { ...computerSkillDoc.active, source: "local", body: body.body },
        };
        return Promise.resolve({ data: computerSkillDoc });
      }
      if (url === "/api/workflows/skills/incident_triage") {
        incidentSkillDoc = {
          ...incidentSkillDoc,
          local_exists: true,
          rename_allowed: true,
          rename_reason: "",
          active: {
            id: "incident_triage",
            label: "incident triage",
            source: "local",
            body: body.body,
          },
        };
        skillCatalog = {
          ...skillCatalog,
          count: 3,
          skills: [
            ...skillCatalog.skills,
            { id: "incident_triage", label: "incident triage", source: "local" },
          ],
        };
        return Promise.resolve({ data: incidentSkillDoc });
      }
      return Promise.resolve({ data: {} });
    });
    vi.spyOn(axios, "delete").mockImplementation((url) => {
      if (url === "/api/workflows/skills/computer_use") {
        computerSkillDoc = {
          ...computerSkillDoc,
          local_exists: false,
          active: { ...computerSkillDoc.active, source: "repo", body: "# Computer Use\n\nBase guidance." },
        };
        return Promise.resolve({ data: computerSkillDoc });
      }
      if (url === "/api/workflows/skills/incident_triage") {
        incidentSkillDoc = {
          ...incidentSkillDoc,
          local_exists: false,
          rename_allowed: false,
          rename_reason: "Save this local skill before renaming it.",
          active: null,
        };
        skillCatalog = {
          ...skillCatalog,
          count: 2,
          skills: skillCatalog.skills.filter((skill) => skill.id !== "incident_triage"),
        };
        return Promise.resolve({ data: incidentSkillDoc });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders workflow and module controls while preserving partial catalog success", async () => {
    skillCatalogError = new Error("Skill catalog unavailable");
    renderTab();

    expect(await screen.findByRole("heading", { name: /skills & workflows/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /workflows & modules/i }));
    expect(
      await screen.findByLabelText(/enable container orchestration tools across chats/i),
    ).not.toBeChecked();
    expect(screen.getByText(/skill documents are unavailable/i)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith("/api/workflows/catalog");
    expect(axios.get).toHaveBeenCalledWith("/api/workflows/skills");
  });

  it("opens the workflows workspace from a direct view query", async () => {
    renderTab(vi.fn(), "/knowledge?tab=skills&view=workflows");

    expect(
      await screen.findByRole("tab", { name: /workflows & modules/i }),
    ).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByLabelText(/default chat workflow/i)).toBeInTheDocument();
  });

  it("keeps system workflows visible for comparison but out of default selection", async () => {
    renderTab(vi.fn(), "/knowledge?tab=skills&view=workflows");

    const select = await screen.findByLabelText(/default chat workflow/i);
    expect(
      within(select).queryByRole("option", { name: "Background Reflection" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/does not launch workers, switch models, or grant tools/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/compare workflow profiles/i));
    expect(screen.getByText("Background Reflection")).toBeInTheDocument();
    expect(screen.getByText("system-only / inspect")).toBeInTheDocument();
    expect(screen.queryByText(/Continue:/i)).not.toBeInTheDocument();
  });

  it("makes module ownership, global scope, and unsaved switch state explicit", async () => {
    renderTab(vi.fn(), "/knowledge?tab=skills&view=workflows");

    const moduleToggle = await screen.findByLabelText(
      /enable container orchestration tools across chats/i,
    );
    const moduleRow = moduleToggle.closest(".knowledge-skills-module-row");
    expect(within(moduleRow).getByText("Local add-on")).toBeInTheDocument();
    expect(within(moduleRow).getByText("Experimental")).toBeInTheDocument();
    expect(within(moduleRow).getByText("Off globally")).toBeInTheDocument();
    expect(screen.getByText(/normal tool approval rules still apply/i)).toBeInTheDocument();

    fireEvent.click(moduleToggle);

    expect(
      screen.getByLabelText(/disable container orchestration tools across chats/i),
    ).toBeChecked();
    expect(within(moduleRow).getByText(/will enable after save/i)).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/module to inspect/i), {
      target: { value: "container_orchestration" },
    });
    expect(screen.getAllByText("Local add-on").length).toBeGreaterThan(1);
    expect(screen.getByText(/package creation and editing are not available/i)).toBeInTheDocument();
  });

  it("separates module inspection from the explicit access switch", async () => {
    renderTab(vi.fn(), "/knowledge?tab=skills&view=workflows");

    const moduleToggle = await screen.findByLabelText(
      /enable container orchestration tools across chats/i,
    );
    const moduleRow = moduleToggle.closest(".knowledge-skills-module-row");
    const inspectButton = within(moduleRow).getByRole("button", {
      name: /inspect container orchestration module package/i,
    });

    fireEvent.click(inspectButton);

    expect(moduleToggle).not.toBeChecked();
    expect(screen.getByLabelText(/module to inspect/i)).toHaveValue("container_orchestration");
    expect(moduleRow).toHaveClass("is-inspected");

    fireEvent.click(within(moduleRow).getByText(/manage local container jobs/i));
    expect(moduleToggle).not.toBeChecked();
  });

  it("hands a linked skill off to the selected and focused module", async () => {
    computerSkillDoc = {
      ...computerSkillDoc,
      id: "COMPUTER_USE",
      linked_modules: [{ id: "COMPUTER_USE" }],
    };
    renderTab();

    const manageButton = await screen.findByRole("button", { name: /manage linked module/i });
    fireEvent.click(manageButton);

    const workflowTab = screen.getByRole("tab", { name: /workflows & modules/i });
    const inspectButton = await screen.findByRole("button", {
      name: /inspect computer use module package/i,
    });
    expect(workflowTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText(/module to inspect/i)).toHaveValue("computer_use");
    expect(inspectButton).toHaveFocus();
    expect(inspectButton.closest(".knowledge-skills-module-row")).toHaveClass("is-inspected");
  });

  it("keeps payload-linked modules visible and inspectable when the module catalog fails", async () => {
    computerSkillDoc = {
      ...computerSkillDoc,
      id: "COMPUTER_USE",
      local_exists: true,
      linked_modules: [{ id: "LEGACY_BRIDGE", label: "Legacy Bridge" }],
      active: {
        ...computerSkillDoc.active,
        source: "local",
      },
    };
    const originalGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, ...args) => {
      if (url === "/api/workflows/catalog") {
        return Promise.reject(new Error("Module catalog unavailable"));
      }
      return originalGet(url, ...args);
    });
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    await waitFor(() => {
      expect(within(editorPanel).getByText(/linked modules/i)).toHaveTextContent(
        "Legacy Bridge",
      );
    });
    window.confirm.mockReturnValueOnce(false);
    fireEvent.click(
      within(editorPanel).getByRole("button", { name: /restore packaged version/i }),
    );
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringMatching(/linked module Legacy Bridge/i),
    );

    fireEvent.click(
      within(editorPanel).getByRole("button", { name: /manage linked module/i }),
    );
    const inspectButton = await screen.findByRole("button", {
      name: /inspect Legacy Bridge module package/i,
    });
    expect(screen.getByRole("tab", { name: /workflows & modules/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText(/module to inspect/i)).toHaveValue("LEGACY_BRIDGE");
    expect(inspectButton).toHaveFocus();
    expect(screen.getByText(/catalog unavailable; access cannot be changed here/i)).toBeInTheDocument();
  });

  it("treats an explicit empty linked-modules payload as authoritative", async () => {
    computerSkillDoc = {
      ...computerSkillDoc,
      id: "COMPUTER_USE",
      linked_modules: [],
    };
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    await waitFor(() => {
      expect(within(editorPanel).getByText(/linked modules/i)).toHaveTextContent("none");
    });
    expect(
      within(editorPanel).queryByRole("button", { name: /manage linked module/i }),
    ).not.toBeInTheDocument();
  });

  it("surfaces and lets users remove saved modules missing from the catalog", async () => {
    renderTab(
      vi.fn(),
      "/knowledge?tab=skills&view=workflows",
      { enabledWorkflowModules: ["legacy_module"] },
    );

    expect(await screen.findByText(/0 of 2 enabled/i)).toBeInTheDocument();
    expect(screen.getByText(/1 unavailable saved/i)).toBeInTheDocument();
    const legacyToggle = screen.getByLabelText(/disable unavailable legacy_module module/i);
    expect(legacyToggle).toBeChecked();

    fireEvent.click(legacyToggle);
    expect(screen.getByText(/will remove after save/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save workflow & module settings/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        default_workflow: "default",
        enabled_workflow_modules: [],
      });
    });
  });

  it("supports arrow-key navigation between the two workspace tabs", async () => {
    renderTab();

    const skillTab = await screen.findByRole("tab", { name: /skill docs/i });
    const workflowTab = screen.getByRole("tab", { name: /workflows & modules/i });
    skillTab.focus();
    fireEvent.keyDown(skillTab, { key: "ArrowRight" });

    await waitFor(() => {
      expect(workflowTab).toHaveAttribute("aria-selected", "true");
      expect(workflowTab).toHaveFocus();
    });
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      "knowledge-skills-tab-workflows",
    );
  });

  it("saves workflow defaults independently from capture settings", async () => {
    const setState = vi.fn();
    renderTab(setState);

    fireEvent.click(await screen.findByRole("tab", { name: /workflows & modules/i }));
    fireEvent.change(await screen.findByLabelText(/default chat workflow/i), {
      target: { value: "mini_execution" },
    });
    fireEvent.click(screen.getByLabelText(/enable container orchestration tools across chats/i));
    fireEvent.click(screen.getByRole("button", { name: /save workflow & module settings/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/user-settings", {
        default_workflow: "mini_execution",
        enabled_workflow_modules: ["computer_use", "container_orchestration"],
      });
    });
    expect(setState).toHaveBeenCalled();
    expect(await screen.findByText(/workflow defaults saved/i)).toBeInTheDocument();
  });

  it("edits and deletes an existing local skill override", async () => {
    renderTab();

    const editor = await screen.findByLabelText(/skill markdown editor/i);
    expect(editor).toHaveValue("# Computer Use\n\nBase guidance.");
    fireEvent.change(editor, { target: { value: "# Computer Use\n\nLocal guidance." } });
    fireEvent.click(screen.getByRole("button", { name: /save local override/i }));

    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/workflows/skills/computer_use", {
        body: "# Computer Use\n\nLocal guidance.",
      });
    });
    expect(await screen.findByText(/local skill override saved/i)).toBeInTheDocument();
    expect(screen.getByText(/saved at/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /restore packaged version/i }));
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringMatching(/permanently deletes the user-owned local override.*packaged guidance/i),
    );
    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/workflows/skills/computer_use");
    });
    expect(
      await screen.findByText(/local override removed; packaged version is active again/i),
    ).toBeInTheDocument();
  });

  it("creates, saves, and deletes a new local-only skill document", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    expect(within(editorPanel).getByText(/Saved docs/i)).toHaveTextContent("2");
    fireEvent.change(within(editorPanel).getByLabelText(/create a local skill draft by id/i), {
      target: { value: "incident_triage" },
    });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /start unsaved local draft/i }));

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith("/api/workflows/skills/incident_triage");
    });
    const body = "# Incident Triage\n\nInspect the highest-impact failure first.";
    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.change(editor, { target: { value: body } });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /save local skill/i }));

    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/workflows/skills/incident_triage", {
        body,
        create_only: true,
      });
    });
    await waitFor(() => {
      expect(within(editorPanel).getByText(/Saved docs/i)).toHaveTextContent("3");
    });

    fireEvent.click(within(editorPanel).getByRole("button", { name: /delete local skill/i }));
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringMatching(/permanently deletes the user-owned file.*not linked to a module/i),
    );
    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/workflows/skills/incident_triage");
    });
    expect(
      await within(editorPanel).findByText(/local skill deleted\. start a new draft to recreate it/i),
    ).toBeInTheDocument();
    expect(editor).toHaveValue("");
  });

  it("uses truthful source, path, and action hierarchy labels", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    expect(within(editorPanel).getByText(/guidance source: packaged \/ read-only/i)).toBeInTheDocument();
    expect(within(editorPanel).getByText(/saved docs/i)).toHaveTextContent("2");
    expect(within(editorPanel).getByText(/will save to/i)).toBeInTheDocument();

    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.change(editor, { target: { value: "# Local override" } });

    expect(
      within(editorPanel).getByRole("button", { name: /save local override/i }),
    ).toHaveClass("is-primary");
    expect(
      within(editorPanel).getByRole("button", { name: /draft suggestion \(no save\)/i }),
    ).toHaveClass("is-quiet");
    expect(
      within(editorPanel).getByRole("button", { name: /restore packaged version/i }),
    ).toHaveClass("is-danger");
  });

  it("loads duplicate preview content as an unsaved no-write draft", async () => {
    const originalPost = axios.post.getMockImplementation();
    const originalPut = axios.put.getMockImplementation();
    axios.post.mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/computer_use/duplicate-preview") {
        return Promise.resolve({
          data: {
            target_id: body.target_id,
            document: {
              id: body.target_id,
              doc_id: `skills:${body.target_id}`,
              repo_exists: false,
              local_exists: false,
              rename_allowed: false,
              local_path: `D:/notebooks/float_dev/data/modules/skills/${body.target_id}.md`,
              active: null,
            },
            proposal: {
              body: "# Computer Use Copy\n\nReview before saving.",
              save_mode: "create_only",
            },
            audit: { wrote_skill_file: false },
          },
        });
      }
      return originalPost(url, body);
    });
    axios.put.mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/computer_use_copy") {
        return Promise.reject({
          response: {
            status: 409,
            data: { detail: "A skill named 'Computer_Use_Copy' already exists." },
          },
        });
      }
      return originalPut(url, body);
    });
    renderTab();

    fireEvent.click(await screen.findByText(/duplicate, rename, import & export/i));
    fireEvent.change(screen.getByLabelText(/new target id/i), {
      target: { value: "computer_use_copy" },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview duplicate/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/workflows/skills/computer_use/duplicate-preview",
        { target_id: "computer_use_copy" },
      );
    });
    expect(await screen.findByLabelText(/skill markdown editor/i)).toHaveValue(
      "# Computer Use Copy\n\nReview before saving.",
    );
    expect(screen.getByLabelText("Skill document")).toHaveValue("computer_use_copy");
    expect(screen.getByText(/duplicate preview loaded.*no skill file was written/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save local skill/i })).toBeEnabled();
    expect(axios.put).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /save local skill/i }));
    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith(
        "/api/workflows/skills/computer_use_copy",
        {
          body: "# Computer Use Copy\n\nReview before saving.",
          create_only: true,
        },
      );
    });
    expect(screen.getByText("A skill named 'Computer_Use_Copy' already exists.")).toBeInTheDocument();
    expect(screen.getByLabelText(/skill markdown editor/i)).toHaveValue(
      "# Computer Use Copy\n\nReview before saving.",
    );
    expect(screen.getByRole("button", { name: /save local skill/i })).toBeEnabled();
  });

  it("loads an imported markdown file as an unsaved no-write draft", async () => {
    const importedBody = "# Imported Triage\n\nEscalate the highest-impact failure.";
    const file = {
      name: "triage.markdown",
      text: vi.fn().mockResolvedValue(importedBody),
    };
    const originalPost = axios.post.getMockImplementation();
    const originalPut = axios.put.getMockImplementation();
    axios.post.mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/import-preview") {
        return Promise.resolve({
          data: {
            target_id: body.target_id,
            document: {
              id: body.target_id,
              doc_id: `skills:${body.target_id}`,
              repo_exists: false,
              local_exists: false,
              rename_allowed: false,
              local_path: `D:/notebooks/float_dev/data/modules/skills/${body.target_id}.md`,
              active: null,
            },
            proposal: { body: body.body, save_mode: "create_only" },
            audit: { wrote_skill_file: false },
          },
        });
      }
      return originalPost(url, body);
    });
    axios.put.mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/imported_triage") {
        return Promise.resolve({
          data: {
            id: "imported_triage",
            doc_id: "skills:imported_triage",
            repo_exists: false,
            local_exists: true,
            rename_allowed: true,
            rename_reason: "",
            linked_modules: [],
            local_path: "D:/notebooks/float_dev/data/modules/skills/imported_triage.md",
            active: {
              id: "imported_triage",
              source: "local",
              body: body.body,
            },
          },
        });
      }
      return originalPut(url, body);
    });
    renderTab();

    fireEvent.click(await screen.findByText(/duplicate, rename, import & export/i));
    fireEvent.change(screen.getByLabelText(/^target id$/i), {
      target: { value: "imported_triage" },
    });
    fireEvent.change(screen.getByLabelText(/markdown or text file/i), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview import/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/workflows/skills/import-preview", {
        filename: "triage.markdown",
        body: importedBody,
        target_id: "imported_triage",
      });
    });
    expect(file.text).toHaveBeenCalled();
    expect(screen.getByLabelText(/skill markdown editor/i)).toHaveValue(importedBody);
    expect(screen.getByText(/import preview loaded.*no skill file was written/i)).toBeInTheDocument();
    expect(axios.put).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /save local skill/i }));
    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/workflows/skills/imported_triage", {
        body: importedBody,
        create_only: true,
      });
    });
  });

  it("blocks rename unless the document payload explicitly allows it", async () => {
    renderTab();

    fireEvent.click(await screen.findByText(/duplicate, rename, import & export/i));
    expect(screen.getByLabelText(/^new id$/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /rename local skill/i })).toBeDisabled();
    expect(screen.getByText(/packaged skills keep their id/i)).toBeInTheDocument();
  });

  it("renames an eligible saved local skill and adopts the returned document", async () => {
    renderTab();
    const editorPanel = await screen.findByLabelText("Skill documents");
    fireEvent.change(within(editorPanel).getByLabelText(/create a local skill draft by id/i), {
      target: { value: "incident_triage" },
    });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /start unsaved local draft/i }));
    await waitFor(() => expect(screen.getByLabelText("Skill document")).toHaveValue("incident_triage"));
    fireEvent.change(screen.getByLabelText(/skill markdown editor/i), {
      target: { value: "# Incident Triage" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save local skill/i }));
    await screen.findByText(/local skill override saved/i);

    const originalPost = axios.post.getMockImplementation();
    axios.post.mockImplementation((url, body) => {
      if (url === "/api/workflows/skills/incident_triage/rename") {
        const newId = body.target_id;
        return Promise.resolve({
          data: {
            new_id: newId,
            document: {
              ...incidentSkillDoc,
              id: newId,
              doc_id: `skills:${newId}`,
              local_path: `D:/notebooks/float_dev/data/modules/skills/${newId}.md`,
              active: { ...incidentSkillDoc.active, id: newId },
            },
          },
        });
      }
      return originalPost(url, body);
    });
    fireEvent.click(screen.getByText(/duplicate, rename, import & export/i));
    fireEvent.change(screen.getByLabelText(/^new id$/i), {
      target: { value: "incident_response" },
    });
    fireEvent.click(screen.getByRole("button", { name: /rename local skill/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/workflows/skills/incident_triage/rename",
        { target_id: "incident_response" },
      );
    });
    expect(screen.getByLabelText("Skill document")).toHaveValue("incident_response");
    expect(screen.getByText(/local skill renamed to incident_response/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/skill markdown editor/i)).toHaveValue("# Incident Triage");
  });

  it("exports active guidance as a downloaded markdown blob", async () => {
    const originalGet = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, config) => {
      if (url === "/api/workflows/skills/computer_use/export") {
        return Promise.resolve({
          data: new Blob(["# Computer Use"], { type: "text/markdown" }),
          headers: { "content-disposition": 'attachment; filename="computer_use.md"' },
        });
      }
      return originalGet(url, config);
    });
    const createObjectURL = vi.fn().mockReturnValue("blob:skill-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(window.URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(window.URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    let downloadedName = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click() {
      downloadedName = this.download;
    });
    renderTab();

    fireEvent.click(await screen.findByText(/duplicate, rename, import & export/i));
    fireEvent.click(screen.getByRole("button", { name: /export markdown/i }));

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith(
        "/api/workflows/skills/computer_use/export",
        { responseType: "blob" },
      );
    });
    expect(createObjectURL).toHaveBeenCalled();
    expect(downloadedName).toBe("computer_use.md");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:skill-export");
    expect(screen.getByText(/exported computer_use\.md/i)).toBeInTheDocument();
  });

  it("labels a module-referenced missing skill as unsaved only after explicit drafting", async () => {
    renderTab();
    const editorPanel = await screen.findByLabelText("Skill documents");
    const skillSelect = within(editorPanel).getByLabelText(/skill document/i);

    fireEvent.change(skillSelect, { target: { value: "container_orchestration" } });

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith(
        "/api/workflows/skills/container_orchestration",
      );
    });
    expect(within(editorPanel).getAllByText("No saved document").length).toBeGreaterThan(0);
    expect(within(editorPanel).queryByText("Unsaved local draft")).not.toBeInTheDocument();

    fireEvent.change(within(editorPanel).getByLabelText(/create a local skill draft by id/i), {
      target: { value: "brand_new_skill" },
    });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /start unsaved local draft/i }));

    await waitFor(() => {
      expect(
        within(editorPanel).getByText(/this draft exists only in the editor/i),
      ).toBeInTheDocument();
    });
  });

  it("loads an audited reflection proposal without saving it", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.click(
      within(editorPanel).getByRole("button", { name: /draft suggestion \(no save\)/i }),
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/workflows/skills/computer_use/draft",
        { model: "thinkingmachines/Inkling" },
      );
    });
    expect(editor).toHaveValue(
      "# Computer Use\n\n## Core loop\n\n- Observe before acting.",
    );
    expect(axios.put).not.toHaveBeenCalled();
    expect(
      within(editorPanel).getByText(/audit: thought-skill-draft/i),
    ).toBeInTheDocument();
    expect(
      within(editorPanel).getByText(/2 reasoning trace parts preserved/i),
    ).toBeInTheDocument();
    expect(
      within(editorPanel).getByText(/loads only unsaved text/i),
    ).toBeInTheDocument();
  });

  it("explains skill ownership and protects a dirty draft from reload", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    const skillSelect = within(editorPanel).getByLabelText(/skill document/i);
    expect(
      within(skillSelect).getByRole("option", { name: /computer use.*packaged \/ read-only/i }),
    ).toBeInTheDocument();
    expect(within(editorPanel).getAllByText(/packaged \/ read-only/i).length).toBeGreaterThan(0);
    expect(
      within(editorPanel).getByText(/editing its text creates a separate user-owned local override/i),
    ).toBeInTheDocument();
    expect(
      within(editorPanel).getByRole("button", { name: /draft suggestion \(no save\)/i }),
    ).toBeInTheDocument();

    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.change(editor, { target: { value: "# Unsaved local change" } });
    expect(
      within(editorPanel).getByRole("button", { name: /save local override/i }),
    ).toBeEnabled();

    window.confirm.mockReturnValueOnce(false);
    fireEvent.click(
      within(editorPanel).getByRole("button", { name: /discard changes & reload/i }),
    );
    expect(window.confirm).toHaveBeenCalledWith(
      "Discard the unsaved changes to this skill document?",
    );
    expect(editor).toHaveValue("# Unsaved local change");
  });

  it("shows inline id guidance and protects unsaved drafts when switching", async () => {
    renderTab();
    const editorPanel = await screen.findByLabelText("Skill documents");
    const newId = within(editorPanel).getByLabelText(/create a local skill draft by id/i);
    fireEvent.change(newId, { target: { value: "bad id" } });
    expect(within(editorPanel).getByText(/use only letters/i)).toBeInTheDocument();

    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.change(editor, { target: { value: "unsaved" } });
    window.confirm.mockReturnValueOnce(false);
    fireEvent.change(within(editorPanel).getByLabelText(/skill document/i), {
      target: { value: "troubleshooting_notes" },
    });
    expect(within(editorPanel).getByLabelText(/skill document/i)).toHaveValue("computer_use");
    expect(editor).toHaveValue("unsaved");
  });

  it("mirrors portable skill-id guidance before a lifecycle request", async () => {
    renderTab();
    const editorPanel = await screen.findByLabelText("Skill documents");
    const newId = within(editorPanel).getByLabelText(/create a local skill draft by id/i);
    const startButton = within(editorPanel).getByRole("button", {
      name: /start unsaved local draft/i,
    });

    fireEvent.change(newId, { target: { value: "README" } });
    expect(within(editorPanel).getByText(/README is reserved for directory documentation/i)).toBeInTheDocument();
    expect(startButton).toBeDisabled();

    fireEvent.change(newId, { target: { value: ".hidden" } });
    expect(within(editorPanel).getByText(/cannot start or end with a dot/i)).toBeInTheDocument();

    fireEvent.change(newId, { target: { value: "CON.notes" } });
    expect(within(editorPanel).getByText(/reserved by Windows/i)).toBeInTheDocument();

    fireEvent.change(newId, { target: { value: "x".repeat(121) } });
    expect(within(editorPanel).getByText(/120 characters or fewer/i)).toBeInTheDocument();

    fireEvent.change(newId, { target: { value: "COMPUTER_USE" } });
    expect(
      within(editorPanel).getByText("A skill named 'computer_use' already exists."),
    ).toBeInTheDocument();
    expect(startButton).toBeDisabled();
  });
});
