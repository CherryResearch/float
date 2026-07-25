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
      description: "Balanced reasoning with normal tool access.",
      thinking_default: "auto",
      preferred_continue: "mini_execution",
      enabled_modules: ["computer_use"],
    },
    {
      id: "mini_execution",
      label: "Mini Execution",
      description: "Short execution bursts.",
      thinking_default: "low",
      preferred_continue: "mini_execution",
      enabled_modules: ["computer_use"],
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

const renderTab = (setState = vi.fn(), route = "/knowledge?tab=skills") =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <GlobalContext.Provider
        value={{
          state: {
            workflowProfile: "default",
            enabledWorkflowModules: ["computer_use"],
            transformerModel: "thinkingmachines/Inkling",
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
        incidentSkillDoc = { ...incidentSkillDoc, local_exists: false, active: null };
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
    expect(await screen.findByLabelText(/container orchestration enabled/i)).not.toBeChecked();
    expect(screen.getByText(/skill documents are unavailable/i)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith("/api/workflows/catalog");
    expect(axios.get).toHaveBeenCalledWith("/api/workflows/skills");
  });

  it("opens the workflows workspace from a direct view query", async () => {
    renderTab(vi.fn(), "/knowledge?tab=skills&view=workflows");

    expect(
      await screen.findByRole("tab", { name: /workflows & modules/i }),
    ).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByLabelText(/default workflow profile/i)).toBeInTheDocument();
  });

  it("saves workflow defaults independently from capture settings", async () => {
    const setState = vi.fn();
    renderTab(setState);

    fireEvent.click(await screen.findByRole("tab", { name: /workflows & modules/i }));
    fireEvent.change(await screen.findByLabelText(/default workflow profile/i), {
      target: { value: "mini_execution" },
    });
    fireEvent.click(screen.getByLabelText(/container orchestration enabled/i));
    fireEvent.click(screen.getByRole("button", { name: /save workflow defaults/i }));

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

    fireEvent.click(screen.getByRole("button", { name: /delete local override/i }));
    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/workflows/skills/computer_use");
    });
    expect(
      await screen.findByText(/local skill override removed; base doc is active again/i),
    ).toBeInTheDocument();
  });

  it("creates, saves, and deletes a new local-only skill document", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    expect(within(editorPanel).getByText(/Known docs/i)).toHaveTextContent("2");
    fireEvent.change(within(editorPanel).getByLabelText(/new local skill id/i), {
      target: { value: "incident_triage" },
    });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /start blank doc/i }));

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith("/api/workflows/skills/incident_triage");
    });
    const body = "# Incident Triage\n\nInspect the highest-impact failure first.";
    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.change(editor, { target: { value: body } });
    fireEvent.click(within(editorPanel).getByRole("button", { name: /save local override/i }));

    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/workflows/skills/incident_triage", { body });
    });
    await waitFor(() => {
      expect(within(editorPanel).getByText(/Known docs/i)).toHaveTextContent("3");
    });

    fireEvent.click(within(editorPanel).getByRole("button", { name: /delete local override/i }));
    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/workflows/skills/incident_triage");
    });
    expect(
      await within(editorPanel).findByText(/local skill doc removed\. save to recreate it locally/i),
    ).toBeInTheDocument();
    expect(editor).toHaveValue("");
  });

  it("loads an audited reflection proposal without saving it", async () => {
    renderTab();

    const editorPanel = await screen.findByLabelText("Skill documents");
    const editor = within(editorPanel).getByLabelText(/skill markdown editor/i);
    fireEvent.click(
      within(editorPanel).getByRole("button", { name: /propose with reflection/i }),
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
      within(editorPanel).getByText(/never writes the skill file until you choose save/i),
    ).toBeInTheDocument();
  });

  it("shows inline id guidance and protects unsaved drafts when switching", async () => {
    renderTab();
    const editorPanel = await screen.findByLabelText("Skill documents");
    const newId = within(editorPanel).getByLabelText(/new local skill id/i);
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
});
