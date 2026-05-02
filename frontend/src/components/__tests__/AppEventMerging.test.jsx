import { describe, expect, it } from "vitest";

import {
  appendAgentEvent,
  buildParentConversationLink,
  buildSubchatLinksByMessage,
  eventsShareMessageScope,
} from "../App";

describe("App event merging", () => {
  it("keeps simultaneous sessions from merging the same chain", () => {
    const existing = [
      {
        type: "stream",
        session_id: "sess-a",
        agent_id: "sess-a",
        chain_id: "chain-1",
        content: "first",
      },
    ];

    const next = appendAgentEvent(existing, {
      type: "stream",
      session_id: "sess-b",
      agent_id: "sess-b",
      chain_id: "chain-1",
      content: "second",
    });

    expect(next).toHaveLength(2);
    expect(next[0].content).toBe("first");
    expect(next[1].content).toBe("second");
  });

  it("still merges chunks from the same session and message scope", () => {
    const existing = [
      {
        type: "content",
        session_id: "sess-a",
        agent_id: "sess-a",
        message_id: "msg-1",
        content: "hello ",
      },
    ];

    const next = appendAgentEvent(existing, {
      type: "content",
      session_id: "sess-a",
      agent_id: "sess-a",
      chain_id: "msg-1",
      content: "world",
    });

    expect(next).toHaveLength(1);
    expect(next[0].content).toBe("hello world");
  });

  it("treats conflicting agent ids as separate scopes", () => {
    expect(
      eventsShareMessageScope(
        { type: "content", session_id: "sess-a", agent_id: "agent-1", message_id: "msg-1" },
        { type: "content", session_id: "sess-a", agent_id: "agent-2", message_id: "msg-1" },
        { type: "content" },
      ),
    ).toBe(false);
  });

  it("indexes subchat conversations by their parent message", () => {
    const index = buildSubchatLinksByMessage(
      [
        {
          name: "task-plan",
          display_name: "Plan branch",
          message_count: 2,
          updated_at: "2026-04-16T22:00:00Z",
          provenance: {
            kind: "subchat",
            parent_session_id: "sess-parent",
            parent_message_id: "assistant-1",
            branch_session_id: "task-plan",
          },
        },
        {
          name: "other-session-task",
          display_name: "Other",
          provenance: {
            kind: "subchat",
            parent_session_id: "sess-other",
            parent_message_id: "assistant-1",
            branch_session_id: "other-session-task",
          },
        },
      ],
      "sess-parent",
    );

    expect(index).toEqual({
      "assistant-1": [
        {
          id: "task-plan",
          conversationId: "task-plan",
          label: "Plan branch",
          kind: "subchat",
          messageCount: 2,
          updatedAt: "2026-04-16T22:00:00Z",
        },
      ],
    });
  });

  it("builds a parent conversation link for active subchats", () => {
    const link = buildParentConversationLink(
      [
        {
          name: "sess-parent",
          display_name: "Main chat",
        },
        {
          name: "task-child",
          display_name: "Child chat",
          provenance: {
            kind: "subchat",
            parent_session_id: "sess-parent",
            parent_message_id: "assistant-1",
            branch_session_id: "task-child",
          },
        },
      ],
      "task-child",
    );

    expect(link).toEqual({
      conversationId: "sess-parent",
      label: "Main chat",
      kind: "subchat",
      parentMessageId: "assistant-1",
    });
  });
});
