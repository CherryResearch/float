import { render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import ToolPayloadView from "../ToolPayloadView";

describe("ToolPayloadView workspace links", () => {
  it("links managed workspace paths to Knowledge documents", () => {
    render(
      <ToolPayloadView
        toolName="write_file"
        value={{ path: "data/workspace/hello.txt", status: "written" }}
      />,
    );

    const link = screen.getByRole("link", { name: "data/workspace/hello.txt" });
    expect(link).toHaveAttribute(
      "href",
      "/knowledge?tab=documents&id=data%2Fworkspace%2Fhello.txt",
    );
  });

  it("links nested list_dir entries using the parent workspace scope", () => {
    render(
      <ToolPayloadView
        toolName="list_dir"
        value={{
          scope: "workspace",
          root: "D:/notebooks/float_dev/data/workspace",
          entries: [{ name: "raw.txt", path: "notes/raw.txt", type: "file" }],
        }}
      />,
    );

    const entry = screen.getByText("raw.txt").closest(".tool-list-item");
    const link = within(entry).getByRole("link", { name: "notes/raw.txt" });
    expect(link).toHaveAttribute(
      "href",
      "/knowledge?tab=documents&id=data%2Fworkspace%2Fnotes%2Fraw.txt",
    );
  });
});
