import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import EventForm from "../EventForm";

describe("EventForm", () => {
  it("does not reset fields while typing", () => {
    render(
      <EventForm
        event={null}
        selectedDate={new Date("2025-12-27T22:53:00")}
        isOpen
        onSaved={() => {}}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit \(0\)/i }));
    const actionsDraft = screen.getByPlaceholderText(/type a tool name/i);
    fireEvent.change(actionsDraft, { target: { value: "Write follow-up plan" } });
    expect(actionsDraft).toHaveValue("Write follow-up plan");

    const startInput = screen.getByLabelText(/Start/i);
    fireEvent.change(startInput, { target: { value: "2025-12-27T22:55" } });
    expect(startInput).toHaveValue("2025-12-27T22:55");

    fireEvent.click(
      screen.getByRole("button", { name: /Advanced settings/i }),
    );
    expect(screen.getByText(/Event ID/i)).toBeInTheDocument();

    const idInput = screen.getByPlaceholderText(/auto-generated/i);
    fireEvent.change(idInput, { target: { value: "custom-event-id" } });
    expect(idInput).toHaveValue("custom-event-id");
    expect(screen.getByText(/Event ID/i)).toBeInTheDocument();
    expect(actionsDraft).toHaveValue("Write follow-up plan");
  });
});
