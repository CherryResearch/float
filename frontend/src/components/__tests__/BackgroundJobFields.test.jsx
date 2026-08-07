import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import BackgroundJobFields from "../BackgroundJobFields";

const Harness = () => {
  const [rrule, setRrule] = useState("");
  const [policy, setPolicy] = useState(null);
  return (
    <BackgroundJobFields
      rrule={rrule}
      onRruleChange={setRrule}
      policy={policy}
      onPolicyChange={setPolicy}
      startValue="2026-07-25T20:00"
    />
  );
};

describe("BackgroundJobFields", () => {
  it("edits periodicity and patience as separate controls", () => {
    render(<Harness />);

    fireEvent.change(screen.getByLabelText(/repeat/i), {
      target: { value: "minutes" },
    });
    fireEvent.change(screen.getByLabelText(/^every$/i), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByLabelText(/series ends/i), {
      target: { value: "count" },
    });
    fireEvent.change(screen.getByLabelText(/run count/i), {
      target: { value: "30" },
    });
    fireEvent.change(screen.getByLabelText(/patience/i), {
      target: { value: "until_useful" },
    });

    expect(screen.getByText("Every 2 minutes · 30 runs")).toBeInTheDocument();
    expect(screen.getByLabelText(/attempt limit/i)).toHaveValue(2);
    expect(screen.getByLabelText(/provider retries/i)).toHaveValue(2);
    fireEvent.change(screen.getByLabelText(/provider retries/i), {
      target: { value: "1" },
    });
    expect(screen.getByLabelText(/provider retries/i)).toHaveValue(1);
    expect(screen.getByText(/calendar controls when each occurrence starts/i)).toBeInTheDocument();
    expect(screen.getByText(/currently run once per occurrence/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /recorded execution policy/i }));
    expect(screen.getByLabelText(/requested model/i)).toHaveValue("inherit");
    expect(screen.getByLabelText(/requested workflow/i)).toHaveValue("inherit");
    expect(screen.getByText(/ownership and lineage/i)).toBeInTheDocument();
  });

  it("defaults date-bounded schedules instead of emitting an unbounded rule", () => {
    render(<Harness />);

    fireEvent.change(screen.getByLabelText(/repeat/i), {
      target: { value: "hours" },
    });
    fireEvent.change(screen.getByLabelText(/series ends/i), {
      target: { value: "until" },
    });

    expect(screen.getByLabelText(/last run by/i)).not.toHaveValue("");
    expect(screen.getByText(/^Every hour · until /i)).toBeInTheDocument();
  });
});
