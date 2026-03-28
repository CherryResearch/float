import React from 'react';
import { vi } from "vitest";
import { render, fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../utils/apiClient', () => ({
  startVoiceSession: vi.fn(async () => ({ provider: 'livekit' })),
}));

vi.mock('../../main', () => {
  const React = require('react');
  return {
    GlobalContext: React.createContext({ state: { theme: 'light' }, setState: vi.fn() }),
  };
});

import ChatUI from '../ChatUI';

const renderComponent = () =>
  render(
    <MemoryRouter>
      <ChatUI />
    </MemoryRouter>,
  );

describe('ChatUI snapshots', () => {
  it('renders default view', () => {
    const { asFragment } = renderComponent();
    expect(asFragment()).toMatchSnapshot();
  });

  it('renders live mode active', async () => {
    const { asFragment, getAllByLabelText } = renderComponent();
    fireEvent.click(getAllByLabelText('Activate live mode')[0]);
    await screen.findByLabelText('Exit live mode');
    expect(asFragment()).toMatchSnapshot();
  });

  it('renders after exiting live mode', async () => {
    const { asFragment, getAllByLabelText } = renderComponent();
    fireEvent.click(getAllByLabelText('Activate live mode')[0]);
    const exitButton = await screen.findByLabelText('Exit live mode');
    fireEvent.click(exitButton);
    await screen.findByTestId('chat-history');
    expect(asFragment()).toMatchSnapshot();
  });
});
