import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { ControlField, IconAction } from "../ui/UiControls";

describe("shared UI controls", () => {
  it("associates the visible field label and help with its input", () => {
    render(
      <ControlField label="Search method" hint="Search document names or their meaning.">
        <select defaultValue="names"><option value="names">Names</option></select>
      </ControlField>,
    );
    expect(screen.getByRole("combobox", { name: "Search method" }))
      .toHaveAccessibleDescription("Search document names or their meaning.");
  });

  it("keeps an action explanation on the focusable button without submitting a form", () => {
    const submit = vi.fn((event) => event.preventDefault());
    const click = vi.fn();
    render(
      <form onSubmit={submit}>
        <IconAction label="Voice message" tooltip="Stopping transcribes and sends it." onClick={click}>
          Voice message
        </IconAction>
      </form>,
    );
    const button = screen.getByRole("button", { name: "Voice message" });
    expect(button).toHaveAccessibleDescription("Stopping transcribes and sends it.");
    fireEvent.click(button);
    expect(click).toHaveBeenCalledOnce();
    expect(submit).not.toHaveBeenCalled();
  });
});
